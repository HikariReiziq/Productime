"""Desktop notifications with a click-to-open action."""

import contextlib
from pathlib import Path

from gi.repository import Gio, GLib

from ..paths import APP_ID, APP_NAME


def _desktop_file_installed() -> bool:
    data_dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    return any(Path(d, "applications", f"{APP_ID}.desktop").is_file() for d in data_dirs)


class DesktopNotifier:
    """Uses Gio.Notification once the launcher is installed, and libnotify before that.

    GNOME Shell only shows Gio.Notification for apps it can match to a .desktop file,
    so a plain checkout run through scripts/run-dev.sh needs the libnotify path.
    """

    def __init__(self, app: Gio.Application):
        self._app = app
        self._use_gio = _desktop_file_installed()
        self._libnotify = None
        self._live: dict[str, object] = {}

    def notify(self, key: str, title: str, body: str, urgent: bool = False) -> None:
        if self._use_gio:
            notification = Gio.Notification.new(title)
            notification.set_body(body)
            notification.set_default_action("app.present")
            notification.add_button(f"Open {APP_NAME}", "app.present")
            if urgent:
                notification.set_priority(Gio.NotificationPriority.URGENT)
            self._app.send_notification(key, notification)
            return
        self._notify_fallback(key, title, body, urgent)

    def withdraw(self, key: str) -> None:
        if self._use_gio:
            self._app.withdraw_notification(key)
            return
        live = self._live.pop(key, None)
        if live is not None:
            with contextlib.suppress(GLib.Error):
                live.close()

    def _notify_fallback(self, key: str, title: str, body: str, urgent: bool) -> None:
        try:
            if self._libnotify is None:
                import gi

                gi.require_version("Notify", "0.7")
                from gi.repository import Notify

                Notify.init(APP_NAME)
                self._libnotify = Notify
            notify = self._libnotify
            # Notification servers may render body markup, and the body carries user text.
            notification = notify.Notification.new(title, GLib.markup_escape_text(body), APP_ID)
            if urgent:
                notification.set_urgency(notify.Urgency.CRITICAL)
            notification.add_action("default", f"Open {APP_NAME}", self._on_action)
            notification.show()
            # The action callback only fires while a reference is held.
            self._live[key] = notification
        except GLib.Error, ValueError, ImportError:
            # No notification service on this session: the in-window alert still shows.
            self._live.pop(key, None)

    def _on_action(self, *_args) -> None:
        self._app.activate()
