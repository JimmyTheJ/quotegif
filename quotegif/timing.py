from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_active_timer: ContextVar[RunTimer | None] = ContextVar("quotegif_run_timer", default=None)


@dataclass
class StepRecord:
    name: str
    seconds: float
    detail: str | None = None


@dataclass
class RunTimer:
    steps: list[StepRecord] = field(default_factory=list)

    @contextmanager
    def track(self, name: str, detail: str | None = None):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.steps.append(StepRecord(name, time.perf_counter() - start, detail))

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.steps)


def set_active_timer(timer: RunTimer | None) -> None:
    _active_timer.set(timer)


def get_active_timer() -> RunTimer | None:
    return _active_timer.get()


@contextmanager
def track_step(name: str, detail: str | None = None):
    """Record step duration on the active RunTimer, if any."""
    timer = get_active_timer()
    if timer is None:
        yield
        return
    with timer.track(name, detail=detail):
        yield


def format_duration(seconds: float) -> str:
    if seconds < 0.001:
        return "0ms"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.0f}s"
