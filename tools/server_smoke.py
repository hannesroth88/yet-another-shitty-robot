"""Smoke test for the Phase 1 control server: WS prompt -> streamed events.

Starts the server in-process, connects a minimal stdlib WebSocket client, sends
one prompt, and asserts we receive phase + assistant_delta + assistant_end +
latency events. No third-party deps.

Run:  TTS_BACKEND=say TTS_EFFECT=none .venv/bin/python -m tools.server_smoke
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time

os.environ.setdefault("TTS_BACKEND", "say")
os.environ.setdefault("TTS_EFFECT", "none")

from http.server import ThreadingHTTPServer  # noqa: E402

from src.server.app import Handler  # noqa: E402


def ws_send(sock, message: str) -> None:
    data = message.encode("utf-8")
    # Client frames MUST be masked.
    mask = os.urandom(4)
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    else:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def ws_recv(sock) -> str | None:
    hdr = _recvn(sock, 2)
    if not hdr:
        return None
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recvn(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recvn(sock, 8))[0]
    return _recvn(sock, length).decode("utf-8")


def _recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def main() -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode())
    resp = _recvn_until_headers(sock)
    assert "101" in resp.split("\r\n")[0], f"no upgrade: {resp!r}"

    seen: set[str] = set()
    deltas: list[str] = []
    ws_send(sock, json.dumps({"type": "prompt", "text": "Sage Hallo in einem Wort."}))

    sock.settimeout(60)
    deadline = time.time() + 60
    while time.time() < deadline:
        raw = ws_recv(sock)
        if raw is None:
            break
        msg = json.loads(raw)
        seen.add(msg["type"])
        if msg["type"] == "assistant_delta":
            deltas.append(msg["text"])
        if msg["type"] == "latency":
            break

    httpd.shutdown()
    print("event types seen:", sorted(seen))
    print("assistant text:", "".join(deltas).strip()[:120])
    required = {"ready", "phase", "assistant_delta", "assistant_end", "latency"}
    missing = required - seen
    if missing:
        print("FAIL: missing events:", missing)
        return 1
    print("OK: control server streamed all required events")
    return 0


def _recvn_until_headers(sock) -> str:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8", "replace")


if __name__ == "__main__":
    raise SystemExit(main())
