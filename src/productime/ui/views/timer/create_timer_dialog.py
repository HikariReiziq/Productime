"""Create or edit a timer: hours, minutes, seconds, name, and sound."""

from collections.abc import Callable

from gi.repository import Gtk

from ....domain.models import TimerDraft, TimerPreset
from ....domain.validation import MAX_NAME_LENGTH, ValidationError
from ...widgets.controls import AppDialog, field, make_button, spin, spin_value, text_label, wrap_row
from ...widgets.sound_picker import SoundPicker


class CreateTimerDialog(AppDialog):
    def __init__(
        self,
        window,
        timer: TimerPreset | None = None,
        on_saved: Callable[[TimerPreset], None] | None = None,
    ):
        super().__init__("Edit Timer" if timer else "Add Timer", width=520)
        self._window = window
        self._timer = timer
        self._on_saved = on_saved
        self.saved_timer: TimerPreset | None = None
        total = timer.duration_seconds if timer else 300
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)

        self.hours_spin = spin(0, 99, hours, pad=2)
        self.minutes_spin = spin(0, 59, minutes, pad=2)
        self.seconds_spin = spin(0, 59, seconds, pad=2)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Name this timer")
        if timer:
            self.name_entry.set_text(timer.name)
        self.sound_picker = SoundPicker(window, timer.sound_id if timer else "bundled-digital")
        self.error_label = text_label("", "error-text", wrap=True)
        self.error_label.set_visible(False)

        for child in (
            field(
                "Duration",
                wrap_row(
                    field("Hours", self.hours_spin),
                    field("Minutes", self.minutes_spin),
                    field("Seconds", self.seconds_spin),
                    spacing=12,
                ),
            ),
            field("Timer name", self.name_entry),
            field(
                "Sound or music",
                self.sound_picker,
                "The chosen sound loops when the timer runs out, until you stop it.",
            ),
            self.error_label,
        ):
            self.body.append(child)
        self.cancel_button = self.add_footer_button(make_button("Cancel", "secondary", on_click=self.close))
        self.save_button = self.add_footer_button(make_button("Save Timer", "primary", on_click=self.save))

    def duration_seconds(self) -> int:
        return (
            spin_value(self.hours_spin) * 3600
            + spin_value(self.minutes_spin) * 60
            + spin_value(self.seconds_spin)
        )

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def save(self) -> None:
        duration = self.duration_seconds()
        if duration < 1:
            self._show_error("Set a duration of at least 1 second.")
            return
        draft = TimerDraft(self.name_entry.get_text(), duration, self.sound_picker.get_sound_id())
        repo = self._window.app.repo
        try:
            if self._timer is None:
                saved = repo.create_timer(draft)
            else:
                saved = repo.update_timer(self._timer.id, draft)
                # A running copy would keep the old duration and sound.
                self._window.scheduler.forget_timer(self._timer.id)
        except ValidationError as error:
            prefix = "Timer name: " if error.field == "name" else ""
            self._show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_timer = saved
        self.close()
        if self._on_saved:
            self._on_saved(saved)
