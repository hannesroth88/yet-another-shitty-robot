"""WorkerTTS -- client for the persistent TTS worker process.

Implements the ``TTS`` protocol (``synthesize(text, out_path)``) so the
orchestrator is unchanged, but the actual synthesis happens in a separate,
long-lived child process (see ``services.tts_worker.worker``). Benefits:

* **No segfault** -- MLX/Metal runs on the worker's main thread only, never on
  the orchestrator's per-turn threads.
* **Warm** -- the model loads once in the worker and stays resident.
* **Isolated** -- if the worker dies, the client respawns it and only the
  current turn fails; the control server keeps running.

The client is synchronous and serialized (one outstanding request at a time,
guarded by a lock), which matches the orchestrator's sentence-at-a-time loop.
The DSP voice effect is applied by the parent ``get_tts()`` wrapper, so the
worker is spawned with ``TTS_EFFECT=none`` and produces the raw voice.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_READY_TIMEOUT = float(os.environ.get("TTS_WORKER_READY_TIMEOUT", "180"))
_SYNTH_TIMEOUT = float(os.environ.get("TTS_WORKER_SYNTH_TIMEOUT", "120"))


class WorkerTTS:
    def __init__(self, inner_backend: str) -> None:
        self._inner = inner_backend
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._id = 0

    # -- process lifecycle ----------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self) -> None:
        env = dict(os.environ)
        env["TTS_BACKEND"] = self._inner   # the real engine the worker loads
        env["TTS_EFFECT"] = "none"          # effect is applied by the parent
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "services.tts_worker.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            text=True, bufsize=1, env=env, cwd=str(_REPO_ROOT),
        )
        self._proc = proc
        # Wait for the {"ready": true} handshake (covers model load time).
        line = self._readline(_READY_TIMEOUT)
        if line is None:
            self._kill()
            raise RuntimeError("TTS worker did not become ready in time")
        try:
            msg = json.loads(line)
        except ValueError:
            self._kill()
            raise RuntimeError(f"TTS worker sent garbage on startup: {line!r}")
        if not msg.get("ready"):
            self._kill()
            raise RuntimeError(f"TTS worker failed to start: {msg.get('error')}")

    def _ensure(self) -> None:
        if not self._alive():
            self._spawn()

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _readline(self, timeout: float) -> str | None:
        assert self._proc is not None and self._proc.stdout is not None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            return None
        return self._proc.stdout.readline()

    # -- TTS protocol ---------------------------------------------------

    def warm(self) -> None:
        """Spawn + load the model now (e.g. at server startup) so the first
        real turn isn't cold. Safe to call repeatedly."""
        with self._lock:
            self._ensure()

    def synthesize(self, text: str, out_path: str) -> None:
        with self._lock:
            self._ensure()
            self._id += 1
            rid = self._id
            assert self._proc is not None and self._proc.stdin is not None
            try:
                self._proc.stdin.write(
                    json.dumps({"id": rid, "text": text, "out": out_path}) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._kill()
                raise RuntimeError(f"TTS worker write failed: {exc}") from exc

            while True:
                line = self._readline(_SYNTH_TIMEOUT)
                if line is None:
                    self._kill()  # hung -> respawn next call
                    raise RuntimeError("TTS worker timed out")
                if line == "":  # EOF: worker died
                    self._kill()
                    raise RuntimeError("TTS worker exited during synthesis")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # ignore stray lines
                if msg.get("id") != rid:
                    continue
                if msg.get("ok"):
                    return
                raise RuntimeError(msg.get("error", "TTS worker error"))

    def close(self) -> None:
        with self._lock:
            if self._alive() and self._proc is not None and self._proc.stdin:
                try:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except Exception:
                    self._kill()
            else:
                self._kill()
