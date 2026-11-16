"""Stopwatch states and lap gap math. Time is supplied by the caller in monotonic ms."""

from dataclasses import dataclass, replace
from enum import StrEnum


class StopwatchStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class InvalidTransition(Exception):
    """Raised when a stopwatch control is not available in the current state."""


@dataclass(frozen=True)
class Lap:
    number: int
    total_elapsed_ms: int
    lap_split_ms: int
    delta_vs_prev_ms: int | None


@dataclass(frozen=True)
class StopwatchState:
    status: StopwatchStatus = StopwatchStatus.IDLE
    accumulated_ms: int = 0
    started_at_ms: int = 0
    laps: tuple[Lap, ...] = ()


_CONTROLS = {
    StopwatchStatus.IDLE: ("start", "lap"),
    StopwatchStatus.RUNNING: ("pause", "lap"),
    StopwatchStatus.PAUSED: ("resume", "clear"),
}


def initial() -> StopwatchState:
    return StopwatchState()


def controls(state: StopwatchState) -> tuple[str, str]:
    return _CONTROLS[state.status]


def can_lap(state: StopwatchState) -> bool:
    return state.status is StopwatchStatus.RUNNING


def elapsed_ms(state: StopwatchState, now_ms: int) -> int:
    if state.status is StopwatchStatus.RUNNING:
        return state.accumulated_ms + max(0, now_ms - state.started_at_ms)
    return state.accumulated_ms


def start(state: StopwatchState, now_ms: int) -> StopwatchState:
    if state.status is not StopwatchStatus.IDLE:
        raise InvalidTransition("Start is only available when the stopwatch is idle.")
    return replace(state, status=StopwatchStatus.RUNNING, started_at_ms=now_ms)


def pause(state: StopwatchState, now_ms: int) -> StopwatchState:
    if state.status is not StopwatchStatus.RUNNING:
        raise InvalidTransition("Pause is only available while running.")
    return replace(state, status=StopwatchStatus.PAUSED, accumulated_ms=elapsed_ms(state, now_ms))


def resume(state: StopwatchState, now_ms: int) -> StopwatchState:
    if state.status is not StopwatchStatus.PAUSED:
        raise InvalidTransition("Resume is only available while paused.")
    return replace(state, status=StopwatchStatus.RUNNING, started_at_ms=now_ms)


def clear(state: StopwatchState) -> StopwatchState:
    if state.status is not StopwatchStatus.PAUSED:
        raise InvalidTransition("Clear is only available while paused.")
    return initial()


def lap(state: StopwatchState, now_ms: int) -> StopwatchState:
    if not can_lap(state):
        raise InvalidTransition("Lap is only available while running.")
    total = elapsed_ms(state, now_ms)
    previous = state.laps[-1] if state.laps else None
    split = total - (previous.total_elapsed_ms if previous else 0)
    delta = split - previous.lap_split_ms if previous else None
    return replace(state, laps=(*state.laps, Lap(len(state.laps) + 1, total, split, delta)))


def add_gap(state: StopwatchState, gap_ms: int) -> StopwatchState:
    """Credit time the monotonic clock did not count, such as a system suspend."""
    if state.status is not StopwatchStatus.RUNNING or gap_ms <= 0:
        return state
    return replace(state, accumulated_ms=state.accumulated_ms + gap_ms)
