# Phase 1.5 — Robot face + control UI (web interface)

**Goal:** a browser-based **robot face** (eyes, eyebrows, a simple mouth) that
reacts live to the pipeline's phase, plus a **control bar** to switch the
environment preset and the active models at runtime. This is our debugging HUD,
our demo surface, and the direct ancestor of the face the real robot's
phone/screen will render.

> Does it make sense? Yes — for three reasons:
> 1. **Free debugging.** The phase state machine (Phase 1) already exists; the
>    face is just a visualization of it. You instantly *see* listening →
>    thinking → speaking → error without reading logs.
> 2. **One place to A/B environments + models.** Mirrors what `benchmarks.html`
>    already does for results, but live — switch Mac vs Gaming-PC preset, swap
>    Llama 3.1 8B vs Qwen3 8B, and watch latency + the face respond.
> 3. **It's the robot's actual face.** The robot is "a small robot with a
>    smartphone" (pibot's framing). The phone renders exactly this page. Building
>    it now means the edge face is done early.

## Scope

In:
- Static front-end (one HTML/CSS/JS bundle, no framework needed) served by the
  Phase-1 control server at `GET /`.
- **Face**: SVG (preferred — crisp, easy to animate) with:
  - Two **eyes** (pupils, blink, look-around).
  - Two **eyebrows** (raise / furrow / neutral — conveys thinking/error).
  - One **mouth** (idle line, talking animation, smile/neutral/concerned).
- **Phase-driven expressions** over the WS event stream:
  | Phase | Eyes | Eyebrows | Mouth |
  |-------|------|----------|-------|
  | `inactive` | half-closed | neutral | flat line |
  | `listening` | open, occasional blink | neutral | small neutral |
  | `hearing` | wide, focused | slightly raised | small open |
  | `thinking` | look up/side | one raised (quizzical) | closed, slight |
  | `speaking` | open, lively | neutral/expressive | **animated talking** |
  | `tool` | look down (busy) | focused | flat |
  | `error` | narrowed | furrowed | concerned curve |
- **Mouth animation while speaking**: drive from TTS audio amplitude (RMS of PCM
  frames) if available, else a simple oscillation gated by the `speaking` phase.
- **Control bar** (top of page):
  - Environment preset dropdown (Mac M1 / Gaming PC / NUC / TrueNAS) — sets the
    backend URLs + model defaults for that host via `POST /api/select`.
  - LLM model dropdown (populated from the selected env's allowed models).
  - STT + TTS backend dropdowns.
  - Live **latency cards** (STT / LLM first-token / first-audio / TTS / TOTAL),
    reusing the visual language of `benchmarks.html`.
  - Text **prompt box** (type instead of talk — works without a mic) + an Abort
    button.
- **Mic capture (optional, behind a toggle)**: stream mic PCM over WS to drive
  real STT. Start with push-to-talk button; continuous + barge-in is a stretch.

Out (later):
- Full continuous-listen + barge-in (needs echo cancellation; revisit with edge).
- 3D / photoreal face. We stay 2D-vector and charming.
- Multi-user face switching (single robot for now).

## Design

### Files

```
public/
  index.html        # face + control bar
  face.js           # SVG face rig + expression/animation state machine
  app.js            # WS client, control bar wiring, latency cards
  styles.css        # dark theme matching benchmarks.html
src/server/
  app.py            # serves public/, WS endpoint (from Phase 1)
  presets.py        # NEW: environment presets (host -> backend URLs + models)
```

### Environment presets (`src/server/presets.py`)

Presets are the "config over code for placement" principle made clickable. Each
preset is just a named bundle of the same env the CLI uses:

```python
PRESETS = {
  "mac-m1": {
    "label": "Mac Pro M1 (dev)",
    "llm": {"backend": "ollama", "url": "http://localhost:11434",
            "models": ["llama3.2:latest", "qwen2.5:7b", "gemma3:12b"]},
    "stt": {"backend": "faster-whisper", "models": ["base", "small"]},
    "tts": {"backend": "say"},
  },
  "gaming-pc": {
    "label": "Gaming PC (RTX 2080, 8GB)",
    "llm": {"backend": "http", "url": "http://gaming-pc:11434",
            "models": ["llama3.1:8b-q4", "qwen3:8b-q4"]},   # fits 8GB VRAM
    "stt": {"backend": "http", "url": "http://gaming-pc:9000"},
    "tts": {"backend": "piper"},
    "wake_on_lan": {"mac": "AA:BB:CC:DD:EE:FF"},             # used in Phase 2
  },
  "nuc": {...},      # always-on, HA host, smaller models
  "truenas": {...},  # light containers
}
```

Switching a preset in the UI calls `POST /api/select`, which reconfigures the
orchestrator's backends live (rebuild the STT/LLM/TTS via the factories). The
face's latency cards then reflect the new placement on the next turn — this is
the live version of the benchmark log.

### Face rig (`public/face.js`)

- Build the face once as SVG: `<g>` groups for `leftEye`, `rightEye`,
  `leftBrow`, `rightBrow`, `mouth`.
- An **expression table** maps phase → target attributes (pupil offset, brow
  angle, mouth path). Tween between current and target with `requestAnimationFrame`
  for smooth transitions (no hard cuts).
- **Idle micro-behaviors** so it feels alive: random blink every 3–6 s, subtle
  pupil drift, occasional brow twitch. Pause idle behaviors during `speaking`.
- **Talking mouth**: a small set of mouth shapes (closed / mid / open) cycled by
  amplitude buckets, or a sine pulse if no amplitude data.

### WS event → face mapping (`public/app.js`)

```js
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "phase")          face.setPhase(msg.phase);
  if (msg.type === "assistant_delta") subtitle.append(msg.text);   // optional caption
  if (msg.type === "tts_audio")       face.pulseMouth(rms(msg.pcm));
  if (msg.type === "latency")         cards.update(msg);
  if (msg.type === "error")           face.setPhase("error");
};
```

## Deliverables

1. `public/index.html`, `face.js`, `app.js`, `styles.css` — working face that
   reacts to phases over WS.
2. `src/server/presets.py` + `/api/config`, `/api/select` endpoints.
3. Control bar: env preset + model/STT/TTS dropdowns, latency cards, text prompt,
   abort.
4. Talking-mouth animation driven by `speaking` phase (amplitude if PCM is
   streamed, oscillation otherwise).
5. Optional push-to-talk mic capture toggle (stretch).
6. README section: "Open the face at http://localhost:8010".

## Acceptance criteria

- Open `http://localhost:8010`: an idle face blinks and drifts.
- Type a prompt → face goes `thinking` (brow raises, eyes look up) → `speaking`
  (mouth animates) → back to `listening`. No console errors.
- Latency cards update each turn and match what `tools/smoke.py` would record.
- Switching the env preset dropdown changes which Ollama endpoint/model the next
  turn uses (verify in server logs), with no code edit.
- Triggering an error (e.g. unreachable LLM URL) shows the `error` face.
- Works offline from the local server (no CDN / external fonts).

## Sequencing note

This depends on the **Phase-1 event stream + control server**. Build order:
1. Phase 1 orchestrator emits phases/events over WS.
2. Static face that subscribes and animates (no controls yet).
3. Control bar + presets.
4. Mouth-from-amplitude + optional mic.

A throwaway **v0** is fine before Phase 1 is done: a static `face.html` with
buttons that fake-set each phase, just to design the rig and expressions. Keep it
in `public/` and wire it to real events once the WS exists.

## Risks / decisions

- **SVG vs Canvas**: SVG first (declarative, easy to tween, accessible). Move to
  Canvas/WebGL only if we want particle/shader effects on the robot later.
- **Amplitude data**: requires TTS to stream PCM (Phase 1 streaming TTS). If we
  only have file-based `say`, gate the mouth on phase + duration instead.
- **Preset hot-swap**: rebuilding backends mid-session must not wedge an
  in-flight turn — abort current turn before re-selecting.
