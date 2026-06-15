"""Audio capture + playback for the Phase 0 host prototype (macOS).

Capture uses ffmpeg avfoundation (push-to-talk). Playback uses afplay.
This is the only intentionally host-specific module; on the Linux fleet it gets
swapped for an ALSA/PulseAudio capture without touching the pipeline.
"""
from __future__ import annotations

import subprocess
import sys
import threading

from .config import config


def record_push_to_talk(out_path: str) -> bool:
    """Record from the mic until the user presses Enter. Returns True on success."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "avfoundation",
        "-i", f":{config.audio_input_device}",
        "-ac", "1",
        "-ar", str(config.sample_rate),
        "-t", str(config.max_record_seconds),
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    print("  ● recording... press Enter to stop", end="", flush=True)
    stop = threading.Event()

    def _wait_enter():
        try:
            sys.stdin.readline()
        finally:
            stop.set()

    threading.Thread(target=_wait_enter, daemon=True).start()

    while proc.poll() is None and not stop.is_set():
        stop.wait(0.05)

    if proc.poll() is None:
        try:
            proc.communicate(input=b"q", timeout=2)  # graceful ffmpeg stop
        except Exception:
            proc.terminate()
            proc.wait(timeout=2)
    print()
    return proc.returncode in (0, 255)  # 255 == stopped via 'q'


def play_wav(path: str) -> None:
    subprocess.run(["afplay", path], check=False)
