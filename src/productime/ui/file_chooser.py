"""Native file dialogs. Kept behind one small class so the app can be driven without them."""

from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

AUDIO_PATTERNS = ("*.mp3", "*.wav", "*.ogg", "*.flac", "*.m4a")


class NativeFileChooser:
    def open_file(
        self,
        parent: Gtk.Window,
        title: str,
        filter_name: str,
        patterns: tuple[str, ...],
        on_path: Callable[[Path], None],
    ) -> None:
        dialog = Gtk.FileDialog(title=title, modal=True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name(filter_name)
        for pattern in patterns:
            file_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.open(parent, None, self._finish, dialog.open_finish, on_path)

    def save_file(
        self,
        parent: Gtk.Window,
        title: str,
        suggested_name: str,
        on_path: Callable[[Path], None],
    ) -> None:
        dialog = Gtk.FileDialog(title=title, modal=True)
        dialog.set_initial_name(suggested_name)
        dialog.save(parent, None, self._finish, dialog.save_finish, on_path)

    @staticmethod
    def _finish(_dialog, result, finish, on_path) -> None:
        try:
            chosen = finish(result)
        except GLib.Error:
            # Cancelling the dialog is reported as an error; there is nothing to do.
            return
        path = chosen.get_path() if chosen else None
        if path:
            on_path(Path(path))

    def open_folder(self, parent: Gtk.Window, folder: Path) -> None:
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder)))
        launcher.launch(parent, None, self._launched, launcher)

    @staticmethod
    def _launched(_source, result, launcher) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            return
