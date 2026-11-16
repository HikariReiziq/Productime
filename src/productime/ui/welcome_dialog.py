"""First-run welcome: what Productime does, in four short pages."""

from gi.repository import Adw, Gtk

from ..paths import APP_NAME, brand_icon_path
from ..services import desktop_integration
from .widgets.controls import (
    bind_escape,
    make_button,
    set_accessible_label,
    set_button_text,
    text_label,
    vbox,
)

PAGES = (
    (
        None,
        "Welcome to Productime",
        "Plan focused work in batches, wake up on time, and see what you finished. "
        "Everything stays on this computer.",
    ),
    (
        "productime-today-symbolic",
        "Targets keep you honest",
        "Split a goal into Pomodoro batches and give each one a subtask. A break only starts "
        "after you check the subtask off.",
    ),
    (
        "productime-alarm-symbolic",
        "Alarms that make sure you are up",
        "The sound loops until you act. Add a typing or math mission so it cannot be stopped half asleep.",
    ),
    (
        "productime-pin-symbolic",
        "Works in the background",
        "GNOME notifications get quick buttons like Stop and Snooze. Keep these on so alarms "
        "still ring after you close the window or restart.",
    ),
)


class WelcomeDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__()
        self.set_title(f"Welcome to {APP_NAME}")
        self.set_content_width(520)
        self.set_content_height(560)
        self._window = window
        self._app = window.app
        self.finished = False

        self.carousel = Adw.Carousel(vexpand=True, allow_scroll_wheel=False)
        self.pages = []
        for index, (icon, title, body) in enumerate(PAGES):
            page = self._page(icon, title, body)
            if index == len(PAGES) - 1:
                page.append(self._background_switches())
            self.carousel.append(page)
            self.pages.append(page)
        self.carousel.connect("page-changed", lambda *_: self._update_buttons())
        dots = Adw.CarouselIndicatorDots(carousel=self.carousel)

        self.skip_button = make_button("Skip", "ghost", on_click=self.close)
        self.back_button = make_button("Back", "secondary", on_click=lambda: self.go(-1))
        self.next_button = make_button("Next", "primary", on_click=self._on_next)
        actions = Gtk.CenterBox()
        actions.set_start_widget(self.skip_button)
        actions.set_center_widget(dots)
        end = Gtk.Box(spacing=8)
        end.append(self.back_button)
        end.append(self.next_button)
        actions.set_end_widget(end)
        actions.set_margin_start(20)
        actions.set_margin_end(20)
        actions.set_margin_bottom(20)

        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("welcome")
        header = Adw.HeaderBar(show_title=False)
        toolbar.add_top_bar(header)
        toolbar.set_content(self.carousel)
        toolbar.add_bottom_bar(actions)
        self.set_child(toolbar)
        self.connect("closed", self._on_closed)
        bind_escape(self)
        self._update_buttons()

    def _page(self, icon: str | None, title: str, body: str) -> Gtk.Box:
        if icon is None:
            art = Gtk.Image(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            art.set_from_file(str(brand_icon_path(128)))
            art.set_pixel_size(112)
            art.add_css_class("brand-mark")
            art.set_overflow(Gtk.Overflow.HIDDEN)
        else:
            image = Gtk.Image(icon_name=icon, pixel_size=48, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            image.add_css_class("welcome-art")
            image.set_hexpand(True)
            image.set_vexpand(True)
            art = Gtk.Box(halign=Gtk.Align.CENTER)
            art.add_css_class("welcome-art-well")
            art.append(image)
        art.set_halign(Gtk.Align.CENTER)
        heading = text_label(title, "welcome-title", xalign=0.5, wrap=True)
        heading.set_justify(Gtk.Justification.CENTER)
        text = text_label(body, "secondary", xalign=0.5, wrap=True)
        text.set_justify(Gtk.Justification.CENTER)
        text.set_max_width_chars(44)
        page = vbox(16, art, heading, text)
        page.add_css_class("welcome-page")
        page.set_valign(Gtk.Align.CENTER)
        page.set_hexpand(True)
        return page

    def _background_switches(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        self.autostart_row = Adw.SwitchRow(title="Start at login", use_markup=False, active=True)
        self.keep_running_row = Adw.SwitchRow(
            title="Keep running when closed",
            use_markup=False,
            active=self._window.keep_running_when_closed(),
        )
        group.add(self.autostart_row)
        group.add(self.keep_running_row)
        group.set_margin_top(8)
        return group

    @property
    def page_index(self) -> int:
        return round(self.carousel.get_position())

    def go(self, delta: int) -> None:
        index = max(0, min(len(self.pages) - 1, self.page_index + delta))
        self.carousel.scroll_to(self.pages[index], False)
        self._update_buttons()

    def _update_buttons(self) -> None:
        last = self.page_index == len(self.pages) - 1
        set_button_text(self.next_button, "Get Started" if last else "Next")
        set_accessible_label(self.next_button, "Get Started" if last else "Next page")
        self.back_button.set_visible(self.page_index > 0)
        self.skip_button.set_visible(not last)

    def _on_next(self) -> None:
        if self.page_index < len(self.pages) - 1:
            self.go(1)
            return
        self.finish()

    def finish(self) -> None:
        """Apply the choices on the last page. Skipping never turns on start at login."""
        try:
            desktop_integration.set_autostart(self._app.config_dir, self.autostart_row.get_active())
        except OSError as error:
            self._window.toast(f"Start at login could not be set: {error}")
        self._window.set_keep_running(self.keep_running_row.get_active())
        self.finished = True
        self.close()

    def _on_closed(self, _dialog) -> None:
        self._app.repo.set_pref("onboarding_done", "1")
