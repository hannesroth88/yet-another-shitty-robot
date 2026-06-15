"""Non-interactive smoke test: STT -> LLM -> TTS without the live mic.

Usage:
  .venv/bin/python -m tools.smoke ["prompt text"]
  .venv/bin/python -m tools.smoke ["prompt text"] --record "Mac M1 Pro"

Generates a test clip with macOS `say`, runs it through the full Pipeline, and
prints per-stage latency. With --record, appends the measured run to
benchmarks.json and regenerates benchmarks.html. Mac-only (uses say).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from src.config import config
from src.latency import Timings
from src.llm import get_llm
from src.pipeline import Pipeline
from src.stt import get_stt
from src.tts import get_tts

ROOT = Path(__file__).resolve().parent.parent


def _detect_accel() -> str:
    import platform
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Metal (LLM) / CPU (STT)"
    return "CPU"


def _record(environment: str, t: Timings, cold: bool) -> None:
    """Append this run to benchmarks.json and regenerate the HTML report."""
    data = json.loads((ROOT / "benchmarks.json").read_text())
    data["records"].append({
        "date": date.today().isoformat(),
        "environment": environment,
        "accel": _detect_accel(),
        "stt_config": f"{config.stt_backend} {config.stt_model} {config.stt_compute_type}",
        "stt_ms": round(t.stages.get("stt", 0)),
        "llm_model": config.llm_model,
        "llm_quant": "?",
        "llm_first_token_ms": round(t.info.get("llm_first_token", 0)),
        "llm_ms": round(t.stages.get("llm", 0)),
        "tts_config": f"{config.tts_backend}"
            + (f" ({config.say_voice})" if config.tts_backend == "say" else ""),
        "tts_ms": round(t.stages.get("tts", 0)),
        "total_ms": round(t.total()),
        "notes": "COLD run (model load)." if cold else "smoke run.",
    })
    (ROOT / "benchmarks.json").write_text(json.dumps(data, indent=2) + "\n")
    from tools import bench_report
    bench_report.main()
    print(f"recorded run for '{environment}' (set llm_quant manually in benchmarks.json)")


def main() -> int:
    args = sys.argv[1:]
    environment = None
    if "--record" in args:
        i = args.index("--record")
        environment = args[i + 1] if i + 1 < len(args) else "unknown"
        del args[i:i + 2]
    prompt = args[0] if args else "What is the capital of France? Answer in one short sentence."

    tmp = Path(tempfile.mkdtemp(prefix="robot-smoke-"))
    clip = str(tmp / "prompt.wav")
    subprocess.run(
        ["say", "-v", config.say_voice, "--data-format=LEI16@22050", "-o", clip, prompt],
        check=True,
    )

    build_start = time.perf_counter()
    pipe = Pipeline(get_stt(), get_llm(), get_tts(), config.system_prompt)
    build_ms = (time.perf_counter() - build_start) * 1000.0

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

    if environment:
        cold = t.info.get("llm_first_token", 0) > 1000
        _record(environment, t, cold)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
