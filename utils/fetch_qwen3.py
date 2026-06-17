"""Warm up / verify Qwen3-TTS model download + config.

This does for Qwen3 what `utils.fetch_kokoro` does for Kokoro: one command to
pull model weights into cache and prove synthesis works with current `.env`.

Usage:
    pip install qwen-tts torch
    python -m utils.fetch_qwen3

Output:
- prints configured model + backend knobs
- loads Qwen3 model (downloads to HF cache on first run)
- prints supported languages/speakers when available
- writes `voices/qwen3-smoke.wav`
"""
from __future__ import annotations

import os
from pathlib import Path

# Keep downloads on classic HTTP path; avoids xet 0%-stall on some setups.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from src.config import config
from src.tts.qwen3_tts import Qwen3TTS, _get_model

DEST = Path(__file__).resolve().parent.parent / "voices"
SMOKE_WAV = DEST / "qwen3-smoke.wav"


def _preview(xs, limit: int = 12) -> str:
    seq = list(xs) if xs is not None else []
    if not seq:
        return "(none)"
    shown = ", ".join(str(x) for x in seq[:limit])
    if len(seq) > limit:
        shown += f", ... (+{len(seq) - limit} more)"
    return shown


def main() -> None:
    print("Qwen3-TTS config:")
    print(f"  model={config.qwen3_model}")
    print(f"  device_map={config.qwen3_device_map}")
    print(f"  dtype={config.qwen3_dtype}")
    print(f"  attn={config.qwen3_attn_implementation}")
    print(f"  mode={config.qwen3_mode}")
    print(f"  language={config.qwen3_language}")
    print(f"  speaker={config.qwen3_speaker}")

    model = _get_model()

    if hasattr(model, "get_supported_languages"):
        print("supported languages:", _preview(model.get_supported_languages()))
    if hasattr(model, "get_supported_speakers"):
        print("supported speakers:", _preview(model.get_supported_speakers()))

    DEST.mkdir(parents=True, exist_ok=True)
    text = "Hallo! Ich bin bereit für den Test mit Qwen drei T T S."
    Qwen3TTS().synthesize(text, str(SMOKE_WAV))
    print(f"smoke wav -> {SMOKE_WAV}")
    print("done. Set TTS_BACKEND=qwen3 and run: python -m tools.smoke \"Hallo\"")


if __name__ == "__main__":
    main()
