# Phase 1 — Service split

**Goal:** break STT, LLM, TTS out from in-process Python calls into swappable
*services* behind a stable network interface, and turn the orchestrator into a
long-running process that emits **events** (phases, deltas, latency) instead of
running a blocking CLI turn. This is the foundation for the fleet (Phase 2), the
robot face (web-face.md), and HA (Phase 3).

> Why now: the current `Pipeline` is synchronous and CLI-bound. Everything we
> want next — a web face, moving the LLM to the GPU box, streaming TTS — needs an
> event-driven orchestrator and a transport seam. We get there without throwing
> away the Phase-0 interfaces.

## Scope

In:
- Promote each component to a service with a **network-capable** backend option,
  while keeping the existing in-process backend as the default fallback.
- Event-driven orchestrator: streams `phase`, `assistant_delta`, `latency`,
  `error` events to any number of subscribers (CLI, web face, logs).
- **Sentence chunker** between LLM stream and TTS (stolen from pibot) so audio
  starts on the first sentence.
- Streaming TTS interface (push text chunks → receive PCM/audio chunks).
- A thin **control server** (HTTP + WebSocket) exposing one entry point; the CLI
  becomes one client of it, the web face another.

Out (later phases):
- Actually distributing services across hosts (Phase 2 — but the transport seam
  lands here).
- Wake-on-LAN (Phase 2).
- Home Assistant / Wyoming (Phase 3).
- Barge-in (needs full-duplex audio; lands with web-face streaming mic).

## Design

### Interfaces stay; backends gain a network variant

The Phase-0 `Protocol` interfaces (`STT`, `LLM`, `TTS`) do not change shape. We
add backends selected by the same `*_BACKEND` env:

```
src/
  stt/
    __init__.py            # factory (unchanged seam)
    faster_whisper_stt.py  # in-process (default)
    parakeet_stt.py        # NEW candidate (eval vs faster-whisper)
    http_stt.py            # NEW: POST audio -> JSON transcript (remote service)
  llm/
    ollama_llm.py          # in-process HTTP to local Ollama (default)
    http_llm.py            # NEW: OpenAI-compatible endpoint (remote Ollama / llama.cpp)
  tts/
    say_tts.py             # mac default
    piper_tts.py           # fleet default
    streaming.py           # NEW: StreamingTTS protocol (push text -> yield PCM)
```

`http_stt` / `http_llm` are how a service "moves to another host" in Phase 2:
flip `LLM_BACKEND=http` + `LLM_HTTP_URL=http://gaming-pc:11434/...` in `.env`.
No orchestrator change.

### Streaming TTS protocol

```python
class StreamingTTS(Protocol):
    def stream(self, text_chunks: Iterator[str]) -> Iterator[bytes]:
        """Consume text as it arrives, yield PCM frames as they synthesize."""
```

Non-streaming engines (`say`, base Piper) are wrapped to satisfy this by
synthesizing per sentence chunk. Piper can stream sentence-by-sentence today.

### Sentence chunker (steal from pibot)

```
src/text/sentence_chunker.py   # NEW
```

`SentenceChunker(sentences_per_chunk=1)`: `push(delta) -> list[str]` emits
complete sentences as they form; `flush() -> str | None` returns the tail.
Orchestrator feeds LLM deltas in, pushes each completed sentence to TTS
immediately. **Biggest perceived-latency win in the project** — measure
"first-audio-out" as a new latency metric.

### Event-driven orchestrator

`src/orchestrator.py` (evolves `pipeline.py`; keep `pipeline.py` as the
synchronous helper it wraps, or fold it in):

- Phase state machine: `inactive → listening → thinking → speaking → error`
  (add `hearing`, `tool` in later phases). Each transition emits an event.
- Event types: `phase`, `heard_text`, `assistant_delta`, `assistant_end`,
  `tts_audio`, `latency`, `error`.
- Subscribers register a callback. CLI prints; web face sends over WS; a logger
  subscriber records latency into the benchmark shape.

### Control server

`src/server/app.py` — minimal stdlib/`aiohttp`-free if possible, but a small
async server is fine:
- `GET /` → serves the web face (Phase web-face.md).
- `WS /ws` → bidirectional: client sends `{type:"prompt"|"abort"|"set_model"|
  "set_env"}`, server streams the orchestrator events.
- `GET /api/config` → current env/model selection + available presets.
- `POST /api/select` → switch environment preset / model at runtime.

Keep audio capture out of the server for now (push-to-talk text or `say`-driven
smoke still works); full mic streaming lands with the web face.

## Deliverables

1. `src/text/sentence_chunker.py` + unit test.
2. `src/tts/streaming.py` protocol + Piper streaming adapter + `say` wrapper.
3. `src/llm/http_llm.py`, `src/stt/http_stt.py` (network backends).
4. `src/stt/parakeet_stt.py` (candidate; behind `STT_BACKEND=parakeet`).
5. `src/orchestrator.py` with phase state machine + event bus.
6. `src/server/app.py` control server (HTTP + WS), serving a placeholder page.
7. CLI (`src/main.py`) refactored to be a client of the orchestrator events.
8. New latency metric **`first_audio_ms`** recorded in `benchmarks.json`.
9. `.env.example` + `config.py` additions for the new backends/URLs.

## Config additions

```bash
# Backends can now be network services
LLM_BACKEND=ollama|http
LLM_HTTP_URL=http://localhost:11434
STT_BACKEND=faster-whisper|parakeet|http
STT_HTTP_URL=http://localhost:9000
TTS_BACKEND=say|piper            # piper now streams per-sentence
TTS_STREAMING=1

# Control server
SERVER_HOST=0.0.0.0
SERVER_PORT=8010
```

## Acceptance criteria

- `python -m src.server.app` starts; opening `http://localhost:8010` serves a
  page; a WS client can send a prompt and receive `phase` + `assistant_delta` +
  `tts_audio` events.
- With `TTS_STREAMING=1` and Piper, **first audio plays before the LLM finishes**
  the full reply (verify `first_audio_ms` < `llm_ms`).
- Flipping `LLM_BACKEND=http` + `LLM_HTTP_URL` to a second machine's Ollama works
  with zero orchestrator code change.
- CLI loop still works end-to-end (regression).
- `tools/smoke.py --record` still appends a valid `benchmarks.json` row, now with
  `first_audio_ms`.

## Risks / decisions to revisit

- **Async vs threads** in the orchestrator — start with threads + a queue if it
  keeps stdlib-only; move to `asyncio` only if the WS server demands it.
- Parakeet adds a native build; keep it optional so faster-whisper stays the
  zero-friction default.
- Audio transport format over WS (raw PCM vs Opus) — PCM first, Opus if bandwidth
  matters for the fleet/edge.
