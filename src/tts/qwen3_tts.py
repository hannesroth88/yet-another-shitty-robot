"""Qwen3-TTS backend.

Uses the official `qwen-tts` Python package (QwenLM/Qwen3-TTS).

Quick setup (optional backend):
    pip install qwen-tts torch

Then set in .env:
    TTS_BACKEND=qwen3
    QWEN3_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice

Notes:
- This backend writes 16-bit PCM mono WAV so our robot effect wrapper
  (RobotTTS/effects.py) can post-process it like Piper/Kokoro.
- Model loading is cached across calls (first call is heavy).
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Any

# Hugging Face XET downloads can stall at 0% on some networks/proxies.
# Keep parity with utils.fetch_kokoro.py and force classic HTTP downloader.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np

from ..config import config

_QWEN3_MODEL: Any = None
_QWEN3_MODEL_KEY: tuple[str, str, str, str] | None = None


def _resolve_dtype(torch_mod, device_map: str):
    raw = (config.qwen3_dtype or "auto").strip().lower()
    if raw == "auto":
        if device_map.startswith("cuda"):
            return torch_mod.bfloat16
        if device_map.startswith("mps"):
            return torch_mod.float16
        return torch_mod.float32

    mapping = {
        "float32": torch_mod.float32,
        "fp32": torch_mod.float32,
        "float16": torch_mod.float16,
        "fp16": torch_mod.float16,
        "bfloat16": torch_mod.bfloat16,
        "bf16": torch_mod.bfloat16,
    }
    if raw not in mapping:
        raise ValueError(f"Unknown QWEN3_DTYPE={config.qwen3_dtype!r}")
    return mapping[raw]


def _resolve_attn_impl(device_map: str) -> str | None:
    raw = (config.qwen3_attn_implementation or "auto").strip()
    if not raw or raw.lower() in ("none", "off"):
        return None
    if raw.lower() != "auto":
        return raw
    # flash_attention_2 is typically CUDA-only.
    return "flash_attention_2" if device_map.startswith("cuda") else None


def _load_kwargs() -> dict[str, Any]:
    import torch  # lazy optional dependency

    device_map = (config.qwen3_device_map or "auto").strip()
    kwargs: dict[str, Any] = {
        "device_map": device_map,
        "dtype": _resolve_dtype(torch, device_map),
    }
    attn = _resolve_attn_impl(device_map)
    if attn:
        kwargs["attn_implementation"] = attn
    return kwargs


def _get_model() -> Any:
    global _QWEN3_MODEL, _QWEN3_MODEL_KEY

    model_id = config.qwen3_model
    key = (
        model_id,
        (config.qwen3_device_map or "").strip(),
        (config.qwen3_dtype or "").strip(),
        (config.qwen3_attn_implementation or "").strip(),
    )
    if _QWEN3_MODEL is not None and _QWEN3_MODEL_KEY == key:
        return _QWEN3_MODEL

    try:
        from qwen_tts import Qwen3TTSModel  # type: ignore
    except Exception as e:  # pragma: no cover - depends on optional deps
        raise RuntimeError(
            "Qwen3-TTS backend requires optional deps. Install with: "
            "pip install qwen-tts torch"
        ) from e

    _QWEN3_MODEL = Qwen3TTSModel.from_pretrained(model_id, **_load_kwargs())
    _QWEN3_MODEL_KEY = key
    return _QWEN3_MODEL


def _pick_speaker(model) -> str:
    speaker = (config.qwen3_speaker or "").strip()
    if speaker:
        return speaker

    # Convenience fallback: pick first supported speaker if available.
    if hasattr(model, "get_supported_speakers"):
        speakers = model.get_supported_speakers() or []
        if speakers:
            return str(speakers[0])
    raise ValueError(
        "QWEN3_SPEAKER is required for custom-voice mode "
        "(or let the model expose supported speakers)."
    )


def _to_pcm16_mono(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 2:
        # Handle either (n_frames, channels) or (channels, n_frames).
        if samples.shape[0] in (1, 2) and samples.shape[1] > samples.shape[0]:
            samples = samples.mean(axis=0)
        else:
            samples = samples.mean(axis=1)
    if samples.ndim != 1:
        samples = samples.reshape(-1)
    return np.clip(samples * 32768.0, -32768, 32767).astype("<i2")


def _write_wav(path: str, pcm: np.ndarray, sr: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


class Qwen3TTS:
    def synthesize(self, text: str, out_path: str) -> None:
        model = _get_model()
        language = (config.qwen3_language or "Auto").strip() or "Auto"

        mode = (config.qwen3_mode or "custom").strip().lower()
        if mode == "clone":
            ref_audio = (config.qwen3_ref_audio or "").strip()
            if not ref_audio:
                raise ValueError("QWEN3_MODE=clone requires QWEN3_REF_AUDIO")
            if not Path(ref_audio).exists():
                raise FileNotFoundError(
                    f"QWEN3_REF_AUDIO not found: {ref_audio}. "
                    "Provide a short reference wav and transcript."
                )
            kwargs: dict[str, Any] = {
                "text": text,
                "language": language,
                "ref_audio": ref_audio,
            }
            ref_text = (config.qwen3_ref_text or "").strip()
            if ref_text:
                kwargs["ref_text"] = ref_text
            wavs, sr = model.generate_voice_clone(**kwargs)
        elif mode == "custom":
            kwargs = {
                "text": text,
                "language": language,
                "speaker": _pick_speaker(model),
            }
            instruct = (config.qwen3_instruct or "").strip()
            if instruct:
                kwargs["instruct"] = instruct
            wavs, sr = model.generate_custom_voice(**kwargs)
        else:
            raise ValueError("QWEN3_MODE must be 'custom' or 'clone'")

        first = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
        _write_wav(out_path, _to_pcm16_mono(first), int(sr))
