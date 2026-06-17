"""Control server (Phase 1 + Phase 4): one HTTP + WebSocket entry point with a
**shared orchestrator and a broadcast hub**.

Why a hub: the robot is one face + one body driven by one pipeline. The phone
(browser avatar) drives input; every connected client -- the phone face AND the
ESP32 body controller -- subscribes to the *same* event stream. So a turn the
phone starts lights the ESP32's status LED and animates the phone's mouth at
once. Single robot, single user: one shared :class:`Orchestrator`, a set of WS
clients, events fanned out to all.

Clients (any of them) can send:
* ``{"type":"prompt","text":"…"}``  -- a typed prompt.
* a **binary** WS frame                -- recorded mic audio (webm/opus); the
  server transcribes it (ffmpeg -> wav -> STT) and runs the turn.
* ``{"type":"ping"}``                  -- liveness.

The ESP32 just connects and ignores everything except ``phase`` events.

Endpoints:
* ``GET /``                  -> the avatar web face.
* ``GET /static/<file>``     -> face.js / app.js / styles.css.
* ``GET /api/config``        -> backend/model selection.
* ``GET /audio?path=…``      -> a synthesized wav (for the phone to play).
* ``GET /ws``                -> the broadcast WebSocket.

Run:  python -m src.server.app
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import config
from ..latency import Timings
from ..llm import get_llm
from ..orchestrator import Event, Orchestrator
from ..stt import get_stt
from ..tts import get_tts

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_STATIC = Path(__file__).resolve().parent / "static"
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
           ".css": "text/css", ".svg": "image/svg+xml",
           ".json": "application/manifest+json"}


class Hub:
    """Shared orchestrator + the set of connected WS clients (broadcast fan-out)."""

    def __init__(self) -> None:
        self._clients: set["Client"] = set()
        self._clients_lock = threading.Lock()
        self._orch: Orchestrator | None = None
        self._stt = None
        self._build_lock = threading.Lock()
        self._turn_lock = threading.Lock()  # one turn at a time (single robot)

    # -- clients ---------------------------------------------------------

    def add(self, client: "Client") -> None:
        with self._clients_lock:
            self._clients.add(client)

    def remove(self, client: "Client") -> None:
        with self._clients_lock:
            self._clients.discard(client)

    def broadcast(self, ev: Event) -> None:
        msg = json.dumps({"type": ev.type, **ev.data})
        with self._clients_lock:
            clients = list(self._clients)
        for c in clients:
            c.send(msg)

    # -- orchestrator ----------------------------------------------------

    def orchestrator(self) -> Orchestrator:
        with self._build_lock:
            if self._orch is None:
                orch = Orchestrator(None, get_llm(), get_tts(),
                                    config.system_prompt, player=None)
                orch.subscribe(self.broadcast)
                self._orch = orch
        return self._orch

    def _ensure_stt(self) -> None:
        if self._stt is None:
            with self._build_lock:
                if self._stt is None:
                    self._stt = get_stt()
            self.orchestrator().stt = self._stt

    # -- turns -----------------------------------------------------------

    def run_prompt(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self._turn_lock.acquire(blocking=False):
            self.broadcast(Event("busy", {"message": "still working on the last turn"}))
            return
        try:
            t = Timings()
            self.orchestrator().respond(text, t, play=False)
        except Exception as exc:  # noqa: BLE001
            self.broadcast(Event("error", {"message": str(exc)}))
        finally:
            self._turn_lock.release()

    def run_audio(self, blob: bytes) -> None:
        if not self._turn_lock.acquire(blocking=False):
            self.broadcast(Event("busy", {"message": "still working on the last turn"}))
            return
        try:
            self._ensure_stt()
            wav = _to_wav(blob)
            if wav is None:
                self.broadcast(Event("error", {"message": "audio decode failed"}))
                return
            t = Timings()
            try:
                text = self.orchestrator().transcribe(wav, t)
            finally:
                Path(wav).unlink(missing_ok=True)
            if not text:
                self.broadcast(Event("phase", {"phase": "inactive"}))
                return
            self.orchestrator().respond(text, t, play=False)
        except Exception as exc:  # noqa: BLE001
            self.broadcast(Event("error", {"message": str(exc)}))
        finally:
            self._turn_lock.release()


HUB = Hub()


def _to_wav(blob: bytes) -> str | None:
    """Convert a recorded audio blob (webm/opus/mp4) to 16k mono wav via ffmpeg."""
    src = Path(tempfile.mktemp(suffix=".bin", prefix="robot-mic-"))
    out = Path(tempfile.mktemp(suffix=".wav", prefix="robot-mic-"))
    try:
        src.write_bytes(blob)
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(src), "-ac", "1", "-ar", str(config.sample_rate), str(out)],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            return None
        return str(out)
    except Exception:
        return None
    finally:
        src.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Minimal WebSocket framing (RFC 6455): text + binary (with fragmentation).
# ---------------------------------------------------------------------------

def _ws_accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()


def _ws_send(sock, message: str) -> None:
    data = message.encode("utf-8")
    header = bytearray([0x81])  # FIN + text
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


def _ws_read_frame(rfile):
    """Read one frame -> (fin, opcode, payload) or None on close/EOF."""
    hdr = rfile.read(2)
    if len(hdr) < 2:
        return None
    b1, b2 = hdr[0], hdr[1]
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b"\x00\x00\x00\x00"
    payload = bytearray()
    remaining = length
    while remaining > 0:
        chunk = rfile.read(remaining)
        if not chunk:
            break
        payload += chunk
        remaining -= len(chunk)
    if masked:
        for i in range(len(payload)):
            payload[i] ^= mask[i % 4]
    return fin, opcode, bytes(payload)


class Client:
    """One connected WebSocket peer (phone face or ESP32 body)."""

    def __init__(self, sock) -> None:
        self.sock = sock
        self._lock = threading.Lock()

    def send(self, message: str) -> None:
        with self._lock:
            try:
                _ws_send(self.sock, message)
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "RobotControl/2.0"

    def log_message(self, *a):
        pass

    # -- HTTP ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/ws":
            return self._handle_ws()
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/config":
            return self._serve_json(self._config_dict())
        if path == "/audio":
            return self._serve_audio(parse_qs(parsed.query))
        self.send_error(404, "not found")

    def _serve_static(self, name: str):
        # Prevent path traversal; only serve known files in the static dir.
        target = (_STATIC / name).resolve()
        if not str(target).startswith(str(_STATIC.resolve())) or not target.is_file():
            return self.send_error(404, "not found")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CTYPES.get(target.suffix, "application/octet-stream"))
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
        if not (path.is_file() and path.suffix == ".wav" and "robot-" in str(path)):
            return self.send_error(403, "forbidden")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
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

        client = Client(self.connection)
        HUB.add(client)
        client.send(json.dumps({"type": "ready", **self._config_dict()}))

        audio_buf = bytearray()
        try:
            while True:
                frame = _ws_read_frame(self.rfile)
                if frame is None:
                    break
                fin, opcode, payload = frame
                if opcode == 0x8:  # close
                    break
                if opcode in (0x9, 0xA):  # ping/pong
                    continue
                if opcode == 0x2 or (opcode == 0x0 and audio_buf is not None):
                    audio_buf += payload  # binary (audio), possibly fragmented
                    if fin:
                        blob = bytes(audio_buf)
                        audio_buf = bytearray()
                        threading.Thread(target=HUB.run_audio, args=(blob,),
                                         daemon=True).start()
                    continue
                if opcode == 0x1:
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                    except ValueError:
                        continue
                    self._dispatch(client, msg)
        except OSError:
            pass
        finally:
            HUB.remove(client)

    def _dispatch(self, client: Client, msg: dict):
        mtype = msg.get("type")
        if mtype == "prompt":
            threading.Thread(target=HUB.run_prompt, args=(msg.get("text", ""),),
                             daemon=True).start()
        elif mtype == "ping":
            client.send(json.dumps({"type": "pong"}))


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or config.server_host
    port = port or config.server_port
    httpd = ThreadingHTTPServer((host, port), Handler)
    shown = host if host not in ("0.0.0.0", "") else "localhost"
    print(f"robot control server on http://{shown}:{port}  (Ctrl-C to stop)")
    print(f"  phone face : open http://<this-host-ip>:{port} on the Pixel")
    print(f"  ESP32 body : connect to ws://<this-host-ip>:{port}/ws")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
        httpd.shutdown()


if __name__ == "__main__":
    serve()
