"""Drive a real Productime GTK 4 application inside the test process."""

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, GLib, Gtk

from productime.app import ProductimeApp
from productime.services.clock import FakeClock
from support import MONDAY_0800, RecordingNotifier, quiet_pygobject_asyncio_warning

# Real decoding and looping, but samples go nowhere, so the suite is silent.
SILENT_SINK = "fakesink sync=true"

# GLib, GTK, libadwaita, Pango, and GStreamer log lines, plus tracebacks that PyGObject
# prints (instead of raising) when a signal handler fails.
NATIVE_PROBLEM = re.compile(
    r"-(WARNING|CRITICAL|ERROR) \*\*|Traceback \(most recent call last\)|Theme pars(er|ing) error"
    r"|: (Py)?(GI)?Warning: "
)


def pump(seconds: float = 0.05) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while True:
        while context.iteration(False):
            pass
        if time.monotonic() >= deadline:
            return
        time.sleep(0.005)


def pump_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump(0.02)
        if predicate():
            return True
    return bool(predicate())


class NativeStderr:
    """Capture file descriptor 2, which is where C libraries write, not sys.stderr."""

    def __init__(self):
        # Closed in stop(); it must outlive start(), so a with-block does not fit.
        self._file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        self._saved = -1

    def start(self) -> None:
        sys.stderr.flush()
        self._saved = os.dup(2)
        os.dup2(self._file.fileno(), 2)

    def stop(self) -> str:
        sys.stderr.flush()
        os.dup2(self._saved, 2)
        os.close(self._saved)
        self._file.seek(0)
        text = self._file.read().decode("utf-8", "replace")
        self._file.close()
        return text


class FakeFileChooser:
    """Stands in for the native file dialogs: answers with a preset path and records requests."""

    def __init__(self):
        self.next_open: Path | None = None
        self.next_save: Path | None = None
        self.requests: list[tuple[str, str, str]] = []
        self.opened_folders: list[Path] = []

    def open_file(self, _parent, title, filter_name, _patterns, on_path) -> None:
        self.requests.append(("open", title, filter_name))
        if self.next_open is not None:
            on_path(Path(self.next_open))

    def save_file(self, _parent, title, suggested_name, on_path) -> None:
        self.requests.append(("save", title, suggested_name))
        if self.next_save is not None:
            on_path(Path(self.next_save))

    def open_folder(self, _parent, folder) -> None:
        self.opened_folders.append(Path(folder))


def text_of(widget) -> str:
    """Visible caption of a button, whether it holds a plain label, an icon and a label, or a row."""
    child = widget.get_child() if hasattr(widget, "get_child") else None
    if isinstance(child, Gtk.Box):
        texts, part = [], child.get_first_child()
        while part is not None:
            if isinstance(part, Gtk.Label) and part.get_visible() and part.get_text():
                texts.append(part.get_text())
            part = part.get_next_sibling()
        return " ".join(texts)
    if isinstance(child, Gtk.Label):
        return child.get_text()
    return (widget.get_label() or "") if hasattr(widget, "get_label") else ""


class GtkAppTestCase(unittest.TestCase):
    window_size: tuple[int, int] | None = None
    show_welcome = False
    start_hidden = False
    _launches = 0

    @classmethod
    def setUpClass(cls):
        quiet_pygobject_asyncio_warning()

    def setUp(self):
        self._native = NativeStderr()
        self._native.start()
        self.addCleanup(self._check_native_output)
        self._tmp = tempfile.TemporaryDirectory(prefix="productime-ui-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.data_dir = self.tmp / "data" / "productime"
        self.clock = FakeClock(MONDAY_0800)
        self.notifier = RecordingNotifier()
        self.chooser = FakeFileChooser()
        self.app = None
        self.addCleanup(self.close_app)
        self.launch()

    def launch(self, data_dir: Path | None = None):
        # Each instance exports a D-Bus object named after its id, so ids must not repeat in one process.
        GtkAppTestCase._launches += 1
        self.app = ProductimeApp(
            application_id=f"org.productime.Productime.Test{GtkAppTestCase._launches}",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
            data_dir=data_dir or self.data_dir,
            clock=self.clock,
            audio_sink=SILENT_SINK,
            notifier=self.notifier,
            file_chooser=self.chooser,
            window_size=self.window_size,
            config_dir=self.tmp / "config",
            show_welcome=self.show_welcome,
        )
        self.app.register(None)
        self.app._start_hidden = self.start_hidden
        self.app.activate()
        pump(0.3)
        self.window = self.app.window
        self.scheduler = self.app.scheduler
        self.repo = self.app.repo
        return self.app

    def close_app(self) -> None:
        app, self.app = self.app, None
        if app is None:
            return
        app.close_window()
        pump(0.1)
        app.close_services()
        pump(0.05)

    def _check_native_output(self) -> None:
        output = self._native.stop()
        if output.strip():
            sys.stderr.write(output)
        problems = [line for line in output.splitlines() if NATIVE_PROBLEM.search(line)]
        self.assertEqual(problems, [], "GTK or Python reported problems on stderr during this test")

    # Interaction helpers

    def click(self, button) -> None:
        """Press a button the way a user could: it must be on screen and enabled."""
        self.assertTrue(button.get_mapped(), f"button {text_of(button)!r} is not on screen")
        self.assertTrue(button.is_sensitive(), f"button {text_of(button)!r} is disabled")
        button.emit("clicked")
        pump(0.05)

    def choose(self, group, value) -> None:
        button = group.button_for(value)
        self.assertTrue(button.get_mapped() and button.is_sensitive())
        button.set_active(True)
        pump(0.02)

    def type_text(self, entry, text: str) -> None:
        """Insert one character at a time, like a keyboard does."""
        delegate = entry.get_delegate()
        for character in text:
            delegate.insert_text(character, len(entry.get_text()))
        pump(0.02)

    def press_escape(self, dialog) -> None:
        """Run the action bound to the Escape key on a dialog, as the key press would."""
        controllers = dialog.observe_controllers()
        for index in range(controllers.get_n_items()):
            controller = controllers.get_item(index)
            if not isinstance(controller, Gtk.ShortcutController):
                continue
            for position in range(controller.get_n_items()):
                shortcut = controller.get_item(position)
                if shortcut.get_trigger().to_string() == "Escape":
                    shortcut.get_action().activate(Gtk.ShortcutActionFlags.EXCLUSIVE, dialog, None)
                    pump(0.3)
                    return
        self.fail("dialog has no Escape shortcut")

    def respond(self, dialog, response: str) -> None:
        """Answer an Adw.AlertDialog."""
        dialog.emit("response", response)
        dialog.force_close()
        pump(0.1)

    def advance(self, seconds: float) -> None:
        """Move the injected clock, then let the scheduler and the window catch up."""
        self.clock.advance(seconds)
        self.scheduler.poll()
        self.window.refresh_live()
        pump(0.1)

    def labels_under(self, widget) -> list:
        return self.widgets_under(widget, Gtk.Label)

    def images_under(self, widget) -> list:
        return self.widgets_under(widget, Gtk.Image)

    @staticmethod
    def widgets_under(widget, kind) -> list:
        """Every descendant of one widget type, in tree order."""
        found, stack = [], [widget]
        while stack:
            current = stack.pop()
            if isinstance(current, kind):
                found.append(current)
            children, child = [], current.get_first_child()
            while child is not None:
                children.append(child)
                child = child.get_next_sibling()
            stack.extend(reversed(children))
        return found

    def toggle(self, check) -> None:
        """Flip a check button the way a click would: it must be on screen and enabled."""
        self.assertTrue(check.get_mapped() and check.is_sensitive())
        check.set_active(not check.get_active())
        pump(0.05)

    def click_spin(self, spin_button, direction: str) -> None:
        """Press the + or - button of a spin button."""
        child = spin_button.get_first_child()
        while child is not None:
            if child.get_css_name() == "button" and child.has_css_class(direction):
                self.assertTrue(child.get_mapped() and child.is_sensitive())
                spin_button.spin(
                    Gtk.SpinType.STEP_FORWARD if direction == "up" else Gtk.SpinType.STEP_BACKWARD, 1
                )
                pump(0.05)
                return
            child = child.get_next_sibling()
        self.fail(f"no {direction} button")

    def open_sheet(self, opener):
        """Click a row or chip that opens an option sheet and return the sheet on screen."""
        self.click(opener)
        pump(0.3)
        sheet = self.window.get_visible_dialog()
        self.assertIsNotNone(sheet, f"{text_of(opener)!r} opened no sheet")
        self.assertTrue(sheet.save_button.get_mapped())
        return sheet

    def open_editor(self, view_name: str):
        """Press a view's Create button and return the editor page it opens."""
        view = self.go(view_name)
        self.click(view.create_button)
        pump(0.3)
        editor = view.editor
        self.assertIsNotNone(editor)
        self.assertTrue(editor.save_button.get_mapped())
        return view, editor

    def open_settings(self):
        """Open Settings from the rail gear icon, as a user would."""
        self.click(self.window.settings_icon(compact=self.window.compact))
        pump(0.4)
        dialog = self.window.settings_dialog
        self.assertIsNotNone(dialog)
        return dialog

    def activate_row(self, row) -> None:
        self.assertTrue(row.get_mapped(), f"row {row.get_title()!r} is not on screen")
        row.emit("activated")
        pump(0.1)

    def go(self, name: str):
        self.window.nav_button(name, bottom=self.window.compact).set_active(True)
        pump(0.15)
        return self.window.view(name)

    def audio_playing(self) -> bool:
        return pump_until(lambda: self.app.audio.alert_state() == "playing", 5)


def target_size(widget) -> tuple[float, float]:
    """Border-box size of a widget: the area that actually receives clicks."""
    ok, bounds = widget.compute_bounds(widget.get_parent())
    return (bounds.get_width(), bounds.get_height()) if ok else (0.0, 0.0)
