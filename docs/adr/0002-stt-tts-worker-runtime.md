# ADR 0002 — STT/TTS worker runtime: Python now, Rust optional later

- **Status:** Accepted
- **Date:** 2026-06-15
- **Deciders:** robot project
- **Relates to:** [ADR 0001](./0001-german-realtime-tts-engine.md) (which TTS engine),
  [Phase 1 — Service split](../../plans/phase-1-service-split.md)

## Context

`badlogic/pibot` (the "boring software" write-up) ships its STT and TTS as
**standalone Rust workers** the server talks to over stdin/stdout with a simple
binary/JSON framing protocol:

- **STT worker:** raw PCM in → Silero VAD on 32 ms chunks → Parakeet TDT 0.6B
  (int8 ONNX, ~50× real time on M1 Max) → interim transcripts every 250 ms (for
  barge-in / stop-words) and a final transcript after 800 ms of silence.
- **TTS worker:** the inverse — sentences in via stdin, raw PCM chunks out via
  stdout, so the server can start playback before the full answer is synthesized.

They went Rust to **escape the Python ML dependency stack** ("ship mostly
self-contained workers") and even vendored + patched a Rust MLX-C Qwen3-TTS engine
to match the Python MLX performance.

The question this ADR answers: **do we also need Rust, or does that just add
complexity over staying in Python?**

## Key insight

**The worker boundary and the worker's language are two separate decisions.**

Whether a stage runs in-process or as a separate worker is an *architecture*
decision (Phase 1 already promotes STT/LLM/TTS to services behind a network/stdio
seam — we need that regardless). The *implementation language* of a given worker
is an *optimization/packaging* decision we can make per worker, later, without
changing the orchestrator.

Because every component already sits behind the `STT` / `LLM` / `TTS` protocols
and is selected by `*_BACKEND`, a worker can be Python today and Rust tomorrow and
the orchestrator never notices. We get the worker either way.

## Decision

1. **Stay in Python for now.** Phase 0 is Python; faster-whisper, Piper, Kokoro,
   and the MLX path for Qwen3-TTS all have first-class Python. We do **not** adopt
   Rust as a requirement.
2. **Adopt the worker pattern (the architecture), not Rust (the language).**
   Phase 1 promotes STT/TTS to long-running workers behind the existing interfaces,
   communicating via a stable framing protocol (stdio or local socket). This is
   the part of pibot worth stealing.
3. **Treat a Rust worker as an opt-in, per-component optimization**, justified only
   by evidence (a benchmark row showing Python is the bottleneck on a target host).
   The framing protocol stays language-agnostic so a drop-in Rust replacement is
   possible without touching the orchestrator.
4. **Cross-platform beats single-host speed for the fleet.** pibot's Rust+MLX path
   is Apple-Silicon-specific; our fleet spans CUDA (RTX 2080) and CPU x86 (NUC/NAS),
   where the Python/ONNX/CUDA path is the portable one. Keep portability as the
   default tie-breaker.

## Rationale / answering "why not just stay in Python?"

- **Python is not more complex here — it's less.** It's already our stack, and the
  models we care about (Parakeet ONNX, Piper, Kokoro, Qwen3-TTS MLX) all run from
  Python today. Rewriting in Rust is *more* upfront complexity, not less.
- **The worker exists regardless.** We need a separate, long-running STT/TTS
  process for streaming, VAD state, and barge-in — that's true in Python too. The
  "similar communication" the question hints at (stdin PCM, stdout events/PCM) is
  exactly what we build in Phase 1, in Python.
- **pibot's Rust motivation was packaging + one specific perf win**, not that
  Python couldn't do it (the Python MLX version performed the same). Their cost:
  vendoring a Rust engine and patching MLX Metal kernels. That's a lot of yak-shaving
  we don't need to take on now.
- **Rust stays on the table** for a future edge/embedded or "ship a self-contained
  binary to the NUC/NAS" need — but only when a benchmark proves Python is the
  limiter on that host. Until then it's premature.

## Consequences

**Positive**
- No new language/toolchain; fastest path to the streaming worker we need.
- The language-agnostic framing protocol keeps a Rust swap cheap *if* we ever need it.
- Portable across the whole fleet (arm64 + CUDA + CPU x86) by default.

**Negative**
- We inherit Python's ML packaging pain (venv, heavy deps) on each host — mitigated
  by pinned `requirements.txt` and per-host install, not by rewriting in Rust.
- On Apple Silicon we may leave some performance on the table vs. pibot's patched
  Rust MLX engine; acceptable for single-/few-user prototyping. Revisit with a
  benchmark if Qwen3-TTS on the Mac becomes the bottleneck.

## Implementation notes (Phase 1)

- Define the worker framing protocol once (length-prefixed binary for PCM, JSON
  lines for events) and document it so a Rust worker could implement the same wire
  format later.
- Keep VAD state + utterance buffer **per user** in the STT worker from the start,
  so multi-user is a config change, not a rewrite (pibot's single-user → multi-user
  note).
