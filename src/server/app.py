"""Control server (Phase 1): one HTTP + WebSocket entry point for the pipeline.

The CLI is one client of the orchestrator; this server is the other -- the web
face (plans/web-face.md) and any future controller connect here. stdlib only
(no aiohttp/websockets dependency): a threaded HTTP server plus a hand-rolled
WebSocket upgrade.

Endpoints:
* ``GET  /``            -> serves the placeholder web face.
* ``GET  /api/config``  -> current backend/model selection.
* ``GET  /ws``          -> WebSocket. Client sends ``{"type":"prompt","text":..}``
                           or ``{"type":"abort"}``; server streams orchestrator
                           events (phase / assistant_delta / tts_audio / latency).

Audio capture stays out of the server for now (push-to-talk text prompts); full
mic streaming lands with the web face. ``tts_audio`` events carry a wav path; the
client can fetch ``GET /audio?path=...`` to play it.

Run:  python -m src.server.app
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import config
from ..latency import Timings
from ..llm import get_llm
from ..orchestrator import Event, Orchestrator
from ..tts import get_tts

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_PAGE = Path(__file__).resolve().parent / "static" / "index.html"

# Shared, lazily-built engines (cheap to share; history stays per-connection).
_engines_lock = threading.Lock()
_shared = {"llm": None, "tts": None}


def _engines():
    with _engines_lock:
        if _shared["llm"] is None:
            _shared["llm"] = get_llm()
        if _shared["tts"] is None:
            _shared["tts"] = get_tts()
    return _shared["llm"], _shared["tts"]


# ---------------------------------------------------------------------------
# Minimal WebSocket framing (RFC 6455, text frames + close).
# ---------------------------------------------------------------------------

def _ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def _ws_send(sock, message: str) -> None:
    data = message.encode("utf-8")
    header = bytearray([0x81])  # FIN + text opcode
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    sock.sendall(bytes(header) + data)


def _ws_recv(rfile):
    """Read one client frame. Returns (opcode, payload bytes) or None on close."""
    hdr = rfile.read(2)
    if len(hdr) < 2:
        return None
    b1, b2 = hdr[0], hdr[1]
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b"\x00\x00\x00\x00"
    payload = bytearray(rfile.read(length))
    if masked:
        for i in range(length):
            payload[i] ^= mask[i % 4]
    return opcode, bytes(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotControl/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- HTTP ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/ws":
            return self._handle_ws()
        if parsed.path in ("/", "/index.html"):
            return self._serve_page()
        if parsed.path == "/api/config":
            return self._serve_json(self._config_dict())
        if parsed.path == "/audio":
            return self._serve_audio(parse_qs(parsed.query))
        self.send_error(404, "not found")

    def _serve_page(self):
        body = _PAGE.read_bytes() if _PAGE.exists() else b"<h1>robot control</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_audio(self, qs):
        paths = qs.get("path", [])
        if not paths:
            return self.send_error(400, "missing path")
        path = Path(paths[0])
        # Only serve wavs from temp dirs we created (prefix guard).
        if not (path.is_file() and path.suffix == ".wav" and "robot-" in str(path)):
            return self.send_error(403, "forbidden")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _config_dict(self):
        return {
            "stt_backend": config.stt_backend,
            "llm_backend": config.llm_backend,
            "llm_model": config.llm_model,
            "tts_backend": config.tts_backend,
            "tts_streaming": config.tts_streaming,
        }

    # -- WebSocket -------------------------------------------------------

    def _handle_ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self.send_error(400, "expected websocket upgrade")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept_key(key))
        self.end_headers()

        sock = self.connection
        llm, tts = _engines()
        orch = Orchestrator(None, llm, tts, config.system_prompt, player=None)

        send_lock = threading.Lock()

        def on_event(ev: Event):
            with send_lock:
                try:
                    _ws_send(sock, json.dumps({"type": ev.type, **ev.data}))
                except OSError:
                    pass

        orch.subscribe(on_event)
        _ws_send(sock, json.dumps({"type": "ready", **self._config_dict()}))

        try:
            while True:
                frame = _ws_recv(self.rfile)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # close
                    break
                if opcode in (0x9,):  # ping -> pong
                    continue
                if opcode != 0x1:
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except ValueError:
                    continue
                self._dispatch(orch, msg, on_event)
        except OSError:
            pass

    def _dispatch(self, orch: Orchestrator, msg: dict, on_event):
        mtype = msg.get("type")
        if mtype == "prompt":
            text = (msg.get("text") or "").strip()
            if not text:
                return
            # Run the turn in a thread so we keep reading (e.g. future abort).
            def run():
                t = Timings()
                try:
                    orch.respond(text, t, play=False)
                except Exception as exc:  # noqa: BLE001
                    on_event(Event("error", {"message": str(exc)}))
            threading.Thread(target=run, daemon=True).start()
        elif mtype == "ping":
            on_event(Event("pong", {}))


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or config.server_host
    port = port or config.server_port
    httpd = ThreadingHTTPServer((host, port), Handler)
    shown = host if host not in ("0.0.0.0", "") else "localhost"
    print(f"robot control server on http://{shown}:{port}  (Ctrl-C to stop)")
    print(f"  WS: ws://{shown}:{port}/ws   config: http://{shown}:{port}/api/config")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
        httpd.shutdown()


if __name__ == "__main__":
    serve()
