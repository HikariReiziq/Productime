"""Full-page form for creating or editing an item, shown in place of a view like a phone clock app."""

from gi.repository import Adw, Gtk

from .controls import bind_escape, hbox, icon_button, make_button, text_label, vbox

SAVE_WIDTH = 560


class EditorPage(Gtk.Box):
    """Close icon and title on top, a scrolling form, and a Save button pinned to the bottom.

    Subclasses fill ``self.body``, set ``self.name_entry``, and implement ``save()``.
    """

    def __init__(self, window, view_name: str, title: str, save_label: str, max_width: int = 600):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("editor-page")
        self.window = window
        self.view_name = view_name
        self.opener: Gtk.Widget | None = None
        self.name_entry: Gtk.Entry | None = None

        self.close_button = icon_button("window-close-symbolic", "Close without saving", on_click=self.close)
        self.title_label = text_label(title, "section-title", xalign=0.5, ellipsize=True)
        self.title_label.set_hexpand(True)
        # An empty box as wide as the close button keeps the title centred on the page.
        balance = Gtk.Box()
        balance.set_size_request(44, -1)
        top = hbox(8, self.close_button, self.title_label, balance)
        top.add_css_class("editor-top")

        self.body = vbox(20)
        clamp = Adw.Clamp(maximum_size=max_width, child=self.body)
        clamp.add_css_class("editor-body")
        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(clamp)

        self.error_label = text_label("", "error-text", wrap=True, xalign=0.5)
        self.error_label.set_visible(False)
        self.save_button = make_button(save_label, "primary", "editor-save", on_click=self.save)
        self.save_button.set_hexpand(True)
        footer = Adw.Clamp(maximum_size=SAVE_WIDTH, child=vbox(8, self.error_label, self.save_button))
        footer.add_css_class("editor-footer")

        for part in (top, self.scroller, footer):
            self.append(part)
        bind_escape(self)

    def save(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Leave without saving. Escape and the close icon both land here."""
        self.window.close_editor(self.view_name)

    def focus_first(self) -> None:
        if self.name_entry is not None:
            self.name_entry.grab_focus()

    def set_compact(self, compact: bool) -> None:
        pass

    def show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))
