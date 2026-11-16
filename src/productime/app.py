"""Adw.Application: service wiring, single instance, and the background lifecycle."""

import sqlite3
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib

from . import paths
from .db.database import open_database
from .db.repositories import Repository
from .services.audio_service import AudioService, SoundLibrary
from .services.clock import SystemClock
from .services.notification_service import DesktopNotifier
from .services.scheduler_service import Scheduler, SleepMonitor
from .ui.file_chooser import NativeFileChooser
from .ui.theme import ThemeManager


class _NoPreferences:
    """Stands in for the repository when the database could not be opened."""

    def get_pref(self, _key: str, default: str) -> str:
        return default

    def set_pref(self, _key: str, _value: str) -> None:
        pass


class ProductimeApp(Adw.Application):
    def __init__(
        self,
        *,
        application_id: str = paths.APP_ID,
        flags: Gio.ApplicationFlags | None = None,
        data_dir: Path | None = None,
        clock=None,
        audio_sink: str | None = None,
        notifier=None,
        file_chooser=None,
        window_size: tuple[int, int] | None = None,
    ):
        if flags is None:
            flags = Gio.ApplicationFlags.DEFAULT_FLAGS | Gio.ApplicationFlags.CAN_OVERRIDE_APP_ID
        super().__init__(application_id=application_id, flags=flags)
        self._data_dir_override = Path(data_dir) if data_dir else None
        self._audio_sink = audio_sink
        self._notifier_override = notifier
        self._window_size = window_size
        self._wakeup_id = 0
        self._services_open = False
        self.clock = clock or SystemClock()
        self.file_chooser = file_chooser or NativeFileChooser()
        self.window = None
        self.startup_error: str | None = None
        self.data_dir: Path | None = None
        self.conn = None
        self.repo = None
        self.library = None
        self.audio = None
        self.notifier = None
        self.scheduler = None
        self.theme = None
        self.sleep_monitor = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._install_actions()
        try:
            self._open_services()
        except (sqlite3.Error, OSError) as error:
            self.startup_error = str(error) or type(error).__name__
            self.theme = ThemeManager(_NoPreferences())
            self.theme.install(Gdk.Display.get_default())

    def do_activate(self) -> None:
        if self.window is None:
            self.window = self._build_window()
        self.window.set_visible(True)
        self.window.present()

    def do_shutdown(self) -> None:
        self.close_services()
        Adw.Application.do_shutdown(self)

    def _open_services(self) -> None:
        self.data_dir = self._data_dir_override or paths.data_dir()
        self.conn = open_database(self.data_dir)
        self.repo = Repository(self.conn)
        self.library = SoundLibrary(self.repo, paths.assets_dir(), paths.sounds_dir(self.data_dir))
        self.audio = AudioService(
            self.library,
            self._audio_sink,
            volume=self._int_pref("volume", 100),
            muted=self.repo.get_pref("muted", "0") == "1",
        )
        self.notifier = self._notifier_override or DesktopNotifier(self)
        self.scheduler = Scheduler(
            self.repo, self.clock, self.audio, self.notifier, arm_wakeup=self._arm_wakeup
        )
        self.theme = ThemeManager(self.repo)
        self.theme.install(Gdk.Display.get_default())
        self.sleep_monitor = SleepMonitor(self.scheduler.poll)
        self._services_open = True
        self._arm_wakeup(self.scheduler.next_wakeup_ms())

    def _int_pref(self, key: str, default: int) -> int:
        value = self.repo.get_pref(key, str(default))
        return int(value) if value.isascii() and value.isdigit() else default

    def apply_preferences(self) -> None:
        """Re-read theme and sound settings, used after a backup restore replaced them."""
        self.theme.apply_saved_preference()
        self.audio.set_volume(self._int_pref("volume", 100))
        self.audio.set_muted(self.repo.get_pref("muted", "0") == "1")

    def close_services(self) -> None:
        if self._wakeup_id:
            GLib.source_remove(self._wakeup_id)
            self._wakeup_id = 0
        if self.theme is not None:
            self.theme.uninstall()
        if not self._services_open:
            return
        self._services_open = False
        self.scheduler.shutdown()
        self.audio.shutdown()
        self.sleep_monitor.close()
        self.conn.close()

    def _arm_wakeup(self, delay_ms: int | None) -> None:
        """One timer to the next deadline, instead of a tick that runs while nothing is pending."""
        if self._wakeup_id:
            GLib.source_remove(self._wakeup_id)
            self._wakeup_id = 0
        if delay_ms is not None:
            self._wakeup_id = GLib.timeout_add(delay_ms, self._on_wakeup)

    def _on_wakeup(self) -> bool:
        self._wakeup_id = 0
        self.scheduler.poll()
        return False

    def _build_window(self):
        if self.startup_error is not None:
            from .ui.main_window import build_startup_error_window

            return build_startup_error_window(self, self.startup_error)
        from .ui.main_window import ProductimeWindow

        if self._window_size:
            return ProductimeWindow(self, *self._window_size)
        return ProductimeWindow(self)

    def forget_window(self, window) -> None:
        if self.window is window:
            self.window = None

    def close_window(self) -> None:
        window = self.window
        if window is not None:
            window.teardown()
            window.destroy()

    def _install_actions(self) -> None:
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit_app())
        self.add_action(quit_action)
        present_action = Gio.SimpleAction.new("present", None)
        present_action.connect("activate", lambda *_: self.activate())
        self.add_action(present_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.create", ["<Control>n"])
        for index, name in enumerate(("today", "alarm", "stopwatch", "timer", "report"), start=1):
            self.set_accels_for_action(f"win.nav::{name}", [f"<Control>{index}"])

    def quit_app(self) -> None:
        self.close_window()
        self.quit()


def run(argv: list[str]) -> int:
    # GNOME matches a Wayland window to its launcher through this name.
    GLib.set_prgname(paths.APP_ID)
    GLib.set_application_name(paths.APP_NAME)
    return ProductimeApp().run(argv)
