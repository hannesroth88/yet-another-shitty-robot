"""TTS interface + factory.

Phase 0 default is macOS `say` (zero-dependency, works today). `piper` is the
cross-platform fleet default and is selected via TTS_BACKEND=piper. Optional
quality backends: `kokoro` (CPU-friendly German) and `qwen3` (heavier, best
naturalness on capable GPU/Apple-Silicon hosts). See AGENTS.md.
"""
from __future__ import annotations

from typing import Protocol

from ..config import config


class TTS(Protocol):
    def synthesize(self, text: str, out_path: str) -> None:
        """Write spoken audio for `text` to `out_path` (wav)."""
        ...


def _make_backend() -> TTS:
    if config.tts_backend == "say":
        from .say_tts import SayTTS
        return SayTTS()
    if config.tts_backend == "piper":
        from .piper_tts import PiperTTS
        return PiperTTS()
    if config.tts_backend == "kokoro":
        from .kokoro_tts import KokoroTTS
        return KokoroTTS()
    if config.tts_backend == "qwen3":
        from .qwen3_tts import Qwen3TTS
        return Qwen3TTS()
    if config.tts_backend in ("qwen3-mlx", "qwen3_mlx"):
        from .qwen3_mlx_tts import Qwen3MlxTTS
        return Qwen3MlxTTS()
    if config.tts_backend == "worker":
        from .worker_tts import WorkerTTS
        return WorkerTTS(config.tts_worker_backend)
    raise ValueError(f"Unknown TTS_BACKEND: {config.tts_backend}")


def get_tts() -> TTS:
    backend = _make_backend()
    # Optional voice effect, applied on top of any backend (engine-agnostic).
    if config.tts_effect == "robot":
        from .robot_tts import RobotTTS
        return RobotTTS(backend)
    if config.tts_effect not in ("", "none"):
        raise ValueError(f"Unknown TTS_EFFECT: {config.tts_effect}")
    return backend
