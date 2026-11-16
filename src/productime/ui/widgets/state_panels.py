"""Empty, loading, and error panels, plus the stack that switches a data view between them."""

import sqlite3
from collections.abc import Callable

from gi.repository import GLib, Gtk

from .controls import make_button, text_label, vbox


class StatePanel(Gtk.Box):
    def __init__(
        self,
        title: str,
        description: str,
        action_label: str | None = None,
        on_action: Callable[[], None] | None = None,
        icon: str | None = None,
        spinner: bool = False,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("state-panel")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.spinner = None
        if spinner:
            self.spinner = Gtk.Spinner()
            self.spinner.set_size_request(28, 28)
            self.spinner.set_halign(Gtk.Align.CENTER)
            self.append(self.spinner)
        elif icon:
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(32)
            image.add_css_class("muted")
            self.append(image)
        self.title_label = text_label(title, "state-title", xalign=0.5, wrap=True)
        self.title_label.set_justify(Gtk.Justification.CENTER)
        self.description_label = text_label(description, "secondary", xalign=0.5, wrap=True)
        self.description_label.set_justify(Gtk.Justification.CENTER)
        self.description_label.set_max_width_chars(52)
        self.append(self.title_label)
        self.append(self.description_label)
        self.action_button = None
        if action_label and on_action:
            self.action_button = make_button(action_label, "primary", on_click=on_action)
            self.action_button.set_halign(Gtk.Align.CENTER)
            self.action_button.set_margin_top(6)
            self.append(self.action_button)


class ViewStates(Gtk.Stack):
    """Shows exactly one of: loading, empty, error, or the real content."""

    def __init__(
        self,
        content: Gtk.Widget,
        *,
        noun: str,
        on_retry: Callable[[], None],
        empty: StatePanel | None = None,
    ):
        super().__init__()
        self.set_transition_type(Gtk.StackTransitionType.NONE)
        self.set_vhomogeneous(False)
        self.set_hhomogeneous(False)
        self._noun = noun
        self._pending = 0
        self.loading_panel = StatePanel(f"Loading {noun}", "Reading from the local database.", spinner=True)
        self.empty_panel = empty
        self.error_panel = StatePanel(
            f"Could not load {noun}",
            "",
            action_label="Try Again",
            on_action=on_retry,
            icon="dialog-error-symbolic",
        )
        self.content = content
        self.add_named(self.loading_panel, "loading")
        if empty is not None:
            self.add_named(empty, "empty")
        self.add_named(self.error_panel, "error")
        self.add_named(vbox(0, content), "content")
        self.show_loading()

    @property
    def state(self) -> str:
        return self.get_visible_child_name()

    def _show(self, name: str) -> None:
        # The spinner animates only while it is the visible page.
        if name == "loading":
            self.loading_panel.spinner.start()
        else:
            self.loading_panel.spinner.stop()
        self.set_visible_child_name(name)

    def show_loading(self) -> None:
        self._show("loading")

    def show_empty(self) -> None:
        self._show("empty")

    def show_content(self) -> None:
        self._show("content")

    def show_error(self, detail: str) -> None:
        message = (
            f"The saved {self._noun} could not be read. Your data file is untouched. Details: {detail[:200]}"
        )
        self.error_panel.description_label.set_text(message)
        self._show("error")

    def load(self, fetch: Callable[[], object], render: Callable[[object], None]) -> None:
        """Fetch on the next idle so the loading panel paints first, then render or report."""
        self.show_loading()
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.idle_add(self._run, fetch, render)

    def run_now(self, fetch: Callable[[], object], render: Callable[[object], None]) -> None:
        """Refresh without flashing the loading panel, for updates to an already shown view."""
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0
        self._run(fetch, render)

    def _run(self, fetch, render) -> bool:
        self._pending = 0
        try:
            data = fetch()
        except (sqlite3.Error, OSError) as error:
            self.show_error(str(error) or type(error).__name__)
            return False
        render(data)
        return False
