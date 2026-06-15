"""Robot-voice lab: audition TTS + robot effect presets, tune params live.

Synthesizes German text with the configured TTS backend (Thorsten/Piper by
default), applies a robot effect, and plays it. Pick a preset, override any
parameter, compare all presets, or drop into an interactive loop.

Examples:
    .venv/bin/python -m tools.voice_lab                      # default text, classic preset
    .venv/bin/python -m tools.voice_lab "Hallo Welt" --preset metallic
    .venv/bin/python -m tools.voice_lab --all                # play every preset back to back
    .venv/bin/python -m tools.voice_lab --mix 0.5 --carrier 60 --comb-ms 1.5 --comb-gain 0.4
    .venv/bin/python -m tools.voice_lab --interactive        # type lines, re-synth live
    .venv/bin/python -m tools.voice_lab --save out.wav --preset classic

Mac plays via `afplay`. On Linux it tries `aplay`/`ffplay`.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from src.tts import _make_backend, effects

# carrier_hz, mix, tremolo_hz, tremolo_depth, comb_ms, comb_gain
PRESETS: dict[str, dict[str, float]] = {
    "dry":        dict(carrier_hz=55, mix=0.0),
    "subtle":     dict(carrier_hz=55, mix=0.45),
    "classic":    dict(carrier_hz=55, mix=0.65, comb_ms=1.5, comb_gain=0.35),
    "mechanical": dict(carrier_hz=50, mix=0.60, tremolo_hz=12, tremolo_depth=0.25,
                       comb_ms=1.5, comb_gain=0.40),
    "metallic":   dict(carrier_hz=180, mix=0.70, comb_ms=0.8, comb_gain=0.50),
    "custom":   dict(carrier_hz=1000, mix=0.8, tremolo_hz=10, tremolo_depth=0.1 ),
}

DEFAULT_TEXT = "Hallo, ich bin dein Roboter. Wie kann ich dir heute helfen?"


def _play(path: str) -> None:
    for player in ("afplay", "aplay", "ffplay"):
        exe = shutil.which(player)
        if exe:
            args = [exe, path]
            if player == "ffplay":
                args = [exe, "-nodisp", "-autoexit",
                        "-loglevel", "quiet", path]
            subprocess.run(args, check=False)
            return
    print(f"(no audio player found; file at {path})", file=sys.stderr)


def _params_from_args(args: argparse.Namespace) -> dict[str, float]:
    """Start from the chosen preset, then apply any explicit CLI overrides."""
    params = dict(PRESETS[args.preset])
    overrides = {
        "carrier_hz": args.carrier,
        "mix": args.mix,
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
    Path(dry).unlink(missing_ok=True)  # backend writes it fresh
    import os
    os.close(fd)
    try:
        backend.synthesize(text, dry)
        samples, sr, _ = effects._read_wav(dry)
        effects._write_wav(out_path, effects.robotize(
            samples, sr, **params), sr)
    finally:
        Path(dry).unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Audition TTS robot-voice presets.")
    p.add_argument("text", nargs="?", default=DEFAULT_TEXT,
                   help="text to speak")
    p.add_argument("--preset", choices=list(PRESETS), default="classic")
    p.add_argument("--all", action="store_true",
                   help="play every preset in sequence")
    p.add_argument("--interactive", "-i", action="store_true",
                   help="loop: type lines to re-synth")
    p.add_argument("--save", metavar="WAV",
                   help="save to this file instead of a temp file")
    # per-parameter overrides (override the preset)
    p.add_argument("--carrier", type=float,
                   help="ring-mod carrier Hz (40-70 intelligible)")
    p.add_argument("--mix", type=float, help="effect mix 0..1")
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
                _, key, val = line.split(maxsplit=2)
                if key in ("carrier_hz", "mix", "tremolo_hz", "tremolo_depth", "comb_ms", "comb_gain"):
                    params[key] = float(val)
                    print(params)
                else:
                    print(
                        "keys: carrier_hz mix tremolo_hz tremolo_depth comb_ms comb_gain")
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
