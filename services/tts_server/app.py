"""TTS HTTP service (Phase 2 fleet).

Runs the configured TTS backend (Piper/Kokoro/say) as a standalone HTTP service
so it can live on the always-on NUC. Returns a full wav per request; sentence
streaming stays in the orchestrator (which calls this once per sentence).

  POST /synthesize   {"text": "..."}   ->  audio/wav bytes
  GET  /healthz                        ->  {"ok": true}

Run:  python -m services.tts_server.app   (honors TTS_* env / .env)
"""
from __future__ import annotations

import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.config import config
from src.tts import get_tts

_tts = None


def _get_tts():
    global _tts
    if _tts is None:
        _tts = get_tts()
    return _tts


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotTTS/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            return self._json({"ok": True, "backend": config.tts_backend})
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path != "/synthesize":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return self.send_error(400, "invalid json")
        text = (payload.get("text") or "").strip()
        if not text:
            return self.send_error(400, "missing 'text'")
        tmp = Path(tempfile.mktemp(suffix=".wav", prefix="robot-ttssrv-"))
        try:
            _get_tts().synthesize(text, str(tmp))
            body = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    import os

    host = os.environ.get("TTS_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("TTS_SERVER_PORT", "9001"))
    print(f"TTS service ({config.tts_backend}) on http://{host}:{port}  POST /synthesize")
    _get_tts()
    print("ready.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
