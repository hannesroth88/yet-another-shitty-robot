"""Non-interactive smoke test: STT -> LLM -> TTS without the live mic.

Usage:  .venv/bin/python -m tools.smoke ["text to speak as the test prompt"]

Generates a test clip with macOS `say`, runs it through the full Pipeline, and
prints per-stage latency. Exits non-zero if any stage fails. Mac-only (uses say).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from src.config import config
from src.latency import Timings
from src.llm import get_llm
from src.pipeline import Pipeline
from src.stt import get_stt
from src.tts import get_tts


def main() -> int:
    prompt = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "What is the capital of France? Answer in one short sentence."
    )
    tmp = Path(tempfile.mkdtemp(prefix="robot-smoke-"))
    clip = str(tmp / "prompt.wav")
    subprocess.run(
        ["say", "-v", config.say_voice, "--data-format=LEI16@22050", "-o", clip, prompt],
        check=True,
    )

    pipe = Pipeline(get_stt(), get_llm(), get_tts(), config.system_prompt)
    t = Timings()
    heard = pipe.transcribe(clip, t)
    print("you:", heard)
    if not heard:
        print("FAIL: STT produced no text", file=sys.stderr)
        return 1
    reply = pipe.respond(heard, t)
    print("bot:", reply)
    if not reply:
        print("FAIL: LLM produced no text", file=sys.stderr)
        return 1
    out = str(tmp / "reply.wav")
    pipe.speak(reply, out, t)
    if Path(out).stat().st_size <= 0:
        print("FAIL: TTS produced empty audio", file=sys.stderr)
        return 1
    print("latency:", t.render())
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
