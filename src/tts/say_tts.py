"""macOS `say` TTS backend. Zero-dependency default for Phase 0 on the Mac."""
from __future__ import annotations

import subprocess

from ..config import config


class SayTTS:
    def synthesize(self, text: str, out_path: str) -> None:
        # 16-bit little-endian PCM wav at 22.05kHz, playable by afplay.
        subprocess.run(
            [
                "say",
                "-v", config.say_voice,
                "--data-format=LEI16@22050",
                "-o", out_path,
                text,
            ],
            check=True,
        )
