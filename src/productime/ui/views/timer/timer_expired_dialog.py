"""Shown when a timer runs out. Its sound loops until one of the two actions is taken."""

from gi.repository import Gtk

from ....services.scheduler_service import SchedulerError
from ...widgets.controls import AppDialog, make_button, text_label, wrap_row


class TimerExpiredDialog(AppDialog):
    def __init__(self, window, timer_id: str):
        super().__init__("Timer finished", width=440)
        self._window = window
        self._scheduler = window.scheduler
        self.timer_id = timer_id

        self.name_label = text_label("", "section-title", xalign=0.5, wrap=True)
        self.name_label.set_justify(Gtk.Justification.CENTER)
        self.detail_label = text_label(
            "Time is up. The sound keeps playing until you stop it or restart the timer.",
            "secondary",
            xalign=0.5,
            wrap=True,
        )
        self.detail_label.set_justify(Gtk.Justification.CENTER)
        self.stop_button = make_button("Stop Alarm", "primary", on_click=self.stop)
        self.restart_button = make_button("Restart Timer", "secondary", on_click=self.restart)
        actions = wrap_row(self.stop_button, self.restart_button, spacing=10)
        actions.set_halign(Gtk.Align.CENTER)
        for child in (self.name_label, self.detail_label, actions):
            self.body.append(child)
        self.refresh()

    def refresh(self) -> None:
        run = self._scheduler.timer_run(self.timer_id)
        if run is not None:
            self.name_label.set_text(run.name)

    def stop(self) -> None:
        self._act(self._scheduler.dismiss_timer)

    def restart(self) -> None:
        self._act(self._scheduler.restart_timer)

    def _act(self, action) -> None:
        try:
            action(self.timer_id)
        except SchedulerError as error:
            self._window.toast(str(error))
