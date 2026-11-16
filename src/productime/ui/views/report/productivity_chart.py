"""Smooth productivity curve drawn with Cairo, explorable by pointer and by arrow keys."""

from collections.abc import Callable, Sequence

import cairo
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from ....domain.formatters import format_duration
from ...widgets.progress_ring import ThemedArea

PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 46, 14, 34, 28
SCALE_STEPS = (900, 1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400)
GRID_LINES = 4


def scale_top(max_seconds: int) -> int:
    """Smallest round ceiling that fits the data, so grid labels stay readable."""
    for step in SCALE_STEPS:
        if max_seconds <= step:
            return step
    return ((max_seconds // 86400) + 1) * 86400


class ProductivityChart(ThemedArea):
    def __init__(self, theme, on_focus: Callable[[int | None], None] | None = None):
        super().__init__(theme, accessible_role=Gtk.AccessibleRole.IMG)
        self._labels: Sequence[str] = ()
        self._values: Sequence[int] = ()
        self._label_every = 1
        self._on_focus = on_focus
        self.focus_index: int | None = None
        self.set_content_height(320)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Productivity chart"])

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: self.set_focus_index(self.index_at(x)))
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.grab_focus())
        self.add_controller(click)
        self.set_draw_func(self._draw)

    def set_data(self, labels: Sequence[str], values: Sequence[int], label_every: int = 1) -> None:
        self._labels, self._values, self._label_every = tuple(labels), tuple(values), label_every
        self.focus_index = None
        self._notify_focus()
        self.queue_draw()

    def describe(self, index: int) -> str:
        return f"{self._labels[index]}: {format_duration(self._values[index])}"

    def index_at(self, x: float) -> int | None:
        count = len(self._values)
        plot_width = self.get_width() - PAD_LEFT - PAD_RIGHT
        if count == 0 or plot_width <= 0:
            return None
        step = plot_width / max(1, count - 1)
        return max(0, min(count - 1, round((x - PAD_LEFT) / step)))

    def set_focus_index(self, index: int | None) -> None:
        if index == self.focus_index:
            return
        self.focus_index = index
        self._notify_focus()
        self.queue_draw()

    def move_focus(self, delta: int) -> None:
        count = len(self._values)
        if count == 0:
            return
        if self.focus_index is None:
            self.set_focus_index(0 if delta > 0 else count - 1)
        else:
            self.set_focus_index(max(0, min(count - 1, self.focus_index + delta)))

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        count = len(self._values)
        if keyval == Gdk.KEY_Left:
            self.move_focus(-1)
        elif keyval == Gdk.KEY_Right:
            self.move_focus(1)
        elif keyval == Gdk.KEY_Home and count:
            self.set_focus_index(0)
        elif keyval == Gdk.KEY_End and count:
            self.set_focus_index(count - 1)
        else:
            return False
        return True

    def _notify_focus(self) -> None:
        description = self.describe(self.focus_index) if self.focus_index is not None else ""
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [description])
        if self._on_focus:
            self._on_focus(self.focus_index)

    def _text(
        self,
        cr,
        text: str,
        x: float,
        y: float,
        token: str,
        size: float = 11,
        align: float = 0.0,
        weight: Pango.Weight = Pango.Weight.NORMAL,
    ) -> tuple[int, int]:
        layout = self.numeric_layout(text, size, weight)
        width, height = layout.get_pixel_size()
        cr.set_source_rgb(*self.theme.rgb(token))
        cr.move_to(x - width * align, y)
        PangoCairo.show_layout(cr, layout)
        return width, height

    def _draw(self, _area, cr, width, height) -> None:
        theme = self.theme
        count = len(self._values)
        plot_width = width - PAD_LEFT - PAD_RIGHT
        plot_height = height - PAD_TOP - PAD_BOTTOM
        if count == 0 or plot_width <= 0 or plot_height <= 0:
            return
        top = scale_top(max(self._values))
        baseline = PAD_TOP + plot_height
        step = plot_width / max(1, count - 1)

        def point(index: int) -> tuple[float, float]:
            return PAD_LEFT + step * index, baseline - plot_height * (self._values[index] / top)

        cr.set_line_width(1)
        for line in range(GRID_LINES + 1):
            y = baseline - plot_height * line / GRID_LINES
            cr.set_source_rgba(*theme.rgb("text_muted"), 0.35)
            cr.set_dash([4, 4] if line else [])
            cr.move_to(PAD_LEFT, y + 0.5)
            cr.line_to(width - PAD_RIGHT, y + 0.5)
            cr.stroke()
            self._text(
                cr,
                format_duration(top * line // GRID_LINES),
                PAD_LEFT - 8,
                y - 8,
                "text_secondary",
                align=1.0,
            )
        cr.set_dash([])

        for index, label in enumerate(self._labels):
            if index % self._label_every == 0:
                self._text(cr, label, point(index)[0], baseline + 8, "text_secondary", align=0.5)

        # Horizontal tangents at every point keep the curve smooth without dipping below zero.
        x0, y0 = point(0)
        cr.move_to(x0, y0)
        for index in range(1, count):
            x1, y1 = point(index)
            middle = (x0 + x1) / 2
            cr.curve_to(middle, y0, middle, y1, x1, y1)
            x0, y0 = x1, y1
        gradient = cairo.LinearGradient(PAD_LEFT, 0, width - PAD_RIGHT, 0)
        gradient.add_color_stop_rgb(0, *theme.rgb("ring_start"))
        gradient.add_color_stop_rgb(1, *theme.rgb("ring_end"))
        cr.set_source(gradient)
        cr.set_line_width(3)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.stroke()

        if self.focus_index is not None:
            self._draw_focus(cr, width, baseline, point(self.focus_index))

    def _draw_focus(self, cr, width: float, baseline: float, position: tuple[float, float]) -> None:
        theme = self.theme
        x, y = position
        cr.set_line_width(1)
        cr.set_source_rgb(*theme.rgb("text_muted"))
        cr.move_to(x + 0.5, PAD_TOP)
        cr.line_to(x + 0.5, baseline)
        cr.stroke()
        cr.set_source_rgba(*theme.rgb("ring_start"), 0.25)
        cr.arc(x, y, 9, 0, 6.2832)
        cr.fill()
        cr.set_source_rgb(*theme.rgb("ring_end"))
        cr.arc(x, y, 4.5, 0, 6.2832)
        cr.fill()

        layout = self.numeric_layout(self.describe(self.focus_index), 12, Pango.Weight.MEDIUM)
        text_width, text_height = layout.get_pixel_size()
        box_width, box_height = text_width + 16, text_height + 8
        box_x = max(PAD_LEFT, min(width - PAD_RIGHT - box_width, x - box_width / 2))
        box_y = 2
        cr.set_source_rgb(*theme.rgb("bg_surface_elevated"))
        cr.rectangle(box_x, box_y, box_width, box_height)
        cr.fill_preserve()
        cr.set_source_rgb(*theme.rgb("border_strong"))
        cr.stroke()
        cr.set_source_rgb(*theme.rgb("text_primary"))
        cr.move_to(box_x + 8, box_y + 4)
        PangoCairo.show_layout(cr, layout)
