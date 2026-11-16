"""Small widget builders shared by every view."""

from collections.abc import Callable, Sequence

from gi.repository import Adw, Gtk, Pango


def set_accessible_label(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def text_label(
    text: str = "",
    *css_classes: str,
    xalign: float = 0.0,
    wrap: bool = False,
    ellipsize: bool = False,
) -> Gtk.Label:
    """A label whose content is always plain text, so user strings can never inject markup."""
    widget = Gtk.Label(xalign=xalign)
    widget.set_text(text)
    for name in css_classes:
        # Empty names come from optional styles, such as a custom category's neutral colour.
        if name:
            widget.add_css_class(name)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    return widget


def make_button(
    text: str,
    *css_classes: str,
    on_click: Callable[[], None] | None = None,
    icon: str | None = None,
    tooltip: str | None = None,
) -> Gtk.Button:
    button = Gtk.Button()
    if icon:
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.set_halign(Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(text_label(text, xalign=0.5))
        button.set_child(content)
        set_accessible_label(button, text)
    else:
        button.set_label(text)
    for name in css_classes:
        button.add_css_class(name)
    if tooltip:
        button.set_tooltip_text(tooltip)
    if on_click:
        button.connect("clicked", lambda _button: on_click())
    return button


def set_button_text(button: Gtk.Button, text: str) -> None:
    """Change the caption of a button built by make_button, with or without an icon."""
    child = button.get_child()
    if isinstance(child, Gtk.Box):
        child.get_last_child().set_text(text)
        set_accessible_label(button, text)
    else:
        button.set_label(text)


def icon_button(
    icon: str, tooltip: str, *css_classes: str, on_click: Callable[[], None] | None = None
) -> Gtk.Button:
    """Icon-only button. The tooltip doubles as its accessible name."""
    button = Gtk.Button.new_from_icon_name(icon)
    button.set_tooltip_text(tooltip)
    set_accessible_label(button, tooltip)
    for name in css_classes or ("ghost",):
        button.add_css_class(name)
    if on_click:
        button.connect("clicked", lambda _button: on_click())
    return button


def chip(text: str, kind: str = "") -> Gtk.Label:
    # Ellipsizing lets a long chip shrink instead of forcing a narrow window wider.
    widget = text_label(text, "chip", xalign=0.5, ellipsize=True)
    if kind:
        widget.add_css_class(kind)
    widget.set_valign(Gtk.Align.CENTER)
    return widget


def hbox(spacing: int = 8, *children: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def vbox(spacing: int = 8, *children: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for child in children:
        box.append(child)
    return box


def tag_style(tag_color: str) -> str:
    """CSS class for a category colour. Custom categories use the neutral chip style."""
    return {"red": "work", "purple": "study", "blue": "coding", "amber": "personal"}.get(tag_color, "")


def wrap_row(
    *children: Gtk.Widget, spacing: int = 8, spread: bool = False, center: bool = False
) -> Adw.WrapBox:
    """A row that wraps onto more lines instead of overflowing a narrow window."""
    box = Adw.WrapBox(child_spacing=spacing, line_spacing=spacing)
    if center:
        box.set_align(0.5)
    if spread:
        # A single line is also the last line, which is only justified when asked.
        box.set_justify(Adw.JustifyMode.SPREAD)
        box.set_justify_last_line(True)
    for child in children:
        box.append(child)
    return box


def clear_box(box: Gtk.Widget) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def field(title: str, widget: Gtk.Widget, hint: str | None = None, center: bool = False) -> Gtk.Box:
    """A captioned form field. The caption is also the control's accessible name."""
    caption = text_label(title, "field-label", xalign=0.5 if center else 0.0)
    box = vbox(6, caption, widget)
    if hint:
        note = text_label(hint, "muted", "small", wrap=True, xalign=0.5 if center else 0.0)
        if center:
            note.set_justify(Gtk.Justification.CENTER)
        box.append(note)
    set_accessible_label(widget, title)
    return box


def switch_row(title: str, switch: Gtk.Switch) -> Gtk.Box:
    """A captioned switch whose caption is part of the tap target."""
    # Wrapping keeps a long caption from widening the fixed-width rail.
    caption = text_label(title, "body-strong", wrap=True)
    caption.set_hexpand(True)
    caption.set_size_request(-1, 44)
    click = Gtk.GestureClick()
    click.connect("released", lambda *_: switch.set_active(not switch.get_active()))
    caption.add_controller(click)
    switch.set_valign(Gtk.Align.CENTER)
    set_accessible_label(switch, title)
    return hbox(8, caption, switch)


def _pad_output(spin_button: Gtk.SpinButton, width: int) -> bool:
    spin_button.set_text(f"{int(spin_button.get_adjustment().get_value()):0{width}d}")
    return True


def spin(
    low: int,
    high: int,
    value: int,
    *,
    pad: int = 0,
    big: bool = False,
    on_change: Callable[[int], None] | None = None,
) -> Gtk.SpinButton:
    """A GNOME Clocks style number field: plus above, the value in the middle, minus below."""
    widget = Gtk.SpinButton.new_with_range(low, high, 1)
    widget.set_orientation(Gtk.Orientation.VERTICAL)
    widget.set_numeric(True)
    widget.set_value(value)
    widget.set_width_chars(2 if big else 3)
    widget.set_alignment(0.5)
    widget.set_halign(Gtk.Align.START)
    widget.add_css_class("numeric")
    if big:
        widget.add_css_class("big")
    if pad:
        widget.connect("output", _pad_output, pad)
    if on_change:
        widget.connect("value-changed", lambda w: on_change(w.get_value_as_int()))
    return widget


def time_picker(
    parts: Sequence[tuple[str, Gtk.SpinButton]], trailing: Gtk.Widget | None = None, separator: str = ":"
) -> Gtk.Grid:
    """Big spin buttons separated by colons, like a clock face, with each caption underneath."""
    grid = Gtk.Grid(column_spacing=8, row_spacing=6, halign=Gtk.Align.START)
    column = 0
    for index, (caption, spin_button) in enumerate(parts):
        if index:
            colon = text_label(separator, "time-colon", "numeric", xalign=0.5)
            colon.set_valign(Gtk.Align.CENTER)
            grid.attach(colon, column, 0, 1, 1)
            column += 1
        set_accessible_label(spin_button, caption)
        # GTK shows a parent's tooltip over the plus and minus buttons inside the field.
        spin_button.set_tooltip_text(caption)
        grid.attach(spin_button, column, 0, 1, 1)
        grid.attach(text_label(caption, "muted", "small", xalign=0.5), column, 1, 1, 1)
        column += 1
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        trailing.set_margin_start(8)
        grid.attach(trailing, column, 0, 1, 1)
    return grid


def spin_value(widget: Gtk.SpinButton) -> int:
    """Read a spin button, committing text the user typed but has not confirmed yet."""
    widget.update()
    return widget.get_value_as_int()


class ChoiceGroup(Gtk.Box):
    """Exactly-one-of-many choice, shown as pill toggles, a segmented switch, or a radio list.

    Each option is (value, text) with an optional icon name and tooltip after it.
    """

    def __init__(
        self,
        options: Sequence[tuple],
        value: object = None,
        on_change: Callable[[object], None] | None = None,
        segmented: bool = False,
        label: str | None = None,
        vertical: bool = False,
        center: bool = False,
        fill: bool = False,
        radio: bool = False,
        visible: int | None = None,
        tile: Callable[[object, Gtk.Label], Gtk.Widget] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.GROUP)
        self._on_change = on_change
        self._quiet = False
        self._buttons: dict[object, Gtk.Widget] = {}
        self._labels: dict[object, Gtk.Label] = {}
        self._current: object = None
        # The choice before the latest one, so a sheet opened by an option can be cancelled back to it.
        self.previous: object = None
        self.more_button: Gtk.Button | None = None
        self._more_box: Gtk.Box | None = None
        self._tile = tile
        if radio:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.add_css_class("radio-list")
        elif segmented:
            orientation = Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
            holder = Gtk.Box(orientation=orientation, homogeneous=True)
            holder.add_css_class("segmented")
        elif fill or tile:
            # One line whose buttons share the full width evenly, so a row never wraps unevenly.
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
            holder.add_css_class("choice-group")
            holder.add_css_class("tiles" if tile else "fill")
        else:
            holder = Adw.WrapBox(child_spacing=6, line_spacing=6)
            holder.add_css_class("choice-group")
            holder.set_hexpand(True)
            if center:
                holder.set_align(0.5)
        self.append(holder)
        if radio and visible is not None and len(options) > visible:
            # Long lists show the common choices first, like a phone clock's "More options".
            self._more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
            self._more_box.add_css_class("radio-list")
            self.more_button = make_button(
                "More options", "ghost", "more-options", on_click=self.expand, icon="pan-down-symbolic"
            )
            self.more_button.set_halign(Gtk.Align.START)
            self.append(self._more_box)
            self.append(self.more_button)

        first = None
        for index, option in enumerate(options):
            option_value, text = option[0], option[1]
            icon = option[2] if len(option) > 2 else None
            tooltip = option[3] if len(option) > 3 else None
            button = self._make_option(option_value, text, icon, radio)
            if tooltip:
                button.set_tooltip_text(tooltip)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._on_toggled, option_value)
            if fill or tile:
                button.set_hexpand(True)
            in_more = self._more_box is not None and index >= visible
            (self._more_box if in_more else holder).append(button)
            self._buttons[option_value] = button
        if label:
            set_accessible_label(self, label)
        initial = value if value in self._buttons else options[0][0]
        self.set_value(initial, notify=False)

    def _make_option(self, value: object, text: str, icon: str | None, radio: bool) -> Gtk.Widget:
        caption = text_label(text, xalign=0.0 if radio else 0.5)
        self._labels[value] = caption
        button = Gtk.CheckButton() if radio else Gtk.ToggleButton()
        if self._tile is not None:
            # A tile draws its own picture around the caption; its value names a style class.
            button.set_child(self._tile(value, caption))
            button.add_css_class(f"option-{value}")
        elif icon:
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_valign(Gtk.Align.CENTER)
            if radio:
                image.add_css_class("option-icon")
            content = hbox(12 if radio else 8, image, caption)
            if not radio:
                content.set_halign(Gtk.Align.CENTER)
            button.set_child(content)
        else:
            button.set_child(caption)
        set_accessible_label(button, text)
        return button

    def _on_toggled(self, button: Gtk.Widget, value: object) -> None:
        if not button.get_active():
            return
        self.previous, self._current = self._current, value
        if not self._quiet and self._on_change:
            self._on_change(value)

    def get_value(self) -> object:
        for value, button in self._buttons.items():
            if button.get_active():
                return value
        return None

    def set_value(self, value: object, notify: bool = True) -> None:
        self._quiet = not notify
        self._buttons[value].set_active(True)
        self._quiet = False
        if self._more_box is not None and self._buttons[value].get_parent() is self._more_box:
            self.expand()

    def set_text(self, value: object, text: str) -> None:
        """Rename one option, such as a Custom chip that now shows the saved custom value."""
        self._labels[value].set_text(text)
        set_accessible_label(self._buttons[value], text)

    def text_for(self, value: object) -> str:
        return self._labels[value].get_text()

    def expand(self) -> None:
        if self._more_box is not None:
            self._more_box.set_visible(True)
            self.more_button.set_visible(False)

    def button_for(self, value: object) -> Gtk.Widget:
        return self._buttons[value]


class SettingRow(Gtk.Button):
    """A tappable row: icon, title, the current value in the accent colour, and a chevron to its sheet."""

    def __init__(self, icon: str, title: str, tooltip: str, on_activate: Callable[[], None] | None = None):
        super().__init__()
        self.add_css_class("setting-row")
        self._title = title
        well = Gtk.Image.new_from_icon_name(icon)
        well.add_css_class("row-icon")
        well.set_valign(Gtk.Align.CENTER)
        caption = text_label(title, "body-strong", ellipsize=True)
        caption.set_hexpand(True)
        self.value_label = text_label("", "accent-value", "numeric", xalign=1.0)
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        self.set_child(hbox(12, well, caption, self.value_label, chevron))
        self.set_tooltip_text(tooltip)
        if on_activate:
            self.connect("clicked", lambda _row: on_activate())

    def set_value(self, text: str) -> None:
        self.value_label.set_text(text)
        set_accessible_label(self, f"{self._title}: {text}")


def bind_escape(dialog: Gtk.Widget) -> None:
    """Bind Escape explicitly so every dialog and editor page closes the same, verifiable way."""

    def close(*_args) -> bool:
        dialog.close()
        return True

    shortcuts = Gtk.ShortcutController()
    shortcuts.add_shortcut(
        Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.CallbackAction.new(close))
    )
    dialog.add_controller(shortcuts)


class AppDialog(Adw.Dialog):
    """Dialog chrome shared by every form and alert: title bar, scrolling body, footer."""

    def __init__(self, title: str, width: int = 480, height: int = -1):
        super().__init__()
        self.set_title(title)
        self.set_content_width(width)
        if height > 0:
            self.set_content_height(height)
        self.body = vbox(16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.body, f"set_margin_{edge}")(20)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(self.body)
        self.footer = hbox(8)
        self.footer.set_halign(Gtk.Align.END)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.footer, f"set_margin_{edge}")(12)
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(Adw.HeaderBar())
        self.toolbar.set_content(scroller)
        self.toolbar.add_bottom_bar(self.footer)
        self.footer.set_visible(False)
        self.set_child(self.toolbar)

        bind_escape(self)

    def add_footer_button(self, button: Gtk.Button) -> Gtk.Button:
        self.footer.append(button)
        self.footer.set_visible(True)
        return button
