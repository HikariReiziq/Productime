import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from gi.repository import Gio, GLib

from productime.services import desktop_integration as desktop
from productime.services.notification_service import FilteredNotifier
from support import quiet_pygobject_asyncio_warning


def setUpModule():
    quiet_pygobject_asyncio_warning()


class RecordingBase:
    def __init__(self):
        self.sent = []
        self.withdrawn = []

    def notify(self, key, title, body, urgent=False, buttons=()):
        self.sent.append(key)

    def withdraw(self, key):
        self.withdrawn.append(key)


class FilteredNotifierTests(unittest.TestCase):
    def test_each_notification_kind_follows_its_own_preference(self):
        prefs = {"notify_alarms": "1", "notify_timers": "0", "notify_targets": "1"}
        base = RecordingBase()
        notifier = FilteredNotifier(base, lambda key: prefs.get(key, "1") == "1")

        for key in ("alarm-1", "timer-2", "target-gate", "target-done", "background"):
            notifier.notify(key, "Title", "Body")
        notifier.withdraw("timer-2")

        self.assertEqual(base.sent, ["alarm-1", "target-gate", "target-done", "background"])
        self.assertEqual(base.withdrawn, ["timer-2"])

    def test_kind_of_a_key(self):
        self.assertEqual(desktop.notification_kind("alarm-abc"), "notify_alarms")
        self.assertEqual(desktop.notification_kind("timer-abc"), "notify_timers")
        self.assertEqual(desktop.notification_kind("target-gate"), "notify_targets")
        self.assertIsNone(desktop.notification_kind("background"))


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name) / "config"

    def test_enabling_writes_a_hidden_background_launcher(self):
        self.assertFalse(desktop.autostart_enabled(self.config))

        desktop.set_autostart(self.config, True)

        path = self.config / "autostart" / "org.productime.Productime.desktop"
        self.assertTrue(desktop.autostart_enabled(self.config))
        keyfile = GLib.KeyFile()
        keyfile.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        exec_line = keyfile.get_string("Desktop Entry", "Exec")
        self.assertTrue(exec_line.endswith("-m productime --background"), exec_line)
        self.assertIn("PYTHONPATH=", exec_line)
        self.assertEqual(keyfile.get_string("Desktop Entry", "Type"), "Application")
        self.assertTrue(keyfile.get_boolean("Desktop Entry", "X-GNOME-Autostart-enabled"))
        self.assertFalse(keyfile.get_boolean("Desktop Entry", "Terminal"))
        _ok, argv = GLib.shell_parse_argv(exec_line)
        self.assertEqual(argv[-3:], ["-m", "productime", "--background"])

    def test_disabling_removes_the_launcher(self):
        desktop.set_autostart(self.config, True)

        desktop.set_autostart(self.config, False)

        self.assertFalse(desktop.autostart_enabled(self.config))
        self.assertEqual(list((self.config / "autostart").iterdir()), [])

    def test_paths_with_spaces_and_quotes_are_escaped_for_the_exec_key(self):
        line = desktop.desktop_exec(["/opt/my apps/python3", 'a"b', "100%", "plain"])

        self.assertEqual(line, '"/opt/my apps/python3" "a\\\\"b" 100%% plain')


class SearchProviderTests(unittest.TestCase):
    """Calls the provider over a real D-Bus connection, the way GNOME Shell does."""

    def setUp(self):
        self.items = {
            "target:t1": ("Website Profile Company", "Target, 6h 40m"),
            "target:t2": ("Belajar Pemrograman Mobile", "Target, 4h 50m"),
            "timer:m1": ("Tea", "Timer, 3m"),
        }
        self.activated = []
        self.launched = []
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.path = f"/org/productime/Test/{id(self)}/SearchProvider"
        self.provider = desktop.SearchProvider(
            search=self.search,
            describe=lambda ids: [(i, *self.items[i]) for i in ids if i in self.items],
            activate=self.activated.append,
            launch=lambda terms: self.launched.append(list(terms)),
        )
        self.provider.register(self.bus, self.path)
        self.addCleanup(self.provider.unregister)

    def search(self, terms):
        return [
            key for key, (name, _d) in self.items.items() if all(t.lower() in name.lower() for t in terms)
        ]

    def call(self, method, params, reply_type):
        results = []
        self.bus.call(
            self.bus.get_unique_name(),
            self.path,
            desktop.SEARCH_INTERFACE,
            method,
            params,
            GLib.VariantType(reply_type) if reply_type else None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            lambda conn, res: results.append(conn.call_finish(res)),
        )
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        while not results and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.005)
        self.assertTrue(results, f"{method} got no reply")
        return results[0].unpack()

    def test_initial_search_matches_every_term(self):
        self.assertEqual(
            self.call("GetInitialResultSet", GLib.Variant("(as)", (["web"],)), "(as)"), (["target:t1"],)
        )
        self.assertEqual(
            self.call("GetInitialResultSet", GLib.Variant("(as)", (["BELAJAR", "mobile"],)), "(as)"),
            (["target:t2"],),
        )
        self.assertEqual(self.call("GetInitialResultSet", GLib.Variant("(as)", (["zzz"],)), "(as)"), ([],))

    def test_subsearch_narrows_the_previous_results(self):
        result = self.call(
            "GetSubsearchResultSet", GLib.Variant("(asas)", (["target:t1", "timer:m1"], ["tea"])), "(as)"
        )

        self.assertEqual(result, (["timer:m1"],))

    def test_result_metas_carry_name_description_and_icon(self):
        (metas,) = self.call("GetResultMetas", GLib.Variant("(as)", (["timer:m1", "gone"],)), "(aa{sv})")

        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["id"], "timer:m1")
        self.assertEqual(metas[0]["name"], "Tea")
        self.assertEqual(metas[0]["description"], "Timer, 3m")
        self.assertEqual(metas[0]["gicon"], "org.productime.Productime")

    def test_activating_a_result_and_launching_a_search(self):
        self.call("ActivateResult", GLib.Variant("(sasu)", ("target:t2", ["belajar"], 0)), None)
        self.call("LaunchSearch", GLib.Variant("(asu)", (["web"], 0)), None)

        self.assertEqual(self.activated, ["target:t2"])
        self.assertEqual(self.launched, [["web"]])

    def test_provider_file_matches_the_registered_object(self):
        ini = (_bootstrap.ROOT / "packaging" / "org.productime.Productime.search-provider.ini").read_text()
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(ini, len(ini.encode()), GLib.KeyFileFlags.NONE)

        group = "Shell Search Provider"
        self.assertEqual(keyfile.get_string(group, "DesktopId"), "org.productime.Productime.desktop")
        self.assertEqual(keyfile.get_string(group, "BusName"), "org.productime.Productime")
        self.assertEqual(keyfile.get_string(group, "ObjectPath"), desktop.SEARCH_OBJECT_PATH)
        self.assertEqual(keyfile.get_string(group, "Version"), "2")


if __name__ == "__main__":
    unittest.main()
