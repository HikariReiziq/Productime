"""Cairo drawing base class and the open-arc countdown ring."""

import math

import cairo
from gi.repository import Gtk, Pango, PangoCairo

# The ring is an open arc with the gap at the bottom, echoing the clock face in the app icon.
ARC_START = math.radians(120)
ARC_SWEEP = math.radians(300)

TONES = {"focus": "ring", "rest": "rest_text", "alert": "alert_text", "idle": "text_muted"}


class ThemedArea(Gtk.DrawingArea):
    """A drawing area that repaints when the theme changes, but only while it is on screen."""

    def __init__(self, theme, **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self.connect("map", lambda _w: theme.subscribe(self.queue_draw))
        self.connect("unmap", lambda _w: theme.unsubscribe(self.queue_draw))

    def numeric_layout(self, text: str, size_px: float, weight: Pango.Weight) -> Pango.Layout:
        """Plain-text layout with tabular digits so a ticking value never shifts sideways."""
        layout = self.create_pango_layout(text)
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_size_new_absolute(int(size_px * Pango.SCALE)))
        attributes.insert(Pango.attr_weight_new(weight))
        attributes.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attributes)
        return layout


class ProgressRing(ThemedArea):
    def __init__(self, theme, size: int = 240, stroke: float = 12, text_px: float = 44):
        super().__init__(theme, accessible_role=Gtk.AccessibleRole.IMG)
        self._stroke = stroke
        self._text_px = text_px
        self._fraction = 1.0
        self._text = ""
        self._caption = ""
        self._tone = "focus"
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    @property
    def text(self) -> str:
        return self._text

    @property
    def fraction(self) -> float:
        return self._fraction

    @property
    def tone(self) -> str:
        return self._tone

    def set_progress(self, fraction: float, text: str, caption: str = "", tone: str = "focus") -> None:
        state = (max(0.0, min(1.0, fraction)), text, caption, tone)
        if state == (self._fraction, self._text, self._caption, self._tone):
            return
        self._fraction, self._text, self._caption, self._tone = state
        description = f"{caption}: {text}" if caption else text
        self.update_property([Gtk.AccessibleProperty.LABEL], [description])
        self.queue_draw()

    def _draw(self, _area, cr, width, height) -> None:
        theme = self.theme
        center_x, center_y = width / 2, height / 2
        radius = (min(width, height) - self._stroke) / 2 - 1
        cr.set_line_width(self._stroke)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        cr.set_source_rgb(*theme.rgb("bg_track"))
        cr.arc(center_x, center_y, radius, ARC_START, ARC_START + ARC_SWEEP)
        cr.stroke()

        if self._fraction > 0:
            cr.set_source_rgb(*theme.rgb(TONES[self._tone]))
            cr.arc(center_x, center_y, radius, ARC_START, ARC_START + ARC_SWEEP * self._fraction)
            cr.stroke()

        layout = self.numeric_layout(self._text, self._text_px, Pango.Weight.LIGHT)
        text_width, text_height = layout.get_pixel_size()
        offset = self._text_px * 0.28 if self._caption else 0
        cr.set_source_rgb(*theme.rgb("text_primary"))
        cr.move_to(center_x - text_width / 2, center_y - text_height / 2 - offset)
        PangoCairo.show_layout(cr, layout)

        if self._caption:
            caption = self.numeric_layout(self._caption, max(11, self._text_px * 0.32), Pango.Weight.NORMAL)
            caption_width, _caption_height = caption.get_pixel_size()
            cr.set_source_rgb(*theme.rgb("text_secondary"))
            cr.move_to(center_x - caption_width / 2, center_y + text_height / 2 - offset + 2)
            PangoCairo.show_layout(cr, caption)
