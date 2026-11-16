"""Adw.Application: service wiring, single instance, and the background lifecycle."""

import sqlite3
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib

from . import __version__, paths
from .db.database import open_database
from .db.repositories import Repository
from .domain.formatters import format_duration, format_hm
from .domain.target_engine import InvalidTransition
from .services import desktop_integration
from .services.audio_service import AudioService, SoundLibrary
from .services.clock import SystemClock
from .services.notification_service import DesktopNotifier, FilteredNotifier
from .services.scheduler_service import Scheduler, SchedulerError, SleepMonitor
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
        config_dir: Path | None = None,
        show_welcome: bool = True,
    ):
        if flags is None:
            flags = Gio.ApplicationFlags.DEFAULT_FLAGS | Gio.ApplicationFlags.CAN_OVERRIDE_APP_ID
        super().__init__(application_id=application_id, flags=flags)
        self._data_dir_override = Path(data_dir) if data_dir else None
        self._audio_sink = audio_sink
        self._notifier_override = notifier
        self._window_size = window_size
        self._show_welcome = show_welcome
        self._wakeup_id = 0
        self._services_open = False
        self._held = False
        self._start_hidden = False
        self._pending_open: str | None = None
        self._search_provider = None
        self.version = __version__
        self.config_dir = Path(config_dir) if config_dir else Path(GLib.get_user_config_dir())
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
        self.add_main_option(
            "background",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Start without opening a window, as used when starting at login",
            None,
        )
        self.add_main_option(
            "open",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            "Open a quick action: new-target, new-alarm, new-timer, or stopwatch",
            "ACTION",
        )

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("open"):
            action = options.lookup_value("open", GLib.VariantType("s")).get_string()
            self.register(None)
            if self.get_is_remote():
                # Productime is already running: hand the request to it and exit.
                self.activate_action("open", GLib.Variant("s", action))
                return 0
            self._pending_open = action
        if options.contains("background"):
            self._start_hidden = True
        return -1

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        self._search_provider = desktop_integration.SearchProvider(
            search=self.search,
            describe=self.describe_results,
            activate=self.activate_result,
            launch=lambda _terms: self.activate(),
        )
        self._search_provider.register(connection, f"{object_path}/SearchProvider")
        return True

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        if self._search_provider is not None:
            self._search_provider.unregister()
            self._search_provider = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

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
        if self._start_hidden:
            # Started at login: stay in the background until something needs the window.
            self._start_hidden = False
            self.update_hold()
            return
        first_window = self.window is None
        if first_window:
            self.window = self._build_window()
        self.window.set_visible(True)
        self.window.present()
        first_run = self.startup_error is None and self.repo.get_pref("onboarding_done", "0") != "1"
        if first_window and first_run and self._show_welcome:
            self.window.open_welcome()
        if self._pending_open:
            action, self._pending_open = self._pending_open, None
            self.open_quick_action(action)

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
        base_notifier = self._notifier_override or DesktopNotifier(self)
        self.notifier = FilteredNotifier(base_notifier, lambda key: self.repo.get_pref(key, "1") == "1")
        self.scheduler = Scheduler(
            self.repo, self.clock, self.audio, self.notifier, arm_wakeup=self._arm_wakeup
        )
        self.theme = ThemeManager(self.repo)
        self.theme.install(Gdk.Display.get_default())
        self.sleep_monitor = SleepMonitor(self.scheduler.poll)
        self.scheduler.subscribe(self._on_scheduler_changed)
        self._services_open = True
        self._arm_wakeup(self.scheduler.next_wakeup_ms())
        self.update_hold()

    def _on_scheduler_changed(self) -> None:
        self.update_hold()
        # Started at login there is no window yet: an alert is what brings it up.
        if self.window is None and self.scheduler.current_alert() is not None:
            self.activate()

    def update_hold(self) -> None:
        """Stay alive without a window only while the user wants it and something is pending."""
        if not self._services_open:
            return
        keep = self.repo.get_pref("close_to_background", "1") == "1"
        wanted = keep and self.scheduler.has_background_work()
        if wanted and not self._held:
            self.hold()
            self._held = True
        elif not wanted and self._held:
            self.release()
            self._held = False

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
        self.scheduler.unsubscribe(self._on_scheduler_changed)
        if self._held:
            self.release()
            self._held = False
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
        handlers = {
            "quit": (None, lambda _v: self.quit_app()),
            "present": (None, lambda _v: self.activate()),
            "open": ("s", lambda v: self.open_quick_action(v.get_string())),
            "gate-complete": (None, lambda _v: self._act(self.scheduler.complete_subtask)),
            "gate-snooze": ("i", lambda v: self._act(self.scheduler.snooze_session, v.get_int32())),
            "alarm-stop": ("s", lambda v: self._act(self.scheduler.stop_alarm, v.get_string())),
            "alarm-snooze": ("s", lambda v: self._act(self.scheduler.snooze_alarm, v.get_string())),
            "timer-stop": ("s", lambda v: self._act(self.scheduler.dismiss_timer, v.get_string())),
            "timer-restart": ("s", lambda v: self._act(self.scheduler.restart_timer, v.get_string())),
        }
        for name, (parameter, handler) in handlers.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType(parameter) if parameter else None)
            action.connect("activate", lambda _a, value, handler=handler: handler(value))
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.create", ["<Control>n"])
        for index, name in enumerate(("today", "alarm", "stopwatch", "timer", "report"), start=1):
            self.set_accels_for_action(f"win.nav::{name}", [f"<Control>{index}"])

    def quit_app(self) -> None:
        self.close_window()
        self.quit()

    def _act(self, method, *args) -> bool:
        """Run a notification button. If it cannot be done there, show the window instead."""
        if self.scheduler is None:
            return False
        try:
            method(*args)
        except (SchedulerError, InvalidTransition, ValueError) as error:
            self.activate()
            if self.window is not None and hasattr(self.window, "toast"):
                self.window.toast(str(error))
            return False
        return True

    def open_quick_action(self, action: str) -> None:
        """Dock menu entries: open the window on the matching view and start the action."""
        self.activate()
        window = self.window
        if window is None or self.startup_error is not None:
            return
        view = {"new-target": "today", "new-alarm": "alarm", "new-timer": "timer", "stopwatch": "stopwatch"}
        if action not in view:
            return
        window.navigate(view[action])
        if action == "stopwatch":
            if self.scheduler.stopwatch.status.value == "idle":
                self.scheduler.stopwatch_start()
        else:
            window.view(view[action]).create()

    def search(self, terms: list[str]) -> list[str]:
        """Activities search: saved targets and timers whose name contains every term."""
        if self.repo is None:
            return []
        words = [term.casefold() for term in terms if term.strip()]
        if not words:
            return []
        found = []
        for target in self.repo.list_targets():
            if all(word in f"{target.name} {target.tag_label}".casefold() for word in words):
                found.append(f"target:{target.id}")
        for timer in self.repo.list_timers():
            if all(word in timer.name.casefold() for word in words):
                found.append(f"timer:{timer.id}")
        return found[:12]

    def describe_results(self, ids: list[str]) -> list[tuple[str, str, str]]:
        described = []
        for key in ids:
            kind, _sep, item_id = key.partition(":")
            if kind == "target" and (target := self.repo.get_target(item_id)):
                described.append(
                    (key, target.name, f"Start target, {format_hm(target.total_timeline_minutes)}")
                )
            elif kind == "timer" and (timer := self.repo.get_timer(item_id)):
                described.append((key, timer.name, f"Start timer, {format_duration(timer.duration_seconds)}"))
        return described

    def activate_result(self, key: str) -> None:
        kind, _sep, item_id = key.partition(":")
        self.activate()
        window = self.window
        if window is None or self.startup_error is not None:
            return
        # A started session matters more than a half-filled form, which stays open behind it.
        if kind == "target":
            window.navigate("today", show_editor=False)
            window.view("today").start_target(item_id)
        elif kind == "timer" and self._act(self.scheduler.start_timer, item_id):
            window.navigate("timer", show_editor=False)


def run(argv: list[str]) -> int:
    # GNOME matches a Wayland window to its launcher through this name.
    GLib.set_prgname(paths.APP_ID)
    GLib.set_application_name(paths.APP_NAME)
    return ProductimeApp().run(argv)
