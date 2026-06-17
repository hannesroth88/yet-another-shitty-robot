# Firmware — robot body controller (ESP32-S3)

> **The ESP32 is the robot's *body*, not its face.** The phone (Pixel) is the
> face + audio + camera front-end. This board is a **subscriber** to the same
> control-server WebSocket the phone uses, reacting to `phase` events with the
> onboard RGB LED (and, later, motors). It never calls the pipeline.

## Why this split

The **ESP32-S3-DevKitC-1 (N16R8)** has WiFi, 36 GPIO, and one WS2812 RGB LED on
GPIO48 — but **no mic, speaker, camera, or display**. The Pixel 3 has all of
those plus a great screen. So:

| Concern | Runs on |
|---|---|
| Mic capture, speaker playback, camera, animated face UI | **Pixel 3** (browser web-face) |
| STT → LLM → TTS pipeline, WS broadcast hub | **Fleet** (Mac/NUC), `src/server/app.py` |
| Status LED, motors, servos, sensors (the *body*) | **ESP32-S3** (this firmware) |

```
 Pixel 3  ──prompt / mic audio──►  control server  ──phase / tts events──►  Pixel 3  (face + sound)
                                        │  (broadcast hub)
                                        └────────────────────────────────►  ESP32-S3 (LED + motors)
```

Both the phone and the ESP32 open `ws://<fleet-host>:8010/ws`. The server runs
**one shared orchestrator** and **broadcasts every event to all clients**, so the
phone's turn lights this board's LED in lock-step.

## Communication contract

The board only needs to read **`phase`** events (JSON text frames):

```json
{"type":"phase","phase":"listening|thinking|speaking|error|inactive"}
```

LED mapping (see the sketch): listening = blue, thinking = pulsing purple,
speaking = green, error = red, inactive = dim. Link-down = amber.

Other event types (`assistant_delta`, `tts_audio`, `latency`, …) are ignored by
the body controller — the phone handles those.

## Flashing (`esp32_face_led/esp32_face_led.ino`)

1. Arduino IDE → install ESP32 board support (Espressif), select **ESP32S3 Dev
   Module**.
2. Library Manager → install:
   - **WebSockets** (Markus Sattler / links2004)
   - **ArduinoJson** (Benoit Blanchon)
   - **Adafruit NeoPixel**
3. Edit the `CONFIG` block: `WIFI_SSID`, `WIFI_PASS`, and `HOST` = the LAN IP of
   the machine running `python -m src.server.app` (e.g. the NUC or your Mac).
4. Upload (use the **USB/OTG** Type-C port; hold **BOOT** on power-up if it won't
   enter download mode). Open Serial Monitor @ 115200 to watch `[phase]` logs.

The onboard WS2812 also lights up on WiFi connect (a board feature), then this
firmware takes over to show pipeline phase.

## Next (Phase 4)

- **Motors**: add a `tool` event from the orchestrator's motion tools
  (`move_forward`, `turn_left_degrees`) and drive the motor driver from
  `onWsEvent` — the `TODO` marker in the sketch. Keep `MotionDriver` abstract so
  `sim` (web face) and `gpio` (this board) are swappable.
- **Servos** for physical eyebrows/head, mirroring the phase the LED already shows.
