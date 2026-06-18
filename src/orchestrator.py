"""Event-driven orchestrator (Phase 1).

Replaces the blocking :class:`~src.pipeline.Pipeline` turn with a long-running
object that emits **events** (phase changes, assistant deltas, audio segments,
latency) to any number of subscribers -- the CLI, the web face, a benchmark
logger. It knows nothing about *where* STT/LLM/TTS run (that's the backend
factories) and nothing about *how* events are displayed (that's subscribers).

Key behaviour: while the LLM streams tokens, a sentence chunker splits them into
sentences and a TTS consumer thread synthesizes+plays each sentence as it forms,
so **the first audio plays before the LLM finishes** (the ``first_audio_ms``
metric). LLM timing stays pure (measured in its own thread) so the benchmark
numbers remain honest.

Phase machine:  inactive -> listening -> thinking -> speaking -> inactive
(error from any state).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator, Optional

from .config import config
from .latency import Timings
from .llm import LLM
from .stt import STT
from .tts import TTS
from .tts.streaming import (
    AudioSegment,
    SentenceStreamingTTS,
    get_streaming_tts,
    iter_sentence_pcm,
)

log = logging.getLogger("robot.turn")


class Phase(str, Enum):
    INACTIVE = "inactive"
    LISTENING = "listening"
    HEARING = "hearing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class AudioSink:
    """Streaming-PCM output target (ADR 0003, Phase B).

    When passed to :meth:`Orchestrator.respond`, TTS is streamed to these
    callbacks as raw PCM16LE frames instead of emitting ``tts_audio`` WAV-file
    events. The control server wires these to binary WebSocket frames so the
    phone plays them with the Web Audio API.
    """

    on_start: Callable[[int], None]  # (sample_rate)
    on_pcm: Callable[[bytes], None]  # one PCM16LE frame
    on_done: Callable[[], None]


@dataclass
class Event:
    type: str  # phase | heard_text | assistant_delta | assistant_end | tts_audio | latency | error
    data: dict = field(default_factory=dict)


Subscriber = Callable[[Event], None]
# Optional playback hook: given a wav path, play it (blocking). Host-specific.
Player = Callable[[str], None]


class Orchestrator:
    def __init__(
        self,
        stt: Optional[STT],
        llm: LLM,
        tts: TTS,
        system_prompt: str,
        player: Optional[Player] = None,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.player = player
        self.history: list[dict] = [{"role": "system", "content": system_prompt}]
        self.streaming_tts = get_streaming_tts(tts, config.tts_sentences_per_chunk)
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()
        self.phase = Phase.INACTIVE
        self._cancel = threading.Event()

    # -- pub/sub ---------------------------------------------------------

    def subscribe(self, cb: Subscriber) -> None:
        self._subs.append(cb)

    def _emit(self, type_: str, **data) -> None:
        ev = Event(type_, data)
        for cb in list(self._subs):
            try:
                cb(ev)
            except Exception:  # a bad subscriber must not kill the turn
                pass

    def _set_phase(self, phase: Phase) -> None:
        self.phase = phase
        self._emit("phase", phase=phase.value)

    # -- STT -------------------------------------------------------------

    def transcribe(self, wav_path: str, t: Timings) -> str:
        if self.stt is None:
            raise RuntimeError("No STT backend loaded in this orchestrator.")
        self._set_phase(Phase.LISTENING)
        with t.stage("stt"):
            text = self.stt.transcribe(wav_path)
        log.info("STT  ◀  %r  (%.0fms)", text, t.stages.get("stt", 0.0))
        self._emit("heard_text", text=text)
        return text

    # -- cancellation (barge-in / stop-word) ----------------------------

    def cancel(self) -> None:
        """Cooperatively abort the in-flight turn (barge-in / stop-word).

        Sets a flag the LLM producer and TTS consumer loops check; no thread is
        killed. Safe to call from another thread (the WS reader).
        """
        self._cancel.set()

    # -- LLM + TTS (overlapped) -----------------------------------------

    def respond(self, user_text: str, t: Timings, play: bool = True,
                audio_sink: Optional[AudioSink] = None) -> str:
        """Stream the LLM reply while speaking sentences as they form.

        Returns the full reply text. Records into ``t``:
        ``llm`` / ``tts`` stages, ``llm_first_token`` and ``first_audio`` info.
        """
        self.history.append({"role": "user", "content": user_text})
        self._cancel.clear()
        self._set_phase(Phase.THINKING)

        turn_start = time.perf_counter()
        sentences: "queue.Queue[Optional[str]]" = queue.Queue()
        reply_parts: list[str] = []
        llm_first_token_ms: Optional[float] = None
        llm_ms = 0.0
        llm_error: list[BaseException] = []

        def produce() -> None:
            nonlocal llm_first_token_ms, llm_ms
            from .text.sentence_chunker import SentenceChunker

            chunker = SentenceChunker(config.tts_sentences_per_chunk)
            start = time.perf_counter()
            try:
                for chunk in self.llm.stream(self.history):
                    if self._cancel.is_set():
                        break
                    if llm_first_token_ms is None:
                        llm_first_token_ms = (time.perf_counter() - start) * 1000.0
                    reply_parts.append(chunk)
                    self._emit("assistant_delta", text=chunk)
                    for sentence in chunker.push(chunk):
                        sentences.put(sentence)
                tail = chunker.flush()
                if tail:
                    sentences.put(tail)
            except BaseException as exc:  # noqa: BLE001 - surfaced to caller
                llm_error.append(exc)
            finally:
                llm_ms = (time.perf_counter() - start) * 1000.0
                sentences.put(None)  # sentinel

        producer = threading.Thread(target=produce, name="llm-producer", daemon=True)
        producer.start()

        # Consumer: synthesize + stream/play each sentence as it arrives.
        first_audio_ms: Optional[float] = None
        tts_ms = 0.0
        spoke = False
        seg_n = 0
        started_sink = False
        sink_sr = 0
        adapter = self.streaming_tts
        while True:
            sentence = sentences.get()
            if sentence is None:
                break
            if self._cancel.is_set():
                continue  # drain to the sentinel, synthesize nothing
            if not sentence.strip():
                continue
            if not spoke:
                self._set_phase(Phase.SPEAKING)
                spoke = True
            seg_n += 1

            if audio_sink is not None:
                # Stream raw PCM frames to the phone (Phase B).
                sr, frames, synth_ms = iter_sentence_pcm(
                    self.tts, sentence, config.tts_pcm_frame_ms)
                tts_ms += synth_ms
                if not started_sink:
                    sink_sr = sr
                    audio_sink.on_start(sr)
                    started_sink = True
                self._emit("assistant_speak", text=sentence)
                for frame in frames:
                    if self._cancel.is_set():
                        break
                    if first_audio_ms is None:
                        first_audio_ms = (time.perf_counter() - turn_start) * 1000.0
                    audio_sink.on_pcm(frame)
                continue

            seg = self._synth_segment(adapter, sentence, seg_n)
            tts_ms += seg.synth_ms
            if first_audio_ms is None:
                first_audio_ms = (time.perf_counter() - turn_start) * 1000.0
            self._emit("tts_audio", text=seg.text, wav_path=seg.wav_path,
                       synth_ms=round(seg.synth_ms))
            if play and self.player is not None:
                self.player(seg.wav_path)

        if audio_sink is not None and started_sink:
            audio_sink.on_done()

        producer.join()
        cancelled = self._cancel.is_set()
        if llm_error and not cancelled:
            self._set_phase(Phase.ERROR)
            self._emit("error", message=str(llm_error[0]))
            raise llm_error[0]

        reply = "".join(reply_parts).strip()
        if cancelled:
            # Keep the partial reply in history so context stays coherent.
            if reply:
                self.history.append({"role": "assistant", "content": reply})
            log.info("LLM  ▶  (cancelled) %r", reply)
            self._emit("assistant_end", text=reply, cancelled=True)
            return reply
        self.history.append({"role": "assistant", "content": reply})
        log.info("LLM  ▶  %r  (%.0fms, first token %.0fms)",
                 reply, llm_ms, llm_first_token_ms or llm_ms)

        t.mark_info("llm_first_token", llm_first_token_ms or llm_ms)
        t.mark("llm", llm_ms)
        t.mark("tts", tts_ms)
        if first_audio_ms is not None:
            t.mark_info("first_audio", first_audio_ms)

        self._emit("assistant_end", text=reply)
        self._emit("latency", stages=dict(t.stages), info=dict(t.info),
                   total=round(t.total()))
        self._set_phase(Phase.LISTENING if audio_sink is not None else Phase.INACTIVE)
        return reply

    def _synth_segment(self, adapter, sentence: str, n: int) -> AudioSegment:
        """Synthesize one sentence to a wav (works for streaming + wrapped TTS)."""
        if isinstance(adapter, SentenceStreamingTTS):
            return adapter._synth(sentence)  # reuse its temp-dir + timing
        # Native StreamingTTS: drive it with a single-sentence iterator.
        segs = list(adapter.stream(iter([sentence])))
        if segs:
            return segs[0]
        # Last-resort fallback: synthesize directly.
        import tempfile
        out = tempfile.mktemp(suffix=".wav", prefix=f"robot-seg{n}-")
        start = time.perf_counter()
        self.tts.synthesize(sentence, out)
        return AudioSegment(sentence, out, (time.perf_counter() - start) * 1000.0)
