# Architecture Decision Records (ADR)

Short, dated records of significant technical decisions for the voice-assistant
robot. Each ADR captures the context, the decision, the options considered, and
the consequences — so we can see *why* a choice was made (and revisit it) later.

These expand the "Open questions / decisions to revisit" in
[../../AGENTS.md](../../AGENTS.md) into committed decisions.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](./0001-german-realtime-tts-engine.md) | TTS engine for German real-time interaction (Piper / Kokoro / **Qwen3-TTS**) | Accepted |
| [0002](./0002-stt-tts-worker-runtime.md) | STT/TTS worker runtime — Python now, Rust optional later | Accepted |

## Conventions

- Filename: `NNNN-short-slug.md`, numbered sequentially.
- Keep them short. State the decision up front, then the reasoning.
- Status: `Proposed` → `Accepted` → (`Superseded by NNNN` / `Deprecated`).
- A decision changes by writing a **new** ADR that supersedes the old one; don't
  rewrite history.
