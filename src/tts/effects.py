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


def _hann(n: int) -> np.ndarray:
    return np.hanning(n).astype(np.float32)


def formant_shift_stft(
    x: np.ndarray,
    *,
    shift: float = 1.0,
    frame: int = 1024,
    hop: int = 128,
) -> np.ndarray:
    """Shift formants up/down while KEEPING phase -> stays fully intelligible.

    Unlike `phase_robotize`, this preserves the original phase (prosody and
    excitation), so words stay clear; it only rescales the spectral envelope.
    `shift` > 1 = smaller/daintier vocal tract (a 'tiny' voice) without the
    robot buzz; < 1 = bigger/deeper. Pitch (fundamental) is unchanged.
    """
    if shift == 1.0:
        return x
    win = _hann(frame)
    n_bins = frame // 2 + 1
    src = np.arange(n_bins, dtype=np.float32)
    pad = frame
    xp = np.concatenate([
        np.zeros(pad, np.float32), x.astype(np.float32),
        np.zeros(pad + frame, np.float32),
    ])
    out = np.zeros(len(xp), np.float32)
    norm = np.zeros(len(xp), np.float32)
    for i in range(0, len(xp) - frame, hop):
        spec = np.fft.rfft(xp[i:i + frame] * win)
        mag = np.abs(spec)
        phase = np.angle(spec)
        mag2 = np.interp(src / shift, src, mag, right=0.0)
        rec = np.fft.irfft(mag2 * np.exp(1j * phase), n=frame).astype(np.float32) * win
        out[i:i + frame] += rec
        norm[i:i + frame] += win * win
    out = (out / (norm + 1e-9))[pad:pad + len(x)]
    rms_in = np.sqrt(np.mean(x ** 2)) + 1e-9
    rms_out = np.sqrt(np.mean(out ** 2)) + 1e-9
    return out * (rms_in / rms_out)


def resample_speed(x: np.ndarray, factor: float) -> np.ndarray:
    """Resample by `factor` -> pitch+formants up and duration shorter (factor>1).

    The classic 'small/fast robot' move: very intelligible (it is just speed),
    clearly tiny. factor 1.1-1.25 is a good range; 1.0 = off.
    """
    if factor == 1.0 or len(x) == 0:
        return x
    n = len(x)
    new_n = max(1, int(round(n / factor)))
    idx = np.linspace(0, n - 1, new_n).astype(np.float32)
    return np.interp(idx, np.arange(n, dtype=np.float32), x).astype(np.float32)


def phase_robotize(
    x: np.ndarray,
    sr: int,
    *,
    frame: int = 2048,
    hop: int = 256,
    strength: float = 1.0,
    lowpass_hz: float = 3500.0,
    formant_shift: float = 1.0,
) -> np.ndarray:
    """Classic 'robotization': STFT, zero the phase, inverse STFT.

    Removing the phase flattens the natural pitch into a steady monotone buzz at
    sr/hop Hz -- the single most 'robot' transform. Language-agnostic.

    `formant_shift` > 1 moves the spectral envelope up, shrinking the apparent
    vocal tract -> a smaller, daintier ('winziger Roboter') voice; < 1 makes it
    bigger/deeper. Combined with a small `hop` (higher buzz) this gives a tiny
    robot. Zero-phase frames are impulse-like, which adds harsh high-frequency
    clicks; a smooth `lowpass_hz` rolloff removes that crackle. `strength`
    crossfades between the original (0.0) and fully robotized (1.0) signal.
    """
    if strength <= 0.0:
        return x
    win = _hann(frame)
    n_bins = frame // 2 + 1
    src_bins = np.arange(n_bins, dtype=np.float32)
    pad = frame
    xp = np.concatenate([
        np.zeros(pad, np.float32),
        x.astype(np.float32),
        np.zeros(pad + frame, np.float32),
    ])
    out = np.zeros(len(xp), np.float32)
    norm = np.zeros(len(xp), np.float32)
    for i in range(0, len(xp) - frame, hop):
        seg = xp[i:i + frame] * win
        mag = np.abs(np.fft.rfft(seg))
        if formant_shift != 1.0:
            # resample the magnitude envelope along frequency (duration-preserving)
            mag = np.interp(src_bins / formant_shift, src_bins, mag,
                            right=0.0).astype(np.float32)
        rob = np.fft.fftshift(np.fft.irfft(mag, n=frame)).astype(np.float32) * win
        out[i:i + frame] += rob
        norm[i:i + frame] += win * win
    out = (out / (norm + 1e-9))[pad:pad + len(x)]

    # Smooth lowpass to remove the impulse-train crackle (the main quality win).
    if lowpass_hz and lowpass_hz > 0:
        n = len(out)
        spec = np.fft.rfft(out)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        rolloff = 1.0 / (1.0 + (freqs / lowpass_hz) ** 6)  # gentle 6th-order
        out = np.fft.irfft(spec * rolloff, n=n).astype(np.float32)

    # Match loudness, then crossfade by strength.
    rms_in = np.sqrt(np.mean(x ** 2)) + 1e-9
    rms_out = np.sqrt(np.mean(out ** 2)) + 1e-9
    out *= rms_in / rms_out
    return (1.0 - strength) * x + strength * out


def bitcrush(x: np.ndarray, *, bits: int = 0, rate_div: int = 1) -> np.ndarray:
    """Lo-fi digital grit: reduce bit depth and/or sample rate (sample & hold)."""
    out = x
    if bits and bits > 0:
        levels = float(2 ** bits)
        out = np.round(out * levels) / levels
    if rate_div and rate_div > 1:
        idx = (np.arange(len(out)) // rate_div) * rate_div
        out = out[idx]
    return out.astype(np.float32)


def robotize(
    samples: np.ndarray,
    sr: int,
    *,
    phase_strength: float = 0.0,
    phase_hop: int = 256,
    phase_frame: int = 2048,
    phase_lowpass_hz: float = 3500.0,
    phase_formant: float = 1.0,
    formant: float = 1.0,
    speed: float = 1.0,
    carrier_hz: float = 55.0,
    mix: float = 0.6,
    bits: int = 0,
    rate_div: int = 1,
    tremolo_hz: float = 0.0,
    tremolo_depth: float = 0.0,
    comb_ms: float = 0.0,
    comb_gain: float = 0.0,
) -> np.ndarray:
    """Apply a robot voice to mono float samples.

    - phase_strength / phase_hop: monotone 'robotization' (STFT zero-phase).
      This is the strongest robot effect; sr/phase_hop sets the buzz pitch.
    - formant: clarity-preserving formant shift (>1 = smaller/tinier voice).
      Keeps prosody, so it stays intelligible -- the best 'tiny' lever.
    - speed: resample pitch/tempo up (>1 = higher + faster 'small robot').
    - carrier_hz / mix: ring modulation. Low carrier (40-70 Hz) stays
      intelligible; higher gets more garbled/metallic. mix=0 disables it.
    - bits / rate_div: bit-crusher (digital lo-fi grit).
    - tremolo_hz / tremolo_depth: amplitude pulsing for a mechanical feel.
    - comb_ms / comb_gain: short feed-forward delay -> metallic "tin" resonance.
    """
    out = samples.astype(np.float32).copy()

    # Clarity-preserving formant shift first (the intelligible 'tiny' lever).
    if formant != 1.0:
        out = formant_shift_stft(out, shift=formant)

    # Monotone robotization (operates on natural pitch).
    if phase_strength > 0.0:
        out = phase_robotize(
            out, sr, frame=phase_frame, hop=phase_hop,
            strength=phase_strength, lowpass_hz=phase_lowpass_hz,
            formant_shift=phase_formant,
        )

    n = out.shape[0]
    t = np.arange(n, dtype=np.float32) / sr

    # Ring modulation (metallic buzz).
    if mix > 0.0:
        carrier = np.sin(2.0 * np.pi * carrier_hz * t).astype(np.float32)
        out = (1.0 - mix) * out + mix * (out * carrier)

    # Bit-crush (digital grit).
    if (bits and bits > 0) or (rate_div and rate_div > 1):
        out = bitcrush(out, bits=bits, rate_div=rate_div)

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

    # Pitch/tempo up last (changes length): the 'small fast robot' move.
    if speed != 1.0:
        out = resample_speed(out, speed)

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
