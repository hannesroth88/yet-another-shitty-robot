"""STT interface + factory. Swap backends without touching the pipeline."""
from __future__ import annotations

from typing import Protocol

from ..config import config


class STT(Protocol):
    def transcribe(self, wav_path: str) -> str: ...


def get_stt() -> STT:
    if config.stt_backend in ("faster-whisper", "faster_whisper", "whisper"):
        from .faster_whisper_stt import FasterWhisperSTT
        return FasterWhisperSTT()
    raise ValueError(f"Unknown STT_BACKEND: {config.stt_backend}")
