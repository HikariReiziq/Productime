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


def wrap_row(*children: Gtk.Widget, spacing: int = 8, spread: bool = False) -> Adw.WrapBox:
    """A row that wraps onto more lines instead of overflowing a narrow window."""
    box = Adw.WrapBox(child_spacing=spacing, line_spacing=spacing)
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


def field(title: str, widget: Gtk.Widget, hint: str | None = None) -> Gtk.Box:
    """A captioned form field. The caption is also the control's accessible name."""
    box = vbox(6, text_label(title, "field-label"), widget)
    if hint:
        box.append(text_label(hint, "muted", "small", wrap=True))
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
    on_change: Callable[[int], None] | None = None,
) -> Gtk.SpinButton:
    widget = Gtk.SpinButton.new_with_range(low, high, 1)
    widget.set_numeric(True)
    widget.set_value(value)
    widget.set_width_chars(3)
    widget.add_css_class("numeric")
    if pad:
        widget.connect("output", _pad_output, pad)
    if on_change:
        widget.connect("value-changed", lambda w: on_change(w.get_value_as_int()))
    return widget


def spin_value(widget: Gtk.SpinButton) -> int:
    """Read a spin button, committing text the user typed but has not confirmed yet."""
    widget.update()
    return widget.get_value_as_int()


class ChoiceGroup(Gtk.Box):
    """Exactly-one-of-many toggle buttons, used for presets and segmented switches."""

    def __init__(
        self,
        options: Sequence[tuple[object, str]],
        value: object = None,
        on_change: Callable[[object], None] | None = None,
        segmented: bool = False,
        label: str | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.GROUP)
        self._on_change = on_change
        self._quiet = False
        self._buttons: dict[object, Gtk.ToggleButton] = {}
        if segmented:
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
            holder.add_css_class("segmented")
        else:
            holder = Adw.WrapBox(child_spacing=6, line_spacing=6)
            holder.add_css_class("choice-group")
            holder.set_hexpand(True)
        first = None
        for option_value, text in options:
            button = Gtk.ToggleButton(label=text)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._on_toggled, option_value)
            holder.append(button)
            self._buttons[option_value] = button
        self.append(holder)
        if label:
            set_accessible_label(self, label)
        initial = value if value in self._buttons else options[0][0]
        self.set_value(initial, notify=False)

    def _on_toggled(self, button: Gtk.ToggleButton, value: object) -> None:
        if button.get_active() and not self._quiet and self._on_change:
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

    def button_for(self, value: object) -> Gtk.ToggleButton:
        return self._buttons[value]


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

        # Escape is bound here explicitly so every dialog closes the same, verifiable way.
        shortcuts = Gtk.ShortcutController()
        shortcuts.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"), Gtk.CallbackAction.new(self._on_escape)
            )
        )
        self.add_controller(shortcuts)

    def _on_escape(self, *_args) -> bool:
        self.close()
        return True

    def add_footer_button(self, button: Gtk.Button) -> Gtk.Button:
        self.footer.append(button)
        self.footer.set_visible(True)
        return button
