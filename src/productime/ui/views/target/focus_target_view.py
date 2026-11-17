"""Focus Studio: countdown ring with controls beside the live batch timeline."""

from collections.abc import Callable
from datetime import datetime, timedelta

from gi.repository import Gtk

from ....domain.formatters import format_countdown
from ....domain.models import TargetPhase
from ....domain.target_engine import InvalidTransition
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import (
    chip,
    clear_box,
    hbox,
    icon_button,
    make_button,
    set_accessible_label,
    set_button_text,
    tag_style,
    text_label,
    vbox,
    wrap_row,
)
from ...widgets.progress_ring import ProgressRing
from .vertical_timeline import VerticalTimeline, slots_from_batches

PHASE_TEXT = {
    TargetPhase.IDLE: "Ready",
    TargetPhase.WORKING: "Focus",
    TargetPhase.PAUSED_WORK: "Paused",
    TargetPhase.AWAITING_SUBTASK_GATE: "Waiting for check-off",
    TargetPhase.SNOOZING: "Snoozed",
    TargetPhase.RESTING: "Break",
    TargetPhase.PAUSED_REST: "Break paused",
    TargetPhase.READY_FOR_NEXT: "Break over",
    TargetPhase.COMPLETED: "Completed",
}
RING_TONE = {
    TargetPhase.WORKING: "focus",
    TargetPhase.SNOOZING: "focus",
    TargetPhase.RESTING: "rest",
    TargetPhase.PAUSED_REST: "rest",
    TargetPhase.AWAITING_SUBTASK_GATE: "alert",
    TargetPhase.READY_FOR_NEXT: "alert",
    TargetPhase.PAUSED_WORK: "idle",
}
_CHECKABLE = (
    TargetPhase.WORKING,
    TargetPhase.PAUSED_WORK,
    TargetPhase.SNOOZING,
    TargetPhase.AWAITING_SUBTASK_GATE,
)
_PAUSABLE = (TargetPhase.WORKING, TargetPhase.SNOOZING, TargetPhase.RESTING)
_PAUSED = (TargetPhase.PAUSED_WORK, TargetPhase.PAUSED_REST)
_RESTING = (TargetPhase.RESTING, TargetPhase.PAUSED_REST)
COMPLETE_TIP = "Complete subtask"
NEXT_TIP = "Start the next batch"


class FocusTargetView(Gtk.Box):
    def __init__(self, window, on_back: Callable[[], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.window = window
        self.scheduler = window.scheduler
        self.phase_text = ""

        self.back_button = make_button(
            "Back to Today", "ghost", on_click=on_back, icon="go-previous-symbolic"
        )
        self.back_button.set_halign(Gtk.Align.START)
        self.title_label = text_label("", "page-title", wrap=True)
        self.chips = wrap_row(spacing=6)
        dot = Gtk.Box()
        dot.add_css_class("status-dot")
        dot.set_valign(Gtk.Align.CENTER)
        self.subtask_label = text_label("", "body-strong", wrap=True)
        self.subtask_label.set_hexpand(True)
        # CSS sets the ring's size per window size; digits and stroke scale with it.
        self.ring = ProgressRing(window.app.theme, size=320, stroke=14, text_px=64, scale=True)
        self.ring.add_css_class("focus-ring")
        self.hint_label = text_label("", "secondary", xalign=0.5, wrap=True)
        self.hint_label.set_justify(Gtk.Justification.CENTER)

        self.complete_button = icon_button(
            "object-select-symbolic", COMPLETE_TIP, "primary", "round-well", on_click=self.complete
        )
        self.pause_button = icon_button(
            "media-playback-pause-symbolic", "Pause", "secondary", "round-well", on_click=self.toggle_pause
        )
        self.quit_button = icon_button(
            "window-close-symbolic",
            "Quit target",
            "destructive-outline",
            "round-well",
            on_click=self.ask_quit,
        )
        controls = hbox(20, self.complete_button, self.pause_button, self.quit_button)
        controls.set_halign(Gtk.Align.CENTER)
        self.finish_button = make_button("Finish Target / Skip Final Rest", "secondary", on_click=self.finish)
        self.finish_button.set_halign(Gtk.Align.CENTER)
        self.gate_button = make_button("Open Check-off", "primary", on_click=window.reopen_alert)
        self.gate_button.set_halign(Gtk.Align.CENTER)
        self.ready_button = make_button(
            "Start", "primary", on_click=self.start_next, icon="media-playback-start-symbolic"
        )
        self.ready_button.set_halign(Gtk.Align.CENTER)

        self.left = vbox(
            14,
            self.title_label,
            self.chips,
            hbox(8, dot, self.subtask_label),
            self.ring,
            self.hint_label,
            controls,
            self.finish_button,
            self.gate_button,
            self.ready_button,
        )
        self.left.set_hexpand(True)

        self.timeline = VerticalTimeline(window.app, origin=self._clock_origin)
        self.right = vbox(12, text_label("Batch timeline", "section-title"), self.timeline)
        self.right.add_css_class("card")
        self.right.set_hexpand(True)
        self.right.set_valign(Gtk.Align.START)

        self.panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.panes.append(self.left)
        self.panes.append(self.right)
        self.idle_label = text_label("No target is running. Start one from Today.", "secondary", wrap=True)
        self.append(self.back_button)
        self.append(self.idle_label)
        self.append(self.panes)

    def set_compact(self, compact: bool) -> None:
        self.panes.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)

    def refresh(self) -> None:
        session = self.scheduler.session
        self.panes.set_visible(session is not None)
        self.idle_label.set_visible(session is None)
        if session is None:
            return
        state, target = session.state, session.target
        self.title_label.set_text(target.name)
        clear_box(self.chips)
        self.chips.append(chip(target.tag_label, tag_style(target.tag_color)))
        self.chips.append(chip(f"Batch {state.batch_index} of {state.batch_count}"))
        phase_text = PHASE_TEXT[state.phase]
        if state.phase in _RESTING and state.plan.break_type == "long":
            phase_text = "Long rest" if state.phase is TargetPhase.RESTING else "Long rest paused"
        self.phase_text = phase_text
        phase_kind = {"rest": "rest", "alert": "alert"}.get(RING_TONE.get(state.phase, ""), "")
        self.chips.append(chip(phase_text, phase_kind))
        self.subtask_label.set_text(session.batch.subtask_title)

        # During a break (but not the final one) the check button starts the next batch early.
        moves_on = not state.is_final_batch and state.phase in (*_RESTING, TargetPhase.READY_FOR_NEXT)
        self.complete_button.set_sensitive(state.phase in _CHECKABLE or moves_on)
        tip = NEXT_TIP if moves_on else COMPLETE_TIP
        self.complete_button.set_tooltip_text(tip)
        set_accessible_label(self.complete_button, tip)
        self.pause_button.set_sensitive(state.phase in _PAUSABLE or state.phase in _PAUSED)
        paused = state.phase in _PAUSED
        self.pause_button.set_icon_name(
            "media-playback-start-symbolic" if paused else "media-playback-pause-symbolic"
        )
        self.pause_button.set_tooltip_text("Resume" if paused else "Pause")
        set_accessible_label(self.pause_button, "Resume" if paused else "Pause")
        self.finish_button.set_visible(state.in_final_rest)
        at_gate = state.phase is TargetPhase.AWAITING_SUBTASK_GATE
        self.gate_button.set_visible(at_gate)
        ready = state.phase is TargetPhase.READY_FOR_NEXT
        self.ready_button.set_visible(ready)
        if ready:
            set_button_text(self.ready_button, f"Start Batch {state.batch_index + 1}")
            self.hint_label.set_text("Break is over. Start the next batch when you are back.")
        elif at_gate:
            self.hint_label.set_text("The batch timer ended. Check off the subtask to start your break.")
        elif state.phase is TargetPhase.SNOOZING:
            self.hint_label.set_text(
                "Extra time is running. The break starts once the subtask is checked off."
            )
        elif state.in_final_rest:
            self.hint_label.set_text("Final rest. Finish now or let the rest run out.")
        else:
            self.hint_label.set_text("")
        self.hint_label.set_visible(bool(self.hint_label.get_text()))

        self.timeline.set_slots(
            slots_from_batches(target.batches),
            live=True,
            active_batch=state.batch_index,
            resting=state.phase in _RESTING,
            completed={b.batch_index for b in target.batches if b.status == "completed"},
            snooze_count=state.snooze_count,
        )
        self.tick()

    def tick(self) -> None:
        session = self.scheduler.session
        if session is None:
            return
        state = session.state
        fraction = state.remaining_ms / state.phase_total_ms if state.phase_total_ms else 0.0
        self.ring.set_progress(
            fraction,
            format_countdown(state.remaining_ms / 1000),
            self.phase_text or PHASE_TEXT[state.phase],
            RING_TONE.get(state.phase, "focus"),
        )
        self.timeline.refresh_clock()

    def _clock_origin(self) -> datetime:
        """When minute zero of the plan falls, judged from where the session stands right now.

        The current block ends after its remaining time, so pauses and snoozes push the rest of
        the plan later, and the block after a finished break starts now.
        """
        now = self.window.app.clock.now()
        session = self.scheduler.session
        if session is None:
            return now
        state = session.state
        slot = slots_from_batches(session.target.batches)[state.batch_index - 1]
        anchor = slot.cumulative_end_minute if state.phase in _CHECKABLE else slot.break_end_minute
        return now + timedelta(milliseconds=state.remaining_ms) - timedelta(minutes=anchor)

    def _run(self, action: Callable[[], None]) -> None:
        try:
            action()
        except (SchedulerError, InvalidTransition, ValueError) as error:
            self.window.toast(str(error))

    def complete(self) -> None:
        session = self.scheduler.session
        if session is None:
            return
        state = session.state
        if state.phase is TargetPhase.READY_FOR_NEXT:
            self.start_next()
        elif state.phase in _RESTING:
            upcoming = state.batch_index + 1
            self.window.confirm(
                f"Start Batch {upcoming} now?",
                "Your break ends early and the next batch starts counting.",
                f"Start Batch {upcoming}",
                self.start_next,
                destructive=False,
            )
        else:
            self._run(self.scheduler.complete_subtask)

    def start_next(self) -> None:
        self._run(self.scheduler.start_next_batch)

    def toggle_pause(self) -> None:
        session = self.scheduler.session
        if session is None:
            return
        if session.state.phase in _PAUSED:
            self._run(self.scheduler.resume_session)
        else:
            self._run(self.scheduler.pause_session)

    def finish(self) -> None:
        self._run(self.scheduler.finish_target)

    def ask_quit(self) -> None:
        self.window.confirm(
            "Quit this target?",
            "Your progress is saved. You can resume the target later from Today.",
            "Quit Target",
            lambda: self._run(self.scheduler.quit_session),
        )
