"""Orchestrator: wires STT -> LLM -> TTS with per-stage latency.

Knows nothing about which host runs which component — that's all behind the
factories in stt/, llm/, tts/. This is the stable seam for the fleet phases.
"""
from __future__ import annotations

import time

from .latency import Timings
from .llm import LLM
from .stt import STT
from .tts import TTS


class Pipeline:
    def __init__(self, stt: STT, llm: LLM, tts: TTS, system_prompt: str) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.history: list[dict] = [{"role": "system", "content": system_prompt}]

    def transcribe(self, wav_path: str, t: Timings) -> str:
        with t.stage("stt"):
            return self.stt.transcribe(wav_path)

    def respond(self, user_text: str, t: Timings) -> str:
        self.history.append({"role": "user", "content": user_text})
        reply_parts: list[str] = []
        start = time.perf_counter()
        first_token_ms = None
        for chunk in self.llm.stream(self.history):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - start) * 1000.0
            reply_parts.append(chunk)
        total_ms = (time.perf_counter() - start) * 1000.0
        t.mark("llm_first_token", first_token_ms or total_ms)
        t.mark("llm_total", total_ms)
        reply = "".join(reply_parts).strip()
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def speak(self, text: str, out_path: str, t: Timings) -> None:
        with t.stage("tts"):
            self.tts.synthesize(text, out_path)
