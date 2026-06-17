# robot — local voice assistant prototype

`mic → STT → LLM → TTS → speaker`, running entirely on the local host.

**Phase 0** (end-to-end loop on the Mac) and **Phase 1** (service split: event-
driven orchestrator, streaming TTS, HTTP/WS control server, network backends) are
done; **Phase 2** (fleet distribution: routed LLM + Wake-on-LAN + standalone
services) has its foundation in place (see [AGENTS.md](./AGENTS.md)). Components
are swappable behind interfaces so STT/LLM/TTS can move onto different machines
(Gaming PC GPU, NUC) and eventually the ESP32-S3-Box / robot without rewriting
the pipeline.

## Architecture

```
src/
  config.py        # all settings (env / .env), nothing hardcoded
  latency.py       # per-stage latency timing (first-class metric)
  audio.py         # mic capture (ffmpeg) + playback (afplay)  [host-specific]
  pipeline.py      # legacy synchronous wiring (Phase 0; kept for reference)
  orchestrator.py  # Phase 1 event-driven orchestrator (phase machine + event bus)
  presets.py       # fleet host presets -> .env snippets (Phase 2)
  main.py          # interactive push-to-talk loop (client of the orchestrator)
  text/            # sentence_chunker.py (stream tokens -> sentences)
  net/             # wol.py (Wake-on-LAN + readiness probes)  [Phase 2]
  server/          # app.py HTTP + WebSocket control server + static web face
  stt/  base interface + faster_whisper_stt.py + parakeet_stt.py + http_stt.py
  llm/  base interface + ollama_llm.py + http_llm.py + routed_llm.py
  tts/  base interface + say_tts.py (mac) + piper_tts.py (fleet default)
        + kokoro_tts.py + qwen3_tts.py + qwen3_mlx_tts.py + streaming.py
        effects.py + robot_tts.py  # optional robot-voice DSP layer
services/          # Phase 2 standalone HTTP services (stt_server, tts_server)
tools/
  smoke.py         # non-interactive end-to-end test (records first_audio_ms)
  server_smoke.py  # WebSocket control-server smoke test
  bench_report.py  # benchmarks.json -> benchmarks.html
tests/             # unit tests (sentence chunker, Wake-on-LAN)
```

Each of STT / LLM / TTS is selected by a `*_BACKEND` env var and built by a
factory, so swapping an implementation (or moving it to another host via the
`http`/`routed` backends) never touches the orchestrator.

## Requirements

- macOS (dev host). `ffmpeg` + `afplay` + `say` (all present on macOS).
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

Interactive voice loop (streaming TTS speaks sentence 1 while the LLM writes
sentence 2):

```bash
.venv/bin/python -m src.main
# Press Enter to talk, Enter again to stop. 'q' to quit.
```

Control server + web face (Phase 1 HTTP + WebSocket entry point) — use the
`robot` CLI to start it and watch logs (see [cli/README.md](cli/README.md)):

```bash
cli/robot start            # foreground, live logs (Ctrl-C stops)
cli/robot start --bg       # detached; HTTPS so the phone mic works
cli/robot start --tts worker   # qwen3 cloned voice (persistent, warm, no crash)
cli/robot status           # running? port / TLS / pids
cli/robot logs             # follow the latest log (tail -f)
cli/robot logs --errors    # only tracebacks / errors / segfaults
cli/robot url              # URLs to open (localhost + phone LAN IP)
cli/robot stop | restart   # lifecycle
```

Or talk to the entry point directly: `.venv/bin/python -m src.server.app`
(open http://localhost:8010).

Non-interactive smoke test (synthesizes a prompt with `say`, no mic required):

```bash
.venv/bin/python -m tools.smoke "What is the capital of France?"
```

Tests:

```bash
.venv/bin/python -m unittest discover -s tests -v   # chunker + Wake-on-LAN
.venv/bin/python -m tools.server_smoke              # control-server WS round-trip
```

### Fleet (Phase 2)

Run a stage as a standalone service on another host, then point the orchestrator
at it via env — no code change:

```bash
# on the always-on host (e.g. NUC):
.venv/bin/python -m services.stt_server.app          # :9000 POST /transcribe
# on the orchestrator host:
STT_BACKEND=http STT_HTTP_URL=http://nuc:9000 .venv/bin/python -m src.main
```

Remote LLM on the Gaming-PC GPU with local fallback + Wake-on-LAN:
`LLM_BACKEND=routed` (see `src/presets.py` → `nuc-gpu`, and `services/README.md`).

Example output:

```
you: What is the capital of France?
bot: The capital of France is Paris.
latency: stt 569ms | llm 276ms | tts 667ms | (first_audio 410ms) | TOTAL 1712ms
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
| `TTS_BACKEND` | `say` | `say` (mac), `piper` (fleet), `kokoro` (German Martin), `qwen3-mlx` (Apple-Silicon voice clone), `qwen3` (PyTorch x86/CUDA) |

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
- **Use Qwen3-TTS on Apple Silicon (MLX) — recommended quality path** (fast +
  clones a custom voice). This runs a 6-bit Base model natively on Metal and
  voice-clones a reference clip in-context (ICL), the same approach as
  [badlogic/pibot](https://github.com/badlogic/pibot). On the M1 it reaches
  **RTF < 1** (vs ~4 for the PyTorch path below) and needs **no robot DSP** —
  the voice comes straight from your reference sample.

  ```bash
  .venv/bin/pip install mlx-audio
  # 1) make a clean mono reference wav from your sample (e.g. an ElevenLabs clip):
  ffmpeg -i voices/sample/your-voice.mp3 -ac 1 -ar 24000 voices/sample/reference.wav
  # 2) put the EXACT transcript of that clip next to it (or set QWEN3_REF_TEXT):
  #    voices/sample/your-voice.txt
  # 3) point .env at them, warm up, and switch backend:
  .venv/bin/python -m utils.fetch_qwen3_mlx   # downloads model + writes voices/qwen3-mlx-smoke.wav
  ```

  Set `TTS_BACKEND=qwen3-mlx`, `TTS_EFFECT=none`, `QWEN3_REF_AUDIO`,
  `QWEN3_REF_TEXT_FILE` (or `QWEN3_REF_TEXT`), and `QWEN3_LANGUAGE` in `.env`.
  Model: `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` (`QWEN3_MLX_MODEL`). The
  model loads once at pipeline startup and stays resident, so per-turn latency
  is just generation time.
- **Use Qwen3-TTS (PyTorch/transformers) on x86/CUDA hosts:** install optional
  deps, warm up once, then set `TTS_BACKEND=qwen3`.

  ```bash
  .venv/bin/pip install qwen-tts torch
  .venv/bin/python -m utils.fetch_qwen3   # downloads model to HF cache + writes voices/qwen3-smoke.wav
  ```

  Default config targets `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`. Tune with
  `QWEN3_*` vars in `.env` (model, device map, dtype, language, speaker,
  optional clone mode with reference audio). Note: `qwen-tts` pins
  `transformers==4.57.3`, which conflicts with `mlx-audio` — keep the two paths
  in separate venvs. On Apple Silicon this path is slow (float32 on MPS,
  RTF ~4); prefer `qwen3-mlx` above.
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
