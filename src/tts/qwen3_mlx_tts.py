"""Qwen3-TTS backend (Apple Silicon / MLX), with ICL voice cloning.

This is the fast, good-sounding path on Mac (arm64). It uses `mlx-audio` to run
a quantized Qwen3-TTS **Base** model natively on Metal, and clones a target
voice in-context from a short reference clip + its transcript -- the same
approach Mario Zechner uses in pibot (badlogic/pibot).

Why this exists (vs `qwen3_tts.py`):
- `qwen3_tts.py` uses the PyTorch/transformers `qwen-tts` package. On the M1 it
  runs float32 on MPS at RTF ~4 (4s compute per 1s audio) -> ~2 min per turn.
- This MLX path runs a 6-bit Base model and reaches RTF < 1 on longer text,
  ~4-5x faster, and clones the ElevenLabs reference voice directly (no robot
  DSP needed).

Setup (optional backend):
    pip install mlx-audio
    # provide a clean mono reference wav + its exact transcript, then set:
    TTS_BACKEND=qwen3-mlx
    QWEN3_REF_AUDIO=voices/sample/reference.wav
    QWEN3_REF_TEXT_FILE=voices/sample/voice_preview_friendly_small_teaching_robot.txt

Notes:
- Writes 16-bit PCM mono WAV so the rest of the pipeline (and the optional
  robot effect) can consume it like Piper/Kokoro.
- Model loading is cached across calls (first call downloads weights + warms up
  Metal kernels; subsequent synthesis is fast).
- Apple Silicon only (requires `mlx`). On x86 fleet hosts use a different
  backend; a CUDA `faster-qwen3-tts` worker can be added later (see pibot).
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Any

# Keep HF downloads on the classic HTTP path (xet can stall at 0% on some nets).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np

from ..config import config

_MLX_MODEL: Any = None
_MLX_MODEL_KEY: str | None = None


def _get_model() -> Any:
    global _MLX_MODEL, _MLX_MODEL_KEY

    model_id = config.qwen3_mlx_model
    if _MLX_MODEL is not None and _MLX_MODEL_KEY == model_id:
        return _MLX_MODEL

    try:
        from mlx_audio.tts.utils import load_model  # type: ignore
    except Exception as e:  # pragma: no cover - optional heavy dep
        raise RuntimeError(
            "qwen3-mlx backend requires mlx-audio (Apple Silicon). "
            "Install with: pip install mlx-audio"
        ) from e

    _MLX_MODEL = load_model(model_id)
    _MLX_MODEL_KEY = model_id
    return _MLX_MODEL


def _resolve_ref_text(ref_audio: str) -> str:
    """Exact transcript of the reference clip (required for ICL cloning)."""
    text = (config.qwen3_ref_text or "").strip()
    if text:
        return text

    file = (config.qwen3_ref_text_file or "").strip()
    if file:
        p = Path(file)
        if not p.exists():
            raise FileNotFoundError(f"QWEN3_REF_TEXT_FILE not found: {file}")
        return p.read_text(encoding="utf8").strip()

    # Convenience: a sibling "<ref_stem>.txt" next to the reference audio.
    sibling = Path(ref_audio).with_suffix(".txt")
    if sibling.exists():
        return sibling.read_text(encoding="utf8").strip()

    raise ValueError(
        "qwen3-mlx voice clone needs the reference transcript. Set QWEN3_REF_TEXT "
        "or QWEN3_REF_TEXT_FILE (or place a <ref>.txt next to QWEN3_REF_AUDIO)."
    )


def _collect_audio(results: Any) -> tuple[np.ndarray, int]:
    chunks: list[np.ndarray] = []
    sr = 24000
    for item in results:
        audio = getattr(item, "audio", item)
        sr = int(getattr(item, "sample_rate", sr) or sr)
        chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))
    if not chunks:
        raise RuntimeError("qwen3-mlx produced no audio")
    return np.concatenate(chunks), sr


def _write_wav(path: str, samples: np.ndarray, sr: int) -> None:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1e-9:
        samples = samples / peak * 0.97  # normalize, leave headroom
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


class Qwen3MlxTTS:
    def synthesize(self, text: str, out_path: str) -> None:
        ref_audio = (config.qwen3_ref_audio or "").strip()
        if not ref_audio:
            raise ValueError("qwen3-mlx backend requires QWEN3_REF_AUDIO")
        if not Path(ref_audio).exists():
            raise FileNotFoundError(f"QWEN3_REF_AUDIO not found: {ref_audio}")

        ref_text = _resolve_ref_text(ref_audio)
        lang = (config.qwen3_language or "auto").strip() or "auto"

        model = _get_model()
        results = list(
            model.generate(
                text=text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                lang_code=lang.lower(),
            )
        )
        samples, sr = _collect_audio(results)
        _write_wav(out_path, samples, sr)
