"""Create or edit a timer on its own page: hours, minutes, seconds, name, and sound."""

from collections.abc import Callable

from gi.repository import Gtk

from ....domain.models import TimerDraft, TimerPreset
from ....domain.validation import MAX_NAME_LENGTH, ValidationError
from ...widgets.controls import set_accessible_label, spin, spin_value, time_picker
from ...widgets.editor_page import EditorPage
from ...widgets.sound_picker import SoundPicker

SOUND_TIP = "The chosen sound loops when the timer runs out, until you stop it."


class TimerEditor(EditorPage):
    def __init__(
        self,
        window,
        timer: TimerPreset | None = None,
        on_saved: Callable[[TimerPreset], None] | None = None,
    ):
        super().__init__(window, "timer", "Edit Timer" if timer else "Add Timer", "Save Timer")
        self._timer = timer
        self._on_saved = on_saved
        self.saved_timer: TimerPreset | None = None
        total = timer.duration_seconds if timer else 300
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)

        self.hours_spin = spin(0, 99, hours, pad=2, big=True)
        self.minutes_spin = spin(0, 59, minutes, pad=2, big=True)
        self.seconds_spin = spin(0, 59, seconds, pad=2, big=True)
        self.time_grid = time_picker(
            (("Hours", self.hours_spin), ("Minutes", self.minutes_spin), ("Seconds", self.seconds_spin))
        )
        self.time_grid.set_halign(Gtk.Align.CENTER)
        set_accessible_label(self.time_grid, "Duration")

        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Timer name")
        set_accessible_label(self.name_entry, "Timer name")
        if timer:
            self.name_entry.set_text(timer.name)
        self.name_entry.connect("activate", lambda _e: self.save())
        sound_id = timer.sound_id if timer else "bundled-digital"
        self.sound_picker = SoundPicker(window, sound_id, tooltip=SOUND_TIP)

        sound_card = self.sound_picker.as_card()
        for child in (self.time_grid, self.name_entry, sound_card):
            self.body.append(child)

    def duration_seconds(self) -> int:
        return (
            spin_value(self.hours_spin) * 3600
            + spin_value(self.minutes_spin) * 60
            + spin_value(self.seconds_spin)
        )

    def save(self) -> None:
        duration = self.duration_seconds()
        if duration < 1:
            self.show_error("Set a duration of at least 1 second.")
            return
        draft = TimerDraft(self.name_entry.get_text(), duration, self.sound_picker.get_sound_id())
        repo = self.window.app.repo
        try:
            if self._timer is None:
                saved = repo.create_timer(draft)
            else:
                saved = repo.update_timer(self._timer.id, draft)
                # A running copy would keep the old duration and sound.
                self.window.scheduler.forget_timer(self._timer.id)
        except ValidationError as error:
            prefix = "Timer name: " if error.field == "name" else ""
            self.show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_timer = saved
        self.close()
        if self._on_saved:
            self._on_saved(saved)
