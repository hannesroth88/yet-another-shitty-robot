"""Streaming TTS protocol + adapter (Phase 1).

A ``StreamingTTS`` consumes text *as it arrives* (an iterator of deltas/sentences)
and yields synthesized audio segments as soon as each is ready. This lets the
orchestrator start playback on sentence 1 while the LLM is still producing
sentence 2 -- the ``first_audio_ms`` win.

Two shapes coexist:

* Native streaming engines (e.g. Qwen3-TTS) implement ``stream`` directly and can
  emit audio before a full sentence is synthesized.
* Non-streaming engines (``say``, Piper, Kokoro) are wrapped by
  ``SentenceStreamingTTS``: it runs the text through the sentence chunker and
  synthesizes one wav per sentence, yielding each as an ``AudioSegment``.

Audio is passed around as ``AudioSegment`` (a wav file on disk + its text), which
keeps playback simple (``afplay`` on the Mac) and lets the control server stream
the bytes over a WebSocket without re-synthesizing.
"""
from __future__ import annotations

import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, Tuple

import numpy as np

from ..config import config
from ..text.sentence_chunker import SentenceChunker
from . import TTS


@dataclass
class AudioSegment:
    """One synthesized chunk: the text that was spoken + a wav file path."""

    text: str
    wav_path: str
    synth_ms: float

    def read_bytes(self) -> bytes:
        return Path(self.wav_path).read_bytes()


class StreamingTTS(Protocol):
    def stream(self, text_chunks: Iterator[str]) -> Iterator[AudioSegment]:
        """Consume text deltas, yield audio segments as they synthesize."""
        ...


class SentenceStreamingTTS:
    """Adapt any non-streaming :class:`TTS` into a :class:`StreamingTTS`.

    Buffers token deltas into complete sentences (via :class:`SentenceChunker`)
    and synthesizes one wav per emitted chunk, so the first sentence can play
    while later sentences are still being generated upstream.
    """

    def __init__(self, backend: TTS, sentences_per_chunk: int = 1) -> None:
        self.backend = backend
        self.sentences_per_chunk = sentences_per_chunk
        self._tmp = Path(tempfile.mkdtemp(prefix="robot-tts-"))
        self._n = 0

    def stream(self, text_chunks: Iterator[str]) -> Iterator[AudioSegment]:
        chunker = SentenceChunker(sentences_per_chunk=self.sentences_per_chunk)
        for delta in text_chunks:
            for sentence in chunker.push(delta):
                yield self._synth(sentence)
        tail = chunker.flush()
        if tail:
            yield self._synth(tail)

    def synthesize_full(self, text: str, out_path: str) -> None:
        """Non-streaming fallback: synthesize the whole reply at once."""
        self.backend.synthesize(text, out_path)

    # -- internals -------------------------------------------------------

    def _synth(self, text: str) -> AudioSegment:
        self._n += 1
        out = str(self._tmp / f"seg_{self._n:03d}.wav")
        start = time.perf_counter()
        self.backend.synthesize(text, out)
        synth_ms = (time.perf_counter() - start) * 1000.0
        return AudioSegment(text=text, wav_path=out, synth_ms=synth_ms)


def get_streaming_tts(backend: TTS, sentences_per_chunk: int = 1) -> StreamingTTS:
    """Return a StreamingTTS for ``backend``.

    Native-streaming backends expose their own ``stream``; everything else is
    wrapped per-sentence. (Qwen3 native streaming can be added here later.)
    """
    if hasattr(backend, "stream") and callable(getattr(backend, "stream")):
        return backend  # type: ignore[return-value]
    return SentenceStreamingTTS(backend, sentences_per_chunk=sentences_per_chunk)


def iter_sentence_pcm(
    backend: TTS, sentence: str, frame_ms: int = 40
) -> Tuple[int, Iterator[bytes], float]:
    """Synthesize one sentence and stream it as raw PCM16LE mono frames.

    Returns ``(sample_rate, frame_iterator, synth_ms)``. This is the transport
    used to stream audio to the phone (ADR 0003, Phase B): the orchestrator
    pushes these frames over the WebSocket and the browser schedules them with
    the Web Audio API.

    * Native-streaming backends (Qwen3-MLX ``stream_pcm``) yield real generator
      chunks, so the first audio leaves before the whole sentence is done.
    * Everything else (Piper / say / Kokoro) is synthesized to a WAV once and
      sliced into frames -- still gapless and cross-sentence streamed.
    """
    native = getattr(backend, "stream_pcm", None)
    if callable(native):
        start = time.perf_counter()
        sr, chunks = native(sentence)
        synth_ms = (time.perf_counter() - start) * 1000.0
        return sr, chunks, synth_ms

    tmp = Path(tempfile.mktemp(suffix=".wav", prefix="robot-pcm-"))
    start = time.perf_counter()
    backend.synthesize(sentence, str(tmp))
    synth_ms = (time.perf_counter() - start) * 1000.0
    with wave.open(str(tmp), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    tmp.unlink(missing_ok=True)

    data = np.frombuffer(raw, dtype="<i2")
    if sw == 2 and ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype("<i2")

    frame_samples = max(1, int(sr * frame_ms / 1000))
    pcm = data.tobytes()
    bytes_per_frame = frame_samples * 2

    def _frames() -> Iterator[bytes]:
        for i in range(0, len(pcm), bytes_per_frame):
            yield pcm[i : i + bytes_per_frame]

    return sr, _frames(), synth_ms
