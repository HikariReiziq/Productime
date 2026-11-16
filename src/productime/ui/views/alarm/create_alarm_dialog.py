"""Create or edit an alarm: 12-hour time, repeat days, sound, snooze rules, and mission."""

from gi.repository import Adw, Gtk

from ....domain.alarm_schedule import (
    EVERY_DAY,
    WEEKDAY_SHORT,
    WEEKDAYS,
    WEEKEND,
    describe_repeat,
    to_12h,
    to_24h,
)
from ....domain.mission_engine import SESSION_POOL_SIZE, TYPING_PHRASES
from ....domain.models import ALARM_SNOOZE_INTERVALS, SNOOZE_LIMIT_CHOICES, Alarm, AlarmDraft
from ....domain.validation import MAX_NAME_LENGTH, ValidationError
from ...widgets.controls import (
    AppDialog,
    ChoiceGroup,
    field,
    make_button,
    set_accessible_label,
    spin,
    spin_value,
    text_label,
    vbox,
    wrap_row,
)
from ...widgets.sound_picker import SoundPicker

WEEKDAY_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MATH_INFO = {
    "easy": "Easy: addition and subtraction with two-digit numbers.",
    "medium": "Medium: larger sums and differences, plus multiplication.",
    "hard": "Hard: three-digit sums, bigger multiplication, and two-step problems.",
}


class CreateAlarmDialog(AppDialog):
    def __init__(self, window, alarm: Alarm | None = None):
        super().__init__("Edit Alarm" if alarm else "New Alarm", width=560, height=760)
        self._window = window
        self._alarm = alarm
        self.saved_alarm: Alarm | None = None
        hour_12, period = to_12h(alarm.hour_24) if alarm else (7, "AM")

        self.hour_spin = spin(1, 12, hour_12, pad=2)
        self.minute_spin = spin(0, 59, alarm.minute if alarm else 0, pad=2)
        self.period_group = ChoiceGroup(
            [("AM", "AM"), ("PM", "PM")], period, segmented=True, label="AM or PM"
        )
        self.period_group.set_valign(Gtk.Align.END)

        self.day_buttons: dict[int, Gtk.ToggleButton] = {}
        days_box = Adw.WrapBox(child_spacing=6, line_spacing=6)
        days_box.add_css_class("choice-group")
        selected = alarm.repeat_days if alarm else ()
        for day, short in enumerate(WEEKDAY_SHORT, start=1):
            button = Gtk.ToggleButton(label=short)
            button.set_active(day in selected)
            set_accessible_label(button, WEEKDAY_FULL[day - 1])
            button.connect("toggled", lambda _b: self._update_repeat_summary())
            self.day_buttons[day] = button
            days_box.append(button)
        self.preset_buttons = {
            name: make_button(name, "ghost", on_click=lambda d=days: self.set_days(d))
            for name, days in (
                ("Once", ()),
                ("Weekdays", WEEKDAYS),
                ("Weekends", WEEKEND),
                ("Every day", EVERY_DAY),
            )
        }
        self.repeat_summary = text_label("", "secondary", "small")

        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Name this alarm")
        if alarm:
            self.name_entry.set_text(alarm.name)
        self.sound_picker = SoundPicker(window, alarm.sound_id if alarm else "bundled-digital")

        self.interval_group = ChoiceGroup(
            [(m, f"{m}m") for m in ALARM_SNOOZE_INTERVALS],
            alarm.snooze_interval_minutes if alarm else 5,
            label="Snooze interval",
        )
        self.limit_group = ChoiceGroup(
            [(limit, "Unlimited" if limit is None else str(limit)) for limit in SNOOZE_LIMIT_CHOICES],
            alarm.snooze_limit if alarm else 3,
            label="Snooze limit",
        )
        self.mission_group = ChoiceGroup(
            [("off", "Off"), ("typing", "Typing"), ("math", "Math")],
            alarm.mission_mode if alarm else "off",
            on_change=lambda _v: self._update_mission(),
            label="Mission mode",
        )
        self.count_group = ChoiceGroup(
            [(1, "1"), (2, "2"), (3, "3")],
            alarm.mission_count if alarm else 1,
            on_change=lambda _v: self._update_mission(),
            label="Mission count",
        )
        self.difficulty_group = ChoiceGroup(
            [("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")],
            alarm.mission_difficulty if alarm else "easy",
            on_change=lambda _v: self._update_mission(),
            label="Math difficulty",
        )
        self.count_field = field("How many to solve", self.count_group)
        self.difficulty_field = field("Math difficulty", self.difficulty_group)
        self.mission_info = text_label("", "secondary", wrap=True)
        self.error_label = text_label("", "error-text", wrap=True)
        self.error_label.set_visible(False)

        for child in (
            field(
                "Time",
                wrap_row(
                    field("Hour", self.hour_spin),
                    field("Minute", self.minute_spin),
                    self.period_group,
                    spacing=12,
                ),
            ),
            field("Repeat", vbox(8, days_box, wrap_row(*self.preset_buttons.values()), self.repeat_summary)),
            field("Alarm name", self.name_entry),
            field("Sound", self.sound_picker),
            field("Snooze interval", self.interval_group),
            field("Snooze limit", self.limit_group),
            field("Mission mode", self.mission_group),
            self.count_field,
            self.difficulty_field,
            self.mission_info,
            self.error_label,
        ):
            self.body.append(child)

        self.cancel_button = self.add_footer_button(make_button("Cancel", "secondary", on_click=self.close))
        self.save_button = self.add_footer_button(make_button("Save Alarm", "primary", on_click=self.save))
        self._update_repeat_summary()
        self._update_mission()

    def selected_days(self) -> tuple[int, ...]:
        return tuple(day for day, button in self.day_buttons.items() if button.get_active())

    def set_days(self, days) -> None:
        for day, button in self.day_buttons.items():
            button.set_active(day in days)

    def _update_repeat_summary(self) -> None:
        days = self.selected_days()
        summary = describe_repeat(days)
        if not days:
            summary = "Once: rings at the next matching time, then turns itself off."
        self.repeat_summary.set_text(summary)

    def _update_mission(self) -> None:
        mode = self.mission_group.get_value()
        count = self.count_group.get_value()
        self.count_field.set_visible(mode != "off")
        self.difficulty_field.set_visible(mode == "math")
        if mode == "typing":
            noun = "phrase" if count == 1 else "phrases"
            info = (
                f"Each ring draws {SESSION_POOL_SIZE} phrases from a bank of {len(TYPING_PHRASES)} and asks "
                f"you to type {count} {noun} exactly. Pasting is blocked. "
                f'Sample phrase: "{TYPING_PHRASES[2]}".'
            )
        elif mode == "math":
            info = (
                MATH_INFO[self.difficulty_group.get_value()]
                + " The alarm keeps ringing until every answer is right."
            )
        else:
            info = "No mission: the alarm stops with one button."
        self.mission_info.set_text(info)

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def save(self) -> None:
        draft = AlarmDraft(
            name=self.name_entry.get_text(),
            hour_24=to_24h(spin_value(self.hour_spin), self.period_group.get_value()),
            minute=spin_value(self.minute_spin),
            repeat_days=self.selected_days(),
            sound_id=self.sound_picker.get_sound_id(),
            snooze_interval_minutes=self.interval_group.get_value(),
            snooze_limit=self.limit_group.get_value(),
            mission_mode=self.mission_group.get_value(),
            mission_count=self.count_group.get_value(),
            mission_difficulty=self.difficulty_group.get_value(),
            enabled=self._alarm.enabled if self._alarm else True,
        )
        repo = self._window.app.repo
        try:
            if self._alarm is None:
                saved = repo.create_alarm(draft)
            else:
                saved = repo.update_alarm(self._alarm.id, draft)
        except ValidationError as error:
            prefix = "Alarm name: " if error.field == "name" else ""
            self._show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_alarm = saved
        self.close()
        self._window.scheduler.refresh_alarms()
