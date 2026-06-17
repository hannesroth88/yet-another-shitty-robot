"""Routed LLM backend (Phase 2): remote-GPU primary with local fallback + WoL.

``LLM_BACKEND=routed`` composes two :class:`HttpLLM` endpoints behind the same
``stream`` interface the orchestrator already speaks:

1. If the primary (Gaming PC GPU) port is open -> stream from it.
2. Else send a Wake-on-LAN magic packet and poll up to ``WOL_TIMEOUT_S`` for the
   Ollama port + ``/api/tags`` to come up; if it does -> use the primary.
3. Else fall back to a smaller local model (NUC/Mac) and record the downgrade.
4. An idle timer suspends the GPU box after ``GPU_IDLE_SUSPEND_MIN`` minutes.

The orchestrator is **unchanged** between all-local and GPU-remote: only env
differs (the Phase 2 acceptance criterion).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterator, Optional

from ..config import config
from ..net import wol
from .http_llm import HttpLLM

# Optional sink for routing notes (wake latency, downgrades) -> benchmark notes.
RouteNote = Callable[[str], None]


class _Endpoint(HttpLLM):
    """An HttpLLM pinned to an explicit url/model/format (not the global config)."""

    def __init__(self, url: str, model: str, fmt: str) -> None:
        self.fmt = fmt
        self.model = model
        self.api_key = config.llm_http_api_key
        base = url.rstrip("/")
        self.url = f"{base}/v1/chat/completions" if fmt == "openai" else f"{base}/api/chat"
        self._base = base


class RoutedLLM:
    def __init__(self, on_note: Optional[RouteNote] = None) -> None:
        self.primary = _Endpoint(
            config.llm_primary_url, config.llm_primary_model, config.llm_primary_format
        )
        self.fallback = _Endpoint(
            config.llm_fallback_url, config.llm_fallback_model, config.llm_fallback_format
        )
        self.on_note = on_note
        self._host = config.wol_host
        self._port = config.wol_port
        self._last_use = 0.0
        self._idle_thread_started = False
        self._lock = threading.Lock()

    def _note(self, msg: str) -> None:
        if self.on_note:
            self.on_note(msg)

    def _primary_ready(self) -> bool:
        if wol.port_open(self._host, self._port, timeout=1.0):
            return True
        if not config.wol_mac:
            return False
        self._note(f"waking {self._host} via WoL…")
        start = time.monotonic()
        ok = wol.wake_and_wait(
            config.wol_mac, self._host, self._port,
            base_url=self.primary._base, broadcast=config.wol_broadcast,
            timeout_s=config.wol_timeout_s,
        )
        if ok:
            self._note(f"{self._host} awake after {time.monotonic() - start:.1f}s (COLD)")
        else:
            self._note(f"{self._host} did not wake in {config.wol_timeout_s}s")
        return ok

    def stream(self, messages: list[dict]) -> Iterator[str]:
        use_primary = self._primary_ready()
        endpoint = self.primary if use_primary else self.fallback
        if not use_primary:
            self._note(
                f"downgrade -> local fallback {config.llm_fallback_model}"
            )
        self._touch()
        try:
            yield from endpoint.stream(messages)
        except Exception as exc:  # primary died mid-stream -> one fallback retry
            if endpoint is self.primary:
                self._note(f"primary error ({exc}); retrying on fallback")
                yield from self.fallback.stream(messages)
            else:
                raise

    # -- idle suspend ----------------------------------------------------

    def _touch(self) -> None:
        self._last_use = time.monotonic()
        if config.gpu_suspend_ssh and config.gpu_idle_suspend_min > 0:
            self._ensure_idle_watcher()

    def _ensure_idle_watcher(self) -> None:
        with self._lock:
            if self._idle_thread_started:
                return
            self._idle_thread_started = True
        t = threading.Thread(target=self._idle_loop, name="gpu-idle", daemon=True)
        t.start()

    def _idle_loop(self) -> None:
        idle_s = config.gpu_idle_suspend_min * 60
        while True:
            time.sleep(min(idle_s, 60))
            if self._last_use and (time.monotonic() - self._last_use) > idle_s:
                if wol.port_open(self._host, self._port, timeout=1.0):
                    if wol.suspend_over_ssh(config.gpu_suspend_ssh):
                        self._note(f"{self._host} idle -> suspended")
                self._last_use = 0.0
