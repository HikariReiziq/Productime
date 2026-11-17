"""Target timeline projection and the gated Pomodoro session state machine."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .models import BreakType, TargetConfig, TargetPhase, TimelineSlot

MS_PER_MINUTE = 60_000

_COUNTING = (TargetPhase.WORKING, TargetPhase.SNOOZING, TargetPhase.RESTING)
_CHECKABLE = (
    TargetPhase.WORKING,
    TargetPhase.PAUSED_WORK,
    TargetPhase.SNOOZING,
    TargetPhase.AWAITING_SUBTASK_GATE,
)


class InvalidTransition(Exception):
    """Raised when an event is not allowed in the current phase."""


def build_timeline(config: TargetConfig, subtask_titles: Sequence[str] = ()) -> list[TimelineSlot]:
    total = config.target_total_minutes
    cycle = config.work_minutes + config.short_break_minutes
    full_cycles, remainder = divmod(total, cycle)

    work_list = [config.work_minutes] * full_cycles
    if remainder > 0:
        work_list.append(max(1, min(config.work_minutes, remainder)))
    if not work_list:
        work_list.append(min(config.work_minutes, total))

    slots: list[TimelineSlot] = []
    cursor = 0
    for index, work in enumerate(work_list, start=1):
        break_type: BreakType
        # The same rule applies to every batch, including the last one, so the projected
        # total always matches what the session will actually run.
        if config.long_rest_enabled and index % config.long_rest_every == 0:
            break_type, break_minutes = "long", config.long_rest_minutes
        elif config.short_break_minutes > 0:
            break_type, break_minutes = "short", config.short_break_minutes
        else:
            break_type, break_minutes = "none", 0
        title = subtask_titles[index - 1] if index <= len(subtask_titles) else ""
        slots.append(
            TimelineSlot(
                batch_index=index,
                subtask_title=title,
                work_minutes=work,
                break_minutes=break_minutes,
                break_type=break_type,
                cumulative_start_minute=cursor,
                cumulative_end_minute=cursor + work,
            )
        )
        cursor += work + break_minutes
    return slots


def total_minutes(slots: Sequence[TimelineSlot]) -> int:
    return slots[-1].break_end_minute if slots else 0


def batch_count(config: TargetConfig) -> int:
    return len(build_timeline(config))


@dataclass(frozen=True)
class BatchPlan:
    work_ms: int
    break_ms: int
    break_type: BreakType


@dataclass(frozen=True)
class SessionState:
    phase: TargetPhase
    batch_index: int
    plans: tuple[BatchPlan, ...]
    snooze_limit: int | None
    snooze_count: int = 0
    remaining_ms: int = 0
    phase_total_ms: int = 0
    worked_ms: int = 0
    snoozed_ms: int = 0
    resume_phase: TargetPhase | None = None
    rest_extensions: int = 0

    @property
    def batch_count(self) -> int:
        return len(self.plans)

    @property
    def plan(self) -> BatchPlan:
        return self.plans[self.batch_index - 1]

    @property
    def is_final_batch(self) -> bool:
        return self.batch_index == len(self.plans)

    @property
    def in_final_rest(self) -> bool:
        return self.is_final_batch and self.phase in (TargetPhase.RESTING, TargetPhase.PAUSED_REST)

    @property
    def can_snooze(self) -> bool:
        if self.phase is not TargetPhase.AWAITING_SUBTASK_GATE:
            return False
        return self.snooze_limit is None or self.snooze_count < self.snooze_limit

    @property
    def snoozes_left(self) -> int | None:
        if self.snooze_limit is None:
            return None
        return max(0, self.snooze_limit - self.snooze_count)

    @property
    def can_extend_rest(self) -> bool:
        if self.phase is not TargetPhase.READY_FOR_NEXT:
            return False
        return self.snooze_limit is None or self.rest_extensions < self.snooze_limit

    @property
    def extensions_left(self) -> int | None:
        """Rest extensions share the target's snooze limit but keep their own count per break."""
        if self.snooze_limit is None:
            return None
        return max(0, self.snooze_limit - self.rest_extensions)


def plans_from_slots(slots: Sequence[TimelineSlot]) -> tuple[BatchPlan, ...]:
    return tuple(
        BatchPlan(s.work_minutes * MS_PER_MINUTE, s.break_minutes * MS_PER_MINUTE, s.break_type)
        for s in slots
    )


def _begin_batch(state: SessionState, batch_index: int, worked_ms: int = 0) -> SessionState:
    work_ms = state.plans[batch_index - 1].work_ms
    remaining = max(0, work_ms - worked_ms)
    phase = TargetPhase.WORKING if remaining > 0 else TargetPhase.AWAITING_SUBTASK_GATE
    return replace(
        state,
        phase=phase,
        batch_index=batch_index,
        snooze_count=0,
        remaining_ms=remaining,
        phase_total_ms=work_ms,
        worked_ms=min(worked_ms, work_ms),
        snoozed_ms=0,
        resume_phase=None,
        rest_extensions=0,
    )


def start_session(
    plans: Sequence[BatchPlan],
    snooze_limit: int | None,
    batch_index: int = 1,
    worked_ms: int = 0,
    snoozed_ms: int = 0,
    snooze_count: int = 0,
) -> SessionState:
    if not plans:
        raise InvalidTransition("A target needs at least one batch.")
    if not 1 <= batch_index <= len(plans):
        raise InvalidTransition("Batch index is outside the target.")
    base = SessionState(TargetPhase.IDLE, batch_index, tuple(plans), snooze_limit)
    state = _begin_batch(base, batch_index, worked_ms)
    return replace(state, snoozed_ms=snoozed_ms, snooze_count=snooze_count)


def advance(state: SessionState, elapsed_ms: int) -> SessionState:
    """Consume elapsed time. Work and snooze expiry stop at the gate, never in rest."""
    if elapsed_ms <= 0 or state.phase not in _COUNTING:
        return state
    used = min(elapsed_ms, state.remaining_ms)
    remaining = state.remaining_ms - used

    if state.phase is TargetPhase.WORKING:
        state = replace(state, remaining_ms=remaining, worked_ms=state.worked_ms + used)
    elif state.phase is TargetPhase.SNOOZING:
        state = replace(state, remaining_ms=remaining, snoozed_ms=state.snoozed_ms + used)
    else:
        state = replace(state, remaining_ms=remaining)
        if remaining == 0:
            if state.is_final_batch:
                return replace(state, phase=TargetPhase.COMPLETED, phase_total_ms=0)
            # Work time must only start once the user is back, so the next batch waits.
            return replace(state, phase=TargetPhase.READY_FOR_NEXT, phase_total_ms=0)
        return state

    if remaining == 0:
        return replace(state, phase=TargetPhase.AWAITING_SUBTASK_GATE, phase_total_ms=0)
    return state


def complete_subtask(state: SessionState) -> SessionState:
    """The only transition that can reach RESTING."""
    if state.phase not in _CHECKABLE:
        raise InvalidTransition(f"Cannot check off a subtask while {state.phase.value}.")
    break_ms = state.plan.break_ms
    if break_ms > 0:
        return replace(
            state,
            phase=TargetPhase.RESTING,
            remaining_ms=break_ms,
            phase_total_ms=break_ms,
            resume_phase=None,
            rest_extensions=0,
        )
    if state.is_final_batch:
        return replace(state, phase=TargetPhase.COMPLETED, remaining_ms=0, phase_total_ms=0)
    return _begin_batch(state, state.batch_index + 1)


def snooze(state: SessionState, minutes: int) -> SessionState:
    if state.phase is not TargetPhase.AWAITING_SUBTASK_GATE:
        raise InvalidTransition("Snooze is only available when the batch timer has ended.")
    if minutes < 1:
        raise InvalidTransition("Snooze needs at least one minute.")
    if not state.can_snooze:
        raise InvalidTransition("Snooze limit reached.")
    duration = minutes * MS_PER_MINUTE
    return replace(
        state,
        phase=TargetPhase.SNOOZING,
        remaining_ms=duration,
        phase_total_ms=duration,
        snooze_count=state.snooze_count + 1,
    )


def pause(state: SessionState) -> SessionState:
    if state.phase in (TargetPhase.WORKING, TargetPhase.SNOOZING):
        return replace(state, phase=TargetPhase.PAUSED_WORK, resume_phase=state.phase)
    if state.phase is TargetPhase.RESTING:
        return replace(state, phase=TargetPhase.PAUSED_REST)
    raise InvalidTransition(f"Cannot pause while {state.phase.value}.")


def resume(state: SessionState) -> SessionState:
    if state.phase is TargetPhase.PAUSED_WORK:
        return replace(state, phase=state.resume_phase or TargetPhase.WORKING, resume_phase=None)
    if state.phase is TargetPhase.PAUSED_REST:
        return replace(state, phase=TargetPhase.RESTING)
    raise InvalidTransition(f"Cannot resume while {state.phase.value}.")


def finish_target(state: SessionState) -> SessionState:
    """Finish Target / Skip Final Rest: only valid during the final batch's rest."""
    if not state.in_final_rest:
        raise InvalidTransition("The target can only be finished during its final rest.")
    return replace(state, phase=TargetPhase.COMPLETED, remaining_ms=0, phase_total_ms=0)


def start_next_batch(state: SessionState) -> SessionState:
    """Begin the next batch after a break ran out, or end a break early on the user's request."""
    if state.phase not in (TargetPhase.READY_FOR_NEXT, TargetPhase.RESTING, TargetPhase.PAUSED_REST):
        raise InvalidTransition(f"The next batch cannot start while {state.phase.value}.")
    if state.is_final_batch:
        raise InvalidTransition("This is the final batch. Finish the target instead.")
    return _begin_batch(state, state.batch_index + 1)


def extend_rest(state: SessionState, minutes: int) -> SessionState:
    if state.phase is not TargetPhase.READY_FOR_NEXT:
        raise InvalidTransition("A rest can only be extended once it is over.")
    if minutes < 1:
        raise InvalidTransition("A rest extension needs at least one minute.")
    if not state.can_extend_rest:
        raise InvalidTransition("Snooze limit reached. Start the next batch or quit the target.")
    duration = minutes * MS_PER_MINUTE
    return replace(
        state,
        phase=TargetPhase.RESTING,
        remaining_ms=duration,
        phase_total_ms=duration,
        rest_extensions=state.rest_extensions + 1,
    )


def suspend(state: SessionState) -> SessionState:
    """The state a session reopens in after the app was closed: exact time left, nothing counting.

    A waiting gate reopens paused so launching the app is silent; resuming returns to the gate.
    Waiting to start reopens as the next batch, paused at its full length.
    """
    if state.phase in (TargetPhase.WORKING, TargetPhase.SNOOZING, TargetPhase.AWAITING_SUBTASK_GATE):
        return replace(state, phase=TargetPhase.PAUSED_WORK, resume_phase=state.phase)
    if state.phase is TargetPhase.RESTING:
        return replace(state, phase=TargetPhase.PAUSED_REST)
    if state.phase is TargetPhase.READY_FOR_NEXT:
        return pause(_begin_batch(state, state.batch_index + 1))
    return state
