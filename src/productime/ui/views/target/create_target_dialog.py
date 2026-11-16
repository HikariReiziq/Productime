"""Create or edit a target: form on the left, live vertical timeline on the right."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ....domain import target_engine
from ....domain.models import SNOOZE_LIMIT_CHOICES, TAG_LABELS, Target, TargetConfig, TargetDraft
from ....domain.validation import MAX_BATCHES, MAX_NAME_LENGTH, ValidationError
from ...widgets.controls import (
    AppDialog,
    ChoiceGroup,
    clear_box,
    field,
    make_button,
    spin,
    spin_value,
    switch_row,
    text_label,
    vbox,
    wrap_row,
)
from ...widgets.sound_picker import SoundPicker
from .vertical_timeline import VerticalTimeline

POMODORO_PRESETS = {"25/5": (25, 5), "50/10": (50, 10)}
LONG_REST_EVERY = (1, 2, 3)
LONG_REST_MINUTES = (15, 20, 30)
CUSTOM = "custom"


class CreateTargetDialog(AppDialog):
    def __init__(
        self, window, target: Target | None = None, on_saved: Callable[[Target], None] | None = None
    ):
        super().__init__("Edit Target" if target else "Create Target", width=920, height=760)
        # Required by libadwaita once a dialog has breakpoints.
        self.set_size_request(360, 420)
        self._window = window
        self._repo = window.app.repo
        self._target = target
        self._on_saved = on_saved
        self.subtask_entries: list[Gtk.Entry] = []
        self.saved_target: Target | None = None
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

        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(MAX_NAME_LENGTH)
        self.name_entry.set_placeholder_text("Name this target")
        if target:
            self.name_entry.set_text(target.name)
        self.tag_group = ChoiceGroup(
            [(tag, tag) for tag in TAG_LABELS], target.tag_label if target else "Study", label="Category"
        )

        self.hours_spin = spin(0, 24, hours, on_change=lambda _v: self._refresh())
        self.minutes_spin = spin(0, 59, minutes, pad=2, on_change=lambda _v: self._refresh())

        self.pomodoro_group = ChoiceGroup(
            [("25/5", "25 / 5"), ("50/10", "50 / 10"), (CUSTOM, "Custom")],
            preset,
            on_change=lambda _v: self._refresh(),
            label="Pomodoro setup",
        )
        self.work_spin = spin(1, 240, config.work_minutes, on_change=lambda _v: self._refresh())
        self.break_spin = spin(0, 240, config.short_break_minutes, on_change=lambda _v: self._refresh())
        self.custom_pomodoro = wrap_row(
            field("Work minutes", self.work_spin), field("Break minutes", self.break_spin), spacing=12
        )

        self.long_switch = Gtk.Switch()
        self.long_switch.set_active(config.long_rest_enabled)
        self.long_switch.connect("notify::active", lambda *_: self._refresh())
        every = config.long_rest_every if config.long_rest_every in LONG_REST_EVERY else CUSTOM
        self.every_group = ChoiceGroup(
            [*((n, f"{n}x") for n in LONG_REST_EVERY), (CUSTOM, "Custom")],
            every,
            on_change=lambda _v: self._refresh(),
            label="Long rest after every N batches",
        )
        self.every_spin = spin(1, 50, config.long_rest_every, on_change=lambda _v: self._refresh())
        long_minutes = config.long_rest_minutes if config.long_rest_minutes in LONG_REST_MINUTES else CUSTOM
        self.long_group = ChoiceGroup(
            [*((n, f"{n}m") for n in LONG_REST_MINUTES), (CUSTOM, "Custom")],
            long_minutes,
            on_change=lambda _v: self._refresh(),
            label="Long rest duration",
        )
        self.long_spin = spin(1, 240, config.long_rest_minutes, on_change=lambda _v: self._refresh())
        self.every_custom = field("Batches between long rests", self.every_spin)
        self.long_custom = field("Long rest minutes", self.long_spin)
        self.long_options = vbox(
            10,
            field("After every", self.every_group),
            self.every_custom,
            field("Long rest duration", self.long_group),
            self.long_custom,
        )

        self.snooze_group = ChoiceGroup(
            [(limit, "Unlimited" if limit is None else str(limit)) for limit in SNOOZE_LIMIT_CHOICES],
            target.snooze_limit if target else 3,
            label="Snooze limit",
        )
        self.sound_picker = SoundPicker(self._window, target.sound_id if target else "bundled-digital")

        self.subtask_box = vbox(10)
        self._initial_titles = [b.subtask_title for b in target.batches] if target else []
        self.error_label = text_label("", "error-text", wrap=True)
        self.error_label.set_visible(False)

        form = vbox(
            14,
            field("Target name", self.name_entry),
            field("Category", self.tag_group),
            field(
                "Target duration",
                wrap_row(field("Hours", self.hours_spin), field("Minutes", self.minutes_spin), spacing=12),
            ),
            field("Pomodoro setup (work / break minutes)", self.pomodoro_group),
            self.custom_pomodoro,
            switch_row("Long rest", self.long_switch),
            self.long_options,
            field(
                "Snooze limit per batch",
                self.snooze_group,
                "How many times a batch can be extended before it must be checked off.",
            ),
            field("Batch end sound", self.sound_picker),
            text_label("Subtasks", "section-title"),
            text_label(
                "Each batch needs one deliverable. A batch only moves to its break after you check it off.",
                "secondary",
                wrap=True,
            ),
            self.subtask_box,
        )
        form.set_hexpand(True)

        self.timeline = VerticalTimeline(self._window.app.theme)
        panel = vbox(12, text_label("Timeline", "section-title"), self.timeline)
        panel.add_css_class("card")
        panel.set_size_request(300, -1)
        panel.set_valign(Gtk.Align.START)

        self.columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.columns.append(form)
        self.columns.append(panel)
        self.body.append(self.columns)
        self.body.append(self.error_label)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760px"))
        narrow.add_setter(self.columns, "orientation", Gtk.Orientation.VERTICAL)
        self.add_breakpoint(narrow)

        self.cancel_button = self.add_footer_button(make_button("Cancel", "secondary", on_click=self.close))
        self.save_button = self.add_footer_button(make_button("Save Target", "primary", on_click=self.save))

    def current_config(self) -> TargetConfig:
        preset = self.pomodoro_group.get_value()
        if preset == CUSTOM:
            work, short = spin_value(self.work_spin), spin_value(self.break_spin)
        else:
            work, short = POMODORO_PRESETS[preset]
        every = self.every_group.get_value()
        long_minutes = self.long_group.get_value()
        return TargetConfig(
            target_total_minutes=spin_value(self.hours_spin) * 60 + spin_value(self.minutes_spin),
            work_minutes=work,
            short_break_minutes=short,
            long_rest_enabled=self.long_switch.get_active(),
            long_rest_every=spin_value(self.every_spin) if every == CUSTOM else every,
            long_rest_minutes=spin_value(self.long_spin) if long_minutes == CUSTOM else long_minutes,
        )

    def subtask_titles(self) -> tuple[str, ...]:
        return tuple(entry.get_text() for entry in self.subtask_entries)

    def _refresh(self) -> None:
        """Recompute the plan from the form and redraw the timeline and subtask fields."""
        self.custom_pomodoro.set_visible(self.pomodoro_group.get_value() == CUSTOM)
        self.long_options.set_visible(self.long_switch.get_active())
        self.every_custom.set_visible(self.every_group.get_value() == CUSTOM)
        self.long_custom.set_visible(self.long_group.get_value() == CUSTOM)

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
            entry.set_placeholder_text(f"What will batch {index} deliver?")
            if index <= len(kept):
                entry.set_text(kept[index - 1])
            entry.connect("changed", lambda _e: self._update_timeline())
            self.subtask_entries.append(entry)
            self.subtask_box.append(field(f"Batch {index}", entry))

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def save(self) -> None:
        config = self.current_config()
        if config.target_total_minutes < 1:
            self._show_error("Set a target duration of at least 1 minute.")
            return
        draft = TargetDraft(
            name=self.name_entry.get_text(),
            tag_label=self.tag_group.get_value(),
            config=config,
            snooze_limit=self.snooze_group.get_value(),
            sound_id=self.sound_picker.get_sound_id(),
            subtask_titles=self.subtask_titles(),
        )
        try:
            if self._target is None:
                saved = self._repo.create_target(draft)
            else:
                saved = self._repo.update_target(self._target.id, draft)
        except ValidationError as error:
            prefix = "Target name: " if error.field == "name" else ""
            self._show_error(prefix + error.message)
            if error.field == "name":
                self.name_entry.grab_focus()
            return
        self.saved_target = saved
        self.close()
        if self._on_saved:
            self._on_saved(saved)
