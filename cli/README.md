# `cli/` — robot command-line tools

One place for the scripts that run and inspect the voice-assistant control
server. Run them from the repo root.

## `robot`

The single entry point for the server lifecycle and logs.

```bash
cli/robot start            # start (foreground, live logs, Ctrl-C stops)
cli/robot start --bg       # start detached (logs to logs/server.log)
cli/robot stop             # stop the server + TTS worker
cli/robot restart [...]    # stop, then start (passes through start flags)
cli/robot status           # running? port / TLS / pids + health probe
cli/robot logs             # follow the latest log (tail -f)
cli/robot logs -n 200      # last 200 lines (no follow)
cli/robot logs --errors    # only tracebacks / errors / segfaults
cli/robot url              # URLs to open (localhost + phone LAN IP)
cli/robot help
```

### Start flags (also valid on `restart`)

| Flag | Effect |
|------|--------|
| `--bg`, `-d` | run detached in the background |
| `--tts <backend>` | `say` \| `piper` \| `worker` \| `qwen3-mlx` \| `kokoro` (sets `TTS_BACKEND`) |
| `--http` | plain HTTP (`SERVER_TLS=0`, localhost only) |
| `--tls` | force HTTPS (`SERVER_TLS=1`, the default — the phone mic needs a secure context) |
| `--port <n>` | `SERVER_PORT` (default `8010`) |

### What it does for you

- **Start** is unbuffered with `faulthandler` on (a crash flushes a traceback
  instead of being swallowed), defaults to HTTPS so the phone's mic works, and
  defaults to Piper TTS (stable + snappy). Each run writes a timestamped
  `logs/server-<ts>.log` and points `logs/server.log` at the latest. A previous
  instance is stopped first.
- **logs** saves you from hunting for the timestamped filename: follow live,
  tail the last N, or grep just the errors.
- **status** shows the server + TTS-worker pids and auto-detects http/https for
  the health probe (so a backgrounded `--http` server still reports `200`).
- **url** prints the Mac URL and the phone URL with the auto-detected LAN IP.

### Common recipes

```bash
# fast iteration (instant macOS voice, plain HTTP on localhost)
cli/robot start --http --tts say

# the qwen3 cloned voice — persistent worker process: warm + crash-free
cli/robot start --bg --tts worker
cli/robot logs

# is it up, and on what scheme/port?
cli/robot status

# what went wrong?
cli/robot logs --errors
```

## `dev_server.sh`

Back-compat shim kept for muscle memory — it just calls `robot start`:

```bash
cli/dev_server.sh            # == cli/robot start
cli/dev_server.sh --bg       # == cli/robot start --bg
```

Prefer `cli/robot` directly; `dev_server.sh` may be removed later.

## Notes

- The TTS **worker** (`--tts worker`) runs the real engine (default
  `qwen3-mlx`, via `TTS_WORKER_BACKEND`) in a separate, single-threaded child
  process so MLX/Metal never runs on the orchestrator's per-turn threads. The
  server pre-warms it at startup; the first turn is warm instead of ~40s cold.
- All flags are also plain environment variables (`TTS_BACKEND`, `SERVER_TLS`,
  `SERVER_PORT`, `TTS_WORKER_BACKEND`, …), so `.env` and inline env still work:
  `TTS_BACKEND=worker cli/robot start`.
