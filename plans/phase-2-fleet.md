# Phase 2 — Fleet distribution

**Goal:** run the three services on the *right* hosts instead of all on the Mac,
driven entirely by config. The big lever: LLM on the **Gaming PC GPU** (woken on
demand), orchestration + always-on services on the **NUC**, with graceful
fallback to local models when the GPU box is asleep.

> Prereq: Phase 1 already gave us network backends (`http_llm`, `http_stt`) and
> environment presets. Phase 2 makes placement real and adds Wake-on-LAN + a
> provider-selection abstraction so the orchestrator doesn't care where the model
> runs.

## Scope

In:
- Deploy each service as a standalone process/container reachable over the LAN:
  - **LLM service** on Gaming PC: Ollama (or llama.cpp server) on `:11434`,
    serving an 8 GB-VRAM-friendly model (Llama 3.1 8B / Qwen3 8B @ Q4).
  - **STT service** on NUC (always-on): faster-whisper or Parakeet behind
    `http_stt`.
  - **TTS service** on NUC: Piper behind a small HTTP wrapper.
  - **Orchestrator + web face** on NUC (always-on, low power).
- **LLM provider abstraction** with placement policy: `remote-gpu` (preferred) →
  `local-fallback` (NUC/Mac small model) when the GPU box is unavailable.
- **Wake-on-LAN** for the Gaming PC: magic packet → poll for Ollama port → route
  the call; fall back if it doesn't wake in time. Idle-timeout suspends it again.
- Per-host model presets (already scaffolded in `presets.py`).
- Fleet latency runs: record each host combo into `benchmarks.json` and compare
  in `benchmarks.html` (this is the data that justifies waking the GPU).

Out:
- Home Assistant (Phase 3).
- ESP32 / edge audio (Phase 4).
- Auto-scaling / multi-GPU. Single GPU box, single robot.

## Design

### Service packaging

Each service gets a tiny entrypoint so it can run anywhere:

```
services/
  llm_server/      # wraps Ollama/llama.cpp config; or just documents running it
  stt_server/      # http_stt server side: POST audio -> {text}
  tts_server/      # http: POST {text} -> streamed PCM/wav (Piper)
  orchestrator/    # the Phase-1 control server (web face lives here)
```

Cross-arch: STT/TTS images must build on x86_64 (NUC/NAS) and arm64 (Mac dev).
Avoid CUDA-only assumptions in STT/TTS — they run CPU/iGPU on the NUC.

### LLM provider abstraction (the WoL seam)

```python
class LlmProvider(Protocol):
    def stream(self, messages) -> Iterator[str]: ...

# Composed policy:
RoutedLLM(
  primary=RemoteOllama("http://gaming-pc:11434", model="qwen3:8b-q4",
                       wake=WolTarget(mac="AA:BB:..", host="gaming-pc",
                                      port=11434, timeout_s=30)),
  fallback=LocalOllama("http://nuc:11434", model="llama3.2:3b-q4"),
)
```

`RoutedLLM.stream`:
1. If primary host port open → use it.
2. Else send magic packet, poll port up to `timeout_s`.
3. If up → use primary; else → `fallback` (smaller local model), log the
   downgrade as a latency note.
4. Background idle-timer suspends the GPU box after N minutes idle.

The orchestrator only sees `LlmProvider`. The web face's "environment" dropdown
selects which routing policy is active.

### Wake-on-LAN details (Gaming PC)

- BIOS + NIC WoL enabled; MAC recorded in the preset.
- `wakeonlan`/raw UDP magic packet to the broadcast address.
- Readiness = TCP connect to `:11434` **and** a successful `/api/tags` probe
  (port-open ≠ model-loaded). Cold model load is part of the latency story —
  record it as a COLD run.
- Idle suspend: orchestrator tracks last-LLM-use; after timeout issues
  `systemctl suspend` over SSH (or the box self-suspends on idle).

## Deliverables

1. `services/*` entrypoints (or documented run commands) for LLM/STT/TTS.
2. `src/llm/routed_llm.py` — primary/fallback routing.
3. `src/net/wol.py` — magic packet + readiness probe + idle suspend hook.
4. Presets filled with real host names/MACs/models for the four hosts.
5. Fleet benchmark runs in `benchmarks.json` (Mac-only vs NUC+GPU vs NUC-only).
6. README/AGENTS update: how to bring up each host.

## Config additions

```bash
LLM_BACKEND=routed
LLM_PRIMARY_URL=http://gaming-pc:11434
LLM_PRIMARY_MODEL=qwen3:8b-q4
LLM_FALLBACK_URL=http://nuc:11434
LLM_FALLBACK_MODEL=llama3.2:3b-q4
WOL_MAC=AA:BB:CC:DD:EE:FF
WOL_HOST=gaming-pc
WOL_PORT=11434
WOL_TIMEOUT_S=30
GPU_IDLE_SUSPEND_MIN=15
```

## Acceptance criteria

- With the Gaming PC asleep, a prompt triggers a magic packet, the box wakes, the
  model loads, and the reply streams — logged as a COLD run with wake latency.
- With the Gaming PC unreachable and not waking in time, the turn completes on the
  NUC/Mac fallback model, logged as a downgrade.
- Orchestrator code is unchanged between "all-local" and "GPU-remote" — only env
  differs.
- `benchmarks.html` shows side-by-side totals for at least: Mac-only, NUC+GPU
  (warm), NUC+GPU (cold/wake), NUC-only fallback.

## Risks / decisions

- **8 GB VRAM ceiling**: keep context modest; verify the chosen 8B Q4 model + ctx
  actually fits without CPU offload (offload tanks latency). Bench it.
- **Wake latency vs fallback quality**: tune `WOL_TIMEOUT_S` — too long and the
  user waits; too short and we always downgrade. The benchmark data decides.
- **Transport**: plain HTTP for now. Revisit Wyoming here if HA (Phase 3) pulls
  it forward — STT/TTS over Wyoming would unify with HA.
