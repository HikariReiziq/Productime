"""GNOME desktop hooks: start at login and the Activities search provider."""

import os
import sys
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from gi.repository import Gio, GLib

from ..paths import APP_ID, APP_NAME, PACKAGE_DIR

SEARCH_INTERFACE = "org.gnome.Shell.SearchProvider2"
SEARCH_OBJECT_PATH = "/org/productime/Productime/SearchProvider"
NOTIFICATION_KINDS = {"alarm-": "notify_alarms", "timer-": "notify_timers", "target-": "notify_targets"}

_SEARCH_XML = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
"""


def notification_kind(key: str) -> str | None:
    """Preference that controls a notification key, or None when it is always shown."""
    for prefix, preference in NOTIFICATION_KINDS.items():
        if key.startswith(prefix):
            return preference
    return None


def desktop_exec(argv: Sequence[str]) -> str:
    """Quote an argument list as it must be written after Exec= in a desktop entry file.

    The Exec quoting rules apply first, then the general string escaping doubles every backslash.
    """
    quoted = []
    for argument in argv:
        text = argument.replace("%", "%%")
        if any(ch in text for ch in " \t\n\"'\\><~|&;$*?#()`"):
            text = '"' + "".join("\\" + ch if ch in '"`$\\' else ch for ch in text) + '"'
        quoted.append(text.replace("\\", "\\\\"))
    return " ".join(quoted)


def launch_argv(*extra: str) -> list[str]:
    """Command that starts this copy of Productime with the system Python and no user site packages."""
    return [
        "env",
        f"PYTHONPATH={PACKAGE_DIR.parent}",
        sys.executable or "/usr/bin/python3",
        "-P",
        "-s",
        "-m",
        "productime",
        *extra,
    ]


def autostart_path(config_dir: Path) -> Path:
    return Path(config_dir) / "autostart" / f"{APP_ID}.desktop"


def autostart_enabled(config_dir: Path) -> bool:
    return autostart_path(config_dir).is_file()


def set_autostart(config_dir: Path, enabled: bool) -> None:
    path = autostart_path(config_dir)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "Comment=Keeps Productime alarms and timers working after you log in",
            f"Exec={desktop_exec(launch_argv('--background'))}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
            "",
        )
    )
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    temp.write_text(entry, encoding="utf-8")
    os.replace(temp, path)


class SearchProvider:
    """org.gnome.Shell.SearchProvider2, so targets and timers can be started from Activities."""

    def __init__(
        self,
        search: Callable[[list[str]], list[str]],
        describe: Callable[[list[str]], list[tuple[str, str, str]]],
        activate: Callable[[str], None],
        launch: Callable[[list[str]], None],
    ):
        self._search = search
        self._describe = describe
        self._activate = activate
        self._launch = launch
        self._interface = Gio.DBusNodeInfo.new_for_xml(_SEARCH_XML).interfaces[0]
        self._connection: Gio.DBusConnection | None = None
        self._registration = 0

    def register(self, connection: Gio.DBusConnection, object_path: str) -> None:
        self._connection = connection
        self._registration = connection.register_object_with_closures2(
            object_path, self._interface, self._on_call, None, None
        )

    def unregister(self) -> None:
        if self._connection is not None and self._registration:
            self._connection.unregister_object(self._registration)
        self._registration = 0
        self._connection = None

    def _on_call(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            args = parameters.unpack()
            if method == "GetInitialResultSet":
                invocation.return_value(GLib.Variant("(as)", (self._search(list(args[0])),)))
            elif method == "GetSubsearchResultSet":
                previous = set(args[0])
                found = [key for key in self._search(list(args[1])) if key in previous]
                invocation.return_value(GLib.Variant("(as)", (found,)))
            elif method == "GetResultMetas":
                metas = [
                    {
                        "id": GLib.Variant("s", key),
                        "name": GLib.Variant("s", name),
                        "description": GLib.Variant("s", description),
                        "gicon": GLib.Variant("s", APP_ID),
                    }
                    for key, name, description in self._describe(list(args[0]))
                ]
                invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
            elif method == "ActivateResult":
                self._activate(args[0])
                invocation.return_value(None)
            elif method == "LaunchSearch":
                self._launch(list(args[0]))
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as error:  # noqa: BLE001 - every D-Bus call must get a reply
            invocation.return_dbus_error("org.productime.Productime.Error", str(error))
