"""Robot-voice decorator: wraps any TTS backend and post-processes its output.

Stays engine-agnostic (say / piper / kokoro / ...) and swappable: the inner
backend synthesizes to a temp wav, then the DSP effect from effects.py is
applied. Selected with TTS_EFFECT=robot; parameters come from config/env.
"""
from __future__ import annotations

import os
import tempfile

from ..config import config
from . import effects


class RobotTTS:
    def __init__(self, inner) -> None:
        self.inner = inner

    def synthesize(self, text: str, out_path: str) -> None:
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self.inner.synthesize(text, tmp)
            effects.robotize_file(
                tmp,
                out_path,
                carrier_hz=config.robot_carrier_hz,
                mix=config.robot_mix,
                tremolo_hz=config.robot_tremolo_hz,
                tremolo_depth=config.robot_tremolo_depth,
                comb_ms=config.robot_comb_ms,
                comb_gain=config.robot_comb_gain,
            )
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
