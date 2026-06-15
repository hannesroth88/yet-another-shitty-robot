"""Piper TTS backend (cross-platform, fleet default).

Requires the `piper` binary and a voice .onnx file. Install per host; on Linux
fleet machines `pip install piper-tts` provides wheels. On Apple Silicon prefer
the prebuilt piper binary if pip wheels are unavailable. See README.
"""
from __future__ import annotations

import subprocess

from ..config import config


class PiperTTS:
    def synthesize(self, text: str, out_path: str) -> None:
        subprocess.run(
            [
                config.piper_bin,
                "--model", config.piper_voice,
                "--output_file", out_path,
            ],
            input=text.encode("utf-8"),
            check=True,
        )
