"""In-process GStreamer playback and the validated custom sound importer."""

import os
import re
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from ..db.repositories import Repository
from ..domain.models import DEFAULT_SOUND_ID, Sound

ALLOWED_EXTENSIONS = {".mp3": "mp3", ".wav": "wav", ".ogg": "ogg", ".flac": "flac", ".m4a": "m4a"}
MAX_IMPORT_BYTES = 50 * 1024 * 1024
MAX_DISPLAY_NAME = 60

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# playbin flags: decode audio only, so album art never opens a video window.
_PLAY_FLAGS_AUDIO_ONLY = 0x02 | 0x10

_Gst = None


class AudioImportError(Exception):
    """The chosen file cannot be used as a custom sound. The message is safe to show."""


class SoundResolveError(Exception):
    """A sound id does not map to a playable file inside the allowed folders."""


def _gst():
    """Import GStreamer on first use so an idle app never pays for it."""
    global _Gst
    if _Gst is None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        _Gst = Gst
    return _Gst


def sniff_audio_kind(header: bytes) -> str | None:
    """Identify an audio container from its first bytes, independent of the file name."""
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"OggS":
        return "ogg"
    if header[:4] == b"fLaC":
        return "flac"
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "m4a"
    if header[:3] == b"ID3":
        return "mp3"
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        version = (header[1] >> 3) & 0x03
        layer = (header[1] >> 1) & 0x03
        if version != 0x01 and layer != 0x00:
            return "mp3"
    return None


def probe_duration(path: Path) -> float:
    """Decode-check a file and return its length. Rejects anything without an audio stream."""
    gst = _gst()
    import gi

    gi.require_version("GstPbutils", "1.0")
    from gi.repository import GLib, GstPbutils

    try:
        discoverer = GstPbutils.Discoverer.new(5 * gst.SECOND)
        info = discoverer.discover_uri(gst.filename_to_uri(str(path)))
    except GLib.Error as error:
        raise AudioImportError("This file could not be decoded as audio.") from error
    if info.get_result() != GstPbutils.DiscovererResult.OK or not info.get_audio_streams():
        raise AudioImportError("This file could not be decoded as audio.")
    duration = info.get_duration() / gst.SECOND
    if duration <= 0:
        raise AudioImportError("This audio file has no playable length.")
    return round(duration, 3)


class SoundLibrary:
    """Maps sound ids to files, and only to files inside Assets/ or the user's sounds folder."""

    def __init__(
        self,
        repo: Repository,
        assets_dir: Path,
        sounds_dir: Path,
        probe: Callable[[Path], float] = probe_duration,
    ):
        self.repo = repo
        self.assets_dir = Path(assets_dir)
        self.sounds_dir = Path(sounds_dir)
        self._probe = probe

    def list(self) -> list[Sound]:
        return self.repo.list_sounds()

    def resolve(self, sound_id: str) -> Path:
        if not isinstance(sound_id, str) or not _ID_RE.match(sound_id):
            raise SoundResolveError("Unknown sound.")
        sound = self.repo.get_sound(sound_id)
        if sound is None:
            raise SoundResolveError("Unknown sound.")
        root = self.assets_dir if sound.source_type == "bundled" else self.sounds_dir
        name = sound.file_path
        if not name or name in (".", "..") or name != os.path.basename(name):
            raise SoundResolveError("Sound file is outside the sounds folder.")
        root_real = Path(os.path.realpath(root))
        real = Path(os.path.realpath(root_real / name))
        # realpath follows symlinks, so a link that escapes the folder fails this check.
        if real.parent != root_real or not real.is_file():
            raise SoundResolveError("Sound file is missing or outside the sounds folder.")
        return real

    def file_exists(self, row: dict) -> bool:
        name = row.get("file_path", "")
        if not name or name != os.path.basename(name):
            return False
        path = self.sounds_dir / name
        return path.is_file() and not path.is_symlink()

    def import_custom(self, source: str | os.PathLike) -> Sound:
        source = Path(source)
        extension = source.suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise AudioImportError("Choose an MP3, WAV, OGG, FLAC, or M4A file.")
        try:
            # O_NONBLOCK keeps a FIFO or device node from hanging the UI before fstat rejects it.
            fd = os.open(os.path.realpath(source), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError as error:
            raise AudioImportError("That file could not be opened.") from error

        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise AudioImportError("Choose a regular audio file.")

        token = uuid.uuid4().hex
        file_name = f"{token}{extension}"
        destination = self.sounds_dir / file_name
        with os.fdopen(fd, "rb") as handle:
            if info.st_size == 0:
                raise AudioImportError("That file is empty.")
            if info.st_size > MAX_IMPORT_BYTES:
                raise AudioImportError("That file is larger than the 50 MB limit.")
            header = handle.read(16)
            if sniff_audio_kind(header) != ALLOWED_EXTENSIONS[extension]:
                raise AudioImportError("That file's content does not match its audio extension.")

            self.sounds_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(out_fd, "wb") as out:
                    out.write(header)
                    copied = len(header)
                    while chunk := handle.read(1 << 16):
                        copied += len(chunk)
                        # The size was checked once already; this catches a file that grows mid-copy.
                        if copied > MAX_IMPORT_BYTES:
                            raise AudioImportError("That file is larger than the 50 MB limit.")
                        out.write(chunk)
                duration = self._probe(destination)
            except AudioImportError:
                destination.unlink(missing_ok=True)
                raise
            except OSError as error:
                destination.unlink(missing_ok=True)
                raise AudioImportError("That file could not be copied.") from error

        display_name = _CONTROL_RE.sub("", source.stem).strip()[:MAX_DISPLAY_NAME] or "Custom sound"
        sound = Sound(f"custom-{token}", display_name, "custom", file_name, duration)
        self.repo.add_sound(sound)
        return sound

    def remove_custom(self, sound_id: str) -> None:
        sound = self.repo.get_sound(sound_id)
        if sound is None or sound.source_type != "custom":
            return
        if sound.file_path == os.path.basename(sound.file_path):
            (self.sounds_dir / sound.file_path).unlink(missing_ok=True)
        self.repo.delete_sound(sound_id)


class LoopPlayer:
    """One playbin. Holds a pipeline only while sound is playing."""

    def __init__(self, audio_sink: str | None = None):
        self._sink_description = audio_sink
        self._pipeline = None
        self._bus = None
        self._bus_handler = 0
        self._loop = False
        self._segment_armed = False
        self._on_error: Callable[[str], None] | None = None
        self._on_finished: Callable[[], None] | None = None
        self.loop_count = 0

    @property
    def has_pipeline(self) -> bool:
        return self._pipeline is not None

    @property
    def state(self) -> str:
        if self._pipeline is None:
            return "null"
        _result, state, _pending = self._pipeline.get_state(0)
        return state.value_nick

    def play(
        self,
        path: Path,
        *,
        loop: bool,
        volume: float,
        on_error: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        gst = _gst()
        self.stop()
        pipeline = gst.ElementFactory.make("playbin", None)
        pipeline.set_property("uri", gst.filename_to_uri(str(path)))
        pipeline.set_property("flags", _PLAY_FLAGS_AUDIO_ONLY)
        pipeline.set_property("volume", max(0.0, min(1.0, volume)))
        if self._sink_description:
            pipeline.set_property("audio-sink", gst.parse_bin_from_description(self._sink_description, True))
        self._pipeline = pipeline
        self._loop = loop
        self._segment_armed = False
        self._on_error = on_error
        self._on_finished = on_finished
        self.loop_count = 0
        self._bus = pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_handler = self._bus.connect("message", self._on_message)
        # A looping clip prerolls paused first so the segment seek can be issued before playback.
        pipeline.set_state(gst.State.PAUSED if loop else gst.State.PLAYING)

    def set_volume(self, volume: float) -> None:
        if self._pipeline is not None:
            self._pipeline.set_property("volume", max(0.0, min(1.0, volume)))

    def stop(self) -> None:
        if self._pipeline is None:
            return
        gst = _gst()
        self._bus.disconnect(self._bus_handler)
        self._bus.remove_signal_watch()
        # NULL releases the decoder and audio device, which is what keeps idle RAM low.
        self._pipeline.set_state(gst.State.NULL)
        self._pipeline = None
        self._bus = None
        self._on_error = None
        self._on_finished = None

    def _on_message(self, _bus, message) -> None:
        gst = _gst()
        pipeline = self._pipeline
        if pipeline is None:
            return
        kind = message.type
        if kind == gst.MessageType.ASYNC_DONE and self._loop and not self._segment_armed:
            self._segment_armed = True
            pipeline.seek(
                1.0,
                gst.Format.TIME,
                gst.SeekFlags.FLUSH | gst.SeekFlags.SEGMENT,
                gst.SeekType.SET,
                0,
                gst.SeekType.NONE,
                -1,
            )
            pipeline.set_state(gst.State.PLAYING)
        elif kind == gst.MessageType.SEGMENT_DONE and self._loop:
            self.loop_count += 1
            # A non-flushing segment seek restarts the clip without a gap.
            pipeline.seek(
                1.0,
                gst.Format.TIME,
                gst.SeekFlags.SEGMENT,
                gst.SeekType.SET,
                0,
                gst.SeekType.NONE,
                -1,
            )
        elif kind == gst.MessageType.EOS:
            if self._loop:
                # Formats that ignore segment seeks still loop, with a short flush gap.
                self.loop_count += 1
                pipeline.seek_simple(gst.Format.TIME, gst.SeekFlags.FLUSH | gst.SeekFlags.KEY_UNIT, 0)
            else:
                finished = self._on_finished
                self.stop()
                if finished:
                    finished()
        elif kind == gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            failed = self._on_error
            self.stop()
            if failed:
                failed(error.message)


class AudioService:
    """One looping alert player plus one preview player, both silent by default."""

    def __init__(
        self,
        library: SoundLibrary,
        audio_sink: str | None = None,
        volume: int = 100,
        muted: bool = False,
    ):
        self.library = library
        self._alert = LoopPlayer(audio_sink)
        self._preview = LoopPlayer(audio_sink)
        self._volume = max(0, min(100, volume))
        self._muted = muted
        self._preview_done: Callable[[], None] | None = None
        self.alert_sound_id: str | None = None
        self.preview_sound_id: str | None = None
        self.last_error: str | None = None
        self.on_error: Callable[[str], None] | None = None

    @property
    def effective_volume(self) -> float:
        return 0.0 if self._muted else self._volume / 100

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def alert_loop_count(self) -> int:
        return self._alert.loop_count

    def alert_state(self) -> str:
        return self._alert.state

    def preview_state(self) -> str:
        return self._preview.state

    def set_volume(self, percent: int) -> None:
        self._volume = max(0, min(100, int(percent)))
        self._apply_volume()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._apply_volume()

    def _apply_volume(self) -> None:
        self._alert.set_volume(self.effective_volume)
        self._preview.set_volume(self.effective_volume)

    def _report(self, message: str) -> None:
        self.last_error = message
        if self.on_error:
            self.on_error(message)

    def _playable_path(self, sound_id: str) -> Path | None:
        try:
            return self.library.resolve(sound_id)
        except SoundResolveError:
            self._report("The chosen sound is unavailable. Playing the default alarm sound.")
        try:
            return self.library.resolve(DEFAULT_SOUND_ID)
        except SoundResolveError:
            self._report("No alarm sound could be found. Check the Productime installation.")
            return None

    def start_alert(self, sound_id: str) -> None:
        if self.alert_sound_id == sound_id and self._alert.has_pipeline:
            return
        self.stop_preview()
        self.alert_sound_id = sound_id
        path = self._playable_path(sound_id)
        if path is None:
            return
        self._alert.play(
            path,
            loop=True,
            volume=self.effective_volume,
            on_error=lambda message: self._alert_failed(sound_id, message),
        )

    def _alert_failed(self, sound_id: str, message: str) -> None:
        self._report(f"Sound playback failed: {message}")
        # An alarm must not go silent because one file is broken, so retry with the default.
        if sound_id == DEFAULT_SOUND_ID or self.alert_sound_id != sound_id:
            return
        try:
            fallback = self.library.resolve(DEFAULT_SOUND_ID)
        except SoundResolveError:
            return
        self._alert.play(fallback, loop=True, volume=self.effective_volume)

    def stop_alert(self) -> None:
        self._alert.stop()
        self.alert_sound_id = None

    def preview(self, sound_id: str, on_finished: Callable[[], None] | None = None) -> bool:
        if self.alert_sound_id is not None:
            return False
        self.stop_preview()
        try:
            path = self.library.resolve(sound_id)
        except SoundResolveError:
            self._report("That sound is unavailable and cannot be previewed.")
            return False
        self.preview_sound_id = sound_id
        self._preview_done = on_finished
        self._preview.play(
            path,
            loop=False,
            volume=self.effective_volume,
            on_finished=self._finish_preview,
            on_error=self._preview_failed,
        )
        return True

    def _preview_failed(self, message: str) -> None:
        self._report(f"Preview failed: {message}")
        self._finish_preview()

    def _finish_preview(self) -> None:
        done, self._preview_done = self._preview_done, None
        self.preview_sound_id = None
        if done:
            done()

    def stop_preview(self) -> None:
        self._preview.stop()
        self._finish_preview()

    def shutdown(self) -> None:
        self.stop_preview()
        self.stop_alert()
