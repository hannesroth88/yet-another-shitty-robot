"""Robot-voice lab: audition TTS + robot effect presets, tune params live.

Synthesizes German text with the configured TTS backend (Thorsten/Piper by
default), applies a robot effect, and plays it. Pick a preset, override any
parameter, compare all presets, or drop into an interactive loop.

The German comes from the TTS backend; the robot character is pure DSP applied
on top, so the language is always preserved.

Examples:
    .venv/bin/python -m utils.voice_lab                      # default text, "android" preset
    .venv/bin/python -m utils.voice_lab "Hallo Welt" --preset dalek
    .venv/bin/python -m utils.voice_lab --all                # play every preset back to back
    .venv/bin/python -m utils.voice_lab --phase 1.0 --hop 220 --bits 6
    .venv/bin/python -m utils.voice_lab --interactive        # type lines, re-synth live
    .venv/bin/python -m utils.voice_lab --save out.wav --preset android

Mac plays via `afplay`. On Linux it tries `aplay`/`ffplay`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from src.tts import _make_backend, effects

# Each preset is a full kwargs set for effects.robotize().
# phase_strength = monotone robotization (the big "robot" lever); phase_hop sets buzz pitch.
PRESETS: dict[str, dict[str, float]] = {
    "dry":        dict(mix=0.0),
    # subtle ring mod only (old "classic")
    "classic":    dict(carrier_hz=55, mix=0.65, comb_ms=1.5, comb_gain=0.35),
    # mechanical: robotization + tremolo pulse (clean, lowpassed)
    "mechanical": dict(phase_strength=0.9, phase_hop=256, phase_lowpass_hz=3500,
                       carrier_hz=50, mix=0.20, tremolo_hz=14, tremolo_depth=0.28,
                       comb_ms=1.5, comb_gain=0.22),
    # tiny: small robot -- high buzz pitch + formants shifted up = dainty/cute
    "tiny":       dict(phase_strength=0.9, phase_hop=150, phase_formant=1.40,
                       phase_lowpass_hz=5000, carrier_hz=0, mix=0.0,
                       tremolo_hz=16, tremolo_depth=0.25, comb_ms=0, comb_gain=0),
    # tiny + subtle metallic ring, no phase flatten -> stays clear
    "tiny_ring":  dict(formant=1.25, speed=1.08, carrier_hz=60, mix=0.15,
                       tremolo_hz=14, tremolo_depth=0.15),
    
}

DEFAULT_TEXT = "Hallo, ich bin dein Roboter. Wie kann ich dir heute helfen?"

PARAM_KEYS = ("phase_strength", "phase_hop", "phase_formant", "formant", "speed",
              "carrier_hz", "mix", "bits", "rate_div",
              "tremolo_hz", "tremolo_depth", "comb_ms", "comb_gain")


def _play(path: str) -> None:
    for player in ("afplay", "aplay", "ffplay"):
        exe = shutil.which(player)
        if exe:
            args = [exe, path]
            if player == "ffplay":
                args = [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
            subprocess.run(args, check=False)
            return
    print(f"(no audio player found; file at {path})", file=sys.stderr)


def _params_from_args(args: argparse.Namespace) -> dict[str, float]:
    """Start from the chosen preset, then apply any explicit CLI overrides."""
    params = dict(PRESETS[args.preset])
    overrides = {
        "phase_strength": args.phase,
        "phase_hop": args.hop,
        "formant": args.formant,
        "speed": args.speed,
        "carrier_hz": args.carrier,
        "mix": args.mix,
        "bits": args.bits,
        "rate_div": args.rate_div,
        "tremolo_hz": args.tremolo_hz,
        "tremolo_depth": args.tremolo_depth,
        "comb_ms": args.comb_ms,
        "comb_gain": args.comb_gain,
    }
    for key, val in overrides.items():
        if val is not None:
            params[key] = val
    return params


def _render(text: str, params: dict[str, float], backend, out_path: str) -> None:
    """Synthesize dry with the backend, apply the robot effect, write out_path."""
    fd, dry = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    Path(dry).unlink(missing_ok=True)
    try:
        backend.synthesize(text, dry)
        samples, sr, _ = effects._read_wav(dry)
        effects._write_wav(out_path, effects.robotize(samples, sr, **params), sr)
    finally:
        Path(dry).unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Audition TTS robot-voice presets.")
    p.add_argument("text", nargs="?", default=DEFAULT_TEXT, help="text to speak")
    p.add_argument("--preset", choices=list(PRESETS), default="tiny")
    p.add_argument("--all", action="store_true", help="play every preset in sequence")
    p.add_argument("--interactive", "-i", action="store_true", help="loop: type lines to re-synth")
    p.add_argument("--save", metavar="WAV", help="save to this file instead of a temp file")
    # per-parameter overrides (override the preset)
    p.add_argument("--phase", type=float, help="monotone robotization 0..1 (the main robot lever)")
    p.add_argument("--hop", type=int, help="robotization hop; buzz pitch = sr/hop (try 130-320)")
    p.add_argument("--formant", type=float, help="clarity-preserving formant shift; >1 = tinier (1.0-1.5)")
    p.add_argument("--speed", type=float, help="pitch/tempo up; >1 = higher + faster small robot (1.0-1.3)")
    p.add_argument("--carrier", type=float, help="ring-mod carrier Hz (30-70)")
    p.add_argument("--mix", type=float, help="ring-mod mix 0..1")
    p.add_argument("--bits", type=int, help="bit-crush depth (4-8; 0=off)")
    p.add_argument("--rate-div", dest="rate_div", type=int, help="sample-rate divider (1=off, 2-4=grittier)")
    p.add_argument("--tremolo-hz", dest="tremolo_hz", type=float)
    p.add_argument("--tremolo-depth", dest="tremolo_depth", type=float)
    p.add_argument("--comb-ms", dest="comb_ms", type=float)
    p.add_argument("--comb-gain", dest="comb_gain", type=float)
    args = p.parse_args()

    backend = _make_backend()  # raw backend, no effect wrapper
    out = args.save or tempfile.mkstemp(suffix=".wav")[1]

    if args.all:
        for name, params in PRESETS.items():
            print(f">>> {name:11s} {params}")
            _render(args.text, params, backend, out)
            _play(out)
        return

    if args.interactive:
        params = _params_from_args(args)
        print(f"preset={args.preset} params={params}")
        print("Type text to hear it. Commands: :preset <name> | :set <key> <val> | :params | :quit")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in (":quit", ":q"):
                break
            if line == ":params":
                print(params)
                continue
            if line.startswith(":preset "):
                name = line.split(maxsplit=1)[1].strip()
                if name in PRESETS:
                    params = dict(PRESETS[name])
                    print(f"preset={name} params={params}")
                else:
                    print(f"unknown preset; choose from {list(PRESETS)}")
                continue
            if line.startswith(":set "):
                try:
                    _, key, val = line.split(maxsplit=2)
                except ValueError:
                    print(":set <key> <val>")
                    continue
                if key in PARAM_KEYS:
                    params[key] = int(val) if key in ("phase_hop", "bits", "rate_div") else float(val)
                    print(params)
                else:
                    print(f"keys: {' '.join(PARAM_KEYS)}")
                continue
            _render(line, params, backend, out)
            _play(out)
        return

    params = _params_from_args(args)
    print(f"text={args.text!r}\npreset={args.preset} params={params}")
    _render(args.text, params, backend, out)
    if args.save:
        print(f"saved -> {args.save}")
    _play(out)


if __name__ == "__main__":
    main()
