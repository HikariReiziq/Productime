"""Stopwatch with strict two-button states and a lap gap table."""

from gi.repository import GLib, Gtk

from ....domain import stopwatch_engine as stopwatch
from ....domain.formatters import format_delta, format_stopwatch
from ....domain.stopwatch_engine import StopwatchStatus
from ...widgets.controls import clear_box, hbox, text_label, vbox

COLUMNS = ("Lap #", "Lap Gap (Split)", "Delta vs Prev", "Total Elapsed")
BUTTON_CLASSES = ("primary", "secondary", "destructive-outline")
# (label, style, enabled) for the left and right button in each state.
STATE_BUTTONS = {
    StopwatchStatus.IDLE: (("Start", "primary", True), ("Lap", "secondary", False)),
    StopwatchStatus.RUNNING: (("Pause", "secondary", True), ("Lap", "primary", True)),
    StopwatchStatus.PAUSED: (("Resume", "primary", True), ("Clear", "destructive-outline", True)),
}


class StopwatchView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.window = window
        self.scheduler = window.scheduler
        self._frame_id = 0
        self._lap_count = -1

        self.clock_label = text_label(format_stopwatch(0), "clock-display", "numeric", xalign=0.5)
        self.clock_label.set_margin_top(12)
        self.left_button = Gtk.Button()
        self.right_button = Gtk.Button()
        self.left_button.connect("clicked", lambda _b: self._on_left())
        self.right_button.connect("clicked", lambda _b: self._on_right())
        for button in (self.left_button, self.right_button):
            button.set_size_request(132, -1)
        buttons = hbox(12, self.left_button, self.right_button)
        buttons.set_halign(Gtk.Align.CENTER)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        header.add_css_class("table-header")
        for title in COLUMNS:
            header.append(text_label(title, wrap=True))
        self.lap_rows = vbox(0)
        self.empty_label = text_label(
            "No laps yet. Press Lap while the stopwatch runs to record the gap since the previous lap.",
            "secondary",
            wrap=True,
        )
        self.empty_label.set_margin_top(12)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(360)
        scroller.set_child(self.lap_rows)
        table = vbox(0, text_label("Laps", "section-title"), header, scroller, self.empty_label)
        table.add_css_class("card")

        self.append(text_label("Stopwatch", "page-title"))
        self.append(self.clock_label)
        self.append(buttons)
        self.append(table)
        self.on_scheduler_changed()

    def on_shown(self) -> None:
        self.on_scheduler_changed()

    def reload(self) -> None:
        self.on_scheduler_changed()

    def tick(self) -> None:
        self._show_time()

    def set_compact(self, compact: bool) -> None:
        if compact:
            self.clock_label.add_css_class("compact")
        else:
            self.clock_label.remove_css_class("compact")

    def on_scheduler_changed(self) -> None:
        state = self.scheduler.stopwatch
        for button, (label, style, enabled) in zip(
            (self.left_button, self.right_button), STATE_BUTTONS[state.status], strict=True
        ):
            button.set_label(label)
            for name in BUTTON_CLASSES:
                button.remove_css_class(name)
            button.add_css_class(style)
            button.set_sensitive(enabled)
        self._sync_frame_callback(state.status is StopwatchStatus.RUNNING)
        self._show_time()
        if len(state.laps) != self._lap_count:
            self._render_laps(state.laps)

    def _sync_frame_callback(self, running: bool) -> None:
        """Repaint per frame only while running. GTK stops calling this when the window is hidden."""
        if running and not self._frame_id:
            self._frame_id = self.clock_label.add_tick_callback(self._on_frame)
        elif not running and self._frame_id:
            self.clock_label.remove_tick_callback(self._frame_id)
            self._frame_id = 0

    def _on_frame(self, _widget, _frame_clock) -> bool:
        self._show_time()
        return GLib.SOURCE_CONTINUE

    def _show_time(self) -> None:
        text = format_stopwatch(self.scheduler.stopwatch_elapsed_ms())
        if text != self.clock_label.get_text():
            self.clock_label.set_text(text)

    def _render_laps(self, laps) -> None:
        self._lap_count = len(laps)
        clear_box(self.lap_rows)
        self.empty_label.set_visible(not laps)
        for lap in reversed(laps):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
            row.add_css_class("table-row")
            row.append(text_label(str(lap.number), "numeric"))
            row.append(text_label(format_stopwatch(lap.lap_split_ms), "numeric", "body-strong"))
            if lap.delta_vs_prev_ms is None:
                row.append(text_label("First lap", "muted"))
            else:
                style = "delta-faster" if lap.delta_vs_prev_ms < 0 else "delta-slower"
                row.append(text_label(format_delta(lap.delta_vs_prev_ms), "numeric", style))
            row.append(text_label(format_stopwatch(lap.total_elapsed_ms), "numeric", "secondary"))
            self.lap_rows.append(row)

    def lap_table(self) -> list[tuple[str, str, str, str]]:
        """Visible lap rows, newest first, as shown on screen."""
        rows, row = [], self.lap_rows.get_first_child()
        while row is not None:
            cells, cell = [], row.get_first_child()
            while cell is not None:
                cells.append(cell.get_text())
                cell = cell.get_next_sibling()
            rows.append(tuple(cells))
            row = row.get_next_sibling()
        return rows

    def _run(self, action) -> None:
        try:
            action()
        except stopwatch.InvalidTransition as error:
            self.window.toast(str(error))

    def _on_left(self) -> None:
        status = self.scheduler.stopwatch.status
        if status is StopwatchStatus.IDLE:
            self._run(self.scheduler.stopwatch_start)
        elif status is StopwatchStatus.RUNNING:
            self._run(self.scheduler.stopwatch_pause)
        else:
            self._run(self.scheduler.stopwatch_resume)

    def _on_right(self) -> None:
        status = self.scheduler.stopwatch.status
        if status is StopwatchStatus.RUNNING:
            self._run(self.scheduler.stopwatch_lap)
        elif status is StopwatchStatus.PAUSED:
            self._run(self.scheduler.stopwatch_clear)
