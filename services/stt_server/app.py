"""STT HTTP service (Phase 2 fleet) -- the server side of ``src/stt/http_stt.py``.

Runs the configured STT backend (faster-whisper/parakeet) as a standalone HTTP
service so it can live on the always-on NUC while the orchestrator runs elsewhere.

  POST /transcribe   multipart form: audio=<wav> [language=de]  ->  {"text": ...}
  GET  /healthz                                                 ->  {"ok": true}

Run:  python -m services.stt_server.app   (honors STT_* env / .env)
Cross-arch: faster-whisper is CPU int8 by default (NUC/NAS friendly); set
STT_COMPUTE_TYPE/STT_MODEL via env per host.
"""
from __future__ import annotations

import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.config import config
from src.stt import get_stt

_stt = None


def _parse_multipart(body: bytes, boundary: str) -> dict[str, bytes]:
    """Tiny multipart/form-data parser (avoids the deprecated stdlib ``cgi``).

    Returns a {field_name: raw_value_bytes} map. Sufficient for our simple
    ``audio`` + ``language`` form; not a general-purpose implementation.
    """
    delim = ("--" + boundary).encode()
    fields: dict[str, bytes] = {}
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        if not _:
            continue
        name = None
        for line in head.split(b"\r\n"):
            low = line.lower()
            if low.startswith(b"content-disposition:"):
                for token in line.split(b";"):
                    token = token.strip()
                    if token.startswith(b"name="):
                        name = token[5:].strip(b'"').decode()
        if name is not None:
            fields[name] = value
    return fields


def _get_stt():
    global _stt
    if _stt is None:
        _stt = get_stt()
    return _stt


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotSTT/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            return self._json({"ok": True, "backend": config.stt_backend})
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path != "/transcribe":
            return self.send_error(404)
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            return self.send_error(400, "expected multipart/form-data")
        boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        fields = _parse_multipart(body, boundary)
        if "audio" not in fields:
            return self.send_error(400, "missing 'audio' field")
        data = fields["audio"]
        tmp = Path(tempfile.mktemp(suffix=".wav", prefix="robot-sttsrv-"))
        try:
            tmp.write_bytes(data)
            text = _get_stt().transcribe(str(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        self._json({"text": text})

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    import os

    host = os.environ.get("STT_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("STT_SERVER_PORT", "9000"))
    print(f"STT service ({config.stt_backend}) on http://{host}:{port}  POST /transcribe")
    print("warming up model…", flush=True)
    _get_stt()
    print("ready.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
