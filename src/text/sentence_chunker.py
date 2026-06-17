"""Streaming sentence chunker (Phase 1).

Turns a stream of LLM token deltas into complete sentences as soon as they form,
so TTS can start speaking sentence 1 while the LLM is still generating sentence 2.
This is the single biggest perceived-latency win in the voice loop (see
plans/phase-1-service-split.md), measured as the new ``first_audio_ms`` metric.

Design goals:
- stdlib only, no NLP dependency;
- robust to abbreviations / decimals so we don't cut "z. B." or "3.14" mid-number;
- ``sentences_per_chunk`` lets a host trade first-audio latency (1) for fewer,
  more natural TTS calls (2+).

Usage::

    chunker = SentenceChunker(sentences_per_chunk=1)
    for delta in llm_stream:
        for chunk in chunker.push(delta):
            tts.speak(chunk)
    tail = chunker.flush()
    if tail:
        tts.speak(tail)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Sentence-final punctuation. German/English share these.
_TERMINATORS = ".!?…"
# Common abbreviations whose trailing dot must NOT end a sentence.
_ABBREVIATIONS = {
    "z.b", "u.a", "d.h", "u.s.w", "usw", "etc", "bzw", "ca", "vgl", "sog",
    "z.t", "i.d.r", "evtl", "ggf", "inkl", "max", "min", "nr", "abs", "art",
    "dr", "prof", "hr", "fr", "st", "ggü", "mind", "mr", "mrs", "ms", "vs",
    "e.g", "i.e", "approx", "fig", "no",
}


@dataclass
class SentenceChunker:
    """Accumulate text deltas, emit complete sentences as boundaries are reached."""

    sentences_per_chunk: int = 1
    _buf: str = ""
    _pending: list[str] = field(default_factory=list)

    def push(self, delta: str) -> list[str]:
        """Feed a text delta; return any chunks that are now complete."""
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        for sentence in self._extract_sentences():
            self._pending.append(sentence)
            if len(self._pending) >= self.sentences_per_chunk:
                out.append(" ".join(self._pending).strip())
                self._pending = []
        return out

    def flush(self) -> str | None:
        """Return any remaining buffered text (incomplete final sentence + tail)."""
        leftover = self._pending
        self._pending = []
        tail = self._buf.strip()
        self._buf = ""
        parts = [*leftover]
        if tail:
            parts.append(tail)
        joined = " ".join(p for p in parts if p).strip()
        return joined or None

    # -- internals -------------------------------------------------------

    def _extract_sentences(self) -> list[str]:
        sentences: list[str] = []
        while True:
            idx = self._next_boundary(self._buf)
            if idx is None:
                break
            sentence = self._buf[: idx + 1].strip()
            self._buf = self._buf[idx + 1 :]
            if sentence:
                sentences.append(sentence)
        return sentences

    def _next_boundary(self, text: str) -> int | None:
        for i, ch in enumerate(text):
            if ch not in _TERMINATORS:
                continue
            # Need a following char to confirm the sentence really ended; if the
            # terminator is the last char in the buffer we wait for more (it may
            # be a decimal or abbreviation mid-token).
            if i + 1 >= len(text):
                return None
            nxt = text[i + 1]
            if not (nxt.isspace() or nxt in "\"'\u201d\u2019)]"):
                # e.g. "3.14" or "v1.2" -> not a boundary
                continue
            if ch == "." and self._looks_like_abbreviation(text[: i + 1]):
                continue
            return i
        return None

    @staticmethod
    def _looks_like_abbreviation(upto: str) -> bool:
        # Grab the trailing token (letters and internal dots) before the boundary dot.
        m = re.search(r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß.]*)\.$", upto)
        if not m:
            return False
        token = m.group(1).lower().rstrip(".")
        if token in _ABBREVIATIONS:
            return True
        # Single capital letter initials ("J." in "J. Smith").
        if len(token) == 1 and token.isalpha():
            return True
        return False
