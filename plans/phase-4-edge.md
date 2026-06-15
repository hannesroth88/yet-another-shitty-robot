# Phase 4 — Edge (ESP32-S3-Box → robot)

**Goal:** move the audio front-end (wake word + mic capture + speaker playback,
and eventually the face render) onto the **ESP32-S3-Box**, streaming audio to the
heavier fleet services. Final target: the physical **robot** hosts/embeds this
front-end and talks to the fleet over the network.

> This is the long pole. By now STT/LLM/TTS are services (Phase 1–2), HA is wired
> (Phase 3), and the face renders in a browser (web-face.md). Phase 4 makes the
> entry point an embedded device instead of a laptop browser.

## Scope

In:
- **Wake word on-device** (ESP32-S3) so the robot only streams after "hey robot"
  — e.g. **microWakeWord**. Keeps the LAN quiet and the fleet asleep until needed.
- **Audio streaming** from the ESP32 to the STT service (Wyoming satellite is the
  natural protocol — pairs with Phase 3's Wyoming adapters and HA).
- **Playback** of TTS PCM back through the ESP32 speaker.
- **Phase/face signal** to the device: at minimum an LED/animation reflecting
  `listening/thinking/speaking/error`; if the robot has a screen, render the
  web-face there.
- **Robot motion tools** (steal pibot's shape): `move_forward(duration)`,
  `turn_left_degrees(deg)` exposed as LLM tools, with hardware constraints encoded
  (e.g. "can only go forward + turn one direction") and auto-stop after duration.
- Bring-up doc for flashing + provisioning the ESP32-S3-Box.

Out:
- On-device STT/LLM/TTS (too heavy for ESP32 — it streams to the fleet).
- Final robot chassis/motor specifics (TBD hardware) — keep the motion interface
  abstract so the real drivers slot in.

## Design

### Edge front-end (ESP32-S3-Box)

- **ESP-IDF / ESPHome** firmware:
  - microWakeWord for the wake phrase.
  - Mic → Opus/PCM → stream to STT (Wyoming satellite or our `http_stt` over WS).
  - Receive TTS PCM → speaker.
  - Status LED ring / display driven by phase events.
- ~8 MB PSRAM budget — wake word + audio buffers only; no model inference beyond
  wake word.

### Why Wyoming here

Phase 3 already stood up Wyoming STT/TTS adapters. HA's **Wyoming Satellite** is
purpose-built for exactly this: an ESP32 satellite that does wake word + audio
I/O and streams to HA Assist / our services. Reusing it means the edge device,
HA, and our orchestrator all speak one protocol.

### Robot motion as LLM tools

```
src/tools/motion.py   # NEW
  move_forward(duration_s)         # auto-stops after duration
  turn_left_degrees(degrees)       # hardware: one rotation direction only
  stop()
```

- Encode hardware constraints in the tool descriptions (pibot does this in its
  system prompt: "can only drive forward and turn counter-clockwise").
- Motion tools go through the same tool loop + `tool` phase + `waitForSpeechBefore
  Tool` as HA tools (Phase 3).
- Behind an interface: a `MotionDriver` Protocol so the simulator (web face) can
  show intended motion and the real robot uses GPIO/motor drivers.

### Compute split (open question → decide here)

Per AGENTS.md: does the robot host only the front-end, or also TTS/STT? Plan:
- **v1**: robot = front-end only (wake word + audio I/O + face). STT/LLM/TTS on
  the fleet. Simplest, lowest edge compute.
- **v2 (optional)**: push TTS or small STT onto a robot SBC (if the robot has a
  Pi-class board, not the ESP32) to cut network round-trips. Decide from latency
  data.

## Deliverables

1. ESP32-S3-Box firmware: wake word + mic stream + speaker playback + status LED.
2. Wyoming satellite (or WS) link from device → STT service.
3. `src/tools/motion.py` + `MotionDriver` interface + simulator driver for the
   web face.
4. Robot motion tools wired into the orchestrator tool loop.
5. Phase → device-LED/face mapping.
6. Bring-up doc: flashing, Wi-Fi provisioning, pairing to the fleet.

## Config additions

```bash
EDGE_WAKE_WORD=hey_robot
EDGE_STREAM_PROTOCOL=wyoming|ws
STT_HTTP_URL=http://nuc:9000        # or Wyoming satellite target
MOTION_DRIVER=sim|gpio              # sim drives the web face; gpio drives hardware
```

## Acceptance criteria

- Saying the wake word on the ESP32 wakes the pipeline; speech streams to STT and
  a reply plays back through the device speaker.
- The device LED/face reflects `listening → thinking → speaking → error`.
- An utterance like "drive forward a little" issues `move_forward(...)`, the
  simulator face/LED shows motion, and (on hardware) the motor runs then
  auto-stops.
- Fleet stays asleep until the wake word fires (WoL from Phase 2 still applies to
  the GPU box).
- No motion or HA action fires while the robot is still speaking.

## Risks / decisions

- **Echo / barge-in on a real speaker+mic** — needs AEC; this is where continuous
  listening + stop-word barge-in (deferred from Phase 1) finally lands.
- **ESP32 audio quality / latency** — Opus vs PCM, buffer sizing; bench it like
  every other stage.
- **Robot compute** — final SBC choice affects whether TTS/STT move on-robot.
  Keep interfaces abstract so the answer is config, not a rewrite.
- **microWakeWord accuracy** — false accepts/rejects; may need a custom-trained
  wake phrase.
