"""Ollama LLM backend (streaming chat). Uses stdlib only."""
from __future__ import annotations

import json
import urllib.request
from typing import Iterator

from ..config import config


class OllamaLLM:
    def __init__(self) -> None:
        self.url = f"{config.ollama_host.rstrip('/')}/api/chat"
        self.model = config.llm_model

    def stream(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "think": False,          # disable CoT/thinking mode (Gemma4, Qwen3, etc.)
            "options": {"temperature": 0.7},
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                obj = json.loads(line)
                chunk = obj.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if obj.get("done"):
                    break
