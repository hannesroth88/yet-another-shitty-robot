# Phase 3 — Home Assistant integration

**Goal:** let the voice assistant trigger real-world actions and automations
through Home Assistant (running on the NUC), and optionally plug our STT/LLM/TTS
into HA's **Assist** pipeline via the **Wyoming** protocol so the two ecosystems
share components instead of duplicating them.

> Two directions, do both:
> 1. **Assistant → HA** (outbound): the LLM can call HA to control devices /
>    run automations ("turn off the kitchen light", "is the garage open?").
> 2. **HA → our services** (inbound): expose our STT and TTS as Wyoming services
>    so HA's Assist can use them, and/or run our wake word into HA. This is the
>    natural fit since whisper/piper/wake-word all speak Wyoming.

## Scope

In:
- **HA tool/function** for the LLM: a tool the orchestrator exposes that calls
  HA's REST or WebSocket API (`/api/services/<domain>/<service>`, `/api/states`).
  Mirrors pibot's tool pattern (`tool_start`/`tool_end` events) — and adds the
  `tool` phase to the face.
- **Intent mapping**: either (a) LLM emits a structured tool call we forward to
  HA, or (b) we route certain utterances to HA's own intent/Assist engine. Start
  with (a) — keeps the LLM in control and is simpler to debug.
- **Wyoming adapters** (inbound) for our STT and TTS so HA Assist can call them:
  - `wyoming-faster-whisper` / our `http_stt` behind a Wyoming shim.
  - `wyoming-piper` / our TTS behind a Wyoming shim.
- `waitForSpeechBeforeTool` behavior (steal from pibot): don't fire an HA action
  while the robot is still mid-sentence.
- Safety: confirmations for destructive actions; allowlist of domains/entities
  the assistant may touch.

Out:
- Replacing HA's UI. We integrate, not rebuild.
- Cloud HA / Nabu Casa. Local only.
- Wake-word on ESP32 (Phase 4) — though the Wyoming wake-word seam is designed
  here.

## Design

### Outbound: HA as a tool

```
src/tools/
  __init__.py        # tool registry (LLM-callable)
  home_assistant.py  # NEW: ha_call_service(domain, service, entity, data),
                     #      ha_get_state(entity), ha_list_entities(area)
```

- The orchestrator gains a **tool loop**: LLM streams → if a tool call appears,
  emit `tool_start`, run it, emit `tool_end`, feed the result back, continue.
- Face shows the `tool` phase (eyes down/busy) during execution.
- Tool calls are **allowlisted**: `HA_ALLOWED_DOMAINS=light,switch,media_player,
  cover`, plus an entity allowlist. Anything else is refused and spoken back.
- Destructive/irreversible actions (locks, garage, alarm) require a spoken
  confirmation turn before execution.

### Inbound: Wyoming adapters

```
services/wyoming/
  stt_wyoming.py   # wraps our STT as a Wyoming ASR service for HA Assist
  tts_wyoming.py   # wraps our TTS as a Wyoming TTS service for HA Assist
```

- Register these in HA → Settings → Voice assistants → Assist pipeline.
- This makes the *same* STT/TTS usable by both our orchestrator and HA, and is
  the seam the ESP32 (Phase 4) streams into.

### Transport decision

This is where **Wyoming** likely wins over plain HTTP (the open question in
AGENTS.md): whisper, piper, and wake-word all speak it, and HA supports it
natively. Plan: keep `http_stt`/`http_tts` for our own orchestrator, add Wyoming
adapters for HA interop, and evaluate consolidating onto Wyoming if it proves
clean.

## Deliverables

1. `src/tools/home_assistant.py` + tool registry + orchestrator tool loop.
2. Domain/entity allowlist + confirmation flow for destructive actions.
3. `tool` phase added to orchestrator + face expression.
4. `services/wyoming/stt_wyoming.py`, `tts_wyoming.py` adapters.
5. HA Assist pipeline configured to use our STT/TTS (documented).
6. Example automations triggered by voice (lights, media, a scene).
7. README/AGENTS: HA setup, token, allowlist, Wyoming registration.

## Config additions

```bash
HA_BASE_URL=http://nuc:8123
HA_TOKEN=...                       # long-lived access token (do NOT commit)
HA_ALLOWED_DOMAINS=light,switch,media_player,cover,scene
HA_ALLOWED_ENTITIES=               # empty = all within allowed domains
HA_CONFIRM_DOMAINS=lock,alarm_control_panel,cover
WYOMING_STT_PORT=10300
WYOMING_TTS_PORT=10200
```

## Acceptance criteria

- "Turn off the living room light" → LLM emits `ha_call_service(light, turn_off,
  light.living_room)` → light turns off → robot confirms; face shows `tool` then
  `speaking`.
- A destructive action (e.g. unlock) asks for confirmation before executing.
- A request for a disallowed domain is politely refused, no HA call made.
- HA Assist pipeline can run a full voice turn using **our** Wyoming STT + TTS.
- No HA action fires while the robot is still speaking (waitForSpeechBeforeTool).
- `HA_TOKEN` is read from env/`.env`, never committed.

## Risks / decisions

- **Who owns intent** — our LLM tool loop vs HA's Assist intents. We start with
  the LLM owning it (more flexible); revisit if HA's native intents are more
  reliable for common commands.
- **Latency of the tool loop** — an extra LLM round-trip after the tool result.
  Measure it; consider speaking an immediate ack ("okay, one moment") before the
  tool returns.
- **Security** — allowlist + confirmations are mandatory; a hallucinated tool
  call must never toggle a lock unprompted.
