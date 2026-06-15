"""Voice effects (DSP) applied on top of any TTS backend.

Local-first, low-latency, engine-agnostic: this post-processes the WAV a TTS
backend produced, so the same robot voice works for say / piper / kokoro / ...
Pure numpy + stdlib `wave` (no scipy, no extra models, no VRAM).

The signature effect is ring modulation (the classic metallic robot buzz),
kept subtle so speech stays intelligible, plus optional tremolo and a short
comb for a mechanical, "tin-can" character.
"""
from __future__ import annotations

import wave

import numpy as np


def _read_wav(path: str) -> tuple[np.ndarray, int, int]:
    """Return (float32 samples in [-1,1], sample_rate, n_channels)."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sampwidth = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sampwidth != 2:
        raise ValueError(f"robot effect expects 16-bit PCM wav, got sampwidth={sampwidth}")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)  # downmix to mono
        ch = 1
    return data, sr, ch


def _write_wav(path: str, samples: np.ndarray, sr: int) -> None:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1e-9:
        samples = samples / peak * 0.95  # normalize, leave headroom
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def robotize(
    samples: np.ndarray,
    sr: int,
    *,
    carrier_hz: float = 55.0,
    mix: float = 0.6,
    tremolo_hz: float = 0.0,
    tremolo_depth: float = 0.0,
    comb_ms: float = 0.0,
    comb_gain: float = 0.0,
) -> np.ndarray:
    """Apply a robot voice to mono float samples.

    - carrier_hz / mix: ring modulation. Low carrier (40-70 Hz) stays
      intelligible; higher gets more garbled/metallic. mix=0 disables it.
    - tremolo_hz / tremolo_depth: amplitude pulsing for a mechanical feel.
    - comb_ms / comb_gain: short feed-forward delay -> metallic "tin" resonance.
    """
    n = samples.shape[0]
    t = np.arange(n, dtype=np.float32) / sr
    out = samples.copy()

    # Ring modulation (the core robot buzz).
    if mix > 0.0:
        carrier = np.sin(2.0 * np.pi * carrier_hz * t).astype(np.float32)
        out = (1.0 - mix) * out + mix * (out * carrier)

    # Tremolo (mechanical amplitude pulse).
    if tremolo_depth > 0.0 and tremolo_hz > 0.0:
        lfo = 1.0 - tremolo_depth * (0.5 + 0.5 * np.sin(2.0 * np.pi * tremolo_hz * t))
        out = out * lfo.astype(np.float32)

    # Short comb / metallic resonance.
    if comb_gain > 0.0 and comb_ms > 0.0:
        delay = max(1, int(sr * comb_ms / 1000.0))
        combed = np.zeros_like(out)
        combed[delay:] = out[:-delay]
        out = out + comb_gain * combed

    return out


def robotize_file(
    in_path: str,
    out_path: str,
    **params: float,
) -> None:
    """Read a 16-bit PCM wav, apply robotize(), write a 16-bit PCM wav."""
    samples, sr, _ = _read_wav(in_path)
    processed = robotize(samples, sr, **params)
    _write_wav(out_path, processed, sr)
