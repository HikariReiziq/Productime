"""Shown when a break runs out: the next batch waits until the user says they are back."""

from gi.repository import Gtk

from ....domain.target_engine import InvalidTransition
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import AppDialog, make_button, set_button_text, text_label, vbox

REST_MINUTES = 5
NO_EXTENSIONS = "No more extensions. Start the next batch or quit."


def session_card(caption: str, *labels: Gtk.Label) -> Gtk.Box:
    """A centred card naming the batch and its subtask, shared by the session alerts."""
    card = vbox(4, text_label(caption, "muted", "small", xalign=0.5), *labels)
    card.add_css_class("option-section")
    card.add_css_class("session-card")
    return card


class NextBatchDialog(AppDialog):
    def __init__(self, window):
        super().__init__("Break is over", width=460)
        self._window = window
        self._scheduler = window.scheduler

        self.heading_label = text_label("Break is over", "alert-heading", xalign=0.5, wrap=True)
        self.batch_label = text_label("", "secondary", "numeric", xalign=0.5)
        self.subtask_label = text_label("", "section-title", xalign=0.5, wrap=True)
        self.subtask_label.set_justify(Gtk.Justification.CENTER)
        self.start_button = make_button(
            "Start", "primary", "alert-primary", on_click=self.start, icon="media-playback-start-symbolic"
        )
        self.start_button.set_hexpand(True)

        self.extend_button = make_button(
            f"Rest {REST_MINUTES} more minutes",
            "secondary",
            on_click=self.extend,
            icon="productime-long-rest-symbolic",
        )
        self.extend_button.set_hexpand(True)
        self.extend_status = text_label("", "secondary", "small", xalign=0.5, wrap=True)
        extend_card = vbox(
            10,
            text_label("Need a bit longer?", "body-strong", xalign=0.5),
            self.extend_button,
            self.extend_status,
        )
        extend_card.add_css_class("option-section")

        self.error_label = text_label("", "error-text", wrap=True, xalign=0.5)
        self.error_label.set_visible(False)
        self.quit_button = make_button("Quit Target", "ghost", "quiet-destructive", on_click=self.quit_target)
        self.quit_button.set_halign(Gtk.Align.CENTER)

        for child in (
            self.heading_label,
            session_card("Up next", self.batch_label, self.subtask_label),
            self.start_button,
            extend_card,
            self.error_label,
            self.quit_button,
        ):
            self.body.append(child)
        self.refresh()

    def refresh(self) -> None:
        session = self._scheduler.session
        if session is None or session.next_batch is None:
            return
        state = session.state
        upcoming = state.batch_index + 1
        self.batch_label.set_text(f"Batch {upcoming} of {state.batch_count}")
        self.subtask_label.set_text(session.next_batch.subtask_title)
        set_button_text(self.start_button, f"Start Batch {upcoming}")
        can_extend = state.can_extend_rest
        self.extend_button.set_sensitive(can_extend)
        if state.extensions_left is None:
            status = "Unlimited extensions for this target."
        elif can_extend:
            status = f"{state.extensions_left} of {state.snooze_limit} extensions left"
        else:
            status = NO_EXTENSIONS
        self.extend_status.set_text(status)

    def _run(self, action, *args) -> None:
        try:
            action(*args)
        except (SchedulerError, InvalidTransition, ValueError) as error:
            self.error_label.set_text(str(error))
            self.error_label.set_visible(True)

    def start(self) -> None:
        self._run(self._scheduler.start_next_batch)

    def extend(self) -> None:
        self._run(self._scheduler.extend_rest, REST_MINUTES)

    def quit_target(self) -> None:
        self._window.confirm(
            "Quit this target?",
            "Your progress is saved. You can resume the target later from Today.",
            "Quit Target",
            self._scheduler.quit_session,
        )
