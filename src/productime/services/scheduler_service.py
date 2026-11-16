"""Runtime state for targets, timers, alarms, and the stopwatch.

Countdowns are anchored to the monotonic clock. The wall clock is consulted only to
schedule alarms and to detect time the monotonic clock skipped during a system suspend.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..db.repositories import TIMESTAMP_FORMAT, Repository
from ..domain import alarm_schedule, mission_engine, target_engine, validation
from ..domain import stopwatch_engine as stopwatch
from ..domain.formatters import format_alarm_time
from ..domain.mission_engine import MathProblem
from ..domain.models import Alarm, Target, TargetBatch, TargetPhase
from ..domain.target_engine import BatchPlan, SessionState
from .clock import Clock

NS_PER_MS = 1_000_000
# Wall time running ahead of monotonic time by more than this means the machine slept.
SLEEP_THRESHOLD_NS = 2_000_000_000
PERSIST_INTERVAL_MS = 30_000
# Upper bound between polls while something is pending, in case a resume signal is missed.
MAX_WAKEUP_MS = 30_000

_COUNTING = (TargetPhase.WORKING, TargetPhase.SNOOZING, TargetPhase.RESTING)
_ACTIVE = (*_COUNTING, TargetPhase.AWAITING_SUBTASK_GATE)
_PAUSED = (TargetPhase.PAUSED_WORK, TargetPhase.PAUSED_REST)


class SchedulerError(Exception):
    """A requested action is not possible right now. The message is safe to show."""


@dataclass
class TargetSession:
    target: Target
    state: SessionState
    segment_started: datetime
    logged_ms: int = 0

    @property
    def batch(self) -> TargetBatch:
        return self.target.batches[self.state.batch_index - 1]


@dataclass
class TimerRun:
    timer_id: str
    name: str
    sound_id: str
    duration_ms: int
    remaining_ms: int
    status: str
    started_at: datetime
    sequence: int = 0


@dataclass
class RingingAlarm:
    alarm: Alarm
    snooze_count: int
    rang_at: datetime
    sequence: int
    pool: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    problems: list[MathProblem] = field(default_factory=list)
    mission_index: int = 0

    @property
    def mission_total(self) -> int:
        return len(self.prompts) or len(self.problems)

    @property
    def mission_pending(self) -> bool:
        return self.mission_index < self.mission_total

    @property
    def current_prompt(self) -> str | None:
        if not self.mission_pending:
            return None
        if self.prompts:
            return self.prompts[self.mission_index]
        return self.problems[self.mission_index].text


@dataclass
class SnoozedAlarm:
    alarm: Alarm
    snooze_count: int
    remaining_ms: int


@dataclass(frozen=True)
class Alert:
    kind: str
    key: str
    sound_id: str
    title: str
    ref_id: str


@dataclass(frozen=True)
class MissionResult:
    correct: bool
    finished: bool


class Scheduler:
    def __init__(
        self,
        repo: Repository,
        clock: Clock,
        audio,
        notifier,
        rng: random.Random | None = None,
        arm_wakeup: Callable[[int | None], None] | None = None,
    ):
        self.repo = repo
        self.clock = clock
        self.audio = audio
        self.notifier = notifier
        # Mission prompts only need to be unpredictable to a sleepy user, not to an attacker.
        self._rng = rng or random.Random()  # noqa: S311
        self._arm_wakeup = arm_wakeup
        self._listeners: list[Callable[[], None]] = []

        self.session: TargetSession | None = None
        self.last_completed_target: Target | None = None
        self.timers: dict[str, TimerRun] = {}
        self.ringing: list[RingingAlarm] = []
        self.snoozed: dict[str, SnoozedAlarm] = {}
        self.stopwatch = stopwatch.initial()

        self._alarms: list[Alarm] = repo.list_alarms()
        self._fired: set[str] = set()
        self._sequence = 0
        self._since_persist_ms = 0
        self._mono_ns = clock.monotonic_ns()
        self._wall_ns = clock.time_ns()
        self._wall = clock.now()
        self._restore_session()

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _after_change(self) -> None:
        self._sync_alert()
        self._emit()
        self._rearm()

    def _rearm(self) -> None:
        if self._arm_wakeup:
            self._arm_wakeup(self.next_wakeup_ms())

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def poll(self) -> None:
        """Consume elapsed time and fire anything that became due."""
        mono, wall_ns, wall = self.clock.monotonic_ns(), self.clock.time_ns(), self.clock.now()
        d_mono = max(0, mono - self._mono_ns)
        d_wall = wall_ns - self._wall_ns
        whole_ms, leftover_ns = divmod(d_mono, NS_PER_MS)
        drift_ns = d_wall - d_mono
        # CLOCK_MONOTONIC stops during suspend. A wall clock set backwards gives negative drift
        # and is ignored, so changing the system time never moves a countdown.
        gap_ms = drift_ns // NS_PER_MS if drift_ns > SLEEP_THRESHOLD_NS else 0
        # Carry the sub-millisecond remainder forward so repeated polls never lose time.
        self._mono_ns = mono - leftover_ns
        self._wall_ns = wall_ns - leftover_ns
        previous_wall, self._wall = self._wall, wall

        changed = False
        elapsed_ms = whole_ms + gap_ms
        if gap_ms:
            self.stopwatch = stopwatch.add_gap(self.stopwatch, gap_ms)
        if elapsed_ms > 0:
            changed |= self._advance_session(elapsed_ms)
            changed |= self._advance_timers(elapsed_ms)
            changed |= self._advance_snoozed(elapsed_ms)
        if wall > previous_wall:
            changed |= self._check_alarms(previous_wall, wall)
        if changed:
            self._sync_alert()
            self._emit()
        self._rearm()

    def next_wakeup_ms(self) -> int | None:
        candidates = []
        if self.session and self.session.state.phase in _COUNTING:
            candidates.append(self.session.state.remaining_ms)
        candidates.extend(r.remaining_ms for r in self.timers.values() if r.status == "running")
        candidates.extend(s.remaining_ms for s in self.snoozed.values())
        upcoming = self.next_alarm()
        if upcoming:
            delta_ms = int((upcoming[1] - self._wall).total_seconds() * 1000)
            candidates.append(max(0, delta_ms) + 20)
        if not candidates:
            return None
        return max(1, min(min(candidates), MAX_WAKEUP_MS))

    def has_live_countdown(self) -> bool:
        """True while a visible countdown is moving, which is when the UI needs a 1 Hz repaint."""
        return bool(
            (self.session and self.session.state.phase in _COUNTING)
            or any(r.status == "running" for r in self.timers.values())
        )

    def has_background_work(self) -> bool:
        return bool(
            (self.session and self.session.state.phase in _ACTIVE)
            or any(r.status in ("running", "expired") for r in self.timers.values())
            or self.stopwatch.status is stopwatch.StopwatchStatus.RUNNING
            or self.ringing
            or self.snoozed
            or any(alarm.enabled for alarm in self._alarms)
        )

    def current_alert(self) -> Alert | None:
        if self.ringing:
            entry = self.ringing[0]
            return Alert(
                "alarm",
                f"alarm:{entry.alarm.id}:{entry.sequence}",
                entry.alarm.sound_id,
                entry.alarm.name,
                entry.alarm.id,
            )
        session = self.session
        if session and session.state.phase is TargetPhase.AWAITING_SUBTASK_GATE:
            state = session.state
            return Alert(
                "gate",
                f"gate:{session.target.id}:{state.batch_index}:{state.snooze_count}",
                session.target.sound_id,
                session.batch.subtask_title,
                session.target.id,
            )
        expired = sorted((r for r in self.timers.values() if r.status == "expired"), key=lambda r: r.sequence)
        if expired:
            run = expired[0]
            key = f"timer:{run.timer_id}:{run.sequence}"
            return Alert("timer", key, run.sound_id, run.name, run.timer_id)
        return None

    def _sync_alert(self) -> None:
        alert = self.current_alert()
        if alert is None:
            self.audio.stop_alert()
        else:
            self.audio.start_alert(alert.sound_id)

    def _require_session(self) -> TargetSession:
        if self.session is None:
            raise SchedulerError("No target is running.")
        return self.session

    def _build_session(self, target: Target) -> TargetSession:
        batch = next((b for b in target.batches if b.status != "completed"), target.batches[0])
        plans = tuple(
            BatchPlan(b.planned_work_minutes * 60_000, b.planned_break_minutes * 60_000, b.break_type)
            for b in target.batches
        )
        state = target_engine.start_session(
            plans,
            target.snooze_limit,
            batch_index=batch.batch_index,
            worked_ms=batch.actual_work_seconds * 1000,
            snoozed_ms=batch.snooze_seconds_used * 1000,
            snooze_count=batch.snooze_count,
        )
        return TargetSession(target, state, self.clock.now(), state.worked_ms + state.snoozed_ms)

    def _restore_session(self) -> None:
        """Bring back an unfinished target paused, so launching the app never starts a countdown."""
        for target in self.repo.list_targets():
            if target.status != "in_progress" or not target.batches:
                continue
            session = self._build_session(target)
            if session.state.phase is TargetPhase.WORKING:
                session.state = target_engine.pause(session.state)
                self.session = session
            return

    def start_target(self, target_id: str) -> None:
        self.poll()
        if self.session is not None:
            if self.session.target.id != target_id:
                raise SchedulerError("Another target is in progress. Quit it before starting a new one.")
            if self.session.state.phase in _PAUSED:
                self.resume_session()
            return
        target = self.repo.get_target(target_id)
        if target is None or not target.batches:
            raise SchedulerError("That target no longer exists.")
        if target.status == "completed" or all(b.status == "completed" for b in target.batches):
            self.repo.reset_target_progress(target_id)
            target = self.repo.get_target(target_id)
        self.session = self._build_session(target)
        self.repo.set_target_status(target_id, "in_progress")
        target.status = "in_progress"
        self.last_completed_target = None
        self._begin_segment()
        self._after_change()

    def _begin_segment(self) -> None:
        session = self.session
        session.segment_started = self.clock.now()
        session.logged_ms = session.state.worked_ms + session.state.snoozed_ms
        self._persist_progress()

    def _persist_progress(self) -> None:
        session = self.session
        batch, state = session.batch, session.state
        batch.actual_work_seconds = state.worked_ms // 1000
        batch.snooze_seconds_used = state.snoozed_ms // 1000
        batch.snooze_count = state.snooze_count
        if batch.status != "completed":
            batch.status = "active"
        self.repo.save_batch_progress(batch)
        self._since_persist_ms = 0

    def _log_segment(self, completed: bool) -> None:
        """Write the work done since the last log entry, so resumed batches are never double counted."""
        session = self.session
        total_ms = session.state.worked_ms + session.state.snoozed_ms
        duration_ms = total_ms - session.logged_ms
        if duration_ms <= 0 and not completed:
            return
        self.repo.add_focus_log(
            source_type="target_batch",
            title=session.batch.subtask_title,
            tag_label=session.target.tag_label,
            started_at=session.segment_started,
            ended_at=self.clock.now(),
            duration_seconds=max(0, duration_ms) // 1000,
            subtask_completed=completed,
            target_id=session.target.id,
            batch_id=session.batch.id,
        )
        session.logged_ms = total_ms

    def _advance_session(self, elapsed_ms: int) -> bool:
        session = self.session
        if session is None:
            return False
        before = session.state
        after = target_engine.advance(before, elapsed_ms)
        if after is before:
            return False
        session.state = after
        self._since_persist_ms += elapsed_ms
        if after.phase is before.phase and after.batch_index == before.batch_index:
            if self._since_persist_ms >= PERSIST_INTERVAL_MS:
                self._persist_progress()
            return False

        if after.phase is TargetPhase.AWAITING_SUBTASK_GATE:
            self._persist_progress()
            self._notify_gate()
        elif after.phase is TargetPhase.COMPLETED:
            self._complete_target()
        elif after.batch_index != before.batch_index:
            self._begin_segment()
            self.notifier.notify(
                "target-batch",
                f"Batch {after.batch_index} started",
                session.batch.subtask_title,
                buttons=(("Open", "app.present", None),),
            )
        return True

    def complete_subtask(self) -> None:
        self.poll()
        session = self._require_session()
        # Validate the transition first so a rejected check-off writes nothing.
        after = target_engine.complete_subtask(session.state)
        batch = session.batch
        batch.status = "completed"
        batch.completed_at = self.clock.now().strftime(TIMESTAMP_FORMAT)
        self._persist_progress()
        self._log_segment(completed=True)
        session.state = after
        self.notifier.withdraw("target-gate")
        if after.phase is TargetPhase.COMPLETED:
            self._complete_target()
        elif after.batch_index != batch.batch_index:
            self._begin_segment()
        self._after_change()

    def snooze_session(self, minutes: int) -> None:
        self.poll()
        session = self._require_session()
        minutes = validation.validate_snooze_minutes(minutes)
        session.state = target_engine.snooze(session.state, minutes)
        self._persist_progress()
        self.notifier.withdraw("target-gate")
        self._after_change()

    def pause_session(self) -> None:
        self.poll()
        session = self._require_session()
        session.state = target_engine.pause(session.state)
        self._persist_progress()
        self._after_change()

    def resume_session(self) -> None:
        self.poll()
        session = self._require_session()
        session.state = target_engine.resume(session.state)
        self._after_change()

    def finish_target(self) -> None:
        self.poll()
        session = self._require_session()
        session.state = target_engine.finish_target(session.state)
        self._complete_target()
        self._after_change()

    def quit_session(self) -> None:
        self.poll()
        self._require_session()
        self._persist_progress()
        self._log_segment(completed=False)
        self.notifier.withdraw("target-gate")
        self.session = None
        self._after_change()

    def _complete_target(self) -> None:
        target = self.session.target
        self.repo.set_target_status(target.id, "completed")
        target.status = "completed"
        self.last_completed_target = target
        self.session = None
        self.notifier.notify(
            "target-done", "Target complete", target.name, buttons=(("Open", "app.present", None),)
        )

    def timer_run(self, timer_id: str) -> TimerRun | None:
        return self.timers.get(timer_id)

    def start_timer(self, timer_id: str) -> None:
        self.poll()
        preset = self.repo.get_timer(timer_id)
        if preset is None:
            raise SchedulerError("That timer no longer exists.")
        duration_ms = preset.duration_seconds * 1000
        self.timers[timer_id] = TimerRun(
            timer_id,
            preset.name,
            preset.sound_id,
            duration_ms,
            duration_ms,
            "running",
            self.clock.now(),
        )
        self.notifier.withdraw(f"timer-{timer_id}")
        self._after_change()

    def restart_timer(self, timer_id: str) -> None:
        self.start_timer(timer_id)

    def _require_timer(self, timer_id: str, status: str) -> TimerRun:
        run = self.timers.get(timer_id)
        if run is None or run.status != status:
            raise SchedulerError(f"That timer is not {status}.")
        return run

    def pause_timer(self, timer_id: str) -> None:
        self.poll()
        self._require_timer(timer_id, "running").status = "paused"
        self._after_change()

    def resume_timer(self, timer_id: str) -> None:
        self.poll()
        self._require_timer(timer_id, "paused").status = "running"
        self._after_change()

    def reset_timer(self, timer_id: str) -> None:
        self.poll()
        self.timers.pop(timer_id, None)
        self.notifier.withdraw(f"timer-{timer_id}")
        self._after_change()

    def dismiss_timer(self, timer_id: str) -> None:
        self.reset_timer(timer_id)

    def forget_timer(self, timer_id: str) -> None:
        """Drop runtime state for a timer that was edited or deleted."""
        if self.timers.pop(timer_id, None) is not None:
            self._after_change()

    def _advance_timers(self, elapsed_ms: int) -> bool:
        changed = False
        for run in self.timers.values():
            if run.status != "running":
                continue
            run.remaining_ms = max(0, run.remaining_ms - elapsed_ms)
            if run.remaining_ms == 0:
                self._expire_timer(run)
                changed = True
        return changed

    def _expire_timer(self, run: TimerRun) -> None:
        run.status = "expired"
        run.sequence = self._next_sequence()
        self.repo.add_focus_log(
            source_type="timer",
            title=run.name,
            tag_label="Timer",
            started_at=run.started_at,
            ended_at=self.clock.now(),
            duration_seconds=run.duration_ms // 1000,
            subtask_completed=False,
        )
        self.notifier.notify(
            f"timer-{run.timer_id}",
            "Timer finished",
            run.name,
            urgent=True,
            buttons=(
                ("Stop", "app.timer-stop", run.timer_id),
                ("Restart", "app.timer-restart", run.timer_id),
            ),
        )

    def refresh_alarms(self) -> None:
        """Reload alarms after any create, edit, toggle, delete, or restore."""
        self._alarms = self.repo.list_alarms()
        known = {alarm.id for alarm in self._alarms}
        self.ringing = [entry for entry in self.ringing if entry.alarm.id in known]
        self.snoozed = {key: value for key, value in self.snoozed.items() if key in known}
        self._after_change()

    def is_alarm_locked(self, alarm_id: str) -> bool:
        """A ringing or snoozed alarm cannot be edited away to dodge its mission."""
        return alarm_id in self.snoozed or any(e.alarm.id == alarm_id for e in self.ringing)

    def next_alarm(self) -> tuple[Alarm, datetime] | None:
        upcoming = [
            (alarm_schedule.next_ring(a.hour_24, a.minute, a.repeat_days, self._wall), a)
            for a in self._alarms
            if a.enabled
        ]
        if not upcoming:
            return None
        when, alarm = min(upcoming, key=lambda pair: pair[0])
        return alarm, when

    def next_ring_for(self, alarm: Alarm) -> datetime:
        return alarm_schedule.next_ring(alarm.hour_24, alarm.minute, alarm.repeat_days, self._wall)

    def _check_alarms(self, previous: datetime, now: datetime) -> bool:
        rang = False
        for alarm in self._alarms:
            if not alarm.enabled or self.is_alarm_locked(alarm.id):
                continue
            due = alarm_schedule.due_between(alarm.hour_24, alarm.minute, alarm.repeat_days, previous, now)
            if due is None:
                continue
            key = alarm_schedule.minute_key(alarm.id, due)
            # Guards the case where the wall clock moves back and crosses the same minute again.
            if key in self._fired:
                continue
            if len(self._fired) > 512:
                self._fired.clear()
            self._fired.add(key)
            self._ring(alarm, snooze_count=0)
            rang = True
        return rang

    def _ring(self, alarm: Alarm, snooze_count: int) -> None:
        entry = RingingAlarm(alarm, snooze_count, self.clock.now(), self._next_sequence())
        if alarm.mission_mode == "typing":
            entry.pool = mission_engine.pick_session_pool(self._rng)
            entry.prompts = mission_engine.pick_typing_prompts(entry.pool, alarm.mission_count, self._rng)
        elif alarm.mission_mode == "math":
            entry.problems = mission_engine.generate_math_set(
                alarm.mission_difficulty, alarm.mission_count, self._rng
            )
        self.ringing.append(entry)
        if not alarm.repeat_days and alarm.enabled:
            self.repo.set_alarm_enabled(alarm.id, False)
            alarm.enabled = False
        self.notifier.notify(
            f"alarm-{alarm.id}",
            alarm.name,
            f"Alarm set for {format_alarm_time(alarm.hour_24, alarm.minute)}",
            urgent=True,
            buttons=self._alarm_buttons(entry),
        )

    def _alarm_buttons(self, entry: RingingAlarm) -> tuple:
        alarm = entry.alarm
        # A mission cannot be answered from a notification, so that button opens the window instead.
        if entry.mission_total:
            buttons = [("Open Mission", "app.present", None)]
        else:
            buttons = [("Stop", "app.alarm-stop", alarm.id)]
        if alarm.snooze_limit is None or entry.snooze_count < alarm.snooze_limit:
            buttons.append((f"Snooze {alarm.snooze_interval_minutes} min", "app.alarm-snooze", alarm.id))
        return tuple(buttons)

    def _notify_gate(self) -> None:
        session = self.session
        buttons = [("Check Off", "app.gate-complete", None)]
        if session.state.can_snooze:
            buttons.append(("Snooze 5 min", "app.gate-snooze", 5))
        self.notifier.notify(
            "target-gate",
            f"Batch {session.state.batch_index} time is up",
            f'Check off "{session.batch.subtask_title}" to start your break.',
            urgent=True,
            buttons=tuple(buttons),
        )

    def _ringing_entry(self, alarm_id: str) -> RingingAlarm:
        for entry in self.ringing:
            if entry.alarm.id == alarm_id:
                return entry
        raise SchedulerError("That alarm is not ringing.")

    def _silence(self, entry: RingingAlarm) -> None:
        self.ringing.remove(entry)
        self.notifier.withdraw(f"alarm-{entry.alarm.id}")

    def can_snooze_alarm(self, alarm_id: str) -> bool:
        try:
            entry = self._ringing_entry(alarm_id)
        except SchedulerError:
            return False
        limit = entry.alarm.snooze_limit
        return limit is None or entry.snooze_count < limit

    def snooze_alarm(self, alarm_id: str) -> None:
        self.poll()
        entry = self._ringing_entry(alarm_id)
        if not self.can_snooze_alarm(alarm_id):
            raise SchedulerError("Snooze limit reached. Stop the alarm to continue.")
        self._silence(entry)
        self.snoozed[alarm_id] = SnoozedAlarm(
            entry.alarm, entry.snooze_count + 1, entry.alarm.snooze_interval_minutes * 60_000
        )
        self._after_change()

    def _advance_snoozed(self, elapsed_ms: int) -> bool:
        due = []
        for alarm_id, entry in self.snoozed.items():
            entry.remaining_ms = max(0, entry.remaining_ms - elapsed_ms)
            if entry.remaining_ms == 0:
                due.append(alarm_id)
        for alarm_id in due:
            entry = self.snoozed.pop(alarm_id)
            self._ring(entry.alarm, entry.snooze_count)
        return bool(due)

    def submit_mission(self, alarm_id: str, answer: str) -> MissionResult:
        """Check one mission answer here, so the dialog never holds the expected value."""
        self.poll()
        entry = self._ringing_entry(alarm_id)
        if not entry.mission_pending:
            return MissionResult(True, True)
        if entry.prompts:
            correct = mission_engine.check_typing(entry.prompts[entry.mission_index], answer)
        else:
            correct = mission_engine.check_math(entry.problems[entry.mission_index], answer)
        if not correct:
            return MissionResult(False, False)
        entry.mission_index += 1
        if entry.mission_pending:
            self._emit()
            return MissionResult(True, False)
        self._silence(entry)
        self._after_change()
        return MissionResult(True, True)

    def stop_alarm(self, alarm_id: str) -> None:
        self.poll()
        entry = self._ringing_entry(alarm_id)
        if entry.mission_pending:
            raise SchedulerError("Complete the mission to stop this alarm.")
        self._silence(entry)
        self._after_change()

    def _mono_ms(self) -> int:
        return self.clock.monotonic_ns() // NS_PER_MS

    def stopwatch_elapsed_ms(self) -> int:
        return stopwatch.elapsed_ms(self.stopwatch, self._mono_ms())

    def stopwatch_start(self) -> None:
        self.poll()
        self.stopwatch = stopwatch.start(self.stopwatch, self._mono_ms())
        self._after_change()

    def stopwatch_pause(self) -> None:
        self.poll()
        self.stopwatch = stopwatch.pause(self.stopwatch, self._mono_ms())
        self._after_change()

    def stopwatch_resume(self) -> None:
        self.poll()
        self.stopwatch = stopwatch.resume(self.stopwatch, self._mono_ms())
        self._after_change()

    def stopwatch_lap(self) -> None:
        self.poll()
        self.stopwatch = stopwatch.lap(self.stopwatch, self._mono_ms())
        self._after_change()

    def stopwatch_clear(self) -> None:
        self.stopwatch = stopwatch.clear(self.stopwatch)
        self._after_change()

    def reset_runtime(self) -> None:
        """Forget in-memory sessions after a backup restore replaced the stored data."""
        self.session = None
        self.last_completed_target = None
        self.timers.clear()
        self.ringing.clear()
        self.snoozed.clear()
        self._alarms = self.repo.list_alarms()
        self._restore_session()
        self._after_change()

    def shutdown(self) -> None:
        """Save progress of a running target so the next launch can resume it."""
        if self.session is None:
            return
        self.poll()
        if self.session is not None:
            self._persist_progress()
            self._log_segment(completed=False)


class SleepMonitor:
    """Calls back when systemd-logind reports the machine woke from suspend."""

    def __init__(self, on_resume: Callable[[], None]):
        from gi.repository import Gio, GLib

        self._on_resume = on_resume
        self._bus = None
        self._subscription = 0
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            # No system bus (containers, some CI): the wall-clock check in poll() still works.
            return
        self._subscription = self._bus.signal_subscribe(
            "org.freedesktop.login1",
            "org.freedesktop.login1.Manager",
            "PrepareForSleep",
            "/org/freedesktop/login1",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_signal,
        )

    def _on_signal(self, _bus, _sender, _path, _interface, _signal, parameters) -> None:
        (going_to_sleep,) = parameters.unpack()
        if not going_to_sleep:
            self._on_resume()

    def close(self) -> None:
        if self._bus is not None and self._subscription:
            self._bus.signal_unsubscribe(self._subscription)
            self._subscription = 0
