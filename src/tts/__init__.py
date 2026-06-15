"""TTS interface + factory.

Phase 0 default is macOS `say` (zero-dependency, works today). `piper` is the
cross-platform fleet default and is selected via TTS_BACKEND=piper. See AGENTS.md.
"""
from __future__ import annotations

from typing import Protocol

from ..config import config


class TTS(Protocol):
    def synthesize(self, text: str, out_path: str) -> None:
        """Write spoken audio for `text` to `out_path` (wav)."""
        ...


def get_tts() -> TTS:
    if config.tts_backend == "say":
        from .say_tts import SayTTS
        return SayTTS()
    if config.tts_backend == "piper":
        from .piper_tts import PiperTTS
        return PiperTTS()
    raise ValueError(f"Unknown TTS_BACKEND: {config.tts_backend}")
