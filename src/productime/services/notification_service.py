"""Desktop notifications with action buttons that work without opening the window."""

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

from gi.repository import Gio, GLib

from ..paths import APP_ID, APP_NAME
from .desktop_integration import notification_kind

# (label, "app.<action>", target) where target is None, a str, or an int.
Button = tuple[str, str, str | int | None]


def _variant(target: str | int | None) -> GLib.Variant | None:
    if target is None:
        return None
    return GLib.Variant("i", target) if isinstance(target, int) else GLib.Variant("s", target)


def _desktop_file_installed() -> bool:
    data_dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    return any(Path(d, "applications", f"{APP_ID}.desktop").is_file() for d in data_dirs)


class FilteredNotifier:
    """Drops notifications whose kind the user turned off in Settings."""

    def __init__(self, base, allowed: Callable[[str], bool]):
        self._base = base
        self._allowed = allowed

    def notify(self, key: str, title: str, body: str, urgent: bool = False, buttons: Sequence[Button] = ()):
        kind = notification_kind(key)
        if kind is not None and not self._allowed(kind):
            return
        self._base.notify(key, title, body, urgent=urgent, buttons=buttons)

    def withdraw(self, key: str) -> None:
        self._base.withdraw(key)


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

    def notify(self, key: str, title: str, body: str, urgent: bool = False, buttons: Sequence[Button] = ()):
        buttons = tuple(buttons) or ((f"Open {APP_NAME}", "app.present", None),)
        if self._use_gio:
            notification = Gio.Notification.new(title)
            notification.set_body(body)
            notification.set_default_action("app.present")
            for label, action, target in buttons:
                variant = _variant(target)
                if variant is None:
                    notification.add_button(label, action)
                else:
                    notification.add_button_with_target(label, action, variant)
            if urgent:
                notification.set_priority(Gio.NotificationPriority.URGENT)
            self._app.send_notification(key, notification)
            return
        self._notify_fallback(key, title, body, urgent, buttons)

    def withdraw(self, key: str) -> None:
        if self._use_gio:
            self._app.withdraw_notification(key)
            return
        live = self._live.pop(key, None)
        if live is not None:
            with contextlib.suppress(GLib.Error):
                live.close()

    def _notify_fallback(self, key: str, title: str, body: str, urgent: bool, buttons) -> None:
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
            notification.add_action("default", f"Open {APP_NAME}", self._on_action, ("app.present", None))
            for index, (label, action, target) in enumerate(buttons):
                notification.add_action(f"button-{index}", label, self._on_action, (action, target))
            notification.show()
            # The action callback only fires while a reference is held.
            self._live[key] = notification
        except GLib.Error, ValueError, ImportError:
            # No notification service on this session: the in-window alert still shows.
            self._live.pop(key, None)

    def _on_action(self, _notification, _action_id, data) -> None:
        action, target = data
        self._app.activate_action(action.removeprefix("app."), _variant(target))
