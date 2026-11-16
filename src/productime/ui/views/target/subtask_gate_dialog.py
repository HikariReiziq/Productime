"""Shown when a batch timer ends: the break only starts after the subtask is checked off."""

from gi.repository import Gtk

from ....domain.models import TARGET_SNOOZE_MINUTES
from ....domain.target_engine import InvalidTransition
from ....domain.validation import MAX_SNOOZE_MINUTES
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import (
    AppDialog,
    field,
    make_button,
    set_button_text,
    spin,
    spin_value,
    text_label,
    vbox,
    wrap_row,
)

LIMIT_REACHED = "Snooze limit reached. Complete the subtask to rest or quit target."


class SubtaskGateDialog(AppDialog):
    def __init__(self, window):
        super().__init__("Batch complete", width=520)
        self._window = window
        self._scheduler = window.scheduler

        self.prompt_label = text_label("", "section-title", wrap=True)
        self.complete_button = make_button(
            "Check Subtask & Start Break", "primary", on_click=self.complete, icon="object-select-symbolic"
        )
        self.snooze_buttons = {
            minutes: make_button(f"{minutes} min", "secondary", on_click=lambda m=minutes: self.snooze(m))
            for minutes in TARGET_SNOOZE_MINUTES
        }
        self.custom_spin = spin(1, MAX_SNOOZE_MINUTES, 15)
        self.custom_button = make_button(
            "Snooze", "secondary", on_click=lambda: self.snooze(spin_value(self.custom_spin))
        )
        self.custom_button.set_valign(Gtk.Align.END)
        self.snooze_status = text_label("", "secondary", wrap=True)
        self.error_label = text_label("", "error-text", wrap=True)
        self.error_label.set_visible(False)
        self.quit_button = make_button("Quit Target", "destructive-outline", on_click=self.quit_target)
        self.quit_button.set_halign(Gtk.Align.START)

        snooze_section = vbox(
            10,
            text_label("Need more time?", "body-strong"),
            wrap_row(*self.snooze_buttons.values()),
            wrap_row(field("Custom minutes", self.custom_spin), self.custom_button, spacing=12),
            self.snooze_status,
        )
        snooze_section.add_css_class("form-section")
        self.body.append(self.prompt_label)
        self.body.append(self.complete_button)
        self.body.append(snooze_section)
        self.body.append(self.error_label)
        self.body.append(self.quit_button)
        self.refresh()

    def refresh(self) -> None:
        session = self._scheduler.session
        if session is None:
            return
        state = session.state
        self.prompt_label.set_text(
            f'Batch {state.batch_index} complete. Did you finish "{session.batch.subtask_title}"?'
        )
        if not state.is_final_batch:
            label = (
                "Check Subtask & Start Break" if state.plan.break_ms else "Check Subtask & Start Next Batch"
            )
        elif state.plan.break_ms:
            label = "Check Subtask & Enter Final Rest"
        else:
            label = "Check Subtask & Finish Target"
        set_button_text(self.complete_button, label)

        can_snooze = state.can_snooze
        for button in (*self.snooze_buttons.values(), self.custom_button):
            button.set_sensitive(can_snooze)
        self.custom_spin.set_sensitive(can_snooze)
        if state.snooze_limit is None:
            status = "Unlimited snoozes for this target."
        elif can_snooze:
            status = f"{state.snoozes_left} of {state.snooze_limit} snoozes left"
        else:
            status = LIMIT_REACHED
        self.snooze_status.set_text(status)
        if can_snooze:
            self.snooze_status.remove_css_class("error-text")
        else:
            self.snooze_status.add_css_class("error-text")

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def complete(self) -> None:
        try:
            self._scheduler.complete_subtask()
        except (SchedulerError, InvalidTransition) as error:
            self._show_error(str(error))

    def snooze(self, minutes: int) -> None:
        try:
            self._scheduler.snooze_session(minutes)
        except (SchedulerError, InvalidTransition, ValueError) as error:
            self._show_error(str(error))

    def quit_target(self) -> None:
        self._window.confirm(
            "Quit this target?",
            "Your progress is saved. You can resume the target later from Today.",
            "Quit Target",
            self._scheduler.quit_session,
        )
