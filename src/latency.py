"""First-class per-stage latency tracking (a core requirement, see AGENTS.md).

`stages` are real wall-clock segments that sum to the turn total.
`info` are diagnostic metrics (e.g. LLM first-token) that are displayed but NOT
summed into the total, so TOTAL stays an honest end-to-end number.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Timings:
    stages: dict[str, float] = field(default_factory=dict)
    info: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter() - start) * 1000.0

    def mark(self, name: str, ms: float) -> None:
        """Record a real stage segment (counts toward TOTAL)."""
        self.stages[name] = ms

    def mark_info(self, name: str, ms: float) -> None:
        """Record a diagnostic metric (shown, but NOT counted toward TOTAL)."""
        self.info[name] = ms

    def total(self) -> float:
        return sum(self.stages.values())

    def render(self) -> str:
        parts = [f"{name} {ms:6.0f}ms" for name, ms in self.stages.items()]
        for name, ms in self.info.items():
            parts.append(f"({name} {ms:.0f}ms)")
        parts.append(f"TOTAL {self.total():6.0f}ms")
        return "  |  ".join(parts)
