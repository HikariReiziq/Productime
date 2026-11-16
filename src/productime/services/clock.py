"""Injectable time source so scheduling can be tested without waiting."""

import time
from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic_ns(self) -> int: ...

    def time_ns(self) -> int: ...

    def now(self) -> datetime: ...


class SystemClock:
    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def time_ns(self) -> int:
        return time.time_ns()

    def now(self) -> datetime:
        return datetime.now()


class FakeClock:
    """Manual clock. `advance` moves both clocks; `suspend` moves only the wall clock,
    which is what CLOCK_MONOTONIC does across a system suspend."""

    def __init__(self, start: datetime):
        self._start = start
        self._mono_ns = 1_000_000_000_000
        self._wall_offset_ns = 0
        self._wall_base_ns = int(start.timestamp()) * 1_000_000_000

    def advance(self, seconds: float) -> None:
        delta = round(seconds * 1_000_000_000)
        self._mono_ns += delta
        self._wall_offset_ns += delta

    def suspend(self, seconds: float) -> None:
        self._wall_offset_ns += round(seconds * 1_000_000_000)

    def shift_wall(self, seconds: float) -> None:
        """Simulate the user or NTP changing the system clock."""
        self._wall_offset_ns += round(seconds * 1_000_000_000)

    def monotonic_ns(self) -> int:
        return self._mono_ns

    def time_ns(self) -> int:
        return self._wall_base_ns + self._wall_offset_ns

    def now(self) -> datetime:
        return self._start + timedelta(microseconds=self._wall_offset_ns // 1000)
