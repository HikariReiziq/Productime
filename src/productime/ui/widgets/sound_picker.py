"""Sound selector: six bundled sounds, custom imports, and an inline preview."""

from collections.abc import Callable
from pathlib import Path

from gi.repository import Gtk

from ...domain.models import DEFAULT_SOUND_ID
from ...services.audio_service import AudioImportError
from ..file_chooser import AUDIO_PATTERNS
from .controls import make_button, set_accessible_label, text_label, vbox, wrap_row


class SoundPicker(Gtk.Box):
    def __init__(self, window, sound_id: str = DEFAULT_SOUND_ID, on_change: Callable[[], None] | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._window = window
        self._library = window.app.library
        self._audio = window.app.audio
        self._on_change = on_change
        self._sounds = []
        self._quiet = False

        self.dropdown = Gtk.DropDown()
        self.dropdown.set_hexpand(True)
        set_accessible_label(self.dropdown, "Sound")
        self.dropdown.connect("notify::selected", self._on_selected)
        self.preview_button = make_button("Play", "secondary", on_click=self.toggle_preview)
        self.import_button = make_button(
            "Import Custom Audio...", "ghost", on_click=self._choose_file, icon="document-open-symbolic"
        )
        self.error_label = text_label("", "error-text", "small", wrap=True)
        self.error_label.set_visible(False)

        self.append(wrap_row(self.dropdown, self.preview_button))
        self.append(vbox(4, self.import_button, self.error_label))
        self.import_button.set_halign(Gtk.Align.START)
        self.refresh(sound_id)
        self.connect("unmap", lambda _w: self.stop_preview())

    def refresh(self, select_id: str | None = None) -> None:
        wanted = select_id or self.get_sound_id()
        self._sounds = self._library.list()
        self._quiet = True
        self.dropdown.set_model(Gtk.StringList.new([sound.display_name for sound in self._sounds]))
        self._quiet = False
        self.set_sound_id(wanted)

    def get_sound_id(self) -> str:
        index = self.dropdown.get_selected()
        if 0 <= index < len(self._sounds):
            return self._sounds[index].id
        return DEFAULT_SOUND_ID

    def set_sound_id(self, sound_id: str) -> None:
        ids = [sound.id for sound in self._sounds]
        fallback = ids.index(DEFAULT_SOUND_ID) if DEFAULT_SOUND_ID in ids else 0
        self._quiet = True
        self.dropdown.set_selected(ids.index(sound_id) if sound_id in ids else fallback)
        self._quiet = False

    def sound_names(self) -> list[str]:
        return [sound.display_name for sound in self._sounds]

    def _on_selected(self, *_args) -> None:
        if self._quiet:
            return
        self.stop_preview()
        if self._on_change:
            self._on_change()

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def toggle_preview(self) -> None:
        if self._audio.preview_sound_id is not None:
            self.stop_preview()
            return
        self._show_error("")
        if self._audio.preview(self.get_sound_id(), on_finished=self._preview_ended):
            self.preview_button.set_label("Stop")
        elif self._audio.alert_sound_id is not None:
            self._show_error("Preview is unavailable while an alarm or timer is sounding.")
        else:
            self._show_error("That sound could not be played.")

    def _preview_ended(self) -> None:
        self.preview_button.set_label("Play")

    def stop_preview(self) -> None:
        if self._audio.preview_sound_id is not None:
            self._audio.stop_preview()
        self.preview_button.set_label("Play")

    def _choose_file(self) -> None:
        self._window.app.file_chooser.open_file(
            self._window, "Import Custom Audio", "Audio files", AUDIO_PATTERNS, self.import_from_path
        )

    def import_from_path(self, path: Path) -> None:
        """Validate and copy a chosen file, then select it. Rejections are shown inline."""
        self.stop_preview()
        try:
            sound = self._library.import_custom(path)
        except AudioImportError as error:
            self._show_error(str(error))
            return
        self._show_error("")
        self.refresh(sound.id)
        if self._on_change:
            self._on_change()
