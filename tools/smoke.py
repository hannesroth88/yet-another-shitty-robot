"""Non-interactive smoke test: STT -> LLM -> TTS without the live mic.

Usage:
  .venv/bin/python -m tools.smoke ["prompt text"]
  .venv/bin/python -m tools.smoke ["prompt text"] --record "Mac M1 Pro"
  .venv/bin/python -m tools.smoke ["prompt text"] --no-play   # benchmark silently

Generates a test clip with macOS `say`, runs it through the full Pipeline,
plays the spoken reply, and prints per-stage latency. With --record, appends the
measured run to benchmarks.json and regenerates benchmarks.html. Mac-only (uses
say/afplay).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

from src.config import config
from src.latency import Timings
from src.llm import get_llm
from src.orchestrator import Orchestrator
from src.stt import get_stt
from src.tts import get_tts

ROOT = Path(__file__).resolve().parent.parent


def _detect_accel() -> str:
    import platform
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Metal (LLM) / CPU (STT)"
    return "CPU"


def _record(environment: str, t: Timings, cold: bool, prompt: str) -> None:
    """Append this run to benchmarks.json and regenerate the HTML report."""
    data = json.loads((ROOT / "benchmarks.json").read_text())
    data["records"].append({
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "environment": environment,
        "accel": _detect_accel(),
        "stt_config": (
            f"{config.parakeet_model.split('/')[-1]} (mlx)"
            if config.stt_backend == "parakeet"
            else f"{config.stt_backend} {config.stt_model} {config.stt_compute_type}"
        ),
        "stt_ms": round(t.stages.get("stt", 0)),
        "llm_model": config.llm_model,
        "llm_quant": "?",
        "llm_first_token_ms": round(t.info.get("llm_first_token", 0)),
        "llm_ms": round(t.stages.get("llm", 0)),
        "tts_config": (
            f"{config.tts_backend}"
            + (f" ({config.say_voice})" if config.tts_backend == "say" else "")
            + (f" ({config.qwen3_model.split('/')[-1]}:{config.qwen3_speaker})"
               if config.tts_backend == "qwen3" else "")
        ),
        "tts_ms": round(t.stages.get("tts", 0)),
        "first_audio_ms": round(t.info.get("first_audio", 0)) or None,
        "total_ms": round(t.total()),
        "notes": "COLD run (model load)." if cold else "smoke run.",
        "coldstart_ms": None,
        "prompt_text": prompt,
    })
    (ROOT / "benchmarks.json").write_text(json.dumps(data, indent=2) + "\n")
    from tools import bench_report
    bench_report.main()
    print(f"recorded run for '{environment}' (set llm_quant manually in benchmarks.json)")


def main() -> int:
    args = sys.argv[1:]
    environment = None
    play = True
    if "--no-play" in args:
        play = False
        args.remove("--no-play")
    if "--record" in args:
        i = args.index("--record")
        environment = args[i + 1] if i + 1 < len(args) else "unknown"
        del args[i:i + 2]
    DEFAULT_PROMPT = (
        "Wie lautet die Hauptstadt von Frankreich? Antworte in einem kurzen Satz."
    )
    prompt = args[0] if args else DEFAULT_PROMPT

    tmp = Path(tempfile.mkdtemp(prefix="robot-smoke-"))
    clip = str(tmp / "prompt.wav")
    subprocess.run(
        ["say", "-v", config.say_voice, "--data-format=LEI16@22050", "-o", clip, prompt],
        check=True,
    )

    build_start = time.perf_counter()
    orch = Orchestrator(get_stt(), get_llm(), get_tts(), config.system_prompt)
    build_ms = (time.perf_counter() - build_start) * 1000.0

    t = Timings()
    heard = orch.transcribe(clip, t)
    print("you:", heard)
    if not heard:
        print("FAIL: STT produced no text", file=sys.stderr)
        return 1
    reply = orch.respond(heard, t, play=False)
    print("bot:", reply)
    if not reply:
        print("FAIL: LLM produced no text", file=sys.stderr)
        return 1
    print("latency:", t.render())

    if play:
        from src.audio import play_wav
        # Re-synthesize the full reply once for a clean playback of the whole turn.
        out = str(tmp / "reply.wav")
        orch.tts.synthesize(reply, out)
        if Path(out).stat().st_size <= 0:
            print("FAIL: TTS produced empty audio", file=sys.stderr)
            return 1
        play_wav(out)

    if environment:
        cold = t.info.get("llm_first_token", 0) > 1000
        _record(environment, t, cold, prompt)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
