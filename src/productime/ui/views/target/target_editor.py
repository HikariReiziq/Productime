"""Create or edit a target on its own page: the plan on the left, the live vertical timeline on the right."""

from collections.abc import Callable

from gi.repository import Gtk

from ....domain import target_engine
from ....domain.formatters import format_minutes, format_snooze_limit
from ....domain.models import SNOOZE_LIMIT_CHOICES, TAG_LABELS, Target, TargetConfig, TargetDraft
from ....domain.validation import MAX_BATCHES, MAX_NAME_LENGTH, MAX_TAG_LENGTH, ValidationError, clean_text
from ...widgets.controls import (
    ChoiceGroup,
    SettingRow,
    clear_box,
    hbox,
    set_accessible_label,
    spin,
    spin_value,
    switch_row,
    text_label,
    time_picker,
    vbox,
)
from ...widgets.editor_page import EditorPage
from ...widgets.option_sheet import OptionSheet
from ...widgets.sound_picker import SoundPicker
from .vertical_timeline import VerticalTimeline

POMODORO_PRESETS = {"25/5": (25, 5), "50/10": (50, 10)}
LONG_REST_EVERY = (1, 2, 3)
LONG_REST_MINUTES = (15, 20, 30)
CUSTOM = "custom"
SUBTASK_TIP = "Each batch needs one deliverable. A batch only moves to its break after you check it off."


def section_title(icon: str, title: str, tooltip: str | None = None) -> Gtk.Box:
    """A short heading led by an icon. Longer explanations live in the tooltip."""
    image = Gtk.Image.new_from_icon_name(icon)
    image.add_css_class("title-icon")
    header = hbox(8, image, text_label(title, "body-strong"))
    if tooltip:
        header.set_tooltip_text(tooltip)
    return header


def every_text(every: int) -> str:
    return "Every batch" if every == 1 else f"Every {every} batches"


class TargetEditor(EditorPage):
    def __init__(
        self, window, target: Target | None = None, on_saved: Callable[[Target], None] | None = None
    ):
        title = "Edit Target" if target else "Create Target"
        super().__init__(window, "today", title, "Save Target", max_width=1120)
        self._repo = window.app.repo
        self._target = target
        self._on_saved = on_saved
        self.subtask_entries: list[Gtk.Entry] = []
        self.saved_target: Target | None = None
        self.sheet: OptionSheet | None = None
        # Choices to return to when a sheet opened by a Custom chip is cancelled.
        self._restore: dict[str, object] = {}
        self._build(target)
        self._refresh()

    def _build(self, target: Target | None) -> None:
        config = target.config if target else TargetConfig(120, 25, 5, False, 3, 30)
        hours, minutes = divmod(config.target_total_minutes, 60)
        preset = next(
            (
                key
                for key, pair in POMODORO_PRESETS.items()
                if pair == (config.work_minutes, config.short_break_minutes)
            ),
            CUSTOM,
        )
        # The detail choices live in sheets, so the page keeps their saved values here.
        self.custom_work, self.custom_break = config.work_minutes, config.short_break_minutes
        self.long_rest = (config.long_rest_enabled, config.long_rest_every, config.long_rest_minutes)
        self.snooze_limit = target.snooze_limit if target else 3
        current_tag = target.tag_label if target else "Study"
        is_custom = current_tag not in TAG_LABELS
        self.custom_tag = current_tag if is_custom else ""

        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Target name")
        set_accessible_label(self.name_entry, "Target name")
        if target:
            self.name_entry.set_text(target.name)

        self.tag_group = ChoiceGroup(
            [
                *((tag, tag, f"productime-{tag.lower()}-symbolic", f"{tag} category") for tag in TAG_LABELS),
                (CUSTOM, self.custom_tag or "Custom", "productime-custom-symbolic", "Name your own category"),
            ],
            CUSTOM if is_custom else current_tag,
            on_change=lambda value: self._remember("tag", value),
            label="Category",
        )
        self.tag_group.button_for(CUSTOM).connect("clicked", lambda _b: self.open_category_sheet())

        self.hours_spin = spin(0, 24, hours, pad=2, big=True, on_change=lambda _v: self._refresh())
        self.minutes_spin = spin(0, 59, minutes, pad=2, big=True, on_change=lambda _v: self._refresh())
        duration = time_picker((("Hours", self.hours_spin), ("Minutes", self.minutes_spin)))

        custom_text = f"{self.custom_work} / {self.custom_break}" if preset == CUSTOM else "Custom"
        self.pomodoro_group = ChoiceGroup(
            [
                ("25/5", "25 / 5", None, "25 minutes of work, then a 5 minute break"),
                ("50/10", "50 / 10", None, "50 minutes of work, then a 10 minute break"),
                (CUSTOM, custom_text, None, "Set your own work and break minutes"),
            ],
            preset,
            on_change=lambda value: self._remember("pomodoro", value),
            label="Pomodoro setup",
        )
        self.pomodoro_group.button_for(CUSTOM).connect("clicked", lambda _b: self.open_pomodoro_sheet())

        self.long_rest_row = SettingRow(
            "productime-long-rest-symbolic",
            "Long rest",
            "A longer break after every few batches",
            on_activate=self.open_long_rest_sheet,
        )
        self.snooze_row = SettingRow(
            "productime-snooze-symbolic",
            "Snooze limit",
            "How many times a batch can be extended before it must be checked off",
            on_activate=self.open_snooze_sheet,
        )
        self.sound_picker = SoundPicker(self.window, target.sound_id if target else "bundled-digital")
        self.subtask_box = vbox(10)
        self._initial_titles = [b.subtask_title for b in target.batches] if target else []

        plan = vbox(
            16,
            section_title("productime-today-symbolic", "Duration"),
            duration,
            section_title("productime-timer-symbolic", "Pomodoro", "Work and break minutes for each batch"),
            self.pomodoro_group,
            vbox(6, self.long_rest_row, self.snooze_row),
        )
        plan.add_css_class("card")
        subtasks_title = section_title("dialog-information-symbolic", "Subtasks", SUBTASK_TIP)
        subtasks = vbox(12, subtasks_title, self.subtask_box)
        subtasks.add_css_class("card")
        form = vbox(
            18,
            self.name_entry,
            vbox(10, section_title("productime-custom-symbolic", "Category"), self.tag_group),
            plan,
            self.sound_picker.as_card("Batch end sound"),
            subtasks,
        )
        form.set_hexpand(True)

        self.timeline = VerticalTimeline(self.window.app, origin=self.window.app.clock.now)
        panel = vbox(12, text_label("Timeline", "section-title"), self.timeline)
        panel.add_css_class("card")
        panel.set_size_request(300, -1)
        panel.set_valign(Gtk.Align.START)

        self.columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.columns.append(form)
        self.columns.append(panel)
        self.body.append(self.columns)
        self._show_choices()

    def set_compact(self, compact: bool) -> None:
        orientation = Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL
        self.columns.set_orientation(orientation)

    def current_config(self) -> TargetConfig:
        preset = self.pomodoro_group.get_value()
        work, short = (self.custom_work, self.custom_break) if preset == CUSTOM else POMODORO_PRESETS[preset]
        enabled, every, long_minutes = self.long_rest
        return TargetConfig(
            target_total_minutes=spin_value(self.hours_spin) * 60 + spin_value(self.minutes_spin),
            work_minutes=work,
            short_break_minutes=short,
            long_rest_enabled=enabled,
            long_rest_every=every,
            long_rest_minutes=long_minutes,
        )

    def subtask_titles(self) -> tuple[str, ...]:
        return tuple(entry.get_text() for entry in self.subtask_entries)

    def _remember(self, key: str, value: object) -> None:
        group = self.tag_group if key == "tag" else self.pomodoro_group
        if value == CUSTOM:
            self._restore[key] = group.previous
        else:
            self._restore.pop(key, None)
        if key == "pomodoro":
            self._refresh()

    def _show_choices(self) -> None:
        enabled, every, minutes = self.long_rest
        self.long_rest_row.set_value(f"Every {every}, {minutes} min" if enabled else "Off")
        self.snooze_row.set_value(format_snooze_limit(self.snooze_limit))

    def _refresh(self) -> None:
        """Recompute the plan from the page and redraw the timeline and subtask fields."""
        config = self.current_config()
        if config.target_total_minutes < 1:
            self._set_subtask_count(0)
            self.timeline.show_message("Set a target duration to see the batch timeline.")
            return
        count = target_engine.batch_count(config)
        if count > MAX_BATCHES:
            self._set_subtask_count(0)
            self.timeline.show_message(
                f"This plan needs {count} batches. Use longer work blocks to stay under {MAX_BATCHES}."
            )
            return
        self._set_subtask_count(count)
        self._update_timeline()

    def _update_timeline(self) -> None:
        config = self.current_config()
        if config.target_total_minutes < 1 or not self.subtask_entries:
            return
        self.timeline.set_slots(target_engine.build_timeline(config, self.subtask_titles()))

    def _set_subtask_count(self, count: int) -> None:
        if count == len(self.subtask_entries):
            return
        kept = list(self.subtask_titles()) or list(self._initial_titles)
        clear_box(self.subtask_box)
        self.subtask_entries = []
        for index in range(1, count + 1):
            entry = Gtk.Entry()
            entry.set_max_length(MAX_NAME_LENGTH)
            entry.set_placeholder_text(f"Batch {index}: what will it deliver?")
            set_accessible_label(entry, f"Batch {index}")
            if index <= len(kept):
                entry.set_text(kept[index - 1])
            entry.connect("changed", lambda _e: self._update_timeline())
            self.subtask_entries.append(entry)
            self.subtask_box.append(entry)

    def _present(self, sheet: OptionSheet, restore_key: str | None = None) -> None:
        if restore_key is not None:
            sheet.on_closed = lambda saved: self._custom_sheet_closed(restore_key, saved)
        self.sheet = sheet
        sheet.present_over(self.window)

    def _custom_sheet_closed(self, key: str, saved: bool) -> None:
        previous = self._restore.pop(key, None)
        if not saved and previous is not None:
            group = self.tag_group if key == "tag" else self.pomodoro_group
            group.set_value(previous)

    def open_category_sheet(self) -> None:
        sheet = OptionSheet("Custom category", on_save=lambda: self._apply_category(sheet))
        sheet.tag_entry = Gtk.Entry()
        sheet.tag_entry.set_max_length(MAX_TAG_LENGTH)
        sheet.tag_entry.set_placeholder_text("Category name")
        sheet.tag_entry.set_text(self.custom_tag)
        set_accessible_label(sheet.tag_entry, "Category name")
        sheet.tag_entry.connect("activate", lambda _e: sheet.save())
        sheet.add_card(sheet.tag_entry)
        sheet.focus_widget = sheet.tag_entry
        self._present(sheet, "tag")

    def _apply_category(self, sheet: OptionSheet) -> str | None:
        try:
            name = clean_text(sheet.tag_entry.get_text(), "tag_label", MAX_TAG_LENGTH)
        except ValidationError as error:
            sheet.tag_entry.grab_focus()
            return f"Category: {error.message}"
        self.custom_tag = name
        self.tag_group.set_text(CUSTOM, name)
        self.tag_group.set_value(CUSTOM, notify=False)
        return None

    def open_pomodoro_sheet(self) -> None:
        sheet = OptionSheet("Custom Pomodoro", on_save=lambda: self._apply_pomodoro(sheet))
        sheet.work_spin = spin(1, 240, self.custom_work)
        sheet.break_spin = spin(0, 240, self.custom_break)
        picker = time_picker((("Work min", sheet.work_spin), ("Break min", sheet.break_spin)), separator="/")
        picker.set_halign(Gtk.Align.CENTER)
        sheet.add_card(picker)
        self._present(sheet, "pomodoro")

    def _apply_pomodoro(self, sheet: OptionSheet) -> None:
        self.custom_work, self.custom_break = spin_value(sheet.work_spin), spin_value(sheet.break_spin)
        self.pomodoro_group.set_text(CUSTOM, f"{self.custom_work} / {self.custom_break}")
        self.pomodoro_group.set_value(CUSTOM, notify=False)
        self._refresh()

    def open_long_rest_sheet(self) -> None:
        enabled, every, minutes = self.long_rest
        sheet = OptionSheet("Long rest", on_save=lambda: self._apply_long_rest(sheet))
        sheet.long_switch = Gtk.Switch(active=enabled)
        sheet.every_spin = spin(1, 50, every)
        sheet.long_spin = spin(1, 240, minutes)
        sheet.every_spin.set_tooltip_text("Batches between long rests")
        sheet.long_spin.set_tooltip_text("Long rest minutes")
        every_custom = hbox(12, sheet.every_spin, text_label("batches", "secondary"))
        minutes_custom = hbox(12, sheet.long_spin, text_label("minutes", "secondary"))

        def describe_every(value: object) -> str:
            return every_text(spin_value(sheet.every_spin) if value == CUSTOM else value)

        def describe_minutes(value: object) -> str:
            return format_minutes(spin_value(sheet.long_spin) if value == CUSTOM else value)

        def update(*_args) -> None:
            on = sheet.long_switch.get_active()
            for key in ("every", "minutes"):
                sheet.sections[key].set_visible(on)
            every_custom.set_visible(sheet.groups["every"].get_value() == CUSTOM)
            minutes_custom.set_visible(sheet.groups["minutes"].get_value() == CUSTOM)
            sheet.refresh()

        sheet.add_card(switch_row("Take a long rest", sheet.long_switch))
        sheet.add_section(
            "every",
            "After",
            [*((n, every_text(n)) for n in LONG_REST_EVERY), (CUSTOM, "Custom")],
            every if every in LONG_REST_EVERY else CUSTOM,
            describe=describe_every,
            on_change=update,
            extra=every_custom,
        )
        sheet.add_section(
            "minutes",
            "Duration",
            [*((n, format_minutes(n)) for n in LONG_REST_MINUTES), (CUSTOM, "Custom")],
            minutes if minutes in LONG_REST_MINUTES else CUSTOM,
            describe=describe_minutes,
            on_change=update,
            extra=minutes_custom,
        )
        sheet.long_switch.connect("notify::active", update)
        sheet.every_spin.connect("value-changed", update)
        sheet.long_spin.connect("value-changed", update)
        sheet.reserve_height()
        update()
        self._present(sheet)

    def _apply_long_rest(self, sheet: OptionSheet) -> None:
        every, minutes = sheet.groups["every"].get_value(), sheet.groups["minutes"].get_value()
        self.long_rest = (
            sheet.long_switch.get_active(),
            spin_value(sheet.every_spin) if every == CUSTOM else every,
            spin_value(sheet.long_spin) if minutes == CUSTOM else minutes,
        )
        self._show_choices()
        self._refresh()

    def open_snooze_sheet(self) -> None:
        sheet = OptionSheet("Snooze limit", on_save=lambda: self._apply_snooze(sheet))
        sheet.add_section(
            "limit",
            "Per batch",
            [(limit, format_snooze_limit(limit)) for limit in SNOOZE_LIMIT_CHOICES],
            self.snooze_limit,
            visible=4,
        )
        self._present(sheet)

    def _apply_snooze(self, sheet: OptionSheet) -> None:
        self.snooze_limit = sheet.groups["limit"].get_value()
        self._show_choices()

    def tag_label(self) -> str:
        value = self.tag_group.get_value()
        return self.custom_tag if value == CUSTOM else value

    def save(self) -> None:
        config = self.current_config()
        if config.target_total_minutes < 1:
            self.show_error("Set a target duration of at least 1 minute.")
            return
        draft = TargetDraft(
            name=self.name_entry.get_text(),
            tag_label=self.tag_label(),
            config=config,
            snooze_limit=self.snooze_limit,
            sound_id=self.sound_picker.get_sound_id(),
            subtask_titles=self.subtask_titles(),
        )
        try:
            if self._target is None:
                saved = self._repo.create_target(draft)
            else:
                saved = self._repo.update_target(self._target.id, draft)
        except ValidationError as error:
            prefix = {"name": "Target name: ", "tag_label": "Category: "}.get(error.field, "")
            self.show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_target = saved
        self.close()
        if self._on_saved:
            self._on_saved(saved)
