"""First-class per-stage latency tracking (a core requirement, see AGENTS.md)."""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Timings:
    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter() - start) * 1000.0

    def mark(self, name: str, ms: float) -> None:
        self.stages[name] = ms

    def total(self) -> float:
        return sum(self.stages.values())

    def render(self) -> str:
        parts = [f"{name} {ms:6.0f}ms" for name, ms in self.stages.items()]
        parts.append(f"TOTAL {self.total():6.0f}ms")
        return "  |  ".join(parts)
