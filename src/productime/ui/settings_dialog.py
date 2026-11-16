"""Settings: appearance, sound, notifications, background behaviour, data and backup, about."""

from gi.repository import Adw, Gtk

from ..paths import APP_NAME
from ..services import desktop_integration
from .widgets.controls import bind_escape

NOTIFICATION_ROWS = (
    ("notify_alarms", "Alarms", "When an alarm rings, with Stop and Snooze buttons."),
    ("notify_timers", "Timers", "When a timer runs out, with Stop and Restart buttons."),
    ("notify_targets", "Targets", "When a batch ends, a break is over, or a target is complete."),
)
BACKUP_EXPLANATION = (
    "Everything you save is stored automatically on this computer. A backup copies every target, "
    "alarm, timer, and report entry into one file, so you can keep it safe or move it to another "
    "computer."
)


def _row(row_type, title: str, subtitle: str = ""):
    row = row_type(title=title, use_markup=False)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__()
        self.set_title("Settings")
        self.set_search_enabled(False)
        self._window = window
        self._app = window.app
        self._syncing = False
        page = Adw.PreferencesPage(title="Settings", icon_name="productime-settings-symbolic")
        for group in (
            self._appearance_group(),
            self._sound_group(),
            self._notification_group(),
            self._background_group(),
            self._data_group(),
            self._about_group(),
        ):
            page.add(group)
        self.add(page)
        self._app.theme.subscribe(self.sync)
        bind_escape(self)
        self.sync()

    def teardown(self) -> None:
        self._app.theme.unsubscribe(self.sync)

    def _appearance_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Appearance")
        self.theme_toggle = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for name, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            self.theme_toggle.add(Adw.Toggle(name=name, label=label))
        self.theme_toggle.connect("notify::active-name", self._on_theme_chosen)
        row = _row(Adw.ActionRow, "Theme", "System follows the GNOME style setting.")
        row.add_suffix(self.theme_toggle)
        group.add(row)
        return group

    def _sound_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Sound")
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_size_request(200, -1)
        self.volume_scale.set_valign(Gtk.Align.CENTER)
        self.volume_scale.update_property([Gtk.AccessibleProperty.LABEL], ["Volume"])
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        volume_row = _row(Adw.ActionRow, "Volume", "Alarms, timers, and previews.")
        volume_row.add_suffix(self.volume_scale)
        self.mute_row = _row(Adw.SwitchRow, "Mute all sounds")
        self.mute_row.connect("notify::active", self._on_mute_changed)
        group.add(volume_row)
        group.add(self.mute_row)
        return group

    def _notification_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Notifications",
            description="Shown in the GNOME notification list, with quick actions on each notification.",
        )
        self.notification_rows: dict[str, Adw.SwitchRow] = {}
        for key, title, subtitle in NOTIFICATION_ROWS:
            row = _row(Adw.SwitchRow, title, subtitle)
            row.connect("notify::active", self._on_notification_changed, key)
            self.notification_rows[key] = row
            group.add(row)
        return group

    def _background_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Background")
        self.keep_running_row = _row(
            Adw.SwitchRow,
            "Keep running when closed",
            "Closing the window hides it while timers, targets, or alarms are pending.",
        )
        self.keep_running_row.connect("notify::active", self._on_keep_running_changed)
        self.autostart_row = _row(
            Adw.SwitchRow,
            "Start at login",
            "Starts hidden when you log in, so alarms still ring after a restart.",
        )
        self.autostart_row.connect("notify::active", self._on_autostart_changed)
        group.add(self.keep_running_row)
        group.add(self.autostart_row)
        return group

    def _data_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Data and Backup", description=BACKUP_EXPLANATION)
        self.save_row = Adw.ButtonRow(
            title="Save Backup", start_icon_name="document-save-symbolic", use_markup=False
        )
        self.save_row.connect("activated", lambda _r: self._window.request_save_backup())
        self.restore_row = Adw.ButtonRow(
            title="Restore Backup", start_icon_name="document-revert-symbolic", use_markup=False
        )
        self.restore_row.connect("activated", lambda _r: self._window.request_restore())
        self.folder_row = Adw.ButtonRow(
            title="Open Data Folder", start_icon_name="folder-open-symbolic", use_markup=False
        )
        self.folder_row.connect("activated", lambda _r: self._window.open_data_folder())
        for row in (self.save_row, self.restore_row, self.folder_row):
            group.add(row)
        return group

    def _about_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="About")
        version = _row(Adw.ActionRow, APP_NAME, f"Version {self._app.version}. MIT License.")
        self.welcome_row = Adw.ButtonRow(
            title="Show Welcome Again", start_icon_name="productime-today-symbolic", use_markup=False
        )
        self.welcome_row.connect("activated", lambda _r: self._show_welcome())
        group.add(version)
        group.add(self.welcome_row)
        return group

    def sync(self) -> None:
        repo = self._app.repo
        self._syncing = True
        self.theme_toggle.set_active_name(self._app.theme.scheme)
        self.volume_scale.set_value(self._app.audio.volume)
        self.mute_row.set_active(self._app.audio.muted)
        for key, row in self.notification_rows.items():
            row.set_active(repo.get_pref(key, "1") == "1")
        self.keep_running_row.set_active(self._window.keep_running_when_closed())
        self.autostart_row.set_active(desktop_integration.autostart_enabled(self._app.config_dir))
        self._syncing = False

    def _on_theme_chosen(self, toggle, _param) -> None:
        if not self._syncing and toggle.get_active_name():
            self._app.theme.set_scheme(toggle.get_active_name())

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        value = int(scale.get_value())
        if self._syncing or value == self._app.audio.volume:
            return
        self._app.audio.set_volume(value)
        self._app.repo.set_pref("volume", str(value))

    def _on_mute_changed(self, row, _param) -> None:
        if self._syncing:
            return
        self._app.audio.set_muted(row.get_active())
        self._app.repo.set_pref("muted", "1" if row.get_active() else "0")

    def _on_notification_changed(self, row, _param, key: str) -> None:
        if not self._syncing:
            self._app.repo.set_pref(key, "1" if row.get_active() else "0")

    def _on_keep_running_changed(self, row, _param) -> None:
        if not self._syncing:
            self._window.set_keep_running(row.get_active())

    def _on_autostart_changed(self, row, _param) -> None:
        if self._syncing:
            return
        try:
            desktop_integration.set_autostart(self._app.config_dir, row.get_active())
        except OSError as error:
            self._window.show_error("Start at login could not be changed", str(error))
            self.sync()

    def _show_welcome(self) -> None:
        self.close()
        self._window.open_welcome()
