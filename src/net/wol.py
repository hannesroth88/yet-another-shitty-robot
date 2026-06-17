"""Networking helpers for the fleet (Phase 2).

Wake-on-LAN + readiness probing for the on-demand GPU box (Gaming PC). Kept tiny
and stdlib-only so it runs on any orchestrator host (Mac/NUC). The routing logic
that *uses* this lives in :mod:`src.llm.routed_llm`; this module only knows how to
poke a host awake and tell whether a service is ready.
"""
from __future__ import annotations

import socket
import subprocess
import time
import urllib.request


def _normalize_mac(mac: str) -> bytes:
    hexstr = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(hexstr) != 12:
        raise ValueError(f"invalid MAC address: {mac!r}")
    return bytes.fromhex(hexstr)


def build_magic_packet(mac: str) -> bytes:
    """Construct a Wake-on-LAN magic packet: 6x 0xFF then the MAC 16 times."""
    mac_bytes = _normalize_mac(mac)
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Broadcast a WoL magic packet to wake the target host."""
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    """True if a TCP connection to host:port succeeds within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ollama_ready(base_url: str, timeout: float = 2.0) -> bool:
    """Port-open is not enough: probe ``/api/tags`` so we know a model can load."""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_until_ready(
    host: str,
    port: int,
    base_url: str | None = None,
    timeout_s: float = 30.0,
    poll_s: float = 1.0,
) -> bool:
    """Poll until host:port is open (and, if ``base_url`` given, Ollama answers).

    Returns True when ready, False if ``timeout_s`` elapses first.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if port_open(host, port, timeout=min(poll_s, 2.0)):
            if base_url is None or ollama_ready(base_url):
                return True
        time.sleep(poll_s)
    return False


def wake_and_wait(
    mac: str,
    host: str,
    port: int,
    base_url: str | None = None,
    broadcast: str = "255.255.255.255",
    timeout_s: float = 30.0,
) -> bool:
    """Send a magic packet and wait for readiness. Returns True if the box came up."""
    if not mac:
        return False
    send_magic_packet(mac, broadcast=broadcast)
    return wait_until_ready(host, port, base_url=base_url, timeout_s=timeout_s)


def suspend_over_ssh(ssh_target: str) -> bool:
    """Best-effort idle suspend of the GPU box via SSH (``systemctl suspend``).

    ``ssh_target`` is e.g. ``user@gaming-pc``. Returns True on a zero exit code.
    No-op (False) if not configured.
    """
    if not ssh_target:
        return False
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             ssh_target, "sudo systemctl suspend"],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False
