"""Shared test doubles and fixtures for the integration and UI suites."""

import math
import struct
import tempfile
import warnings
import wave
from datetime import datetime
from pathlib import Path

from productime.db.database import open_database
from productime.db.repositories import Repository
from productime.domain.models import AlarmDraft, TargetConfig, TargetDraft, TimerDraft

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "Assets"

# 2026-11-16 is a Monday.
MONDAY_0800 = datetime(2026, 11, 16, 8, 0, 0)

EXAMPLE_1 = TargetDraft(
    name="Website Profile Company",
    tag_label="Coding",
    config=TargetConfig(360, 50, 10, True, 3, 30),
    snooze_limit=3,
    sound_id="bundled-digital",
    subtask_titles=(
        "Membuat PRD",
        "Membuat desain",
        "Coding backend",
        "Coding database dan frontend",
        "Security dan deploy",
        "Finish selesai semua",
    ),
)
EXAMPLE_2 = TargetDraft(
    name="Belajar Pemrograman Mobile",
    tag_label="Study",
    config=TargetConfig(240, 25, 5, True, 4, 30),
    snooze_limit=None,
    sound_id="bundled-lofi",
    subtask_titles=(
        "Belajar fundamental pemrograman mobile",
        "Membuat JavaScript",
        "Coding JavaScript",
        "Coding XML",
        "Belajar coding JavaScript tingkat lanjut",
        "Belajar Flutter",
        "Belajar deploy dan menggunakan Android Studio",
        "Finish",
    ),
)


def quiet_pygobject_asyncio_warning() -> None:
    """Hide one DeprecationWarning raised inside the system PyGObject on Python 3.14.

    Call from setUpModule: unittest resets warning filters when a run starts.
    """
    warnings.filterwarnings(
        "ignore",
        message=r".*asyncio\.(get_event_loop_policy|AbstractEventLoopPolicy).*",
        category=DeprecationWarning,
    )


def short_target(**overrides) -> TargetDraft:
    """Two one-minute batches with one-minute breaks: fast to walk through."""
    values = {
        "name": "Quick target",
        "tag_label": "Work",
        "config": TargetConfig(4, 1, 1, False, 3, 30),
        "snooze_limit": 1,
        "sound_id": "bundled-community",
        "subtask_titles": ("First step", "Second step"),
    }
    values.update(overrides)
    return TargetDraft(**values)


def alarm_draft(**overrides) -> AlarmDraft:
    values = {
        "name": "Morning class",
        "hour_24": 8,
        "minute": 5,
        "repeat_days": (),
        "sound_id": "bundled-digital",
        "snooze_interval_minutes": 5,
        "snooze_limit": 1,
        "mission_mode": "off",
        "mission_count": 1,
        "mission_difficulty": "easy",
        "enabled": True,
    }
    values.update(overrides)
    return AlarmDraft(**values)


def timer_draft(**overrides) -> TimerDraft:
    values = {"name": "Tea", "duration_seconds": 90, "sound_id": "bundled-funny"}
    values.update(overrides)
    return TimerDraft(**values)


class TempData:
    """A throwaway XDG-style data directory with an open repository."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="productime-test-")
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "productime"
        self.conn = open_database(self.data_dir)
        self.repo = Repository(self.conn)

    def reopen(self) -> Repository:
        self.conn.close()
        self.conn = open_database(self.data_dir)
        self.repo = Repository(self.conn)
        return self.repo

    def close(self):
        self.conn.close()
        self._tmp.cleanup()


class RecordingNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.withdrawn: list[str] = []
        self.buttons: dict[str, tuple] = {}

    def notify(self, key, title, body, urgent=False, buttons=()):
        self.sent.append((key, title, body))
        self.buttons[key] = tuple(buttons)

    def button_labels(self, key) -> list[str]:
        return [label for label, _action, _target in self.buttons.get(key, ())]

    def withdraw(self, key):
        self.withdrawn.append(key)

    def titles(self) -> list[str]:
        return [title for _key, title, _body in self.sent]


class FakeAudio:
    """Records alert requests so scheduler tests need no audio stack."""

    def __init__(self):
        self.alert_sound_id: str | None = None
        self.history: list[str] = []

    def start_alert(self, sound_id):
        if sound_id != self.alert_sound_id:
            self.history.append(f"start:{sound_id}")
        self.alert_sound_id = sound_id

    def stop_alert(self):
        if self.alert_sound_id is not None:
            self.history.append("stop")
        self.alert_sound_id = None

    def stop_preview(self):
        pass

    def shutdown(self):
        self.stop_alert()


def make_wav(path: Path, seconds: float = 0.25, rate: int = 8000) -> Path:
    """Write a real, tiny sine-wave WAV so imports and playback use valid audio."""
    frames = int(seconds * rate)
    samples = (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(frames))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return path
