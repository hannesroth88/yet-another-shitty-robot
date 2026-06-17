"""Warm up / verify the Qwen3-TTS MLX backend (Apple Silicon voice clone).

Does for the MLX clone backend what `utils.fetch_qwen3` does for the PyTorch
one: a single command to pull the quantized Base model into cache, warm the
Metal kernels, and prove ICL voice cloning works with the current `.env`.

Usage:
    pip install mlx-audio
    python -m utils.fetch_qwen3_mlx

Output:
- prints configured model + reference clip/transcript
- loads the MLX model (downloads to HF cache on first run)
- clones the reference voice and writes `voices/qwen3-mlx-smoke.wav`
- prints generation latency / real-time factor
"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np

from src.config import config
from src.tts.qwen3_mlx_tts import Qwen3MlxTTS, _get_model, _resolve_ref_text

DEST = Path(__file__).resolve().parent.parent / "voices"
SMOKE_WAV = DEST / "qwen3-mlx-smoke.wav"


def main() -> None:
    print("Qwen3-TTS (MLX) config:")
    print(f"  model={config.qwen3_mlx_model}")
    print(f"  language={config.qwen3_language}")
    print(f"  ref_audio={config.qwen3_ref_audio}")
    ref_text = _resolve_ref_text(config.qwen3_ref_audio)
    print(f"  ref_text={ref_text[:70]!r}{'...' if len(ref_text) > 70 else ''}")

    t0 = time.time()
    _get_model()  # download + load + cache (warms Metal kernels on first gen)
    print(f"model loaded in {time.time() - t0:.1f}s")

    DEST.mkdir(parents=True, exist_ok=True)
    text = "Hallo! Ich bin bereit. Das ist ein kurzer Test der geklonten Stimme."
    t0 = time.time()
    Qwen3MlxTTS().synthesize(text, str(SMOKE_WAV))
    gen = time.time() - t0

    import wave

    with wave.open(str(SMOKE_WAV), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    rtf = gen / dur if dur else 0.0
    print(f"smoke wav -> {SMOKE_WAV}  (gen {gen:.1f}s, audio {dur:.1f}s, rtf {rtf:.2f})")
    print("done. Set TTS_BACKEND=qwen3-mlx and run: python -m tools.smoke \"Hallo\"")


if __name__ == "__main__":
    main()
