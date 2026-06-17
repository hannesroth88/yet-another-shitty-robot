"""Fleet host presets (Phase 2).

Declarative description of which host runs what, so placement is *config*, not
code (AGENTS.md design principle). The orchestrator never imports this directly;
it's a reference + a source for `.env` snippets and the web face's environment
dropdown. Fill in real MACs/IPs as hosts come online.

A preset answers: "for environment X, what are the STT/LLM/TTS endpoints and the
Wake-on-LAN target?" Selecting a preset = exporting its env (see ``as_env``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostPreset:
    name: str
    description: str
    env: dict[str, str] = field(default_factory=dict)


# Fill MACs/IPs as hosts are provisioned. Models reflect the VRAM reality check
# in AGENTS.md (8 GB GPU -> 7-8B @ Q4; NUC/Mac -> 3B fallback).
PRESETS: dict[str, HostPreset] = {
    "mac-local": HostPreset(
        name="Mac M1 (all-local)",
        description="Everything on the dev Mac. Phase 0/1 baseline.",
        env={
            "STT_BACKEND": "faster-whisper",
            "LLM_BACKEND": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "LLM_MODEL": "llama3.2:latest",
            "TTS_BACKEND": "qwen3-mlx",
            "TTS_STREAMING": "1",
        },
    ),
    "nuc-gpu": HostPreset(
        name="NUC orchestrator + Gaming-PC GPU",
        description=(
            "Orchestrator + STT/TTS on the always-on NUC; LLM on the on-demand "
            "Gaming PC GPU (woken via WoL), local fallback when asleep."
        ),
        env={
            "STT_BACKEND": "faster-whisper",
            "TTS_BACKEND": "piper",
            "TTS_STREAMING": "1",
            "LLM_BACKEND": "routed",
            "LLM_PRIMARY_URL": "http://gaming-pc:11434",
            "LLM_PRIMARY_MODEL": "qwen2.5:7b",
            "LLM_FALLBACK_URL": "http://localhost:11434",
            "LLM_FALLBACK_MODEL": "llama3.2:3b",
            "WOL_MAC": "AA:BB:CC:DD:EE:FF",   # <-- set the Gaming PC NIC MAC
            "WOL_HOST": "gaming-pc",
            "WOL_PORT": "11434",
            "WOL_TIMEOUT_S": "30",
            "GPU_IDLE_SUSPEND_MIN": "15",
            "GPU_SUSPEND_SSH": "",            # e.g. user@gaming-pc to auto-suspend
        },
    ),
    "nuc-only": HostPreset(
        name="NUC only (GPU offline)",
        description="Always-on NUC running the small local model; no GPU box.",
        env={
            "STT_BACKEND": "faster-whisper",
            "TTS_BACKEND": "piper",
            "TTS_STREAMING": "1",
            "LLM_BACKEND": "ollama",
            "OLLAMA_HOST": "http://localhost:11434",
            "LLM_MODEL": "llama3.2:3b",
        },
    ),
    "distributed-stt-tts": HostPreset(
        name="Distributed STT/TTS services",
        description=(
            "STT and TTS run as HTTP services on another host (services/*); the "
            "orchestrator calls them over the LAN."
        ),
        env={
            "STT_BACKEND": "http",
            "STT_HTTP_URL": "http://nuc:9000",
            "TTS_BACKEND": "piper",
            "LLM_BACKEND": "routed",
            "LLM_PRIMARY_URL": "http://gaming-pc:11434",
            "LLM_FALLBACK_URL": "http://nuc:11434",
        },
    ),
}


def as_env(preset_key: str) -> str:
    """Render a preset as copy-paste ``.env`` lines."""
    preset = PRESETS[preset_key]
    lines = [f"# {preset.name} -- {preset.description}"]
    lines += [f"{k}={v}" for k, v in preset.env.items()]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(as_env(sys.argv[1]))
    else:
        for key, preset in PRESETS.items():
            print(f"{key:24} {preset.name}")
