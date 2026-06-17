# Plans

Phase-by-phase build plans for the voice-assistant robot. These expand the
roadmap in [../AGENTS.md](../AGENTS.md) into concrete, reviewable work.

Each plan is self-contained: goal, scope, deliverables, file layout, acceptance
criteria, and what we explicitly do **not** do yet. Plans build on the Phase-0
seam (`src/pipeline.py` + `*_BACKEND` factories) and never hardcode host/model
placement.

Committed technical decisions live in [../docs/adr/](../docs/adr/) (ADRs). Notably
[ADR 0001](../docs/adr/0001-german-realtime-tts-engine.md) settles the German TTS
engine tiering (Piper / Kokoro / **Qwen3-TTS**) and
[ADR 0002](../docs/adr/0002-stt-tts-worker-runtime.md) settles worker runtime
(**Python now, Rust optional later**).

| Plan | Phase | Status | Depends on |
|------|-------|--------|------------|
| [phase-1-service-split.md](./phase-1-service-split.md) | 1 — Service split | TODO | Phase 0 ✅ |
| [web-face.md](./web-face.md) | 1.5 — Robot face + control UI | TODO | Phase 1 event stream |
| [phase-2-fleet.md](./phase-2-fleet.md) | 2 — Fleet distribution | TODO | Phase 1 |
| [phase-3-home-assistant.md](./phase-3-home-assistant.md) | 3 — Home Assistant | TODO | Phase 1–2 |
| [phase-4-edge.md](./phase-4-edge.md) | 4 — Edge (ESP32 / robot) | TODO | Phase 1–3 |

## Cross-cutting principles (apply to every plan)

- **Local-first / offline-capable** — no cloud APIs by default.
- **Swappable components** — STT / LLM / TTS behind stable interfaces; swapping
  an implementation never touches the orchestrator.
- **Config over code for placement** — which host runs which service is env, not
  code.
- **Measure latency** — every new transport/stage logs per-stage timing into the
  same `benchmarks.json` shape.
- **Cross-arch aware** — arm64 (Mac, ESP32 host) + x86_64 (GPU box, NUC, NAS).

## What we steal from `badlogic/pibot` (and what we don't)

Stealing (architecture, model-agnostic):
- Per-stage worker/service behind an `onEvent` interface.
- **Sentence chunker** between LLM stream and TTS (first-sentence playback).
- **Barge-in / stop-word** detection on STT interims.
- **Explicit phase state machine** (`inactive → listening → hearing → thinking →
  tool → speaking → error`) streamed to the UI — drives the robot face and
  per-phase latency logging.
- `PI_PROVIDER` / `PI_MODEL`-style env overrides → maps onto Wake-on-LAN.
- Auto-download + pinned binaries for repeatable fleet rollout.

Not stealing:
- Single-host assumption (we want fleet placement).
- Hardcoded German system prompt + Spotify tools baked into the harness.
- No Wyoming/HA path (we need it).

Model note: pibot defaults to **Gemma 3 26B A4B MoE Q4** (~8–10 GB unified mem) —
great on the M1 Mac, **does not fit the 8 GB RTX 2080**. Our LLM targets stay
**Llama 3.1 8B / Qwen2.5 7B / Qwen3 8B @ Q4** for the gaming box, with Gemma 3
12B Q4 as an optional Mac-dev-parity model. STT: evaluate **Parakeet TDT 0.6B**
against faster-whisper. TTS: keep **Piper** as fleet default, **Kokoro** for
CPU-only quality, **Qwen3-TTS** as the Mac/GPU quality + native-streaming upgrade
(see [ADR 0001](../docs/adr/0001-german-realtime-tts-engine.md)).
