# ADR 0001 — TTS engine for German real-time robot interaction

- **Status:** Accepted
- **Date:** 2026-06-15
- **Deciders:** robot project
- **Phase context:** Phase 0 (host prototype) → Phase 1 (service split) → Phase 4 (edge/robot)
- **Supersedes / relates to:** the "TTS: keep Piper as fleet default, Qwen3-TTS
  as the Mac/GPU upgrade path" note in [../../plans/README.md](../../plans/README.md)

## Context

The voice loop is `mic → STT → LLM → TTS → speaker` (see [AGENTS.md](../../AGENTS.md)).
TTS is the **last** stage before the user hears anything, so its latency adds
directly to perceived response time, and its quality is what makes the robot feel
alive or robotic-in-a-bad-way.

Constraints specific to us:

- **German is a first-class language**, not an afterthought. Most open TTS models
  optimize for English + CJK and sound wrong in German.
- **Local-first / offline.** No cloud TTS (ElevenLabs etc.) in the default path —
  cost, privacy (kids' data), and offline operation all rule it out.
- **Cross-arch fleet.** arm64 Mac (Metal/MPS), x86_64 NUC/NAS (CPU/iGPU), x86_64
  gaming PC (RTX 2080, 8 GB CUDA), and eventually an ESP32-S3 edge front-end.
- **Streaming matters more than raw speed.** For a conversational loop we want
  *time-to-first-audio* low; we feed the TTS one sentence at a time as the LLM
  streams, so audio starts before the full answer exists (sentence chunker).
- **Swappable components.** Whatever we pick sits behind the existing `TTS`
  protocol (`src/tts/__init__.py`) and is selected by `TTS_BACKEND` — swapping it
  must not ripple through the orchestrator.

### What we already have

| Backend | `TTS_BACKEND` | Notes |
|---------|---------------|-------|
| macOS `say` | `say` | Phase-0 zero-dependency default on the Mac. Not portable. |
| Piper (Thorsten DE) | `piper` | Cross-platform fleet default. Fast on CPU, decent German. |
| Kokoro-82M German "Martin" (ONNX) | `kokoro` | Tiny (82M), great on CPU, good German prosody, limited streaming. |
| robot effect | `TTS_EFFECT=robot` | Engine-agnostic DSP layer on top of any backend. |

The open question this ADR closes: **which engine is the quality/expressiveness
upgrade path for German**, and under what hardware does it win.

## Decision

**Adopt a tiered TTS strategy, with Qwen3-TTS as the new high-quality variant:**

1. **Piper stays the cross-arch fleet default** (`TTS_BACKEND=piper`).
   Always-available, fast on CPU, runs on every host including the NUC/NAS.
2. **Kokoro-82M German "Martin" is the CPU / resource-constrained pick**
   (`TTS_BACKEND=kokoro`). Best fit for the NUC, N100 NAS, or any GPU-less host
   that wants better-than-Piper prosody without GPU cost.
3. **Qwen3-TTS (0.6B / 1.7B) is the quality + native-streaming upgrade variant**
   (`TTS_BACKEND=qwen3` — to be implemented), used where a capable GPU or Apple
   Silicon with enough unified memory is available (Mac M1, gaming-PC RTX). It is
   the engine we reach for when naturalness/expressiveness is the priority and the
   host can afford it.

Selection remains **config, not code**: `TTS_BACKEND` + per-host `.env` decides
which engine runs where. No host is hardcoded.

## Options considered

### Qwen3-TTS (0.6B / 1.7B)

- **Upstream:** <https://github.com/QwenLM/Qwen3-TTS>
- German is first-class; strong naturalness, prosody, expressiveness, and voice
  cloning (a 30 s reference is enough for a custom robot voice).
- **Native streaming** — audio can start before the sentence is fully synthesized,
  which is exactly what our sentence-chunker pipeline wants.
- Costs: comparatively **large** model, GPU-hungry. On Apple Silicon the practical
  route is the **MLX** build with a **6-bit quantized 1.7B** model — reported
  ~2× real time on M1 Max, ~4× on M5 Max. On the RTX 2080 (8 GB) the **0.6B** is
  the realistic fit alongside STT/LLM VRAM pressure.
- Weak on CPU-only hosts (NUC/NAS) — not the right pick there.

### Kokoro-82M-ONNX-German-Martin

- **Upstream:** <https://huggingface.co/Godelaune/Kokoro-82M-ONNX-German-Martin>
- Tiny (82M), ONNX, **excellent CPU performance and memory footprint**, low
  latency without a GPU. Good German for its size.
- Prosody/naturalness below Qwen3-TTS; streaming support limited. Already wired in
  (`src/tts/kokoro_tts.py`).

### Piper (Thorsten DE) — incumbent default

- Fast, local, runs everywhere; the safe portable baseline. Quality below both
  Kokoro and Qwen3 for expressive/natural German. Stays as the default and fallback.

### Cloud (ElevenLabs etc.) — rejected

- Best quality + easy German, but **violates local-first**: cost, sends data off
  device, needs internet. Useful only as a private reference target for "what
  good sounds like." Not in the default pipeline.

### Comparison

| Aspect | Qwen3-TTS | Kokoro DE Martin | Piper DE |
|--------|-----------|------------------|----------|
| German pronunciation | ★★★★★ | ★★★★ | ★★★★ |
| Naturalness | ★★★★★ | ★★★ | ★★★ |
| Streaming (time-to-first-audio) | ★★★★★ native | ★★★ limited | ★★★ |
| Real-time conversation | ★★★★★ | ★★★★ | ★★★★ |
| CPU performance | ★★ | ★★★★★ | ★★★★★ |
| GPU performance | ★★★★★ | ★★★★ | ★★★ |
| Memory footprint | large (0.6B/1.7B) | tiny (82M) | small |
| Expressiveness / voice cloning | ★★★★★ (clone) | ★★★ | ★★ |
| Cross-arch / CPU-only hosts | weak | strong | strongest |

## Consequences

**Positive**
- A clear per-host rule: **Piper everywhere, Kokoro for CPU-only quality, Qwen3-TTS
  for GPU/Apple-Silicon quality** — driven by `.env`, matching fleet placement.
- Native streaming from Qwen3-TTS pairs with the planned sentence chunker for low
  time-to-first-audio on capable hosts.
- Voice cloning gives us a consistent custom robot voice from one short reference.

**Negative / costs**
- Qwen3-TTS adds a heavy dependency and VRAM/unified-memory pressure; it won't run
  well on the NUC/NAS or the ESP32 edge — those stay on Piper/Kokoro.
- A new backend (`src/tts/qwen3_tts.py` + `TTS_BACKEND=qwen3`) must be implemented
  and benchmarked (`tools/smoke.py --record`) per host before it becomes a default.
- Engine vs. **inference runtime** (Python MLX vs. Rust MLX-C) is a separate
  decision — see ADR 0002.

## Implementation notes (deferred to Phase 1)

- Add `qwen3` to the `TTS` factory in `src/tts/__init__.py` and a `Qwen3TTS`
  backend behind the existing protocol; expose `QWEN3_*` config in `src/config.py`.
- Prefer the **streaming** TTS interface landing in Phase 1 (push sentences → get
  PCM chunks) so Qwen3-TTS's native streaming is actually used.
- Record a benchmark row per host (Mac M1, gaming PC) so the quality/latency
  trade-off is visible in `benchmarks.json` / `benchmarks.html`.
