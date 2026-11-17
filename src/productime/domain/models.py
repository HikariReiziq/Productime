from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

BreakType = Literal["none", "short", "long"]

SNOOZE_LIMIT_CHOICES: tuple[int | None, ...] = (None, 1, 2, 3, 5, 10)
ALARM_SNOOZE_INTERVALS = (1, 5, 10, 15, 20, 25, 30)
TARGET_SNOOZE_MINUTES = (1, 3, 5, 10)
TAG_COLORS = {"Work": "red", "Study": "purple", "Coding": "blue", "Personal": "amber"}
TAG_LABELS = tuple(TAG_COLORS)
MISSION_MODES = ("off", "typing", "math", "click")
MISSION_DIFFICULTIES = ("easy", "medium", "hard")
CLICK_PRESETS = (20, 30, 50)
MIN_CLICKS, MAX_CLICKS, DEFAULT_CLICKS = 10, 200, 30
DEFAULT_SOUND_ID = "bundled-digital"


class TargetPhase(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED_WORK = "paused_work"
    AWAITING_SUBTASK_GATE = "awaiting_subtask_gate"
    SNOOZING = "snoozing"
    RESTING = "resting"
    PAUSED_REST = "paused_rest"
    # A break ran out: the next batch waits until the user says they are back.
    READY_FOR_NEXT = "ready_for_next"
    COMPLETED = "completed"


@dataclass(frozen=True)
class TimelineSlot:
    batch_index: int
    subtask_title: str
    work_minutes: int
    break_minutes: int
    break_type: BreakType
    cumulative_start_minute: int
    cumulative_end_minute: int

    @property
    def break_end_minute(self) -> int:
        return self.cumulative_end_minute + self.break_minutes


@dataclass(frozen=True)
class TargetConfig:
    target_total_minutes: int
    work_minutes: int
    short_break_minutes: int
    long_rest_enabled: bool = False
    long_rest_every: int = 3
    long_rest_minutes: int = 30


@dataclass(frozen=True)
class Sound:
    id: str
    display_name: str
    source_type: str
    file_path: str
    duration_seconds: float = 0.0


@dataclass
class TargetBatch:
    id: str
    target_id: str
    batch_index: int
    subtask_title: str
    planned_work_minutes: int
    planned_break_minutes: int
    break_type: BreakType
    status: str = "pending"
    actual_work_seconds: int = 0
    snooze_seconds_used: int = 0
    snooze_count: int = 0
    completed_at: str | None = None


@dataclass
class Target:
    id: str
    name: str
    tag_label: str
    tag_color: str
    config: TargetConfig
    snooze_limit: int | None
    sound_id: str
    status: str
    created_at: str
    updated_at: str
    batches: list[TargetBatch] = field(default_factory=list)

    @property
    def total_timeline_minutes(self) -> int:
        return sum(b.planned_work_minutes + b.planned_break_minutes for b in self.batches)

    @property
    def completed_batches(self) -> int:
        return sum(1 for b in self.batches if b.status == "completed")


@dataclass(frozen=True)
class TargetDraft:
    name: str
    tag_label: str
    config: TargetConfig
    snooze_limit: int | None
    sound_id: str
    subtask_titles: tuple[str, ...] = ()


@dataclass
class Alarm:
    id: str
    name: str
    hour_24: int
    minute: int
    repeat_days: tuple[int, ...]
    sound_id: str
    snooze_interval_minutes: int
    snooze_limit: int | None
    mission_mode: str
    mission_count: int
    mission_difficulty: str
    enabled: bool
    created_at: str = ""
    mission_clicks: int = DEFAULT_CLICKS


@dataclass(frozen=True)
class AlarmDraft:
    name: str
    hour_24: int
    minute: int
    repeat_days: tuple[int, ...]
    sound_id: str
    snooze_interval_minutes: int = 5
    snooze_limit: int | None = None
    mission_mode: str = "off"
    mission_count: int = 1
    mission_difficulty: str = "easy"
    enabled: bool = True
    mission_clicks: int = DEFAULT_CLICKS


@dataclass
class TimerPreset:
    id: str
    name: str
    duration_seconds: int
    sound_id: str
    created_at: str = ""


@dataclass(frozen=True)
class TimerDraft:
    name: str
    duration_seconds: int
    sound_id: str


@dataclass(frozen=True)
class FocusLog:
    id: str
    source_type: str
    target_id: str | None
    batch_id: str | None
    title: str
    tag_label: str
    started_at: str
    ended_at: str
    duration_seconds: int
    subtask_completed: bool
    local_date_key: str
    target_name: str = ""


@dataclass(frozen=True)
class ReportSummary:
    period: str
    start_date: str
    end_date: str
    task_completed: int
    total_seconds: int
    bucket_labels: tuple[str, ...]
    bucket_seconds: tuple[int, ...]
    logs: tuple[FocusLog, ...]
