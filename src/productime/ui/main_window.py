"""Adaptive shell: left rail or bottom bar, alert recovery bar, settings, and backup actions."""

import sqlite3
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from ..domain.formatters import format_countdown
from ..domain.models import TargetPhase
from ..paths import APP_ID, APP_NAME, brand_icon_path
from ..services import backup_service
from ..services.backup_service import BackupError
from .widgets.controls import (
    AppDialog,
    hbox,
    icon_button,
    make_button,
    set_accessible_label,
    switch_row,
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
ALERT_BAR_TEXT = {
    "alarm": "Alarm ringing: {title}",
    "gate": "Waiting for check-off: {title}",
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
        self.settings_dialog: AppDialog | None = None
        self._views: dict[str, Gtk.Widget] = {}
        self._rail_buttons: dict[str, Gtk.ToggleButton] = {}
        self._bottom_buttons: dict[str, Gtk.ToggleButton] = {}
        self._settings_controls: list[dict] = []
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

        app.audio.on_error = self.toast
        app.theme.subscribe(self._sync_settings)
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

        self.compact_brand = hbox(8, self._brand_image(24), text_label(APP_NAME, "brand-title"))
        self.compact_brand.set_visible(False)
        self.settings_button = icon_button(
            "open-menu-symbolic", "Settings and data", on_click=self.open_settings_dialog
        )
        self.settings_button.set_visible(False)
        header = Adw.HeaderBar()
        header.set_title_widget(self.compact_brand)
        header.pack_end(self.settings_button)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vhomogeneous(False)
        # Only the visible view decides the width, so one wide view cannot widen the others.
        self.stack.set_hhomogeneous(False)
        self.page = Adw.Clamp(maximum_size=1120)
        self.page.set_child(self.stack)
        for edge, value in (("top", 24), ("bottom", 32), ("start", 24), ("end", 24)):
            getattr(self.page, f"set_margin_{edge}")(value)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.page)

        main = Adw.ToolbarView()
        main.add_top_bar(header)
        main.set_content(vbox(0, self.alert_bar, scroller))
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
        breakpoint_.add_setter(self.settings_button, "visible", True)
        breakpoint_.add_setter(self.page, "margin-start", 16)
        breakpoint_.add_setter(self.page, "margin-end", 16)
        breakpoint_.add_setter(self.page, "margin-top", 16)
        breakpoint_.connect("apply", lambda _b: self._set_compact(True))
        breakpoint_.connect("unapply", lambda _b: self._set_compact(False))
        self.add_breakpoint(breakpoint_)

    def _brand_image(self, size: int) -> Gtk.Image:
        image = Gtk.Image(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        image.set_from_file(str(brand_icon_path()))
        image.set_pixel_size(size)
        image.add_css_class("brand-mark")
        image.set_overflow(Gtk.Overflow.HIDDEN)
        return image

    def _nav_button(self, name: str, title: str, icon: str, vertical: bool) -> Gtk.ToggleButton:
        orientation = Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
        content = Gtk.Box(orientation=orientation, spacing=2 if vertical else 12)
        if vertical:
            content.set_halign(Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(text_label(title, xalign=0.5 if vertical else 0.0))
        button = Gtk.ToggleButton()
        button.set_child(content)
        button.add_css_class("nav-item")
        set_accessible_label(button, title)
        button.connect("toggled", self._on_nav_toggled, name)
        return button

    def _build_rail(self) -> Gtk.Box:
        rail = vbox(6)
        rail.add_css_class("rail")
        rail.set_size_request(232, -1)
        # Children that expand would otherwise make the rail share the window's spare width.
        rail.set_hexpand(False)
        brand = hbox(10, self._brand_image(32), text_label(APP_NAME, "brand-title"))
        brand.set_margin_bottom(14)
        brand.set_margin_start(4)
        rail.append(brand)

        nav = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4, accessible_role=Gtk.AccessibleRole.NAVIGATION
        )
        set_accessible_label(nav, "Main navigation")
        first = None
        for name, title, icon in NAV_ITEMS:
            button = self._nav_button(name, title, icon, vertical=False)
            if first is None:
                first = button
            else:
                button.set_group(first)
            self._rail_buttons[name] = button
            nav.append(button)
        rail.append(nav)

        self.mini_session = Gtk.Button()
        self.mini_session.add_css_class("mini-session")
        self.mini_title = text_label("", "body-strong", ellipsize=True)
        self.mini_detail = text_label("", "secondary", "small", "numeric", ellipsize=True)
        self.mini_session.set_child(vbox(2, self.mini_title, self.mini_detail))
        self.mini_session.connect("clicked", lambda _b: self._open_live_session())
        self.mini_session.set_margin_top(12)
        self.mini_session.set_visible(False)
        rail.append(self.mini_session)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        rail.append(spacer)
        rail.append(self._build_settings())
        return rail

    def _build_bottom_bar(self) -> Gtk.Box:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            homogeneous=True,
            accessible_role=Gtk.AccessibleRole.NAVIGATION,
        )
        bar.add_css_class("bottom-bar")
        set_accessible_label(bar, "Main navigation")
        first = None
        for name, title, icon in NAV_ITEMS:
            button = self._nav_button(name, title, icon, vertical=True)
            if first is None:
                first = button
            else:
                button.set_group(first)
            self._bottom_buttons[name] = button
            bar.append(button)
        return bar

    def _build_settings(self) -> Gtk.Box:
        """Theme, volume, and backup controls. Built once for the rail and once for the compact dialog."""
        theme_switch = Gtk.Switch()
        theme_switch.connect("notify::active", self._on_theme_switched)
        background_switch = Gtk.Switch()
        background_switch.set_tooltip_text(
            "When on, closing the window keeps running timers, targets, and alarms alive."
        )
        background_switch.connect("notify::active", self._on_background_switched)

        mute_button = Gtk.ToggleButton()
        mute_button.set_icon_name("audio-volume-high-symbolic")
        mute_button.add_css_class("ghost")
        mute_button.set_tooltip_text("Mute sound")
        set_accessible_label(mute_button, "Mute sound")
        mute_button.connect("toggled", self._on_mute_toggled)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(-1, 44)
        set_accessible_label(scale, "Sound volume")
        scale.connect("value-changed", self._on_volume_changed)

        save_button = make_button(
            "Save Backup", "ghost", on_click=self.request_save_backup, icon="document-save-symbolic"
        )
        restore_button = make_button(
            "Restore Backup", "ghost", on_click=self.request_restore, icon="document-revert-symbolic"
        )
        folder_button = make_button(
            "Open Data Folder", "ghost", on_click=self.open_data_folder, icon="folder-open-symbolic"
        )
        for button in (save_button, restore_button, folder_button):
            button.set_halign(Gtk.Align.START)

        box = vbox(
            4,
            switch_row("Dark mode", theme_switch),
            switch_row("Keep running when closed", background_switch),
            hbox(4, mute_button, scale),
            text_label("Data & Backup", "field-label"),
            save_button,
            restore_button,
            folder_button,
        )
        box.add_css_class("settings-group")
        controls = {
            "box": box,
            "theme": theme_switch,
            "background": background_switch,
            "mute": mute_button,
            "scale": scale,
            "save": save_button,
            "restore": restore_button,
            "folder": folder_button,
        }
        self._settings_controls.append(controls)
        self._sync_settings()
        return box

    def settings_controls(self, index: int = 0) -> dict:
        return self._settings_controls[index]

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

    def nav_button(self, name: str, bottom: bool = False) -> Gtk.ToggleButton:
        return (self._bottom_buttons if bottom else self._rail_buttons)[name]

    def view(self, name: str) -> Gtk.Widget:
        if name not in self._views:
            created = self._create_view(name)
            created.set_compact(self.compact)
            self.stack.add_named(created, name)
            self._views[name] = created
        return self._views[name]

    def navigate(self, name: str) -> None:
        if name not in {item[0] for item in NAV_ITEMS}:
            return
        target = self.view(name)
        self.current = name
        self.stack.set_visible_child_name(name)
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
        for view in self._views.values():
            view.set_compact(compact)

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
        self._update_mini_session()

    def _on_scheduler_changed(self) -> None:
        for view in self._views.values():
            view.on_scheduler_changed()
        self._update_mini_session()
        self._sync_alert_ui()
        self.update_tick()
        done = self.scheduler.last_completed_target
        if done is not None and done.id != self._announced_target:
            self._announced_target = done.id
            self.toast(f"Target complete: {done.name}")
        elif done is None:
            self._announced_target = None

    def _update_mini_session(self) -> None:
        session = self.scheduler.session
        running = [r for r in self.scheduler.timers.values() if r.status == "running"]
        if session is not None and session.state.phase is not TargetPhase.COMPLETED:
            state = session.state
            self.mini_title.set_text(session.target.name)
            self.mini_detail.set_text(
                f"Batch {state.batch_index} of {state.batch_count}   "
                f"{format_countdown(state.remaining_ms / 1000)}"
            )
            self.mini_session.set_visible(True)
        elif running:
            run = min(running, key=lambda r: r.remaining_ms)
            self.mini_title.set_text(run.name)
            self.mini_detail.set_text(f"Timer   {format_countdown(run.remaining_ms / 1000)}")
            self.mini_session.set_visible(True)
        else:
            self.mini_session.set_visible(False)

    def _open_live_session(self) -> None:
        if self.scheduler.session is not None:
            self.navigate("today")
            self.view("today").show_focus()
        else:
            self.navigate("timer")

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

    def _sync_settings(self) -> None:
        self._syncing = True
        for controls in self._settings_controls:
            controls["theme"].set_active(self.app.theme.dark)
            controls["background"].set_active(self._keep_running_when_closed())
            controls["mute"].set_active(self.app.audio.muted)
            controls["mute"].set_icon_name(
                "audio-volume-muted-symbolic" if self.app.audio.muted else "audio-volume-high-symbolic"
            )
            controls["scale"].set_value(self.app.audio.volume)
        self._syncing = False

    def _keep_running_when_closed(self) -> bool:
        return self.app.repo.get_pref("close_to_background", "1") == "1"

    def _on_background_switched(self, switch: Gtk.Switch, _param) -> None:
        if self._syncing:
            return
        self.app.repo.set_pref("close_to_background", "1" if switch.get_active() else "0")
        self._sync_settings()

    def _on_theme_switched(self, switch: Gtk.Switch, _param) -> None:
        if self._syncing:
            return
        self.app.theme.set_dark(switch.get_active())
        self._sync_settings()

    def _on_mute_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing:
            return
        self.app.audio.set_muted(button.get_active())
        self.app.repo.set_pref("muted", "1" if button.get_active() else "0")
        self._sync_settings()

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        value = int(scale.get_value())
        if self._syncing or value == self.app.audio.volume:
            return
        self.app.audio.set_volume(value)
        self.app.repo.set_pref("volume", str(value))
        self._sync_settings()

    def open_settings_dialog(self) -> None:
        dialog = AppDialog("Settings and data", width=380)
        box = self._build_settings()
        controls = self._settings_controls[-1]
        dialog.body.append(box)
        dialog.connect("closed", lambda _d: self._forget_settings(controls))
        self.settings_dialog = dialog
        dialog.present(self)

    def _forget_settings(self, controls: dict) -> None:
        if controls in self._settings_controls:
            self._settings_controls.remove(controls)
        self.settings_dialog = None

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
        self._sync_settings()
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
        if not (self._keep_running_when_closed() and self.scheduler.has_background_work()):
            self.teardown()
            return False
        self.set_visible(False)
        if not self._told_background:
            self._told_background = True
            self.app.notifier.notify(
                "background",
                f"{APP_NAME} is still running",
                "Timers and alarms keep working. Open Productime again to bring the window back.",
            )
        return True

    def teardown(self) -> None:
        """Detach from the services before the window goes away.

        GTK 4 emits "destroy" only when the last reference drops, and Python keeps
        references alive, so cleanup cannot wait for that signal.
        """
        self.scheduler.unsubscribe(self._on_scheduler_changed)
        self.app.theme.unsubscribe(self._sync_settings)
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        if self.app.audio is not None:
            self.app.audio.on_error = None
        self.app.forget_window(self)
