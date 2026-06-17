"""HTTP LLM backend: talk to an OpenAI-compatible or Ollama endpoint on another
host (Phase 1 transport seam; Phase 2 moves the model to the Gaming PC GPU).

Selected via ``LLM_BACKEND=http`` + ``LLM_HTTP_URL``. Supports two wire formats:

* ``ollama`` (default): POST ``/api/chat`` with ``stream:true`` (NDJSON).
* ``openai``: POST ``/v1/chat/completions`` with ``stream:true`` (SSE) -- works
  with llama.cpp ``--api``, vLLM, LM Studio, etc.

The orchestrator never changes; only env flips to point at a remote host::

    LLM_BACKEND=http
    LLM_HTTP_URL=http://gaming-pc:11434
    LLM_HTTP_FORMAT=ollama        # or: openai
"""
from __future__ import annotations

import json
import urllib.request
from typing import Iterator

from ..config import config


class HttpLLM:
    def __init__(self) -> None:
        base = config.llm_http_url.rstrip("/")
        self.fmt = config.llm_http_format
        self.model = config.llm_model
        self.api_key = config.llm_http_api_key
        if self.fmt == "openai":
            self.url = f"{base}/v1/chat/completions"
        else:
            self.url = f"{base}/api/chat"

    def stream(self, messages: list[dict]) -> Iterator[str]:
        if self.fmt == "openai":
            yield from self._stream_openai(messages)
        else:
            yield from self._stream_ollama(messages)

    def _request(self, payload: dict) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def _stream_ollama(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.7},
        }
        req = self._request(payload)
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

    def _stream_openai(self, messages: list[dict]) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        req = self._request(payload)
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                delta = obj.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content", "")
                if chunk:
                    yield chunk
