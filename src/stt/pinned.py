"""PinnedSTT -- run an MLX STT backend on a single dedicated thread.

MLX/Metal keeps a per-thread GPU stream, so a model loaded on one thread and
evaluated on another raises ``no stream gpu in current thread`` (the recoverable
cousin of the qwen3-mlx segfault). The control server spawns a fresh thread per
turn, which triggers exactly that. This wrapper pins the backend's construction
*and* every ``transcribe()`` onto one dedicated worker thread so MLX is always
touched from the same thread.

Only MLX backends need this; faster-whisper/http are thread-safe and skip it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable


class PinnedSTT:
    def __init__(self, factory: Callable[[], object]) -> None:
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt-mlx")
        self._impl = None
        # Build the model ON the pinned thread (load + ops must share a thread).
        self._ex.submit(self._build, factory).result()

    def _build(self, factory: Callable[[], object]) -> None:
        self._impl = factory()

    def transcribe(self, wav_path: str) -> str:
        assert self._impl is not None
        return self._ex.submit(self._impl.transcribe, wav_path).result()
