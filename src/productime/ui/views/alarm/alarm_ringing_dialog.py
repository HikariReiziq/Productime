"""Shown while an alarm rings. Snooze, or pass the mission, to stop the looping sound."""

from collections.abc import Callable

from gi.repository import Gdk, Gtk

from ....domain.formatters import format_alarm_time
from ....domain.mission_engine import is_single_keystroke_edit
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import (
    AppDialog,
    make_button,
    set_accessible_label,
    set_button_text,
    text_label,
    vbox,
)

LIMIT_REACHED = "Snooze limit reached. Finish the alarm to stop the sound."
PASTE_BLOCKED = "Pasting is disabled. Type the phrase yourself."


class PasteGuardEntry(Gtk.Entry):
    """An entry that only keeps edits a single key press could have made."""

    def __init__(self, on_blocked: Callable[[], None]):
        super().__init__()
        self._on_blocked = on_blocked
        self._accepted = ""
        self._reverting = False
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("changed", self._on_changed)

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        control = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        if (control and keyval in (Gdk.KEY_v, Gdk.KEY_V)) or (shift and keyval == Gdk.KEY_Insert):
            self._on_blocked()
            return True
        return False

    def _on_changed(self, _entry) -> None:
        # Every route into the buffer ends here: shortcuts, the context menu, middle click, drops.
        if self._reverting:
            return
        text = self.get_text()
        if is_single_keystroke_edit(self._accepted, text):
            self._accepted = text
            return
        self._restore(self._accepted)
        self._on_blocked()

    def _restore(self, text: str) -> None:
        self._reverting = True
        self.set_text(text)
        self.set_position(-1)
        self._reverting = False

    def reset(self) -> None:
        self._restore("")
        self._accepted = ""


class HeldKeyGuard(Gtk.EventControllerKey):
    """Lets one press of Space or Enter through and swallows the auto-repeat of a held key."""

    KEYS = (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)

    def __init__(self):
        super().__init__()
        self.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._held: set[int] = set()
        self.connect("key-pressed", self._on_pressed)
        self.connect("key-released", self._on_released)

    def _on_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval not in self.KEYS:
            return False
        if keyval in self._held:
            return True
        self._held.add(keyval)
        return False

    def _on_released(self, _controller, keyval, _keycode, _state) -> None:
        self._held.discard(keyval)


class AlarmRingingDialog(AppDialog):
    def __init__(self, window, alarm_id: str):
        super().__init__("Alarm", width=520)
        self._window = window
        self._scheduler = window.scheduler
        self.alarm_id = alarm_id
        self._shown_index = -1

        self.time_label = text_label("", "ringing-time", "numeric", xalign=0.5)
        self.name_label = text_label("", "section-title", xalign=0.5, wrap=True)
        self.name_label.set_justify(Gtk.Justification.CENTER)

        self.instruction_label = text_label("", "secondary", wrap=True)
        self.prompt_label = text_label("", "mission-prompt", "numeric", xalign=0.5, wrap=True)
        self.prompt_label.set_justify(Gtk.Justification.CENTER)
        self.counter_label = text_label("", "secondary", "numeric")
        self.answer_entry = PasteGuardEntry(self._on_paste_blocked)
        self.answer_entry.connect("activate", lambda _e: self.submit())
        set_accessible_label(self.answer_entry, "Mission answer")
        self.feedback_label = text_label("", "small", wrap=True)
        self.submit_button = make_button("Submit Answer", "primary", on_click=self.submit)
        self.mission_box = vbox(
            10,
            self.instruction_label,
            self.prompt_label,
            self.counter_label,
            self.answer_entry,
            self.feedback_label,
            self.submit_button,
        )
        self.click_instruction = text_label("", "secondary", xalign=0.5, wrap=True)
        self.click_instruction.set_justify(Gtk.Justification.CENTER)
        self.click_button = Gtk.Button(halign=Gtk.Align.CENTER)
        click_icon = Gtk.Image.new_from_icon_name("productime-click-symbolic")
        click_icon.set_pixel_size(48)
        self.click_button.set_child(click_icon)
        self.click_button.add_css_class("click-target")
        self.click_button.set_tooltip_text("Click to count")
        set_accessible_label(self.click_button, "Click to count")
        self.click_button.connect("clicked", lambda _b: self.register_click())
        self.key_guard = HeldKeyGuard()
        self.click_button.add_controller(self.key_guard)
        self.click_counter = text_label("", "click-counter", "numeric", xalign=0.5)
        self.click_progress = Gtk.ProgressBar()
        self.click_progress.add_css_class("click-progress")
        set_accessible_label(self.click_progress, "Clicks done")
        self.click_box = vbox(
            12, self.click_instruction, self.click_button, self.click_counter, self.click_progress
        )
        self.stop_button = make_button("Stop Alarm", "primary", on_click=self.stop)

        self.snooze_button = make_button("Snooze", "secondary", on_click=self.snooze)
        self.snooze_status = text_label("", "secondary", wrap=True)
        snooze_box = vbox(8, self.snooze_button, self.snooze_status)
        snooze_box.add_css_class("form-section")

        for child in (
            self.time_label,
            self.name_label,
            self.mission_box,
            self.click_box,
            self.stop_button,
            snooze_box,
        ):
            self.body.append(child)
        self.refresh()

    def _entry(self):
        for entry in self._scheduler.ringing:
            if entry.alarm.id == self.alarm_id:
                return entry
        return None

    def refresh(self) -> None:
        entry = self._entry()
        if entry is None:
            return
        alarm = entry.alarm
        self.time_label.set_text(format_alarm_time(alarm.hour_24, alarm.minute))
        self.name_label.set_text(alarm.name)

        has_mission = entry.mission_total > 0
        clicking = bool(entry.clicks_needed) and entry.mission_pending
        self.click_box.set_visible(clicking)
        self.mission_box.set_visible(has_mission and entry.mission_pending and not clicking)
        self.stop_button.set_visible(not entry.mission_pending)
        if clicking:
            needed = entry.clicks_needed
            self.click_instruction.set_text(f"Click the button {needed} times to stop the alarm.")
            self.click_counter.set_text(f"{entry.clicks_done} / {needed}")
            self.click_progress.set_fraction(entry.clicks_done / needed)
            if self._shown_index != -2:
                self._shown_index = -2
                self.click_button.grab_focus()
        elif has_mission and entry.mission_pending:
            typing = bool(entry.prompts)
            self.instruction_label.set_text(
                "Type this phrase exactly to stop the alarm." if typing else "Solve this to stop the alarm."
            )
            self.prompt_label.set_text(entry.current_prompt if typing else f"{entry.current_prompt} = ?")
            self.counter_label.set_text(f"{entry.mission_index + 1}/{entry.mission_total}")
            self.answer_entry.set_input_purpose(
                Gtk.InputPurpose.FREE_FORM if typing else Gtk.InputPurpose.DIGITS
            )
            if entry.mission_index != self._shown_index:
                self._shown_index = entry.mission_index
                self.answer_entry.reset()
                self.answer_entry.grab_focus()

        interval = alarm.snooze_interval_minutes
        can_snooze = self._scheduler.can_snooze_alarm(self.alarm_id)
        set_button_text(self.snooze_button, f"Snooze {interval} min")
        self.snooze_button.set_sensitive(can_snooze)
        if alarm.snooze_limit is None:
            status = "Unlimited snoozes for this alarm."
        elif can_snooze:
            left = alarm.snooze_limit - entry.snooze_count
            status = f"{left} of {alarm.snooze_limit} snoozes left"
        else:
            status = LIMIT_REACHED
        self.snooze_status.set_text(status)
        if can_snooze:
            self.snooze_status.remove_css_class("error-text")
        else:
            self.snooze_status.add_css_class("error-text")

    def _set_feedback(self, message: str, error: bool) -> None:
        self.feedback_label.set_text(message)
        if error:
            self.feedback_label.add_css_class("error-text")
        else:
            self.feedback_label.remove_css_class("error-text")

    def _on_paste_blocked(self) -> None:
        self._set_feedback(PASTE_BLOCKED, error=True)

    def submit(self) -> None:
        try:
            result = self._scheduler.submit_mission(self.alarm_id, self.answer_entry.get_text())
        except SchedulerError as error:
            self._set_feedback(str(error), error=True)
            return
        if not result.correct:
            self._set_feedback("Not correct yet. Check it and try again.", error=True)
            self.answer_entry.grab_focus()
        elif not result.finished:
            self._set_feedback("Correct. One more to go.", error=False)
            self.refresh()

    def register_click(self) -> None:
        try:
            self._scheduler.register_click(self.alarm_id)
        except SchedulerError as error:
            self._set_feedback(str(error), error=True)

    def stop(self) -> None:
        try:
            self._scheduler.stop_alarm(self.alarm_id)
        except SchedulerError as error:
            self._set_feedback(str(error), error=True)

    def snooze(self) -> None:
        try:
            self._scheduler.snooze_alarm(self.alarm_id)
        except SchedulerError as error:
            self.snooze_status.set_text(str(error))
            self.snooze_status.add_css_class("error-text")
