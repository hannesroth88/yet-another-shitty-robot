# AGENTS.md

Project context for the voice-assistant robot prototype. Read this first.

## What we're building

A local-first **voice assistant pipeline**:

```
mic ──► STT ──► LLM ──► TTS ──► speaker
              (+ Home Assistant for actions/automations)
```

Everything runs **on-prem / on-device** — no cloud APIs by default. The same
pipeline must be portable across a small fleet of machines and, eventually, an
embedded device + a physical robot.

### Roadmap (rough)

1. **Phase 0 — Host prototype (DONE ✅):** STT→LLM→TTS working end-to-end on the
   Mac (M1) as a push-to-talk loop with per-stage latency. See `README.md`,
   `src/`, run `python -m src.main` or `python -m tools.smoke`. ~1.7–2.1s/turn
   (faster-whisper base + Ollama llama3.2 + macOS `say`).
2. **Phase 1 — Service split:** Break STT, LLM, TTS into swappable services
   behind a stable interface so any component can run on a different host.
3. **Phase 2 — Fleet:** Distribute services across the machines below (e.g. LLM
   on the Gaming PC GPU, orchestration on the NUC).
4. **Phase 3 — Home Assistant:** Wire the assistant into HA (Assist pipeline /
   intents) so voice can trigger automations.
5. **Phase 4 — Edge:** Run the wake-word + audio front-end on an **ESP32-S3-Box**,
   streaming to the heavier services; final target is the **robot**.

## Design principles

- **Local-first / offline-capable.** Prefer models that run without internet.
- **Swappable components.** STT, LLM, TTS each sit behind a clear interface
  (HTTP/gRPC/socket). Picking whisper.cpp vs faster-whisper, or Piper vs
  another TTS, must not ripple through the rest of the system.
- **Config over code for placement.** Which host runs which service is config
  (endpoints/env), not hardcoded. Same code runs on Mac, x86, GPU box, ESP32 host.
- **Cross-arch aware.** Code runs on arm64 (Mac, ESP32 host stack) and x86_64
  (Gaming PC, NUC, TrueNAS). Don't bake in Mac-only or CUDA-only assumptions.
- **Measure latency.** Track per-stage latency (STT, LLM first-token, TTS) — it's
  the key UX metric for a voice loop and the reason to move work between hosts.
- **Wake-on-LAN friendly.** The GPU box should sleep and be woken on demand, not
  run 24/7.

## Hardware fleet

| Host | Role | CPU | RAM | GPU / Accel | Storage | Arch | Notes |
|------|------|-----|-----|-------------|---------|------|-------|
| **Mac Pro M1** | Dev / Phase-0 host | Apple M1 Pro (16") | 32 GB | M1 GPU + ANE (Metal) | — | arm64 | Current dev machine. Use Metal / MPS. |
| **Gaming PC** | On-demand LLM/GPU | Ryzen 5 5600X | 32 GB DDR4-3600 | **RTX 2080, 8 GB VRAM (CUDA)** | 2 TB NVMe | x86_64 | Wake-on-LAN target. Runs Ollama/LLM only when needed. |
| **Intel NUC 10** | Home Assistant + orchestrator | Core i5-10210U | 32 GB DDR4-2666 | iGPU only | 1 TB NVMe | x86_64 | Runs Home Assistant. Always-on, low power. Good for STT/TTS + glue. |
| **TrueNAS server** | Storage / always-on services | ASRock N100 | 32 GB DDR4-3200 | iGPU only (low TDP) | 12 TB HDD + 500 GB NVMe | x86_64 | NAS, model storage, light containers. PoE switch for cameras/devices. |
| **ESP32-S3-Box** | Edge audio front-end | ESP32-S3 | ~8 MB PSRAM | — | flash | xtensa | Wake word + mic/speaker; streams to services. Phase 4. |
| **Robot** | Final target | TBD | — | — | — | TBD | Hosts/embeds the front-end; talks to fleet over network. |

### VRAM reality check (Gaming PC, 8 GB)

8 GB VRAM is the main constraint for local LLM. Plan around quantized models:
- ~7–8B params at Q4 (e.g. Llama 3.1 8B, Qwen2.5 7B) fit with modest context.
- Keep context windows reasonable; offload to CPU/RAM if needed (slower).
- STT (whisper) and TTS (Piper) are light and can share the GPU or run on CPU.

## Candidate stack (to evaluate, not final)

- **STT:** `whisper.cpp` (Metal on Mac, CUDA/CPU on x86) or `faster-whisper`.
  ESP32 side: on-device wake word (e.g. microWakeWord) + stream audio out.
- **LLM:** **Ollama** as the serving layer (simple API, model management, runs on
  Mac/x86/CUDA). Models: Llama 3.1 8B / Qwen2.5 7B class at Q4 for the 8 GB GPU.
- **TTS:** **Piper** (fast, local, good quality, runs everywhere) as default.
- **Orchestration:** a thin pipeline service that wires STT→LLM→TTS and exposes
  one entry point; component endpoints are configurable.
- **Home Assistant:** integrate via HA's **Assist** pipeline / Wyoming protocol
  (Wyoming is the natural fit — whisper, piper, and wake word all speak it and HA
  already supports it).

### Ollama vs Open WebUI

- **Ollama** = the model server / runtime. This is what the pipeline talks to
  (HTTP API on `:11434`). Required.
- **Open WebUI** = an optional chat **frontend** on top of Ollama for humans to
  poke at models in a browser. Nice for manual testing/model comparison, **not**
  part of the automated voice loop. Treat it as a dev convenience, optional.

## Wake-on-LAN (Gaming PC)

The GPU box stays asleep and is woken when an LLM request needs it:

1. Enable WoL in BIOS + NIC; record the MAC.
2. Orchestrator sends a magic packet, waits for the host/Ollama port to come up,
   then routes the LLM call. Falls back to a smaller local model (Mac/NUC) if the
   box doesn't wake in time.
3. Idle-timeout suspends the box again.

Keep this logic in the orchestrator behind a "LLM provider" abstraction so the
rest of the pipeline doesn't know or care whether the model is local or remote.

## Repo / dev conventions

- **OS dev host:** macOS 15 (arm64). Tools present: `python3` (3.9 — prefer a
  venv with newer Python), `node` v25, `ffmpeg`, `ollama`, `brew`.
- **Audio:** use `ffmpeg` for capture/conversion during prototyping.
- **Secrets/endpoints:** host placement and model choices live in config/env
  (e.g. `.env`), never hardcoded. Don't commit secrets.
- **Keep components behind interfaces** so we can bench the same pipeline on
  different hosts and swap implementations.
- **Latency logging** is a first-class feature, not an afterthought.

## Latency benchmark log

**What it is:** a living record of pipeline latency per hardware environment,
so we can compare hosts and track improvement over time. This is the concrete
expression of the "Measure latency" design principle and the data that drives
fleet-placement decisions (e.g. is the Gaming PC's GPU worth waking vs running
the LLM locally on the NUC/Mac?).

**What it's for:**
- Compare environments side by side (Mac M1 vs Gaming PC vs NUC vs TrueNAS).
- See where the time goes per turn (STT vs LLM vs TTS) so we optimize the right
  stage on each host.
- Weigh model/quant trade-offs (e.g. 3B Q4 vs 7B Q4 on 8 GB VRAM) against latency.
- Expose warmup/cold-start cost — relevant to the Wake-on-LAN strategy.

**How it works:**
- `benchmarks.json` is the source of truth (one record per run).
- `tools/bench_report.py` regenerates a self-contained `benchmarks.html`
  (sortable, highlights fastest/slowest per column, filter by environment,
  best-total cards). Works offline from `file://`.
- `tools/smoke.py "prompt" --record "<Environment>"` measures a run and appends
  it to the JSON + regenerates the HTML. Set `llm_quant` by hand afterward
  (Ollama doesn't report it over the API).

**Columns:** Date · Environment · Accel · STT Config · STT ms · LLM Model ·
Quant · LLM 1st-token ms · LLM ms · TTS Config · TTS ms · TOTAL ms · Notes.
TOTAL is real end-to-end (STT + LLM + TTS); LLM first-token is informational
(a subset of LLM time, not added into the total).

**Workflow:** when testing a new host or swapping a component, run `--record`
for that environment and commit the updated `benchmarks.json` + `benchmarks.html`.
Keep trying to lower the per-environment best total.

## Open questions / decisions to revisit

- Final STT engine (whisper.cpp vs faster-whisper) per host.
- Transport between services (plain HTTP vs Wyoming vs gRPC). Leaning Wyoming for
  HA compatibility.
- Wake-word engine on ESP32-S3-Box and how much runs on-device vs streamed.
- Robot compute: does the robot host the front-end only, or also TTS/STT?
