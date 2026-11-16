"""Small modal of option cards, opened from a row or chip on an editor page.

Nothing changes on the page until Save; Cancel and Escape leave it as it was.
"""

from collections.abc import Callable, Sequence

from gi.repository import Gtk

from .controls import AppDialog, ChoiceGroup, hbox, make_button, text_label, vbox


class OptionSheet(AppDialog):
    def __init__(self, title: str, on_save: Callable[[], str | None], width: int = 440):
        super().__init__(title, width=width)
        self._on_save = on_save
        self._describe: dict[str, Callable[[object], str]] = {}
        self.groups: dict[str, ChoiceGroup] = {}
        self.sections: dict[str, Gtk.Box] = {}
        self.section_values: dict[str, Gtk.Label] = {}
        self.focus_widget: Gtk.Widget | None = None
        self.saved = False
        # Called once with whether Save closed the sheet, so a page can undo a choice on Cancel.
        self.on_closed: Callable[[bool], None] | None = None
        self._finished = False
        self.connect("closed", lambda _sheet: self._finish())

        self.cards = vbox(12)
        self.error_label = text_label("", "error-text", wrap=True)
        self.error_label.set_visible(False)
        self.body.append(self.cards)
        self.body.append(self.error_label)
        self.cancel_button = self.add_footer_button(make_button("Cancel", "secondary", on_click=self.close))
        self.save_button = self.add_footer_button(make_button("Save", "primary", on_click=self.save))

    def add_section(
        self,
        key: str,
        title: str,
        options: Sequence[tuple],
        value: object,
        *,
        visible: int | None = None,
        chips: bool = False,
        tile: Callable[[object, Gtk.Label], Gtk.Widget] | None = None,
        describe: Callable[[object], str] | None = None,
        on_change: Callable[[object], None] | None = None,
        extra: Gtk.Widget | None = None,
    ) -> ChoiceGroup:
        """A card with a title, the chosen value in accent on the right, and its choices below."""

        def changed(chosen: object) -> None:
            self.refresh(key)
            if on_change:
                on_change(chosen)

        group = ChoiceGroup(
            options,
            value,
            on_change=changed,
            label=title,
            radio=not (chips or tile),
            fill=chips,
            visible=visible,
            tile=tile,
        )
        caption = text_label(title, "body-strong")
        caption.set_hexpand(True)
        shown = text_label("", "accent-value", xalign=1.0)
        card = vbox(10, hbox(8, caption, shown), group)
        if extra is not None:
            card.append(extra)
        card.add_css_class("option-section")
        self.cards.append(card)
        self.groups[key] = group
        self.sections[key] = card
        self.section_values[key] = shown
        self._describe[key] = describe or group.text_for
        self.refresh(key)
        return group

    def add_card(self, *children: Gtk.Widget) -> Gtk.Box:
        card = vbox(10, *children)
        card.add_css_class("option-section")
        self.cards.append(card)
        return card

    def refresh(self, key: str | None = None) -> None:
        for name in [key] if key else list(self.groups):
            self.section_values[name].set_text(self._describe[name](self.groups[name].get_value()))

    def show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def close(self) -> bool:
        # Cancel, Save, and Escape come through here, so the page updates before the close animation.
        self._finish()
        return super().close()

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            if self.on_closed is not None:
                self.on_closed(self.saved)

    def save(self) -> None:
        error = self._on_save()
        if error:
            self.show_error(error)
            return
        self.saved = True
        self.close()

    def reserve_height(self) -> None:
        """Size the sheet for its tallest state, because libadwaita does not regrow a floating dialog
        when a hidden section appears later."""
        hidden, stack = [], [self.cards]
        while stack:
            widget = stack.pop()
            if not widget.get_visible():
                hidden.append(widget)
                widget.set_visible(True)
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        natural = self.toolbar.measure(Gtk.Orientation.VERTICAL, self.get_content_width())[1]
        for widget in hidden:
            widget.set_visible(False)
        self.set_content_height(natural)

    def present_over(self, parent: Gtk.Widget) -> None:
        self.present(parent)
        if self.focus_widget is not None:
            self.focus_widget.grab_focus()
