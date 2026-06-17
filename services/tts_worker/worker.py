"""Persistent TTS worker process (the badlogic pattern, in Python).

Loads the real TTS engine **once** and synthesizes on its **main thread only**,
so MLX/Metal is never touched from a different thread per turn (the cause of the
`Segmentation fault: 11` we hit when qwen3-mlx ran in the orchestrator's per-turn
threads). It lives in its own process, so a crash restarts the worker instead of
killing the control server, and the model stays warm between turns.

Protocol (line-delimited JSON, request/response by id):

    in  (stdin) : {"id": 1, "text": "Hallo.", "out": "/tmp/seg.wav"}
                  {"cmd": "shutdown"}
    out (stdout): {"ready": true}                  # once, after model warm-up
                  {"id": 1, "ok": true}            # wav written to "out"
                  {"id": 1, "ok": false, "error": "..."}

stdout is reserved for the protocol; *all* library/log output is forced to
stderr (via an fd dup) so model chatter can't corrupt the channel.

Run (normally spawned by src.tts.worker_tts, with TTS_BACKEND set to the inner
engine and TTS_EFFECT=none):

    TTS_BACKEND=qwen3-mlx python -m services.tts_worker.worker
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# --- reserve stdout (fd 1) as a clean protocol channel -------------------
# Dup the original stdout, then point fd 1 at stderr so any C-level/library
# prints (MLX, torch, tqdm) go to stderr instead of our JSON protocol.
_PROTO_FD = os.dup(1)
os.dup2(2, 1)
_PROTO = os.fdopen(_PROTO_FD, "w", buffering=1)
sys.stdout = sys.stderr  # Python-level prints also go to stderr


def _send(obj: dict) -> None:
    _PROTO.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _PROTO.flush()


def main() -> int:
    # Imported here so model-loading chatter is already redirected to stderr.
    from src.tts import get_tts

    try:
        tts = get_tts()
    except Exception as exc:  # noqa: BLE001
        _send({"ready": False, "error": f"failed to build TTS: {exc}"})
        return 1

    # Warm up: force the model to load now so the first real turn isn't cold.
    try:
        tmp = Path(tempfile.mktemp(suffix=".wav", prefix="robot-tts-warm-"))
        tts.synthesize("Hallo.", str(tmp))
        tmp.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[tts-worker] warmup failed (continuing): {exc}", file=sys.stderr)

    _send({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if req.get("cmd") == "shutdown":
            break
        rid = req.get("id")
        text = req.get("text", "")
        out = req.get("out")
        try:
            tts.synthesize(text, out)
            _send({"id": rid, "ok": True})
        except Exception as exc:  # noqa: BLE001
            _send({"id": rid, "ok": False, "error": str(exc)})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
