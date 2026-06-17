"""LLM interface + factory.

The interface hides whether the model is local (Mac/NUC) or remote (Gaming PC
woken via WoL). Only the backend cares. See AGENTS.md 'Wake-on-LAN'.
"""
from __future__ import annotations

from typing import Iterator, Protocol

from ..config import config


class LLM(Protocol):
    def stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield response text chunks (first chunk = first-token latency)."""
        ...


def get_llm() -> LLM:
    if config.llm_backend == "ollama":
        from .ollama_llm import OllamaLLM
        return OllamaLLM()
    if config.llm_backend == "http":
        from .http_llm import HttpLLM
        return HttpLLM()
    if config.llm_backend == "routed":
        from .routed_llm import RoutedLLM
        return RoutedLLM()
    raise ValueError(f"Unknown LLM_BACKEND: {config.llm_backend}")
