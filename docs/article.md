heard about mario zechners Robot build https://mariozechner.at/posts/2026-05-30-shitty-robot/
have also 2 kids and watned to have a fun project and of course learn more about intersection of robot and AI also learn about model inteference quantization and optimize on my hardware setup.
For robot live interaction its super important to have that human interaction. Listen when needed, answer when human is done speaking and so on.

Ordered 2 octobots, wnated to use my old Pixel smartphone. esp32 and a motor driver for the one motor in the octobot.

## Text-to-speech: making it fast and making it sound like *my* robot

The voice was the first thing that really annoyed me. I had Qwen3-TTS running,
but two problems: it took ~2 minutes before I heard anything, and it sounded
bad.

Turned out I was on the PyTorch/transformers path of Qwen3-TTS. On my M1 that
runs float32 on the Metal/MPS backend at a real-time factor of about 4 - meaning
4 seconds of compute for every 1 second of audio. I measured it: a 10s clip took
40s, plus ~10s to load the model every single run. On top of that I was running
the canned "ryan" speaker through a homemade robot DSP effect (bitcrush, ring
mod, the whole "tiny robot" preset), which is what made it sound rough.

Then I looked at how Mario actually did it in pibot. He does *not* use the slow
PyTorch path and he does *not* fake the robot sound with DSP. Instead he uses the
Qwen3-TTS **Base** model, which can clone a voice in-context (ICL) from a short
reference clip plus its transcript, and he runs it through native, quantized
runtimes (MLX 6-bit on Apple Silicon, or a C++/Metal GGUF build). His reference
voice is a clip he generated on ElevenLabs.

So I did the same thing, the Python/MLX variant:

- Made my own reference voice on elevenlabs.io (a friendly small teaching robot),
  exported the mp3 + the exact transcript.
- Converted it to a clean 24 kHz mono wav with ffmpeg.
- Installed `mlx-audio` (Apple-native, runs on Metal).
- Loaded the 6-bit Base model `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` and
  voice-cloned my reference clip with `model.generate(text=..., ref_audio=...,
  ref_text=...)`.
- Dropped the robot DSP entirely - the voice now comes straight from my
  reference sample.

The difference: real-time factor went from ~4 down to **under 1 on longer text**
(0.85), roughly 4-5x faster, and it actually sounds like the voice I picked
instead of a bitcrushed "ryan". Soundcheck successful.

In the repo this is a new backend (`src/tts/qwen3_mlx_tts.py`) selected with
`TTS_BACKEND=qwen3-mlx`; the reference clip + transcript live in `voices/sample/`
and are wired through config (`QWEN3_REF_AUDIO` / `QWEN3_REF_TEXT_FILE`). The old
PyTorch path is still there for x86/CUDA fleet hosts.

### Two things that confused me

**"Why does it download something every time?"** Every run prints
`Fetching 12 files...` and a download bar. It's not actually re-downloading - the
weights are cached under `~/.cache/huggingface`. That line is Hugging Face
*resolving/verifying* the snapshot against the cache (notice `84449 it/s` and
`Download complete: 0.00B` - nothing is transferred). To skip the network check
entirely and force fully offline use, set `HF_HUB_OFFLINE=1` (and
`TRANSFORMERS_OFFLINE=1`).

**"The first short reply ("Hallo") I couldn't really hear."** Two reasons.
First, `tools/smoke.py` is a one-shot process: it loads the whole TTS model from
scratch every call (~7s) before it can speak, which is why a tiny "Hallo!" shows
`tts 8873ms` - that's mostly model load, not synthesis. Second, very short
utterances have a high fixed overhead (RTF ~1.3 for one word vs 0.85 for a full
sentence) and the clip is so short it's easy to miss.

Both of these go away with the client/server split (Phase 1): the TTS service
loads the model **once** at startup and stays resident, so per-turn latency is
just generation, and the persistent worker can stream audio chunks so you hear
the first words before the whole clip is done. So yes - this is a one-shot-CLI
artifact, not a real pipeline cost.

## Improvement: stream Qwen3-TTS audio instead of buffering

**Observed:** the 2026-06-17 benchmark run with a long German prompt (~70 words)
recorded **17,162 ms TTS** and a **20,320 ms total**. The model itself is fast
(RTF < 1 on Apple Silicon), but `qwen3_mlx_tts.py` does:

```python
results = list(model.generate(...))   # ← collects ALL chunks first
samples, sr = _collect_audio(results) # ← then writes one big WAV
```

That `list()` call is the problem — it blocks until the full audio is ready
before any sound plays. For a 70-word sentence that means ~17 s of silence
followed by the whole clip at once.

**Why Qwen3-TTS is especially good for robots:** `model.generate()` is already a
generator that yields audio chunks as they are synthesized. Streaming means the
pipeline can hand the first chunk (~100–200 ms of audio) to the speaker while
the rest is still being generated. The robot starts speaking almost immediately —
which is exactly the low-latency feel that makes a voice assistant feel alive.

**What needs to change:**

1. **`Qwen3MlxTTS`** — add a `synthesize_stream()` generator that yields
   `(samples: np.ndarray, sr: int)` chunks directly from `model.generate()`,
   instead of collecting them all with `list()`.
2. **`Pipeline.speak()`** — detect if the TTS backend supports streaming and, if
   so, start piping chunks to the audio output (sounddevice / pyaudio) while
   remaining chunks arrive.
3. **Benchmark** — add a new row with `tts_config = "qwen3-mlx (streaming)"` to
   compare first-audio latency vs the buffered 17 s baseline.

**Expected improvement:** first audio in **~1–2 s** (one synthesized chunk)
instead of 17 s, with the rest of the sentence following seamlessly. TOTAL
could drop from ~20 s to ~5 s for the same long German prompt.

**Tracked as:** Phase 1 / TTS streaming — prerequisite for the robot feeling
responsive. Implement alongside the client/server split so the TTS service stays
resident and streams over the wire.

## Phase 1 — the service split (done)

The Phase-0 loop was synchronous: record → transcribe → wait for the *whole* LLM
reply → synthesize the *whole* reply → play. Phase 1 rebuilds that into an
event-driven orchestrator with a transport seam, and — the big UX lever — it
overlaps the LLM and the TTS.

**Sentence chunker.** `src/text/sentence_chunker.py` turns the LLM token stream
into complete sentences as soon as they form (`push(delta) -> [sentences]`,
`flush() -> tail`). It's careful about German abbreviations ("z. B.") and decimals
("3.14") so it doesn't cut mid-token. 8 unit tests cover it.

**Streaming TTS.** `src/tts/streaming.py` defines a `StreamingTTS` protocol and a
`SentenceStreamingTTS` adapter that wraps any non-streaming engine (say, Piper,
Kokoro): it synthesizes one wav per sentence and yields each as it's ready.
Native-streaming engines (Qwen3) can plug straight in later.

**Event-driven orchestrator.** `src/orchestrator.py` replaces the blocking
`Pipeline`. It runs a phase state machine (`inactive → listening → thinking →
speaking`) and emits events (`phase`, `assistant_delta`, `tts_audio`, `latency`,
`error`) to any number of subscribers. Internally the LLM streams on its own
thread while a consumer synthesizes + plays each sentence as it arrives — so
**LLM timing stays pure** (honest benchmark numbers) and audio starts early.

**Control server.** `src/server/app.py` is a stdlib-only HTTP + WebSocket server
(hand-rolled WS framing, no aiohttp/websockets dependency). The CLI is one client
of the orchestrator; a tiny web face (`src/server/static/index.html`) is another.
Send `{type:"prompt"}` over the WS, receive the event stream live.

**Network backends (the Phase 2 seam, landed early).** `src/llm/http_llm.py`
(Ollama or OpenAI-compatible) and `src/stt/http_stt.py` (multipart POST) let any
stage move to another host by flipping env — no orchestrator change. Proven
locally: `LLM_BACKEND=http LLM_HTTP_URL=…` against Ollama worked with zero code
change.

### The headline metric: `first_audio_ms`

New benchmark column. With Piper streaming a 3-sentence German reply:

| metric | value |
|--------|-------|
| STT | 451 ms |
| LLM (full) | 2297 ms |
| TTS (full, 3 sentences) | 6841 ms |
| **first audio** | **3332 ms** |
| TOTAL (full turn) | 9589 ms |

Without streaming the user waits for the whole turn — ~9.6 s — before hearing a
word. With streaming the first sentence plays at **~3.3 s**, a **~60 % cut in
perceived latency**. The win grows with reply length (more sentences = earlier
first audio relative to the total). The strict `first_audio < llm` holds for
multi-sentence replies whose opening sentence is short; either way the
perceived-latency-vs-total win is the real story.

## Phase 2 — fleet distribution (foundation laid, hardware pending)

Phase 2 makes placement real: LLM on the on-demand Gaming-PC GPU, always-on
services on the NUC, with graceful fallback. The code that doesn't need the other
hosts is in and tested on the Mac; the actual cross-host benchmark runs wait on
bringing the NUC + GPU box online.

**Routed LLM + Wake-on-LAN.** `src/llm/routed_llm.py` composes a remote-GPU
primary with a local fallback behind the same `stream()` interface
(`LLM_BACKEND=routed`). On a request it checks the primary port; if closed it
sends a WoL magic packet (`src/net/wol.py`) and polls `/api/tags` until the box
is up (logged as a COLD run); if it doesn't wake in time it downgrades to the
local small model and notes the downgrade. An idle timer suspends the GPU box
over SSH. Proven locally: with an unreachable primary the turn completed on the
local fallback, emitting `downgrade -> local fallback llama3.2` — **orchestrator
unchanged**.

**Standalone services.** `services/stt_server` and `services/tts_server` run the
STT/TTS backends as HTTP services so they can live on the NUC. The STT service
was validated end-to-end on the Mac: `http_stt` → `stt_server` → faster-whisper
round-tripped "Hallo, das ist ein Test." correctly. (Multipart parsing is
hand-rolled to avoid the deprecated `cgi` module — future-proof for Python 3.13.)

**Presets.** `src/presets.py` declares 4 host layouts (mac-local, nuc-gpu,
nuc-only, distributed-stt-tts); `python -m src.presets <key>` prints the matching
`.env`. Real MACs/IPs get filled in as hosts come online.

**Still pending (needs the hardware):** actually waking the Gaming PC, and the
side-by-side fleet benchmark rows (Mac-only vs NUC+GPU warm/cold vs NUC-only).
The WoL packet builder is unit-tested; the wake itself waits on a configured MAC
and a box that's plugged in.

## The phone is the face, the ESP32 is the body

A design fork worth recording. The plan (Phase 4) imagined an **ESP32-S3-Box**
as the audio front-end. But the board I actually have is a bare
**ESP32-S3-DevKitC-1 (N16R8)** — WiFi, 36 GPIO, one WS2812 RGB LED on GPIO48, and
**no mic, speaker, camera, or display**. Meanwhile the robot is "a small robot
with a smartphone" (pibot's framing) and I have a **Pixel 3** with all of that I/O
plus a great screen.

So the roles invert from the naive "ESP32 = edge device" reading:

- **Phone = front-end + caller.** It runs the web-face in the browser, captures
  the mic, plays TTS, shows the camera, renders the animated avatar, and is the
  one that *calls the pipeline* (over the control-server WebSocket).
- **ESP32 = body.** It's a *second subscriber* to the same WebSocket. It never
  calls the pipeline; it reacts to `phase` events (RGB LED now, motors later).
- **Fleet = brains.** STT → LLM → TTS + the broadcast hub.

The one change this forced: the control server used to give each WS connection
its own orchestrator, so the phone's turn was invisible to the ESP32. It's now
**one shared orchestrator + a broadcast hub** — any client can send input, and
*every* client (phone face + ESP32 body) receives the same event stream. One
robot, one face, one body, one pipeline.

### The avatar (phone web-face)

An SVG robot face — two eyes with pupils + glowing irises, two eyebrows, and a
mouth — rigged in `src/server/static/face.js`. A tiny tween engine eases between
per-phase expression targets, with idle micro-behaviours (blink every few
seconds, subtle pupil drift) so it feels alive. Phase → expression:

| Phase | Eyes | Brows | Mouth |
|-------|------|-------|-------|
| inactive | half-lidded | neutral | gentle smile |
| listening | open | raised | friendly smile |
| thinking | look up/side | one raised | small, focused |
| speaking | open, lively | neutral | **animated talking** + smile |
| error | narrowed | furrowed | concerned frown |

The mouth is a quadratic curve whose middle dips down for a smile or up for a
frown; talking is a `speaking`-gated oscillation plus a per-`tts_audio`-chunk
twitch. `app.js` is the WS client: it maps events to expressions, queues + plays
TTS segments in order, records mic audio via `MediaRecorder` and ships the blob
over the WS as a binary frame (the server ffmpeg-converts → STT → turn), and has
an optional local webcam preview (a hook for future vision).

### Kiosk on the phone

To make the Pixel behave like a robot face and not a browser tab: a PWA manifest
(`display: fullscreen`) so **Add to Home screen** launches it chrome-less, a
`navigator.wakeLock` so the screen never sleeps while it's up, and tap-the-face
→ `requestFullscreen`. First mic + webcam test on real hardware: the avatar
heard the prompt, answered, and the camera preview worked.

### The ESP32 firmware

`firmware/esp32_face_led/` — an Arduino sketch (arduinoWebSockets + ArduinoJson +
Adafruit NeoPixel) that joins the same `ws://host:8010/ws`, parses `phase`
events, and eases the onboard WS2812 between colours (listening = blue, thinking
= pulsing purple, speaking = green, error = red). It sends nothing; it's pure
output. The motor/motion `tool` events are a marked TODO for the next Phase-4
step.
