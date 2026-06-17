"""Phase 1 voice-assistant loop: mic -> STT -> LLM -> TTS -> speaker.

The CLI is now a *client* of the event-driven :class:`~src.orchestrator.Orchestrator`
(streaming TTS speaks sentence 1 while the LLM is still writing sentence 2).

Run from the repo root:  python -m src.main
Press Enter to talk, Enter again to stop. Type 'q' + Enter to quit.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .audio import default_output_device, play_wav, record_push_to_talk
from .config import config
from .latency import Timings
from .llm import get_llm
from .orchestrator import Event, Orchestrator
from .stt import get_stt
from .tts import get_tts


def banner() -> None:
    stream = "on" if config.tts_streaming else "off"
    print("=" * 64)
    print(" Phase 1 voice assistant  (mic -> STT -> LLM -> TTS -> speaker)")
    print("-" * 64)
    print(f" STT : {config.stt_backend} ({config.stt_model})")
    print(f" LLM : {config.llm_backend} ({config.llm_model})")
    print(f" TTS : {config.tts_backend}  [streaming {stream}]")
    print(f" Mic : avfoundation device :{config.audio_input_device}")
    print(f" Out : {default_output_device()}  (afplay -> macOS default output)")
    print("=" * 64)


def make_printer() -> "callable":
    """A subscriber that renders orchestrator events to the console."""
    state = {"spoke": False}

    def on_event(ev: Event) -> None:
        if ev.type == "heard_text":
            print(f"  you : {ev.data['text']}")
        elif ev.type == "assistant_end":
            print(f"  bot : {ev.data['text']}")
        elif ev.type == "tts_audio":
            print(f"      ♪ {ev.data['text']}  ({ev.data['synth_ms']}ms)")
        elif ev.type == "error":
            print(f"  !! error: {ev.data['message']}", file=sys.stderr)
        elif ev.type == "latency":
            pass  # rendered from Timings below

    return on_event


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

    player = play_wav if config.tts_streaming else None
    orch = Orchestrator(stt, llm, tts, config.system_prompt, player=player)
    orch.subscribe(make_printer())

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

        if not record_push_to_talk(wav_in):
            print("  (recording failed)\n")
            continue

        text = orch.transcribe(wav_in, t)
        if not text:
            print("  (heard nothing)\n")
            continue

        try:
            reply = orch.respond(text, t, play=config.tts_streaming)
        except Exception as exc:
            print(f"  (turn failed: {exc})\n")
            continue

        # Non-streaming mode: nothing played yet, so play the whole reply once.
        if not config.tts_streaming and reply:
            wav_out = str(tmp / f"out_{turn}.wav")
            tts.synthesize(reply, wav_out)
            play_wav(wav_out)

        print(f"  ⏱  {t.render()}\n")

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
