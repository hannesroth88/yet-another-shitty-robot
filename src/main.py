"""Phase 0 voice-assistant loop: mic -> whisper -> ollama -> tts -> speaker.

Run from the repo root:  python -m src.main
Press Enter to talk, Enter again to stop. Type 'q' + Enter (empty prompt) to quit.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .audio import default_output_device, play_wav, record_push_to_talk
from .config import config
from .latency import Timings
from .llm import get_llm
from .pipeline import Pipeline
from .stt import get_stt
from .tts import get_tts


def banner() -> None:
    print("=" * 64)
    print(" Phase 0 voice assistant  (mic -> STT -> LLM -> TTS -> speaker)")
    print("-" * 64)
    print(f" STT : {config.stt_backend} ({config.stt_model})")
    print(f" LLM : {config.llm_backend} ({config.llm_model})")
    print(f" TTS : {config.tts_backend}")
    print(f" Mic : avfoundation device :{config.audio_input_device}")
    print(f" Out : {default_output_device()}  (afplay -> macOS default output)")
    print("=" * 64)


def main() -> int:
    banner()
    print("\nLoading models...", flush=True)
    try:
        stt = get_stt()
        llm = get_llm()
        tts = get_tts()
    except Exception as exc:  # pragma: no cover
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1

    pipe = Pipeline(stt, llm, tts, config.system_prompt)
    tmp = Path(tempfile.mkdtemp(prefix="robot-"))
    print("Ready.\n")

    turn = 0
    while True:
        try:
            cmd = input("[Enter]=talk  q=quit > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if cmd == "q":
            break

        turn += 1
        t = Timings()
        wav_in = str(tmp / f"in_{turn}.wav")
        wav_out = str(tmp / f"out_{turn}.wav")

        if not record_push_to_talk(wav_in):
            print("  (recording failed)\n")
            continue

        text = pipe.transcribe(wav_in, t)
        if not text:
            print("  (heard nothing)\n")
            continue
        print(f"  you : {text}")

        reply = pipe.respond(text, t)
        print(f"  bot : {reply}")

        pipe.speak(reply, wav_out, t)
        play_wav(wav_out)

        print(f"  ⏱  {t.render()}\n")

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
