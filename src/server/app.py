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
import logging
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
from ..orchestrator import AudioSink, Event, Orchestrator
from ..stt import get_stt
from ..stt.streaming import SttEvent, StreamingSTT
from ..tts import get_tts

log = logging.getLogger("robot.server")

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
        # Conversation mode (ADR 0003): per-client streaming STT + stop-words.
        self._streams: dict["Client", StreamingSTT] = {}
        self._stop_words = [w.strip().lower() for w in config.stop_words.split(",") if w.strip()]
        self._turn_thread: threading.Thread | None = None
        self._barge_pending = False  # set when a client signals a real barge-in

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

    # -- conversation mode: continuous mic + VAD (ADR 0003) -------------

    def _looks_like_stop(self, text: str) -> bool:
        t = " " + text.lower().strip(" .,!?;:“”\"'") + " "
        return any((" " + w + " ") in t or t.strip() == w for w in self._stop_words)

    def start_stream(self, client: "Client") -> None:
        """Begin a continuous-mic STT stream for this client."""
        self._ensure_stt()

        def sink(ev: SttEvent) -> None:
            self._on_stt_event(client, ev)

        with self._build_lock:
            old = self._streams.pop(client, None)
        if old is not None:
            old.close()
        stream = StreamingSTT(self._stt, sink)
        with self._build_lock:
            self._streams[client] = stream
        self.broadcast(Event("phase", {"phase": "listening"}))

    def stop_stream(self, client: "Client") -> None:
        with self._build_lock:
            stream = self._streams.pop(client, None)
        if stream is not None:
            stream.close()

    def feed_audio(self, client: "Client", pcm: bytes) -> None:
        with self._build_lock:
            stream = self._streams.get(client)
        if stream is not None:
            stream.feed(pcm)

    def _on_stt_event(self, client: "Client", ev: SttEvent) -> None:
        orch = self.orchestrator()
        if ev.type == "speech_start":
            self.broadcast(Event("phase", {"phase": "hearing"}))
            return
        if ev.type == "interim":
            self.broadcast(Event("interim", {"text": ev.text}))
            log.info("STT …  %r", ev.text)
            # Stop-word while the robot is mid-turn -> abort immediately.
            if orch.phase.value in ("thinking", "speaking") and self._looks_like_stop(ev.text):
                log.info("stop-word interim -> cancel")
                orch.cancel()
            return
        if ev.type == "speech_drop":
            log.info("STT speech dropped (too short / no text)")
            if orch.phase.value not in ("thinking", "speaking"):
                self.broadcast(Event("phase", {"phase": "listening"}))
            return
        if ev.type == "final":
            if not ev.text:
                return
            log.info("STT ◀  %r", ev.text)
            if self._looks_like_stop(ev.text):
                log.info("stop-word final -> cancel + listen")
                self._barge_pending = False
                orch.cancel()
                self.broadcast(Event("phase", {"phase": "listening"}))
                return
            # If the robot is mid-turn, only honour this as a barge-in turn when
            # the client explicitly signalled one. Otherwise it is almost
            # certainly the robot's own voice echoing back into the mic -- drop
            # it so the robot does not talk to itself.
            if orch.phase.value in ("thinking", "speaking"):
                if not self._barge_pending:
                    log.info("ignoring final during %s (no barge signal; echo?)",
                             orch.phase.value)
                    return
                log.info("barge-in turn: %r", ev.text)
                self._barge_pending = False
                orch.cancel()
            self._start_turn(client, ev.text)

    def _start_turn(self, client: "Client", text: str) -> None:
        def run() -> None:
            # The just-cancelled turn may still be releasing the lock; wait
            # briefly rather than dropping a barge-in utterance.
            if not self._turn_lock.acquire(timeout=2.0):
                return
            try:
                # Pause this client's VAD intake while the robot speaks so its
                # own TTS doesn't echo back as speech; the client-side barge-in
                # detector re-enables streaming when the user truly interrupts.
                self.broadcast(Event("heard_text", {"text": text}))
                t = Timings()
                sink = AudioSink(
                    on_start=lambda sr: self._pcm_start(client, sr),
                    on_pcm=lambda b: client.send_binary(b),
                    on_done=lambda: client.send(json.dumps({"type": "tts_done"})),
                )
                self.orchestrator().respond(text, t, play=False, audio_sink=sink)
            except Exception as exc:  # noqa: BLE001
                self.broadcast(Event("error", {"message": str(exc)}))
            finally:
                self._turn_lock.release()

        self._turn_thread = threading.Thread(target=run, name="turn", daemon=True)
        self._turn_thread.start()

    def _pcm_start(self, client: "Client", sample_rate: int) -> None:
        client.send(json.dumps({"type": "tts_start", "sample_rate": sample_rate}))

    def barge_in(self, client: "Client") -> None:
        """Client detected the user talking over the robot: abort + listen."""
        log.info("barge-in signalled by client -> cancel")
        self._barge_pending = True
        self.orchestrator().cancel()
        self.broadcast(Event("phase", {"phase": "hearing"}))

    def abort(self, client: "Client") -> None:
        self.orchestrator().cancel()
        self.broadcast(Event("phase", {"phase": "listening"}))

    # -- orchestrator ----------------------------------------------------

    def orchestrator(self) -> Orchestrator:
        with self._build_lock:
            if self._orch is None:
                orch = Orchestrator(None, get_llm(), get_tts(thread_safe=True),
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
    _ws_send_frame(sock, 0x1, message.encode("utf-8"))


def _ws_send_binary(sock, data: bytes) -> None:
    _ws_send_frame(sock, 0x2, data)


def _ws_send_frame(sock, opcode: int, data: bytes) -> None:
    header = bytearray([0x80 | opcode])  # FIN + opcode
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

    def send_binary(self, data: bytes) -> None:
        with self._lock:
            try:
                _ws_send_binary(self.sock, data)
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
            "conversation_mode": config.conversation_mode,
            "conv_sample_rate": config.conv_sample_rate,
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
        streaming_pcm = False
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
                if opcode == 0x2 or (opcode == 0x0 and not streaming_pcm and audio_buf is not None):
                    if streaming_pcm:
                        # Conversation mode: raw PCM16 frames -> per-client VAD.
                        HUB.feed_audio(client, bytes(payload))
                        continue
                    audio_buf += payload  # legacy webm blob, possibly fragmented
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
                    mtype = msg.get("type")
                    if mtype == "audio_start":
                        streaming_pcm = True
                        HUB.start_stream(client)
                        continue
                    if mtype == "audio_stop":
                        streaming_pcm = False
                        HUB.stop_stream(client)
                        continue
                    self._dispatch(client, msg)
        except OSError:
            pass
        finally:
            HUB.stop_stream(client)
            HUB.remove(client)

    def _dispatch(self, client: Client, msg: dict):
        mtype = msg.get("type")
        if mtype == "prompt":
            threading.Thread(target=HUB.run_prompt, args=(msg.get("text", ""),),
                             daemon=True).start()
        elif mtype == "barge_in":
            HUB.barge_in(client)
        elif mtype == "abort":
            HUB.abort(client)
        elif mtype == "ping":
            client.send(json.dumps({"type": "pong"}))


def _ensure_cert(cert_path: Path, key_path: Path) -> bool:
    """Make a self-signed cert/key via openssl if missing. Returns True if usable."""
    if cert_path.is_file() and key_path.is_file():
        return True
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key_path), "-out", str(cert_path),
             "-days", "3650", "-subj", "/CN=robot.local",
             "-addext", "subjectAltName=DNS:robot.local,DNS:localhost,IP:127.0.0.1"],
            check=True, capture_output=True, timeout=30,
        )
        print(f"  generated self-signed cert -> {cert_path}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not generate TLS cert ({exc}); falling back to HTTP")
        return False


def serve(host: str | None = None, port: int | None = None) -> None:
    from ..logsetup import setup_logging
    setup_logging()
    host = host or config.server_host
    port = port or config.server_port
    httpd = ThreadingHTTPServer((host, port), Handler)

    scheme = "http"
    if config.server_tls:
        cert = (Path.cwd() / config.server_tls_cert)
        key = (Path.cwd() / config.server_tls_key)
        if _ensure_cert(cert, key):
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert), str(key))
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"

    shown = host if host not in ("0.0.0.0", "") else "localhost"
    print(f"robot control server on {scheme}://{shown}:{port}  (Ctrl-C to stop)")

    # Pre-warm ALL pipeline stages in the background so the first real turn
    # isn't cold.  Each stage is independent — failures are logged, not fatal.
    def _prewarm():
        import tempfile, os

        # 1. TTS -- load model / start worker process.
        try:
            orch = HUB.orchestrator()
            warm = getattr(orch.tts, "warm", None)
            if callable(warm):
                print("  [prewarm] TTS: loading model...")
                warm()
                print("  [prewarm] TTS: ready.")
            else:
                print(f"  [prewarm] TTS: {config.tts_backend} (no warm() needed).")
        except Exception as exc:  # noqa: BLE001
            print(f"  [prewarm] TTS: skipped — {exc}")

        # 2. STT -- loads/downloads the model (e.g. Parakeet from HuggingFace,
        #    faster-whisper from HF, etc.).  This is the most expensive cold-start
        #    because the first call triggers the HF download.
        try:
            print(f"  [prewarm] STT: loading {config.stt_backend} model...")
            HUB._ensure_stt()
            # Warm the JIT with a short silent clip so the first real turn is
            # fast (~150ms) instead of paying one-time graph compilation.
            try:
                import wave
                wp = tempfile.mktemp(suffix=".wav", prefix="robot-stt-warm-")
                with wave.open(wp, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                    w.writeframes(b"\x00" * 16000 * 2)  # 1s silence
                HUB._stt.transcribe(wp)
                Path(wp).unlink(missing_ok=True)
            except Exception:
                pass
            print("  [prewarm] STT: ready.")
        except Exception as exc:  # noqa: BLE001
            print(f"  [prewarm] STT: skipped — {exc}")

        # 3. LLM -- send a minimal no-op generation so Ollama loads the model
        #    weights into RAM/VRAM now rather than on the first real turn.
        try:
            print(f"  [prewarm] LLM: pinging {config.llm_backend}/{config.llm_model}...")
            orch = HUB.orchestrator()
            # Consume and discard one token — enough to force model load.
            for _ in orch.llm.stream([{"role": "user", "content": "hi"}]):
                break
            print("  [prewarm] LLM: ready.")
        except Exception as exc:  # noqa: BLE001
            print(f"  [prewarm] LLM: skipped — {exc}")

    threading.Thread(target=_prewarm, daemon=True).start()
    print(f"  phone face : open {scheme}://<this-host-ip>:{port} on the Pixel")
    if scheme == "http":
        print("  note       : mic/camera work on localhost, but the phone (LAN IP)")
        print("               needs HTTPS -> set SERVER_TLS=1 (secure context).")
    else:
        print("  note       : accept the self-signed cert warning once on the phone.")
    print(f"  ESP32 body : connect to ws{'s' if scheme=='https' else ''}://<this-host-ip>:{port}/ws")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
        httpd.shutdown()


if __name__ == "__main__":
    serve()
