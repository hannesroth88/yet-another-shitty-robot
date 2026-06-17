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
    stt_language: str = os.environ.get("STT_LANGUAGE", "de") or None  # type: ignore[assignment]

    # LLM
    llm_backend: str = _get("LLM_BACKEND", "ollama")
    ollama_host: str = _get("OLLAMA_HOST", "http://localhost:11434")
    llm_model: str = _get("LLM_MODEL", "llama3.2:latest")
    system_prompt: str = _get(
        "SYSTEM_PROMPT",
        "Du bist ein knapper, freundlicher Sprachassistent. Antworte immer auf "
        "Deutsch und halte deine Antworten kurz und gut vorlesbar. Verwende kein "
        "Markdown, keine Listen und keine Emojis.",
    )

    # TTS
    tts_backend: str = _get("TTS_BACKEND", "piper")
    say_voice: str = _get("SAY_VOICE", "Anna")
    piper_bin: str = _get("PIPER_BIN", "piper")
    piper_voice: str = _get("PIPER_VOICE", "voices/de_DE-thorsten-high.onnx")
    # Kokoro German "Martin" (ONNX). Files from Godelaune/Kokoro-82M-ONNX-German-Martin.
    kokoro_model: str = _get("KOKORO_MODEL", "voices/kokoro-martin.onnx")
    kokoro_voices: str = _get("KOKORO_VOICES", "voices/voices-martin.npz")
    kokoro_voice: str = _get("KOKORO_VOICE", "martin")
    kokoro_speed: float = float(_get("KOKORO_SPEED", "1.0"))
    kokoro_lang: str = _get("KOKORO_LANG", "de")

    # Qwen3-TTS (quality/streaming-oriented backend; optional heavy deps)
    qwen3_model: str = _get("QWEN3_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    qwen3_device_map: str = _get("QWEN3_DEVICE_MAP", "auto")
    qwen3_dtype: str = _get("QWEN3_DTYPE", "auto")
    qwen3_attn_implementation: str = _get("QWEN3_ATTN_IMPLEMENTATION", "auto")
    qwen3_mode: str = _get("QWEN3_MODE", "custom")  # custom | clone
    qwen3_language: str = _get("QWEN3_LANGUAGE", "German")
    qwen3_speaker: str = _get("QWEN3_SPEAKER", "Ryan")
    qwen3_instruct: str = _get("QWEN3_INSTRUCT", "")
    qwen3_ref_audio: str = _get("QWEN3_REF_AUDIO", "voices/robot-ref.wav")
    qwen3_ref_text: str = _get("QWEN3_REF_TEXT", "")
    qwen3_ref_text_file: str = _get("QWEN3_REF_TEXT_FILE", "")
    # Apple-Silicon MLX path (fast + ICL voice clone). Base model required.
    qwen3_mlx_model: str = _get("QWEN3_MLX_MODEL", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit")

    # Voice effect (DSP applied on top of any TTS backend)
    tts_effect: str = _get("TTS_EFFECT", "none")  # none | robot
    robot_phase_strength: float = float(_get("ROBOT_PHASE_STRENGTH", "1.0"))
    robot_phase_hop: int = int(_get("ROBOT_PHASE_HOP", "256"))
    robot_phase_frame: int = int(_get("ROBOT_PHASE_FRAME", "2048"))
    robot_phase_lowpass_hz: float = float(_get("ROBOT_PHASE_LOWPASS_HZ", "3500"))
    robot_phase_formant: float = float(_get("ROBOT_PHASE_FORMANT", "1.0"))
    robot_carrier_hz: float = float(_get("ROBOT_CARRIER_HZ", "55"))
    robot_mix: float = float(_get("ROBOT_MIX", "0.2"))
    robot_bits: int = int(_get("ROBOT_BITS", "7"))
    robot_rate_div: int = int(_get("ROBOT_RATE_DIV", "1"))
    robot_tremolo_hz: float = float(_get("ROBOT_TREMOLO_HZ", "0"))
    robot_tremolo_depth: float = float(_get("ROBOT_TREMOLO_DEPTH", "0"))
    robot_comb_ms: float = float(_get("ROBOT_COMB_MS", "1.2"))
    robot_comb_gain: float = float(_get("ROBOT_COMB_GAIN", "0.3"))


config = Config()
