"""Vertical batch and rest timeline. Text rows are widgets; the spine and nodes are Cairo."""

import math
from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timedelta
from functools import cache

from gi.repository import Gio, GLib, Gtk

from ....domain import target_engine
from ....domain.formatters import format_clock, format_clock_range, format_hm, format_range
from ....domain.models import TargetBatch, TimelineSlot
from ...widgets.controls import ChoiceGroup, chip, clear_box, hbox, text_label, vbox, wrap_row
from ...widgets.progress_ring import ThemedArea

NODE_WIDTH = 28
SPINE_TOKENS = {"work": "ring", "short": "text_muted", "long": "rest_text"}
BREAK_TITLES = {"short": "Short break", "long": "Long rest"}
CLOCK_PREF = "timeline_clock"
# While the clock view is on screen, its times move with the real clock.
CLOCK_REFRESH_SECONDS = 20


@cache
def desktop_uses_24h() -> bool:
    """Follow the GNOME clock format; a desktop without that setting gets the 24-hour clock."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    if schema is None or not schema.has_key("clock-format"):
        return True
    return Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format") != "12h"


def slots_from_batches(batches: Sequence[TargetBatch]) -> list[TimelineSlot]:
    """Rebuild cumulative minute markers for a saved target."""
    slots, cursor = [], 0
    for batch in batches:
        end = cursor + batch.planned_work_minutes
        slots.append(
            TimelineSlot(
                batch.batch_index,
                batch.subtask_title,
                batch.planned_work_minutes,
                batch.planned_break_minutes,
                batch.break_type,
                cursor,
                end,
            )
        )
        cursor = end + batch.planned_break_minutes
    return slots


class TimelineNode(ThemedArea):
    """One segment of the spine with its node. Purely visual: the row text carries the meaning."""

    def __init__(self, theme, kind: str, state: str, first: bool, last: bool):
        super().__init__(theme, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.kind, self.state, self._first, self._last = kind, state, first, last
        self.set_content_width(NODE_WIDTH)
        self.set_content_height(40)
        # The node fills its whole row, so each segment of the spine meets the next one.
        self.set_valign(Gtk.Align.FILL)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height) -> None:
        theme = self.theme
        center_x, center_y = width / 2, height / 2
        cr.set_line_width(2)
        cr.set_source_rgb(*theme.rgb(SPINE_TOKENS[self.kind]))
        if not self._first:
            cr.move_to(center_x, 0)
            cr.line_to(center_x, center_y)
            cr.stroke()
        if not self._last:
            cr.move_to(center_x, center_y)
            cr.line_to(center_x, height)
            cr.stroke()

        if self.state == "done":
            cr.set_source_rgb(*theme.rgb("success_text"))
            cr.arc(center_x, center_y, 8, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(*theme.rgb("bg_surface"))
            cr.set_line_width(2)
            cr.move_to(center_x - 3.5, center_y + 0.2)
            cr.line_to(center_x - 1, center_y + 2.8)
            cr.line_to(center_x + 3.8, center_y - 2.6)
            cr.stroke()
            return

        radius = {"work": 6, "short": 4, "long": 5}[self.kind]
        cr.set_source_rgb(*theme.rgb("bg_surface"))
        cr.arc(center_x, center_y, radius + 2, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*theme.rgb(SPINE_TOKENS[self.kind]))
        if self.state == "active":
            cr.set_line_width(3)
            cr.arc(center_x, center_y, 8, 0, 2 * math.pi)
            cr.stroke()
            cr.arc(center_x, center_y, 3, 0, 2 * math.pi)
            cr.fill()
        elif self.state == "upcoming":
            cr.set_line_width(2)
            cr.arc(center_x, center_y, radius, 0, 2 * math.pi)
            cr.stroke()
        else:
            cr.arc(center_x, center_y, radius, 0, 2 * math.pi)
            cr.fill()


class VerticalTimeline(Gtk.Box):
    """Batch and rest rows with their minutes from the start, or, in clock mode, the time of day.

    ``origin`` returns the moment that minute zero of the plan falls on. The owner decides it:
    now for a plan being written, and the live session's position for a running target.
    """

    def __init__(self, app, origin: Callable[[], datetime] | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._theme = app.theme
        self._repo = app.repo
        self._origin = origin
        self._signature = None
        self._title_labels: dict[int, Gtk.Label] = {}
        self._entries: list[tuple[str, str, str, str]] = []
        self._ranges: list[tuple[Gtk.Label, int, int]] = []
        self._total_minutes = 0
        self._refresh_id = 0
        self.nodes: list[TimelineNode] = []
        self.total_label = text_label("", "total-badge", "numeric")
        self.total_label.set_halign(Gtk.Align.START)
        self.total_label.set_valign(Gtk.Align.CENTER)
        self.mode_group = ChoiceGroup(
            [
                ("duration", "Duration", None, "Show minutes from the start of the plan"),
                ("clock", "Clock", None, "Show the time of day each block starts and ends"),
            ],
            "clock" if self._clock_preferred() else "duration",
            on_change=self._on_mode,
            segmented=True,
            label="Timeline times",
        )
        self.mode_group.set_visible(origin is not None)
        self.message_label = text_label("", "secondary", wrap=True)
        self.message_label.set_visible(False)
        self.rows = vbox(0)
        self.append(wrap_row(self.total_label, self.mode_group, spacing=8, spread=True))
        self.append(self.message_label)
        self.append(self.rows)
        self.connect("map", lambda _w: self._on_map())
        self.connect("unmap", lambda _w: self._stop_refresh())

    @property
    def clock_mode(self) -> bool:
        return self._origin is not None and self.mode_group.get_value() == "clock"

    def _clock_preferred(self) -> bool:
        return self._repo.get_pref(CLOCK_PREF, "0") == "1"

    def _on_map(self) -> None:
        # Another timeline may have switched the shared choice while this one was hidden.
        wanted = "clock" if self._clock_preferred() else "duration"
        if self.mode_group.get_value() != wanted:
            self.mode_group.set_value(wanted, notify=False)
        self.refresh_clock()

    def _on_mode(self, mode: object) -> None:
        self._repo.set_pref(CLOCK_PREF, "1" if mode == "clock" else "0")
        self.refresh_clock()

    def _sync_refresh(self) -> None:
        wanted = self.clock_mode and self.get_mapped()
        if wanted and not self._refresh_id:
            self._refresh_id = GLib.timeout_add_seconds(CLOCK_REFRESH_SECONDS, self._on_refresh_timer)
        elif not wanted:
            self._stop_refresh()

    def _stop_refresh(self) -> None:
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0

    def _on_refresh_timer(self) -> bool:
        self._apply_times()
        return True

    def refresh_clock(self) -> None:
        """Rewrite every time label for the current mode and moment, without rebuilding rows."""
        self._apply_times()
        self._sync_refresh()

    def _apply_times(self) -> None:
        origin = self._origin() if self.clock_mode else None
        twenty_four = desktop_uses_24h()
        for index, (label, start, end) in enumerate(self._ranges):
            if origin is None:
                text = format_range(start, end)
            else:
                text = format_clock_range(
                    origin + timedelta(minutes=start), origin + timedelta(minutes=end), twenty_four
                )
            label.set_text(text)
            kind, title, _old, state = self._entries[index]
            self._entries[index] = (kind, title, text, state)
        total = f"Total Timeline: {format_hm(self._total_minutes)}"
        if origin is not None:
            total += f", ends {format_clock(origin + timedelta(minutes=self._total_minutes), twenty_four)}"
        self.total_label.set_text(total)

    @property
    def total_text(self) -> str:
        return self.total_label.get_text()

    def entries(self) -> list[tuple[str, str, str, str]]:
        """(kind, title, minute range, state) per row, top to bottom."""
        return list(self._entries)

    def show_message(self, text: str) -> None:
        self._signature = None
        self._entries = []
        self._ranges = []
        self.nodes = []
        self._title_labels = {}
        clear_box(self.rows)
        self.total_label.set_visible(False)
        self.message_label.set_text(text)
        self.message_label.set_visible(True)

    @staticmethod
    def _work_title(slot: TimelineSlot) -> str:
        title = slot.subtask_title.strip()
        return f"Batch {slot.batch_index}: {title}" if title else f"Batch {slot.batch_index}"

    def set_slots(
        self,
        slots: Sequence[TimelineSlot],
        *,
        live: bool = False,
        active_batch: int | None = None,
        resting: bool = False,
        completed: Collection[int] = (),
        snooze_count: int = 0,
    ) -> None:
        signature = (
            tuple((s.work_minutes, s.break_minutes, s.break_type) for s in slots),
            live,
            active_batch,
            resting,
            tuple(sorted(completed)),
            snooze_count,
        )
        self.message_label.set_visible(False)
        self.total_label.set_visible(True)
        self._total_minutes = target_engine.total_minutes(slots)
        if signature == self._signature:
            # Only subtask names changed: update text in place instead of rebuilding every row.
            for index, slot in enumerate(slots):
                self._title_labels[slot.batch_index].set_text(self._work_title(slot))
                position = self._work_positions[index]
                kind, _title, minutes, state = self._entries[position]
                self._entries[position] = (kind, self._work_title(slot), minutes, state)
            self._apply_times()
            return

        self._signature = signature
        self._entries = []
        self._ranges = []
        self.nodes = []
        self._title_labels = {}
        self._work_positions: list[int] = []
        clear_box(self.rows)
        rows_total = sum(2 if s.break_minutes else 1 for s in slots)
        position = 0
        for slot in slots:
            index = slot.batch_index
            if not live:
                work_state = rest_state = "plan"
            elif index in completed or (index == active_batch and resting):
                work_state = "done"
                rest_state = "active" if index == active_batch and resting else "done"
            elif index == active_batch:
                work_state, rest_state = "active", "upcoming"
            else:
                work_state = rest_state = "upcoming"

            trailing = [chip(f"{slot.work_minutes}m", "coding")]
            if live and index == active_batch and snooze_count and not resting:
                trailing.append(chip(f"Snoozed {snooze_count}x", "alert"))
            title_label = self._add_row(
                "work",
                work_state,
                self._work_title(slot),
                (slot.cumulative_start_minute, slot.cumulative_end_minute),
                trailing,
                first=position == 0,
                last=position == rows_total - 1,
            )
            self._title_labels[index] = title_label
            self._work_positions.append(position)
            position += 1

            if slot.break_minutes:
                rest_chip = chip(f"{slot.break_minutes}m", "rest" if slot.break_type == "long" else "")
                self._add_row(
                    slot.break_type,
                    rest_state,
                    BREAK_TITLES[slot.break_type],
                    (slot.cumulative_end_minute, slot.break_end_minute),
                    [rest_chip],
                    first=False,
                    last=position == rows_total - 1,
                )
                position += 1
        self._apply_times()

    def _add_row(self, kind, state, title, minutes, trailing, *, first, last) -> Gtk.Label:
        title_label = text_label(title, "timeline-title" if kind == "work" else "secondary", ellipsize=True)
        start, end = minutes
        range_label = text_label(format_range(start, end), "timeline-range", "numeric")
        texts = vbox(1, title_label, range_label)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        texts.add_css_class("timeline-texts")
        for widget in trailing:
            widget.set_valign(Gtk.Align.CENTER)
        node = TimelineNode(self._theme, kind, state, first, last)
        row = hbox(8, node, texts, *trailing)
        row.add_css_class("timeline-row")
        if state == "active":
            row.add_css_class("active")
        self.rows.append(row)
        self.nodes.append(node)
        self._ranges.append((range_label, start, end))
        self._entries.append((kind, title, format_range(start, end), state))
        return title_label
