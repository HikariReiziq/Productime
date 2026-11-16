"""Create or edit an alarm on its own page, laid out like a phone clock's wake-up alarm screen."""

from gi.repository import Gtk

from ....domain.alarm_schedule import EVERY_DAY, WEEKDAY_SHORT, describe_repeat, to_12h, to_24h
from ....domain.formatters import format_minutes, format_snooze_limit
from ....domain.mission_engine import SESSION_POOL_SIZE, TYPING_PHRASES
from ....domain.models import ALARM_SNOOZE_INTERVALS, SNOOZE_LIMIT_CHOICES, Alarm, AlarmDraft
from ....domain.validation import MAX_NAME_LENGTH, ValidationError
from ...widgets.controls import (
    ChoiceGroup,
    SettingRow,
    hbox,
    set_accessible_label,
    spin,
    spin_value,
    text_label,
    time_picker,
    vbox,
)
from ...widgets.editor_page import EditorPage
from ...widgets.option_sheet import OptionSheet
from ...widgets.sound_picker import SoundPicker

WEEKDAY_FULL = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ONCE_TIP = "Once: rings at the next matching time, then turns itself off."
REPEAT_TIP = "Pick the days this alarm repeats on."
MISSION_TIPS = {
    "off": "No mission. The alarm stops with one button.",
    "typing": (
        f"Type 1 to 3 phrases exactly to stop the alarm. Each ring draws {SESSION_POOL_SIZE} phrases "
        f"from a bank of {len(TYPING_PHRASES)}, and pasting is blocked."
    ),
    "math": "Solve 1 to 3 math problems to stop the alarm. It keeps ringing until every answer is right.",
}
# Real problems from each level's ranges in the mission engine, shown as a taste of the difficulty.
DIFFICULTY_SAMPLES = {"easy": "24 + 17", "medium": "13 \u00d7 7", "hard": "18 \u00d7 12 + 90"}
LEVELS = {"easy": 1, "medium": 2, "hard": 3}
MATH_INFO = {
    "easy": "Easy: addition and subtraction with two-digit numbers.",
    "medium": "Medium: larger sums and differences, plus multiplication.",
    "hard": "Hard: three-digit sums, bigger multiplication, and two-step problems.",
}


def limit_options() -> list[tuple]:
    return [(limit, format_snooze_limit(limit)) for limit in SNOOZE_LIMIT_CHOICES]


def count_text(mode: str, count: int) -> str:
    noun = "phrase" if mode == "typing" else "problem"
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def level_meter(level: int) -> Gtk.Box:
    """Three rising bars with the first ``level`` lit, like a signal meter."""
    meter = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER)
    for index, height in enumerate((8, 12, 16), start=1):
        bar = Gtk.Box(valign=Gtk.Align.END)
        bar.set_size_request(6, height)
        bar.add_css_class("meter-bar")
        if index <= level:
            bar.add_css_class("on")
        meter.append(bar)
    return meter


def difficulty_tile(level: str, caption: Gtk.Label) -> Gtk.Box:
    caption.add_css_class("body-strong")
    sample = text_label(DIFFICULTY_SAMPLES[level], "small", "numeric", xalign=0.5)
    return vbox(6, level_meter(LEVELS[level]), caption, sample)


def mission_summary(mode: str, count: int, difficulty: str) -> str:
    if mode == "typing":
        noun = "phrase" if count == 1 else "phrases"
        return f"Typing mission: type {count} {noun} exactly. Click to change."
    if mode == "math":
        noun = "problem" if count == 1 else "problems"
        return f"Math mission: {count} {difficulty.capitalize()} {noun}. Click to change."
    return "Add a wake-up mission"


class MissionSlot(Gtk.Button):
    """One dashed box: a plus until a mission is chosen, then that mission's icon and count."""

    def __init__(self, on_click):
        super().__init__(halign=Gtk.Align.START)
        self.add_css_class("mission-slot")
        self.icon = Gtk.Image()
        self.icon.set_pixel_size(28)
        self.caption = text_label("", "small", "body-strong", xalign=0.5)
        content = vbox(4, self.icon, self.caption)
        content.set_valign(Gtk.Align.CENTER)
        self.set_child(content)
        self.connect("clicked", lambda _b: on_click())

    def show(self, mode: str, count: int, difficulty: str) -> None:
        chosen = mode != "off"
        self.icon.set_from_icon_name(f"productime-{mode}-symbolic" if chosen else "list-add-symbolic")
        self.caption.set_text(f"{count}x" if chosen else "")
        self.caption.set_visible(chosen)
        summary = mission_summary(mode, count, difficulty)
        self.set_tooltip_text(summary)
        set_accessible_label(self, summary)
        if chosen:
            self.add_css_class("filled")
        else:
            self.remove_css_class("filled")


class AlarmEditor(EditorPage):
    def __init__(self, window, alarm: Alarm | None = None):
        super().__init__(window, "alarm", "Edit Alarm" if alarm else "New Alarm", "Save Alarm", max_width=560)
        self._alarm = alarm
        self.saved_alarm: Alarm | None = None
        self.sheet: OptionSheet | None = None
        self._syncing = False
        hour_12, period = to_12h(alarm.hour_24) if alarm else (7, "AM")
        self.interval = alarm.snooze_interval_minutes if alarm else 5
        self.limit = alarm.snooze_limit if alarm else 3
        self.mission = ("off", 1, "easy")
        if alarm:
            self.mission = (alarm.mission_mode, alarm.mission_count, alarm.mission_difficulty)

        self.hour_spin = spin(1, 12, hour_12, pad=2, big=True)
        self.minute_spin = spin(0, 59, alarm.minute if alarm else 0, pad=2, big=True)
        self.period_group = ChoiceGroup(
            [("AM", "AM"), ("PM", "PM")], period, segmented=True, vertical=True, label="AM or PM"
        )
        self.time_grid = time_picker(
            (("Hour", self.hour_spin), ("Minute", self.minute_spin)), trailing=self.period_group
        )
        self.time_grid.set_halign(Gtk.Align.CENTER)

        self.repeat_summary = text_label("", "body-strong", ellipsize=True)
        self.repeat_summary.set_hexpand(True)
        self.every_day_check = Gtk.CheckButton(label="Every day")
        self.every_day_check.add_css_class("every-day")
        self.every_day_check.set_tooltip_text("Ring on all seven days")
        self.every_day_check.connect("toggled", self._on_every_day)
        self.day_buttons: dict[int, Gtk.ToggleButton] = {}
        # One row of round day toggles, centred, the way a clock app shows repeat days.
        days_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        days_box.add_css_class("day-toggles")
        self.days_box = days_box
        selected = alarm.repeat_days if alarm else ()
        for day, short in enumerate(WEEKDAY_SHORT, start=1):
            button = Gtk.ToggleButton(label=short)
            button.set_active(day in selected)
            button.set_tooltip_text(WEEKDAY_FULL[day - 1])
            set_accessible_label(button, WEEKDAY_FULL[day - 1])
            button.connect("toggled", lambda _b: self._update_repeat())
            self.day_buttons[day] = button
            days_box.append(button)

        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Alarm name")
        set_accessible_label(self.name_entry, "Alarm name")
        if alarm:
            self.name_entry.set_text(alarm.name)
        self.name_entry.connect("activate", lambda _e: self.save())

        self.mission_slot = MissionSlot(self.open_mission_sheet)
        mission_title = text_label("Wake-up mission", "body-strong")
        mission_title.set_tooltip_text("A mission you must finish before the alarm can stop")
        self.snooze_row = SettingRow(
            "productime-snooze-symbolic",
            "Snooze",
            "How long a snooze lasts and how many times the alarm can be snoozed",
            on_activate=self.open_snooze_sheet,
        )
        self.sound_picker = SoundPicker(window, alarm.sound_id if alarm else "bundled-digital")
        options = vbox(14, mission_title, self.mission_slot, self.snooze_row)
        options.add_css_class("card")

        repeat = vbox(12, hbox(8, self.repeat_summary, self.every_day_check), days_box)
        for child in (self.time_grid, repeat, self.name_entry, options, self.sound_picker.as_card()):
            self.body.append(child)
        self._update_repeat()
        self._show_choices()

    def selected_days(self) -> tuple[int, ...]:
        return tuple(day for day, button in self.day_buttons.items() if button.get_active())

    def set_days(self, days) -> None:
        for day, button in self.day_buttons.items():
            button.set_active(day in days)

    def _on_every_day(self, check: Gtk.CheckButton) -> None:
        if not self._syncing:
            self.set_days(EVERY_DAY if check.get_active() else ())

    def _update_repeat(self) -> None:
        days = self.selected_days()
        self.repeat_summary.set_text(describe_repeat(days) if days else "Once")
        self.repeat_summary.set_tooltip_text(REPEAT_TIP if days else ONCE_TIP)
        self._syncing = True
        self.every_day_check.set_active(days == EVERY_DAY)
        self._syncing = False

    def _show_choices(self) -> None:
        self.snooze_row.set_value(f"{self.interval} min, {format_snooze_limit(self.limit).lower()}")
        self.mission_slot.show(*self.mission)

    def open_snooze_sheet(self) -> None:
        sheet = OptionSheet("Snooze", on_save=lambda: self._apply_snooze(sheet))
        sheet.add_section(
            "interval",
            "Interval",
            [(m, format_minutes(m)) for m in ALARM_SNOOZE_INTERVALS],
            self.interval,
            visible=3,
        )
        sheet.add_section("limit", "Snooze limit", limit_options(), self.limit, visible=4)
        self._present(sheet)

    def _apply_snooze(self, sheet: OptionSheet) -> None:
        self.interval = sheet.groups["interval"].get_value()
        self.limit = sheet.groups["limit"].get_value()
        self._show_choices()

    def open_mission_sheet(self) -> None:
        mode, count, difficulty = self.mission
        sheet = OptionSheet("Wake-up mission", on_save=lambda: self._apply_mission(sheet))
        count_icons: list[Gtk.Image] = []

        def count_tile(n: int, caption: Gtk.Label) -> Gtk.Box:
            # Each tile repeats the mission's icon, so the count reads as a picture.
            caption.add_css_class("tile-number")
            icons = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
            for _ in range(n):
                image = Gtk.Image()
                count_icons.append(image)
                icons.append(image)
            return vbox(4, caption, icons)

        def update(_value=None) -> None:
            chosen = sheet.groups["mission"].get_value()
            sheet.sections["count"].set_visible(chosen != "off")
            sheet.sections["difficulty"].set_visible(chosen == "math")
            icon = "productime-math-symbolic" if chosen == "math" else "productime-typing-symbolic"
            for image in count_icons:
                image.set_from_icon_name(icon)
            sheet.refresh("count")

        icons = {"off": "productime-mission-off-symbolic"}
        sheet.add_section(
            "mission",
            "Mission",
            [
                (key, title, icons.get(key, f"productime-{key}-symbolic"), MISSION_TIPS[key])
                for key, title in (("off", "Off"), ("typing", "Typing"), ("math", "Math"))
            ],
            mode,
            on_change=update,
        )
        sheet.add_section(
            "count",
            "How many",
            [(n, str(n)) for n in (1, 2, 3)],
            count,
            tile=count_tile,
            describe=lambda n: count_text(sheet.groups["mission"].get_value(), n),
        )
        sheet.add_section(
            "difficulty",
            "Difficulty",
            [(key, key.capitalize(), None, MATH_INFO[key]) for key in ("easy", "medium", "hard")],
            difficulty,
            tile=difficulty_tile,
        )
        sheet.reserve_height()
        update()
        self._present(sheet)

    def _apply_mission(self, sheet: OptionSheet) -> None:
        self.mission = tuple(sheet.groups[key].get_value() for key in ("mission", "count", "difficulty"))
        self._show_choices()

    def _present(self, sheet: OptionSheet) -> None:
        self.sheet = sheet
        sheet.present_over(self.window)

    def save(self) -> None:
        mode, count, difficulty = self.mission
        draft = AlarmDraft(
            name=self.name_entry.get_text(),
            hour_24=to_24h(spin_value(self.hour_spin), self.period_group.get_value()),
            minute=spin_value(self.minute_spin),
            repeat_days=self.selected_days(),
            sound_id=self.sound_picker.get_sound_id(),
            snooze_interval_minutes=self.interval,
            snooze_limit=self.limit,
            mission_mode=mode,
            mission_count=count,
            mission_difficulty=difficulty,
            enabled=self._alarm.enabled if self._alarm else True,
        )
        repo = self.window.app.repo
        try:
            if self._alarm is None:
                saved = repo.create_alarm(draft)
            else:
                saved = repo.update_alarm(self._alarm.id, draft)
        except ValidationError as error:
            prefix = "Alarm name: " if error.field == "name" else ""
            self.show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_alarm = saved
        self.close()
        self.window.scheduler.refresh_alarms()
