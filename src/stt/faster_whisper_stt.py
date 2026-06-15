"""faster-whisper STT backend.

On Apple Silicon this runs on CPU (int8) via CTranslate2 and is plenty fast for
a prototype. On the x86 fleet hosts it can use CUDA by setting device/compute.
"""
from __future__ import annotations

from ..config import config


class FasterWhisperSTT:
    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        # device="cpu" + int8 is the portable default; override via env on GPU hosts.
        self.model = WhisperModel(
            config.stt_model,
            device="cpu",
            compute_type=config.stt_compute_type,
        )

    def transcribe(self, wav_path: str) -> str:
        segments, _info = self.model.transcribe(
            wav_path,
            language=config.stt_language,
            vad_filter=True,
        )
        return "".join(seg.text for seg in segments).strip()
