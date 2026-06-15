"""Central config. Loads .env (if present) then environment, with defaults.

Placement/model choices are config, never hardcoded (see AGENTS.md).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency)."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _get(key: str, default: str) -> str:
    val = os.environ.get(key, default)
    return val if val != "" else default


@dataclass(frozen=True)
class Config:
    # Audio
    audio_input_device: str = _get("AUDIO_INPUT_DEVICE", "1")
    sample_rate: int = int(_get("SAMPLE_RATE", "16000"))
    max_record_seconds: int = int(_get("MAX_RECORD_SECONDS", "30"))

    # STT
    stt_backend: str = _get("STT_BACKEND", "faster-whisper")
    stt_model: str = _get("STT_MODEL", "base")
    stt_compute_type: str = _get("STT_COMPUTE_TYPE", "int8")
    stt_language: str = os.environ.get("STT_LANGUAGE", "") or None  # type: ignore[assignment]

    # LLM
    llm_backend: str = _get("LLM_BACKEND", "ollama")
    ollama_host: str = _get("OLLAMA_HOST", "http://localhost:11434")
    llm_model: str = _get("LLM_MODEL", "llama3.2:latest")
    system_prompt: str = _get(
        "SYSTEM_PROMPT",
        "You are a concise, friendly voice assistant. Keep replies short and speakable.",
    )

    # TTS
    tts_backend: str = _get("TTS_BACKEND", "say")
    say_voice: str = _get("SAY_VOICE", "Samantha")
    piper_bin: str = _get("PIPER_BIN", "piper")
    piper_voice: str = _get("PIPER_VOICE", "voices/en_US-amy-medium.onnx")


config = Config()
