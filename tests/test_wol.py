"""Unit tests for Wake-on-LAN packet building + readiness probing (Phase 2).

Run:  .venv/bin/python -m unittest tests.test_wol -v
"""
from __future__ import annotations

import socket
import threading
import unittest

from src.net import wol


class MagicPacketTest(unittest.TestCase):
    def test_packet_shape(self) -> None:
        pkt = wol.build_magic_packet("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(pkt), 6 + 16 * 6)  # 102 bytes
        self.assertEqual(pkt[:6], b"\xff" * 6)
        self.assertEqual(pkt[6:12], bytes.fromhex("AABBCCDDEEFF"))
        # MAC repeated 16x
        self.assertEqual(pkt[6:], bytes.fromhex("AABBCCDDEEFF") * 16)

    def test_separator_variants(self) -> None:
        a = wol.build_magic_packet("aa-bb-cc-dd-ee-ff")
        b = wol.build_magic_packet("aabb.ccdd.eeff")
        c = wol.build_magic_packet("AABBCCDDEEFF")
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_invalid_mac(self) -> None:
        with self.assertRaises(ValueError):
            wol.build_magic_packet("not-a-mac")


class PortProbeTest(unittest.TestCase):
    def test_port_open_true_then_false(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        stop = threading.Event()

        def accept_loop():
            srv.settimeout(0.5)
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                    conn.close()
                except OSError:
                    pass

        threading.Thread(target=accept_loop, daemon=True).start()
        try:
            self.assertTrue(wol.port_open("127.0.0.1", port, timeout=1.0))
        finally:
            stop.set()
            srv.close()
        # A port nobody is listening on should be closed.
        self.assertFalse(wol.port_open("127.0.0.1", port, timeout=0.3))

    def test_wait_until_ready_times_out_fast(self) -> None:
        # Unused high port; should give up within the timeout budget.
        ok = wol.wait_until_ready("127.0.0.1", 1, timeout_s=0.5, poll_s=0.2)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
