"""Adaptive shell: icon rail or bottom bar, live session pill, alert recovery bar, and backup actions."""

import sqlite3
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from ..domain.formatters import format_countdown
from ..domain.models import TargetPhase
from ..paths import APP_ID, APP_NAME, brand_icon_path
from ..services import backup_service
from ..services.backup_service import BackupError
from .widgets.controls import (
    hbox,
    make_button,
    set_accessible_label,
    text_label,
    vbox,
)
from .widgets.state_panels import StatePanel

NAV_ITEMS = (
    ("today", "Today", "productime-today-symbolic"),
    ("alarm", "Alarm", "productime-alarm-symbolic"),
    ("stopwatch", "Stopwatch", "productime-stopwatch-symbolic"),
    ("timer", "Timer", "productime-timer-symbolic"),
    ("report", "Report", "productime-report-symbolic"),
)
COMPACT_CONDITION = "max-width: 899px"
# A full-screen or large window: clocks and the timeline grow to use the space.
LARGE_CONDITION = "min-width: 1400px and min-height: 860px"
LARGE_PAGE_WIDTH = 1600
ALERT_BAR_TEXT = {
    "alarm": "Alarm ringing: {title}",
    "gate": "Waiting for check-off: {title}",
    "ready": "Break is over: {title}",
    "timer": "Timer finished: {title}",
}


def build_startup_error_window(app, detail: str) -> Adw.ApplicationWindow:
    """Shown instead of the app when the database cannot be opened at launch."""
    window = Adw.ApplicationWindow(application=app)
    window.set_title(APP_NAME)
    window.add_css_class("productime")
    window.set_default_size(560, 420)
    panel = StatePanel(
        "Productime could not open its data",
        f"The local database could not be opened, so nothing was loaded or changed. Details: {detail[:240]}",
        action_label="Quit Productime",
        on_action=app.quit_app,
        icon="dialog-error-symbolic",
    )
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    toolbar.set_content(panel)
    window.set_content(toolbar)
    window.error_panel = panel
    window.teardown = lambda: app.forget_window(window)
    window.connect("close-request", lambda _w: window.teardown())
    return window


class ProductimeWindow(Adw.ApplicationWindow):
    def __init__(self, app, default_width: int = 1180, default_height: int = 780):
        super().__init__(application=app)
        self.app = app
        self.scheduler = app.scheduler
        self.compact = False
        self.current: str | None = None
        self.last_confirm: Adw.AlertDialog | None = None
        self.last_error: Adw.AlertDialog | None = None
        self.last_toast: str | None = None
        self.settings_dialog = None
        self.welcome_dialog = None
        self._views: dict[str, Gtk.Widget] = {}
        self._editors: dict[str, Gtk.Widget] = {}
        self._rail_buttons: dict[str, Gtk.ToggleButton] = {}
        self._bottom_buttons: dict[str, Gtk.ToggleButton] = {}
        self._theme_buttons: list[Gtk.Button] = []
        self._pin_buttons: list[Gtk.ToggleButton] = []
        self._settings_buttons: list[Gtk.Button] = []
        self._syncing = False
        self._tick_id = 0
        self._alert_key: str | None = None
        self.alert_dialog = None
        self._alert_collapsed = False
        self._announced_target: str | None = None
        self._told_background = False

        self.set_title(APP_NAME)
        self.set_icon_name(APP_ID)
        self.add_css_class("productime")
        self.set_default_size(default_width, default_height)
        # Breakpoints need an explicit minimum; 420px is the narrowest supported layout.
        self.set_size_request(420, 560)
        self._build()
        self._install_actions()
        self._sync_tools()

        app.audio.on_error = self.toast
        app.theme.subscribe(self._sync_tools)
        self.scheduler.subscribe(self._on_scheduler_changed)
        self.connect("close-request", self._on_close_request)
        self.connect("map", lambda _w: self.update_tick())
        self.connect("unmap", lambda _w: self.update_tick())
        self.navigate("today")
        self._on_scheduler_changed()

    def _build(self) -> None:
        self.alert_bar_label = text_label("", "body-strong", wrap=True)
        self.alert_bar_label.set_hexpand(True)
        self.alert_bar_button = make_button("Open", "secondary", on_click=self.reopen_alert)
        self.alert_bar = hbox(12, self.alert_bar_label, self.alert_bar_button)
        self.alert_bar.add_css_class("alert-bar")
        self.alert_bar.set_visible(False)

        self.rail = self._build_rail()
        self.bottom_bar = self._build_bottom_bar()
        self.bottom_bar.set_visible(False)

        self.compact_brand_label = text_label(APP_NAME, "brand-title")
        self.compact_brand = hbox(8, self._brand_image(24), self.compact_brand_label)
        self.compact_brand.set_visible(False)
        self.live_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.live_dot.add_css_class("status-dot")
        self.live_label = text_label("", "numeric", ellipsize=True)
        self.live_label.set_max_width_chars(36)
        self.live_pill = Gtk.Button(child=hbox(8, self.live_dot, self.live_label))
        self.live_pill.add_css_class("live-pill")
        self.live_pill.set_tooltip_text("Open the running session")
        self.live_pill.connect("clicked", lambda _b: self._open_live_session())
        self.live_pill.set_visible(False)
        self.header_tools = hbox(2, *self._tool_buttons())
        self.header_tools.add_css_class("header-tools")
        self.header_tools.set_visible(False)
        header = Adw.HeaderBar()
        header.set_title_widget(hbox(10, self.compact_brand, self.live_pill))
        header.pack_end(self.header_tools)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vhomogeneous(False)
        # Only the visible view decides the width, so one wide view cannot widen the others.
        self.stack.set_hhomogeneous(False)
        self.page = Adw.Clamp(maximum_size=1120)
        self.page.set_child(self.stack)
        for edge, value in (("top", 12), ("bottom", 32), ("start", 28), ("end", 28)):
            getattr(self.page, f"set_margin_{edge}")(value)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.page)
        self.views_page = scroller

        # Create and Edit pages replace the view beside the rail, each with its own scrolling and footer.
        self.content = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False)
        self.content.set_transition_type(Gtk.StackTransitionType.NONE)
        self.content.add_named(scroller, "views")

        main = Adw.ToolbarView()
        main.add_top_bar(header)
        main.set_content(vbox(0, self.alert_bar, self.content))
        main.set_hexpand(True)

        body = hbox(0, self.rail, main)
        body.set_vexpand(True)
        self.root = vbox(0, body, self.bottom_bar)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.root)
        self.set_content(self.toast_overlay)

        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(COMPACT_CONDITION))
        breakpoint_.add_setter(self.rail, "visible", False)
        breakpoint_.add_setter(self.bottom_bar, "visible", True)
        breakpoint_.add_setter(self.compact_brand, "visible", True)
        breakpoint_.add_setter(self.header_tools, "visible", True)
        breakpoint_.add_setter(self.page, "margin-start", 16)
        breakpoint_.add_setter(self.page, "margin-end", 16)
        breakpoint_.add_setter(self.page, "margin-top", 16)
        breakpoint_.connect("apply", lambda _b: self._set_compact(True))
        breakpoint_.connect("unapply", lambda _b: self._set_compact(False))
        self.add_breakpoint(breakpoint_)

        large = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(LARGE_CONDITION))
        large.add_setter(self.page, "maximum-size", LARGE_PAGE_WIDTH)
        large.connect("apply", lambda _b: self.add_css_class("large"))
        large.connect("unapply", lambda _b: self.remove_css_class("large"))
        self.add_breakpoint(large)

    def _brand_image(self, size: int) -> Gtk.Image:
        image = Gtk.Image(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        image.set_from_file(str(brand_icon_path(size)))
        image.set_pixel_size(size)
        image.add_css_class("brand-mark")
        image.set_overflow(Gtk.Overflow.HIDDEN)
        return image

    def _nav_button(self, name: str, title: str, icon: str) -> Gtk.ToggleButton:
        """Icon-only navigation. The name shows as a tooltip and is the accessible label."""
        button = Gtk.ToggleButton(icon_name=icon, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        button.add_css_class("rail-item")
        button.set_tooltip_text(title)
        set_accessible_label(button, title)
        button.connect("toggled", self._on_nav_toggled, name)
        return button

    def _tool_buttons(self) -> tuple[Gtk.Widget, ...]:
        """Theme, keep-running, and Settings icons. Built for the rail and for the compact header."""
        theme = Gtk.Button(halign=Gtk.Align.CENTER)
        theme.add_css_class("rail-tool")
        theme.connect("clicked", lambda _b: self.toggle_theme())
        pin = Gtk.ToggleButton(icon_name="productime-pin-symbolic", halign=Gtk.Align.CENTER)
        pin.add_css_class("rail-tool")
        pin.connect("toggled", self._on_pin_toggled)
        settings = Gtk.Button(icon_name="productime-settings-symbolic", halign=Gtk.Align.CENTER)
        settings.add_css_class("rail-tool")
        settings.set_tooltip_text("Settings")
        set_accessible_label(settings, "Settings")
        settings.connect("clicked", lambda _b: self.open_settings_dialog())
        self._theme_buttons.append(theme)
        self._pin_buttons.append(pin)
        self._settings_buttons.append(settings)
        return theme, pin, settings

    def _build_rail(self) -> Gtk.Box:
        rail = vbox(0)
        rail.add_css_class("rail")
        rail.set_size_request(80, -1)
        # Children that expand would otherwise make the rail share the window's spare width.
        rail.set_hexpand(False)
        brand = self._brand_image(40)
        brand.set_halign(Gtk.Align.CENTER)
        brand.set_tooltip_text(APP_NAME)
        brand.set_margin_bottom(14)
        self.rail_brand = brand
        rail.append(brand)

        nav = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            accessible_role=Gtk.AccessibleRole.NAVIGATION,
        )
        set_accessible_label(nav, "Main navigation")
        first = None
        for name, title, icon in NAV_ITEMS:
            button = self._nav_button(name, title, icon)
            if first is None:
                first = button
            else:
                button.set_group(first)
            self._rail_buttons[name] = button
            nav.append(button)
        rail.append(nav)

        spacer = Gtk.Box(vexpand=True)
        rail.append(spacer)
        divider = Gtk.Box()
        divider.add_css_class("rail-divider")
        rail.append(divider)
        self.rail_tools = vbox(6, *self._tool_buttons())
        rail.append(self.rail_tools)
        return rail

    def _build_bottom_bar(self) -> Gtk.Box:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            homogeneous=True,
            accessible_role=Gtk.AccessibleRole.NAVIGATION,
        )
        bar.add_css_class("nav-bar")
        set_accessible_label(bar, "Main navigation")
        first = None
        for name, title, icon in NAV_ITEMS:
            button = self._nav_button(name, title, icon)
            if first is None:
                first = button
            else:
                button.set_group(first)
            self._bottom_buttons[name] = button
            bar.append(button)
        return bar

    def _install_actions(self) -> None:
        nav = Gio.SimpleAction.new("nav", GLib.VariantType.new("s"))
        nav.connect("activate", lambda _a, target: self.navigate(target.get_string()))
        self.add_action(nav)
        create = Gio.SimpleAction.new("create", None)
        create.connect("activate", lambda *_: self._create_in_current_view())
        self.add_action(create)

    def _create_in_current_view(self) -> None:
        view = self._views.get(self.current or "")
        if view is not None and hasattr(view, "create"):
            view.create()

    def _create_view(self, name: str) -> Gtk.Widget:
        """Views are built on first visit, so an idle launch only pays for the Today screen."""
        if name == "today":
            from .views.target.today_view import TodayView

            return TodayView(self)
        if name == "alarm":
            from .views.alarm.alarm_view import AlarmView

            return AlarmView(self)
        if name == "stopwatch":
            from .views.stopwatch.stopwatch_view import StopwatchView

            return StopwatchView(self)
        if name == "timer":
            from .views.timer.timer_view import TimerView

            return TimerView(self)
        from .views.report.report_view import ReportView

        return ReportView(self)

    def theme_button(self, compact: bool = False) -> Gtk.Button:
        return self._theme_buttons[1 if compact else 0]

    def pin_button(self, compact: bool = False) -> Gtk.ToggleButton:
        return self._pin_buttons[1 if compact else 0]

    def settings_icon(self, compact: bool = False) -> Gtk.Button:
        return self._settings_buttons[1 if compact else 0]

    def nav_button(self, name: str, bottom: bool = False) -> Gtk.ToggleButton:
        return (self._bottom_buttons if bottom else self._rail_buttons)[name]

    def view(self, name: str) -> Gtk.Widget:
        if name not in self._views:
            created = self._create_view(name)
            created.set_compact(self.compact)
            self.stack.add_named(created, name)
            self._views[name] = created
        return self._views[name]

    def navigate(self, name: str, show_editor: bool = True) -> None:
        """Show a view, or the Create or Edit page left open on it so a draft is never lost."""
        if name not in {item[0] for item in NAV_ITEMS}:
            return
        target = self.view(name)
        self.current = name
        self.stack.set_visible_child_name(name)
        editor = self._editors.get(name) if show_editor else None
        self.content.set_visible_child(editor or self.views_page)
        self._syncing = True
        self._rail_buttons[name].set_active(True)
        self._bottom_buttons[name].set_active(True)
        self._syncing = False
        target.on_shown()
        self.update_tick()

    def _on_nav_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if button.get_active() and not self._syncing:
            self.navigate(name)

    def _set_compact(self, compact: bool) -> None:
        self.compact = compact
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        for view in (*self._views.values(), *self._editors.values()):
            view.set_compact(compact)

    def editor_for(self, view_name: str):
        return self._editors.get(view_name)

    def open_editor(self, page, opener: Gtk.Widget | None = None) -> None:
        """Show a Create or Edit page in place of its view. One page per view; a newer one replaces it."""
        name = page.view_name
        old = self._editors.pop(name, None)
        if old is not None and old is not page:
            self.content.remove(old)
        page.opener = opener
        page.set_compact(self.compact)
        self._editors[name] = page
        if page.get_parent() is None:
            self.content.add_named(page, f"editor-{name}")
        self.navigate(name)
        page.focus_first()

    def close_editor(self, view_name: str) -> None:
        page = self._editors.pop(view_name, None)
        if page is None:
            return
        showing = self.content.get_visible_child() is page
        if showing:
            self.content.set_visible_child(self.views_page)
        self.content.remove(page)
        if not showing:
            return
        # Focus goes back to the button that opened the page, or to Create when that row is gone.
        view = self._views.get(view_name)
        opener = page.opener
        if opener is None or opener.get_root() is not self or not opener.get_mapped():
            opener = getattr(view, "create_button", None)
        if opener is not None:
            opener.grab_focus()

    def reload_views(self) -> None:
        for view in self._views.values():
            view.reload()

    def update_tick(self) -> None:
        """Repaint once a second only while the window is on screen and something counts down."""
        wanted = self.get_mapped() and self.scheduler.has_live_countdown()
        if wanted and not self._tick_id:
            self._tick_id = GLib.timeout_add(1000, self._on_tick)
        elif not wanted and self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _on_tick(self) -> bool:
        self.scheduler.poll()
        self.refresh_live()
        return True

    def refresh_live(self) -> None:
        view = self._views.get(self.current or "")
        if view is not None:
            view.tick()
        self._update_live_pill()

    def _on_scheduler_changed(self) -> None:
        for view in self._views.values():
            view.on_scheduler_changed()
        self._update_live_pill()
        self._sync_alert_ui()
        self.update_tick()
        done = self.scheduler.last_completed_target
        if done is not None and done.id != self._announced_target:
            self._announced_target = done.id
            self.toast(f"Target complete: {done.name}")
        elif done is None:
            self._announced_target = None

    def _update_live_pill(self) -> None:
        session = self.scheduler.session
        running = [r for r in self.scheduler.timers.values() if r.status == "running"]
        if session is not None and session.state.phase is TargetPhase.READY_FOR_NEXT:
            text = f"{session.target.name}  \u00b7  Ready for Batch {session.state.batch_index + 1}"
        elif session is not None and session.state.phase is not TargetPhase.COMPLETED:
            state = session.state
            text = (
                f"{session.target.name}  \u00b7  Batch {state.batch_index}/{state.batch_count}  "
                f"\u00b7  {format_countdown(state.remaining_ms / 1000)}"
            )
        elif running:
            run = min(running, key=lambda r: r.remaining_ms)
            text = f"{run.name}  \u00b7  {format_countdown(run.remaining_ms / 1000)}"
        else:
            self.live_pill.set_visible(False)
            self.compact_brand_label.set_visible(True)
            return
        self.live_label.set_text(text)
        set_accessible_label(self.live_pill, f"Running: {text}")
        self.live_pill.set_visible(True)
        # A narrow header has room for the session or the app name, and the session matters more.
        self.compact_brand_label.set_visible(False)

    def _open_live_session(self) -> None:
        if self.scheduler.session is not None:
            self.navigate("today", show_editor=False)
            self.view("today").show_focus()
        else:
            self.navigate("timer", show_editor=False)

    def _sync_alert_ui(self) -> None:
        alert = self.scheduler.current_alert()
        key = alert.key if alert else None
        if key != self._alert_key:
            self._close_alert_dialog()
            self._alert_key = key
            self._alert_collapsed = False
            if alert is not None:
                self.set_visible(True)
                self.present()
                self._open_alert_dialog(alert)
        elif self.alert_dialog is not None:
            self.alert_dialog.refresh()
        self._update_alert_bar()

    def _open_alert_dialog(self, alert) -> None:
        if alert.kind == "alarm":
            from .views.alarm.alarm_ringing_dialog import AlarmRingingDialog

            dialog = AlarmRingingDialog(self, alert.ref_id)
        elif alert.kind == "gate":
            from .views.target.subtask_gate_dialog import SubtaskGateDialog

            dialog = SubtaskGateDialog(self)
        elif alert.kind == "ready":
            from .views.target.next_batch_dialog import NextBatchDialog

            dialog = NextBatchDialog(self)
        else:
            from .views.timer.timer_expired_dialog import TimerExpiredDialog

            dialog = TimerExpiredDialog(self, alert.ref_id)
        dialog.connect("closed", self._on_alert_dialog_closed, alert.key)
        self.alert_dialog = dialog
        self._alert_collapsed = False
        dialog.present(self)

    def _close_alert_dialog(self) -> None:
        dialog, self.alert_dialog = self.alert_dialog, None
        if dialog is not None:
            dialog.force_close()

    def _on_alert_dialog_closed(self, dialog, key: str) -> None:
        """Escape hides the dialog but not the obligation: the bar keeps it one click away."""
        if dialog is not self.alert_dialog:
            return
        self.alert_dialog = None
        alert = self.scheduler.current_alert()
        if alert is not None and alert.key == key:
            self._alert_collapsed = True
        self._update_alert_bar()

    def _update_alert_bar(self) -> None:
        alert = self.scheduler.current_alert()
        visible = alert is not None and self._alert_collapsed and self.alert_dialog is None
        if visible:
            self.alert_bar_label.set_text(ALERT_BAR_TEXT[alert.kind].format(title=alert.title))
        self.alert_bar.set_visible(visible)

    def reopen_alert(self) -> None:
        alert = self.scheduler.current_alert()
        if alert is not None and self.alert_dialog is None:
            self._open_alert_dialog(alert)
            self._update_alert_bar()

    def _sync_tools(self) -> None:
        dark = self.app.theme.dark
        theme_icon = "productime-sun-symbolic" if dark else "productime-moon-symbolic"
        theme_tip = "Switch to light mode" if dark else "Switch to dark mode"
        for button in self._theme_buttons:
            button.set_icon_name(theme_icon)
            button.set_tooltip_text(theme_tip)
            set_accessible_label(button, theme_tip)
        keep = self.keep_running_when_closed()
        pin_tip = (
            "Keep running when closed: on. Alarms and timers keep working after you close the window."
            if keep
            else "Keep running when closed: off. Closing the window quits Productime."
        )
        self._syncing = True
        for button in self._pin_buttons:
            button.set_active(keep)
            button.set_tooltip_text(pin_tip)
            set_accessible_label(button, "Keep running when closed")
        self._syncing = False

    def keep_running_when_closed(self) -> bool:
        return self.app.repo.get_pref("close_to_background", "1") == "1"

    def set_keep_running(self, keep: bool) -> None:
        self.app.repo.set_pref("close_to_background", "1" if keep else "0")
        self.app.update_hold()
        self._sync_tools()
        if self.settings_dialog is not None:
            self.settings_dialog.sync()

    def toggle_theme(self) -> None:
        self.app.theme.set_dark(not self.app.theme.dark)

    def _on_pin_toggled(self, button: Gtk.ToggleButton) -> None:
        if not self._syncing:
            self.set_keep_running(button.get_active())

    def open_settings_dialog(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self)
        dialog.connect("closed", self._on_settings_closed)
        self.settings_dialog = dialog
        dialog.present(self)

    def _on_settings_closed(self, dialog) -> None:
        dialog.teardown()
        if self.settings_dialog is dialog:
            self.settings_dialog = None

    def open_welcome(self) -> None:
        from .welcome_dialog import WelcomeDialog

        dialog = WelcomeDialog(self)
        dialog.connect("closed", lambda _d: setattr(self, "welcome_dialog", None))
        self.welcome_dialog = dialog
        dialog.present(self)

    def request_save_backup(self) -> None:
        name = f"productime-backup-{self.app.clock.now():%Y%m%d}.json"
        self.app.file_chooser.save_file(self, "Save Backup", name, self.save_backup_to)

    def save_backup_to(self, path: Path) -> None:
        try:
            backup_service.write_backup(self.app.repo, path, self.app.clock.now())
        except BackupError as error:
            self.show_error("Backup could not be saved", str(error))
            return
        self.toast(f"Backup saved to {path.name}")

    def request_restore(self) -> None:
        self.confirm(
            "Restore from a backup?",
            "Restoring replaces every target, alarm, timer, and report entry saved on this computer "
            "with the contents of the backup file.",
            "Choose Backup File",
            self._choose_restore_file,
        )

    def _choose_restore_file(self) -> None:
        self.app.file_chooser.open_file(
            self, "Restore Backup", "Productime backups", ("*.json",), self.restore_from_path
        )

    def restore_from_path(self, path: Path) -> None:
        try:
            data = backup_service.read_backup(path)
            backup_service.restore_backup(self.app.repo, self.app.library, data)
        except (BackupError, sqlite3.Error) as error:
            self.show_error("Backup could not be restored", f"{error} Your current data was not changed.")
            return
        self.app.apply_preferences()
        self.scheduler.reset_runtime()
        self.reload_views()
        self._sync_tools()
        if self.settings_dialog is not None:
            self.settings_dialog.sync()
        self.toast("Backup restored.")

    def open_data_folder(self) -> None:
        self.app.file_chooser.open_folder(self, self.app.data_dir)

    def toast(self, message: str) -> None:
        self.last_toast = message
        self.toast_overlay.add_toast(Adw.Toast(use_markup=False, title=message))

    def confirm(
        self,
        heading: str,
        body: str,
        confirm_label: str,
        on_confirm,
        destructive: bool = True,
    ) -> Adw.AlertDialog:
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", confirm_label)
        if destructive:
            dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: on_confirm() if response == "confirm" else None)
        self.last_confirm = dialog
        dialog.present(self)
        return dialog

    def show_error(self, heading: str, body: str) -> Adw.AlertDialog:
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        self.last_error = dialog
        dialog.present(self)
        return dialog

    def _on_close_request(self, _window) -> bool:
        """Closing hides the window while timers or alarms still need the process alive."""
        if not (self.keep_running_when_closed() and self.scheduler.has_background_work()):
            self.teardown()
            return False
        self.set_visible(False)
        if not self._told_background:
            self._told_background = True
            self.app.notifier.notify(
                "background",
                f"{APP_NAME} is still running",
                "Timers and alarms keep working. Open Productime again to bring the window back.",
                buttons=((f"Open {APP_NAME}", "app.present", None),),
            )
        return True

    def teardown(self) -> None:
        """Detach from the services before the window goes away.

        GTK 4 emits "destroy" only when the last reference drops, and Python keeps
        references alive, so cleanup cannot wait for that signal.
        """
        self.scheduler.unsubscribe(self._on_scheduler_changed)
        self.app.theme.unsubscribe(self._sync_tools)
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        if self.app.audio is not None:
            self.app.audio.on_error = None
        self.app.forget_window(self)
