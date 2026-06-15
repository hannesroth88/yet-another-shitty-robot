# robot — Phase 0 voice assistant prototype

`mic → STT → LLM → TTS → speaker`, running entirely on the local host.

This is **Phase 0** from [AGENTS.md](./AGENTS.md): prove the end-to-end voice loop
on the Mac (M1). Components are swappable behind interfaces so later phases can
move STT/LLM/TTS onto different machines (Gaming PC GPU, NUC, etc.) and onto the
ESP32-S3-Box / robot without rewriting the pipeline.

## Architecture

```
src/
  config.py        # all settings (env / .env), nothing hardcoded
  latency.py       # per-stage latency timing (first-class metric)
  audio.py         # mic capture (ffmpeg) + playback (afplay)  [host-specific]
  pipeline.py      # wires STT -> LLM -> TTS, stable seam for the fleet
  main.py          # interactive push-to-talk loop
  stt/  base interface + faster_whisper_stt.py
  llm/  base interface + ollama_llm.py
  tts/  base interface + say_tts.py (mac default) + piper_tts.py (fleet default)
tools/
  smoke.py         # non-interactive end-to-end test (no mic needed)
```

Each of STT / LLM / TTS is selected by a `*_BACKEND` env var and built by a
factory, so swapping an implementation never touches `pipeline.py` or `main.py`.

## Requirements

- macOS (Phase 0 host). `ffmpeg` + `afplay` + `say` (all present on macOS).
- [Ollama](https://ollama.com) running with a model pulled:
  `ollama pull llama3.2`
- Python 3.12 venv.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp env.example .env        # optional; defaults work out of the box
```

Find your mic's ffmpeg device index and set `AUDIO_INPUT_DEVICE` in `.env`:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

## Run

Interactive voice loop:

```bash
.venv/bin/python -m src.main
# Press Enter to talk, Enter again to stop. 'q' to quit.
```

Non-interactive smoke test (synthesizes a prompt with `say`, no mic required):

```bash
.venv/bin/python -m tools.smoke "What is the capital of France?"
```

Example output:

```
you: What is the capital of France?
bot: The capital of France is Paris.
latency: stt 569ms | llm_first_token 200ms | llm_total 276ms | tts 667ms | TOTAL 1712ms
```

## Latency benchmark log

We track latency per environment and try to improve it over time.

- **Source of truth:** `benchmarks.json` (append a record, or use `--record`).
- **Report:** `benchmarks.html` — self-contained, sortable, highlights fastest
  (green) / slowest (red) per column, filter by environment, best-total cards.
  Regenerate after editing the JSON:

```bash
.venv/bin/python -m tools.bench_report   # benchmarks.json -> benchmarks.html
open benchmarks.html
```

- **Auto-record a measured run** (appends to JSON + regenerates HTML):

```bash
.venv/bin/python -m tools.smoke "prompt" --record "Gaming PC"
# then set llm_quant in benchmarks.json (e.g. Q4_K_M) since Ollama doesn't report it via API
```

Columns: Environment · Accel · STT Config · STT ms · LLM Model · Quant ·
LLM 1st-token ms · LLM ms · TTS Config · TTS ms · TOTAL ms · Notes. TOTAL is
real end-to-end (STT + LLM + TTS); first-token is informational.

## Configuration

All knobs live in `env.example` (copy to `.env`). Highlights:

| Var | Default | Notes |
|-----|---------|-------|
| `AUDIO_INPUT_DEVICE` | `1` | ffmpeg avfoundation index |
| `STT_BACKEND` / `STT_MODEL` | `faster-whisper` / `base` | `tiny`/`base`/`small`/`medium` |
| `LLM_BACKEND` / `LLM_MODEL` | `ollama` / `llama3.2:latest` | any Ollama model |
| `OLLAMA_HOST` | `http://localhost:11434` | point at a remote host later (e.g. woken Gaming PC) |
| `TTS_BACKEND` | `say` | `say` (mac) or `piper` (fleet) |

## Swapping components (preview of later phases)

- **Run the LLM on another host:** set `OLLAMA_HOST=http://gaming-pc:11434`.
  Nothing else changes. (Wake-on-LAN logic lands in the LLM backend in Phase 2.)
- **Use Piper TTS** (cross-platform): set `TTS_BACKEND=piper`, install a piper
  binary + a `.onnx` voice, point `PIPER_VOICE` at it.
- **Bigger/smaller STT:** change `STT_MODEL`.

## Known Phase-0 limitations (intentional)

- Push-to-talk only (no wake word / VAD streaming yet — that's Phase 4 on the ESP32).
- `audio.py` is macOS-specific; Linux fleet hosts get an ALSA/Pulse capture swap.
- TTS default is `say` (mac-only) for a zero-dependency demo; Piper is the
  portable default once voices are installed.
- No Home Assistant wiring yet (Phase 3).
