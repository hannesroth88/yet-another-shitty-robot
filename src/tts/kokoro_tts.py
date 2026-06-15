"""Kokoro German TTS backend (ONNX Runtime).

Uses the German "Martin" Kokoro-82M ONNX model via the `kokoro-onnx` package
(NOT the `kokoro` KPipeline, which has no German voice). Model files come from
the Godelaune/Kokoro-82M-ONNX-German-Martin repo:

    pip install kokoro-onnx soundfile huggingface_hub
    python -m utils.fetch_kokoro          # downloads model + voices into voices/

Outputs 16-bit PCM mono wav so the robot effect (effects.py) can post-process
it: TTS_BACKEND=kokoro + TTS_EFFECT=robot chains Kokoro -> robot filter.
"""
from __future__ import annotations

import wave

import numpy as np

from ..config import config

# Cache the loaded model across calls -- loading the ONNX graph + voices is the
# expensive part; we only want to pay it once (latency matters for the loop).
_KOKORO = None


def _get_kokoro():
    global _KOKORO
    if _KOKORO is None:
        from kokoro_onnx import Kokoro  # lazy: heavy import, optional dependency
        _KOKORO = Kokoro(config.kokoro_model, config.kokoro_voices)
    return _KOKORO


class KokoroTTS:
    def synthesize(self, text: str, out_path: str) -> None:
        kokoro = _get_kokoro()
        samples, sample_rate = kokoro.create(
            text,
            voice=config.kokoro_voice,
            speed=config.kokoro_speed,
            lang=config.kokoro_lang,
        )
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim > 1:  # mix to mono if the model ever returns stereo
            samples = samples.mean(axis=1)
        pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit -> matches effects._read_wav
            w.setframerate(int(sample_rate))
            w.writeframes(pcm.tobytes())
