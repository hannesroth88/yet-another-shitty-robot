# Fleet services (Phase 2)

Standalone entrypoints so each pipeline stage can run on the *right* host instead
of all on the Mac. Placement is config (`.env` / presets), not code — see
`src/presets.py` and `AGENTS.md`.

```
mic ─► [STT service] ─► [LLM service] ─► [TTS service] ─► speaker
        NUC (always-on)   Gaming PC GPU     NUC (always-on)
                          (woken via WoL)
```

## LLM service — Gaming PC GPU (on-demand)

The LLM service is just **Ollama** on the GPU box; no custom code. Bring it up:

```bash
# on the Gaming PC (x86 + RTX 2080, 8 GB VRAM)
ollama serve                     # listens on :11434
ollama pull qwen2.5:7b           # ~7B @ Q4 fits 8 GB with modest context
```

The orchestrator reaches it via `LLM_BACKEND=routed` (primary = Gaming PC,
fallback = local small model) with Wake-on-LAN. See `src/llm/routed_llm.py`.
Enable WoL in BIOS + NIC, record the MAC into `WOL_MAC`.

## STT service — NUC (always-on)

```bash
# on the NUC (or any host)
STT_BACKEND=faster-whisper STT_MODEL=base \
  python -m services.stt_server.app          # :9000  POST /transcribe
```

Orchestrator side: `STT_BACKEND=http` + `STT_HTTP_URL=http://nuc:9000`.

## TTS service — NUC (always-on)

```bash
TTS_BACKEND=piper python -m services.tts_server.app   # :9001  POST /synthesize
```

(An `http` TTS backend on the orchestrator side can be added when we move TTS off
the dev host; for now the orchestrator runs Piper locally or calls this service
directly.)

## Orchestrator + web face — NUC

```bash
python -m src.server.app          # :8010 HTTP + WS (the Phase 1 control server)
```

## Cross-arch note

STT/TTS images/processes must run on x86_64 (NUC/NAS) and arm64 (Mac). Avoid
CUDA-only assumptions in STT/TTS — they run CPU/iGPU on the NUC. Only the LLM
service assumes the GPU, and only on the Gaming PC.
