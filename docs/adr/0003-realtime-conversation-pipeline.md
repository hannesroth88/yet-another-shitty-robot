# ADR 0003 — Real-time conversation pipeline (continuous VAD + streaming PCM + client barge-in)

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** robot project
- **Relates to:** [ADR 0001](./0001-german-realtime-tts-engine.md) (TTS engine),
  [ADR 0002](./0002-stt-tts-worker-runtime.md) (worker runtime),
  [Phase 1 — Service split](../../plans/phase-1-service-split.md)

## Context

Phase 0/1 used **push-to-talk**: the phone records a whole `webm/opus` blob on
button release, the server transcribes it, runs the turn, synthesizes one WAV
per sentence, and the phone plays each WAV via an `<audio src="/audio?path=…">`
element. That works but feels like a walkie-talkie, not a conversation:

- You must press a button for every turn.
- TTS audio reaches the phone as **complete WAV files**, so playback of a
  sentence cannot start until that sentence is fully synthesized, and there is
  an audible gap between sentences (the next sentence's synth time).
- There is no way to interrupt the robot mid-sentence (no barge-in).

`badlogic/pibot` ("boring software" write-up) demonstrates a smooth
speech-to-speech UX on the same class of hardware (M1 Max). We read its source
to extract the mechanisms (see `src/server/index.ts`, `src/client/barge-in.ts`,
`src/client/tools/speech.ts`, `src/server/tts.ts`).

## How pibot does it (the parts worth stealing)

1. **Continuous mic + server-side VAD.** The phone continuously streams mic PCM.
   A worker runs Silero VAD on 32 ms chunks, buffers an utterance on speech, runs
   the ASR (Parakeet) on the last ~4000 ms every 250 ms for an **interim**
   transcript, and emits a **final** transcript after ~800 ms of silence.
2. **Interim transcripts drive stop-words / barge-in.** Because interims arrive
   while the user is still talking, the server can detect "stop"/"halt" and abort
   the robot mid-sentence.
3. **Streaming PCM TTS to the phone.** As the LLM streams tokens, a sentence
   chunker pushes complete sentences to a persistent TTS worker; the worker
   streams **raw PCM chunks** back; the server forwards them to the phone over the
   WebSocket as binary frames; the phone schedules them gaplessly with the **Web
   Audio API** (`createBuffer` + scheduled `BufferSource` + a `nextPlayTime`
   accumulator). Audio starts before the full answer is synthesized, and there is
   no inter-sentence gap.
4. **Barge-in detection runs on the client.** WebRTC echo cancellation was not
   clean enough for ASR, so the phone keeps a ring buffer of playback audio as a
   reference and, per mic frame, correlates the mic against that reference at
   delays of 20–420 ms to estimate how much mic energy is just speaker bleed. It
   fires barge-in only when mic RMS **and** the unexplained residual are above
   thresholds for several consecutive frames, then flushes a mic **preroll** so
   the server gets the interrupting utterance from its true start.

## Decision

Adopt the pibot-style real-time pipeline, implemented in **Python on the server**
and **vanilla JS on the phone**, reusing our existing seams (phase machine, WS
broadcast hub, Parakeet STT, Qwen3-MLX TTS, sentence chunker). Three phases:

- **Phase A — continuous mic + server VAD.** The phone streams 16 kHz PCM16
  frames over the WS. A per-client `StreamingSTT` runs a VAD gate, buffers the
  utterance, emits `speech_start` / `interim` / `final`, and reuses the existing
  `STT.transcribe` for ASR. Turns are driven by `final`, not a button.
- **Phase B — streaming PCM TTS to the phone.** The orchestrator gains an
  `AudioSink` (start/pcm/done). TTS backends expose `iter_pcm(sentence)`; for
  non-streaming engines (Piper/say/Kokoro) this yields the synthesized WAV's PCM
  in small frames (still gapless + cross-sentence streaming), and Qwen3-MLX
  yields real generator chunks. The server forwards PCM as **binary WS frames**;
  the phone plays them with Web Audio scheduling (replaces `<audio src>`).
- **Phase C — barge-in.** Port pibot's client `BargeInDetector` (playback
  reference ring + correlation residual + preroll). On trigger the phone stops
  local playback, sends `barge_in`, and streams the live mic; the server cancels
  TTS, aborts the turn (a `threading.Event` cooperative cancel in the
  orchestrator), and returns to `hearing`. Stop-words in interims also abort.

### Wire protocol (phone ⇄ server over the existing `/ws`)

- **Phone → server**
  - `{"type":"audio_start","sampleRate":16000,"format":"pcm16le"}` then **binary**
    frames of raw mono PCM16LE @ 16 kHz (continuous mic).
  - `{"type":"audio_stop"}` — pause the mic stream.
  - `{"type":"barge_in"}` — user talked over the robot; followed by live mic PCM.
  - `{"type":"abort"}` / `{"type":"prompt","text":…}` / `{"type":"ping"}`.
- **Server → phone**
  - `{"type":"phase"|"heard_text"|"assistant_delta"|"assistant_end"|"latency"|…}`
    (unchanged JSON events) plus `{"type":"interim","text":…}`.
  - `{"type":"tts_start","sample_rate":N}` → one or more **binary** PCM16LE frames
    → `{"type":"tts_done"}`. Binary frames from the server are always TTS PCM.

The binary-PCM-over-WS + Web-Audio-scheduling choice is the direct answer to the
earlier design question "will streaming work when the sound plays on the phone?"
— yes, this is exactly the transport that makes it work.

## Consequences

**Positive**
- Hands-free, interruptible conversation; first audio is earlier and gap-free.
- Subsumes the two TTS improvements we had queued (synth-ahead **and**
  intra-sentence streaming) — they fall out of the worker + PCM scheduling.
- Stays within our interfaces: backends and the phase machine are reused; the
  protocol is language-agnostic so a Rust STT/TTS worker (ADR 0002) can drop in.

**Negative / risks**
- A VAD dependency. We ship an **energy-based VAD by default** (zero new deps) and
  keep **Silero VAD opt-in** (`VAD_BACKEND=silero`) for robustness.
- Barge-in tuning (thresholds, delays) is acoustic and device-specific; defaults
  are ported from pibot and will need on-device tuning. Push-to-talk and typed
  prompts are kept as fallbacks.
- Re-transcribing interims every 250 ms costs ASR compute; bounded by the 4000 ms
  window and gated by a minimum-audio threshold.

## Implementation notes

- Keep VAD state + utterance buffer **per client** from the start (multi-user is
  then a config change, per ADR 0002).
- Cooperative cancellation only (a `threading.Event` checked in the LLM/TTS
  loops); no thread killing.
- `CONVERSATION_MODE` config gates the new path; the legacy blob + `/audio` WAV
  path remains for fallback and the local CLI (`main.py`).
