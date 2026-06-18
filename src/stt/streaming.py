"""Streaming STT engine (ADR 0003, Phase A).

Consumes a continuous stream of 16 kHz mono PCM16 frames (as they arrive from
the phone over the WebSocket) and turns them into utterance events:

* ``speech_start`` -- VAD crossed into speech.
* ``interim``      -- partial transcript of the last ``interim_window_ms`` of
  audio, emitted every ``interim_interval_ms`` while the user is still talking
  (this is what lets the server catch stop-words / barge-in early).
* ``final``        -- full-utterance transcript after ``vad_min_silence_ms`` of
  trailing silence.

It reuses the existing :class:`~src.stt.STT` backend (Parakeet / faster-whisper)
for the actual ASR by writing the buffered audio to a temp WAV -- no new model.
A dedicated worker thread drains an input queue so feeding audio never blocks the
WebSocket read loop, and ASR (which can take tens of ms) never stalls intake.

Per-instance state (one per connected client) keeps multi-user a config change,
not a rewrite (ADR 0002).
"""
from __future__ import annotations

import logging
import queue
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..config import config
from . import STT
from .vad import make_vad

log = logging.getLogger("robot.stt.stream")


@dataclass
class SttEvent:
    type: str  # speech_start | interim | final | speech_drop
    index: int
    text: str = ""
    duration: float = 0.0


SttSink = Callable[[SttEvent], None]


class StreamingSTT:
    """Feed PCM16 bytes; get speech_start/interim/final events on ``sink``."""

    def __init__(self, stt: STT, sink: SttSink) -> None:
        self._stt = stt
        self._sink = sink
        self._sr = config.conv_sample_rate
        self._frame = int(self._sr * config.vad_frame_ms / 1000)  # samples/frame
        self._vad = make_vad(self._sr)

        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._buf = np.zeros(0, dtype=np.float32)  # leftover < one frame
        self._tmp = Path(tempfile.mkdtemp(prefix="robot-stt-stream-"))

        # Utterance state.
        self._preroll = np.zeros(0, dtype=np.float32)
        self._utt = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_frames = 0
        self._silence_ms = 0.0
        self._index = 0
        self._last_interim = 0.0
        self._interim_text = ""

        self._preroll_max = int(self._sr * config.vad_preroll_ms / 1000)
        self._utt_max = int(self._sr * config.vad_max_utterance_ms / 1000)

        self._running = True
        self._enabled = True
        self._thread = threading.Thread(target=self._run, name="stt-stream", daemon=True)
        self._thread.start()

    # -- public API ------------------------------------------------------

    def feed(self, pcm16: bytes) -> None:
        if self._running:
            self._q.put(pcm16)

    def set_enabled(self, enabled: bool) -> None:
        """Gate VAD processing (e.g. ignore mic while the robot is speaking)."""
        self._enabled = enabled
        if not enabled:
            self._reset_utterance()

    def close(self) -> None:
        self._running = False
        self._q.put(None)

    # -- worker ----------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            item = self._q.get()
            if item is None:
                break
            try:
                self._process(item)
            except Exception:  # noqa: BLE001 - one bad frame must not kill STT
                log.exception("StreamingSTT frame failed")

    def _process(self, pcm16: bytes) -> None:
        if not self._enabled:
            return
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        if samples.size == 0:
            return
        self._buf = np.concatenate([self._buf, samples])
        frame_ms = config.vad_frame_ms
        while self._buf.size >= self._frame:
            frame = self._buf[: self._frame]
            self._buf = self._buf[self._frame :]
            self._on_frame(frame, frame_ms)

    def _on_frame(self, frame: np.ndarray, frame_ms: float) -> None:
        speech = self._vad.is_speech(frame)
        if not self._in_speech:
            # Maintain a rolling preroll so we capture the word onset.
            self._preroll = np.concatenate([self._preroll, frame])[-self._preroll_max :]
            if speech:
                self._speech_frames += 1
                if self._speech_frames >= config.vad_start_frames:
                    self._begin_utterance()
            else:
                self._speech_frames = 0
            return

        # In speech: accumulate and track trailing silence.
        self._utt = np.concatenate([self._utt, frame])
        if speech:
            self._silence_ms = 0.0
        else:
            self._silence_ms += frame_ms

        now = time.perf_counter() * 1000.0
        utt_ms = self._utt.size / self._sr * 1000.0
        if (now - self._last_interim) >= config.interim_interval_ms and \
                utt_ms >= config.interim_min_audio_ms:
            self._last_interim = now
            self._emit_interim()

        if self._silence_ms >= config.vad_min_silence_ms or self._utt.size >= self._utt_max:
            self._end_utterance()

    # -- utterance lifecycle --------------------------------------------

    def _begin_utterance(self) -> None:
        self._in_speech = True
        self._utt = self._preroll.copy()
        self._preroll = np.zeros(0, dtype=np.float32)
        self._silence_ms = 0.0
        self._last_interim = 0.0
        self._interim_text = ""
        self._index += 1
        self._sink(SttEvent("speech_start", self._index))

    def _emit_interim(self) -> None:
        window = int(self._sr * config.interim_window_ms / 1000)
        audio = self._utt[-window:] if self._utt.size > window else self._utt
        text = self._transcribe(audio)
        if text and text != self._interim_text:
            self._interim_text = text
            self._sink(SttEvent("interim", self._index, text=text))

    def _end_utterance(self) -> None:
        audio = self._utt
        index = self._index
        dur = audio.size / self._sr
        self._reset_utterance()
        text = self._transcribe(audio)
        if text:
            self._sink(SttEvent("final", index, text=text, duration=dur))
        else:
            self._sink(SttEvent("speech_drop", index, duration=dur))

    def _reset_utterance(self) -> None:
        self._in_speech = False
        self._speech_frames = 0
        self._silence_ms = 0.0
        self._utt = np.zeros(0, dtype=np.float32)
        self._preroll = np.zeros(0, dtype=np.float32)

    # -- ASR -------------------------------------------------------------

    def _transcribe(self, audio: np.ndarray) -> str:
        if audio.size < int(self._sr * 0.2):  # < 200 ms -> nothing useful
            return ""
        pcm = np.clip(audio * 32768.0, -32768, 32767).astype("<i2")
        path = str(self._tmp / "utt.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self._sr)
            w.writeframes(pcm.tobytes())
        try:
            return self._stt.transcribe(path).strip()
        except Exception:  # noqa: BLE001
            log.exception("interim/final ASR failed")
            return ""
