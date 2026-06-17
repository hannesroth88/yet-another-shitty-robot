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
        + kokoro_tts.py + qwen3_tts.py
        effects.py + robot_tts.py  # optional robot-voice DSP layer
tools/
  smoke.py         # non-interactive end-to-end test (no mic needed)
utils/
  voice_lab.py     # audition / tune the robot-voice effect (presets + live REPL)
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
| `TTS_BACKEND` | `say` | `say` (mac), `piper` (fleet), `kokoro` (German Martin), `qwen3` (quality) |

## Swapping components (preview of later phases)

- **Run the LLM on another host:** set `OLLAMA_HOST=http://gaming-pc:11434`.
  Nothing else changes. (Wake-on-LAN logic lands in the LLM backend in Phase 2.)
- **Use Piper TTS** (cross-platform, natural German voice): install Piper and
  the German `thorsten-high` voice, then set `TTS_BACKEND=piper`.

  ```bash
  .venv/bin/pip install piper-tts
  mkdir -p voices && cd voices
  BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high
  curl -sL -o de_DE-thorsten-high.onnx      $BASE/de_DE-thorsten-high.onnx
  curl -sL -o de_DE-thorsten-high.onnx.json $BASE/de_DE-thorsten-high.onnx.json
  ```

  `PIPER_VOICE` already defaults to `voices/de_DE-thorsten-high.onnx`. Run with
  the venv active so the `piper` CLI is on `PATH`.
- **Use Kokoro German "Martin" TTS** (82M ONNX, often more natural German than
  Piper): install `kokoro-onnx` and fetch the model, then set
  `TTS_BACKEND=kokoro`.

  ```bash
  .venv/bin/pip install kokoro-onnx huggingface_hub
  .venv/bin/python -m utils.fetch_kokoro   # -> voices/kokoro-martin.onnx + voices-martin.npz
  ```

  Uses the `kokoro-onnx` runtime (not the `kokoro` KPipeline, which has no
  German voice). Model files come from
  `Godelaune/Kokoro-82M-ONNX-German-Martin`. `KOKORO_*` vars (model, voices,
  voice name, speed, lang) are in `.env`. The robot effect below still applies
  on top — `TTS_BACKEND=kokoro` + `TTS_EFFECT=robot` chains Kokoro → robot
  filter.
- **Use Qwen3-TTS** (best German naturalness / expressiveness on capable HW):
  install optional deps, warm up once, then set `TTS_BACKEND=qwen3`.

  ```bash
  .venv/bin/pip install qwen-tts torch
  .venv/bin/python -m utils.fetch_qwen3   # downloads model to HF cache + writes voices/qwen3-smoke.wav
  ```

  Default config targets `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`. Tune with
  `QWEN3_*` vars in `.env` (model, device map, dtype, language, speaker,
  optional clone mode with reference audio).
- **Bigger/smaller STT:** change `STT_MODEL`.

## Robot voice effect

The TTS output can be run through a local DSP layer (`src/tts/effects.py`,
wrapped by `src/tts/robot_tts.py`) to give any backend a robot character. It is
engine-agnostic and low-latency — the German always comes from the TTS voice;
the robot sound is pure post-processing on top. Enable with `TTS_EFFECT=robot`.
It works the same over `say`, `piper`, `kokoro`, or `qwen3` (e.g. Kokoro Martin
→ robot filter is just `TTS_BACKEND=kokoro` + `TTS_EFFECT=robot`).

Key knobs (all in `env.example` / `.env`):

| Var | Default | Notes |
|-----|---------|-------|
| `TTS_EFFECT` | `none` | `none` or `robot` |
| `ROBOT_PHASE_STRENGTH` | `0.9` | monotone "robotization" 0..1 (the main robot lever) |
| `ROBOT_PHASE_HOP` | `150` | buzz pitch = `sample_rate / hop`; smaller = higher/tinier |
| `ROBOT_PHASE_FORMANT` | `1.4` | formant shift; `>1` = smaller/daintier "tiny robot" voice |
| `ROBOT_PHASE_LOWPASS_HZ` | `5000` | smooths the robotization (removes crackle); lower = mellower |
| `ROBOT_CARRIER_HZ` / `ROBOT_MIX` | `0` / `0` | ring modulation (metallic buzz) |
| `ROBOT_BITS` / `ROBOT_RATE_DIV` | `0` / `1` | bit-crusher (digital lo-fi grit) |
| `ROBOT_TREMOLO_HZ` / `ROBOT_TREMOLO_DEPTH` | `16` / `0.25` | mechanical amplitude pulse |
| `ROBOT_COMB_MS` / `ROBOT_COMB_GAIN` | `0` / `0` | short comb = metallic tin-can resonance |

### Auditioning / tuning — `utils/voice_lab.py`

A standalone tool to find your sound without touching the loop. It synthesizes
German text with the configured TTS backend, applies the robot effect, and plays
it. Ships with presets (`dry`, `classic`, `mechanical`, `tiny`, `tiny_ring`) and
lets you override any parameter or tune live.

```bash
.venv/bin/python -m utils.voice_lab "Hallo Welt" --preset tiny   # one preset
.venv/bin/python -m utils.voice_lab --all                        # compare all presets
.venv/bin/python -m utils.voice_lab --formant 1.5 --hop 130       # override params
.venv/bin/python -m utils.voice_lab -i                            # interactive REPL
.venv/bin/python -m utils.voice_lab "Test" --save out.wav         # write a wav
```

In the REPL (`-i`): type text to hear it; `:preset <name>`, `:set <key> <val>`,
`:params`, `:quit`. Once you like a setting, copy the matching `ROBOT_*` values
into `.env`.

## Known Phase-0 limitations (intentional)

- Push-to-talk only (no wake word / VAD streaming yet — that's Phase 4 on the ESP32).
- `audio.py` is macOS-specific; Linux fleet hosts get an ALSA/Pulse capture swap.
- TTS default is `say` (mac-only) for a zero-dependency demo; Piper is the
  portable default once voices are installed.
- No Home Assistant wiring yet (Phase 3).
