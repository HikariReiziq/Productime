"""Input bounds for forms and strict schema validation for JSON backups."""

import re
from dataclasses import replace

from . import target_engine
from .models import (
    ALARM_SNOOZE_INTERVALS,
    MISSION_DIFFICULTIES,
    MISSION_MODES,
    SNOOZE_LIMIT_CHOICES,
    TAG_COLORS,
    AlarmDraft,
    TargetDraft,
    TimerDraft,
)

BACKUP_FORMAT = "productime-backup"
BACKUP_VERSION = 1

MAX_NAME_LENGTH = 80
MAX_TAG_LENGTH = 24
CUSTOM_TAG_COLOR = "gray"
MAX_TARGET_MINUTES = 24 * 60
MAX_WORK_MINUTES = 240
MAX_BREAK_MINUTES = 240
MAX_LONG_REST_EVERY = 50
MAX_SNOOZE_MINUTES = 120
MAX_TIMER_SECONDS = 99 * 3600 + 59 * 60 + 59
MAX_BATCHES = 200
MAX_ROWS = {"sounds": 500, "targets": 2000, "alarms": 500, "timers": 500, "focus_logs": 200_000}

SOUND_EXTENSIONS = ("mp3", "wav", "ogg", "flac", "m4a")
_FLAG = ("0", "1")
PREFERENCE_RULES = {
    "theme": lambda v: v in ("system", "light", "dark"),
    "language": lambda v: v in ("en", "id"),
    "close_to_background": lambda v: v in _FLAG,
    "muted": lambda v: v in _FLAG,
    "volume": lambda v: v.isascii() and v.isdigit() and 0 <= int(v) <= 100,
    "notify_alarms": lambda v: v in _FLAG,
    "notify_timers": lambda v: v in _FLAG,
    "notify_targets": lambda v: v in _FLAG,
    "onboarding_done": lambda v: v in _FLAG,
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SOUND_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(" + "|".join(SOUND_EXTENSIONS) + r")$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ValidationError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def clean_text(value: object, field: str, max_length: int = MAX_NAME_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValidationError(field, "Enter text for this field.")
    text = _CONTROL_RE.sub("", value).strip()
    if not text:
        raise ValidationError(field, "This field cannot be empty.")
    if len(text) > max_length:
        raise ValidationError(field, f"Keep this under {max_length} characters.")
    return text


def _int_in(value: object, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, "Enter a whole number.")
    if not low <= value <= high:
        raise ValidationError(field, f"Enter a number between {low} and {high}.")
    return value


def _sound_id(value: object, field: str = "sound_id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValidationError(field, "Choose a sound from the list.")
    return value


def _snooze_limit(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or value not in SNOOZE_LIMIT_CHOICES:
        raise ValidationError("snooze_limit", "Choose a snooze limit from the list.")
    return value


def tag_color(tag_label: str) -> str:
    """Preset categories keep their own colour; a name the user typed gets the neutral one."""
    return TAG_COLORS.get(tag_label, CUSTOM_TAG_COLOR)


def validate_snooze_minutes(minutes: object) -> int:
    return _int_in(minutes, "snooze_minutes", 1, MAX_SNOOZE_MINUTES)


def validate_target_draft(draft: TargetDraft) -> TargetDraft:
    name = clean_text(draft.name, "name")
    tag_label = clean_text(draft.tag_label, "tag_label", MAX_TAG_LENGTH)
    config = draft.config
    _int_in(config.target_total_minutes, "target_total_minutes", 1, MAX_TARGET_MINUTES)
    _int_in(config.work_minutes, "work_minutes", 1, MAX_WORK_MINUTES)
    _int_in(config.short_break_minutes, "short_break_minutes", 0, MAX_BREAK_MINUTES)
    _int_in(config.long_rest_every, "long_rest_every", 1, MAX_LONG_REST_EVERY)
    _int_in(config.long_rest_minutes, "long_rest_minutes", 1, MAX_BREAK_MINUTES)

    count = target_engine.batch_count(config)
    if count > MAX_BATCHES:
        raise ValidationError("work_minutes", f"This plan needs more than {MAX_BATCHES} batches.")
    titles = []
    for index in range(1, count + 1):
        raw = draft.subtask_titles[index - 1] if index <= len(draft.subtask_titles) else ""
        cleaned = _CONTROL_RE.sub("", raw).strip() if isinstance(raw, str) else ""
        if len(cleaned) > MAX_NAME_LENGTH:
            raise ValidationError(f"subtask_{index}", f"Keep this under {MAX_NAME_LENGTH} characters.")
        titles.append(cleaned or f"Batch {index}")
    return replace(
        draft,
        name=name,
        tag_label=tag_label,
        snooze_limit=_snooze_limit(draft.snooze_limit),
        sound_id=_sound_id(draft.sound_id),
        subtask_titles=tuple(titles),
    )


def _repeat_days(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError("repeat_days", "Repeat days must be a list of weekdays.")
    days = set()
    for day in value:
        days.add(_int_in(day, "repeat_days", 1, 7))
    return tuple(sorted(days))


def _mission(mode: object, count: object, difficulty: object) -> tuple[str, int, str]:
    if mode not in MISSION_MODES:
        raise ValidationError("mission_mode", "Choose a mission mode from the list.")
    if difficulty not in MISSION_DIFFICULTIES:
        raise ValidationError("mission_difficulty", "Choose a difficulty from the list.")
    return mode, _int_in(count, "mission_count", 1, 3), difficulty


def validate_alarm_draft(draft: AlarmDraft) -> AlarmDraft:
    name = clean_text(draft.name, "name")
    _int_in(draft.hour_24, "hour_24", 0, 23)
    _int_in(draft.minute, "minute", 0, 59)
    if draft.snooze_interval_minutes not in ALARM_SNOOZE_INTERVALS:
        raise ValidationError("snooze_interval_minutes", "Choose a snooze interval from the list.")
    _mission(draft.mission_mode, draft.mission_count, draft.mission_difficulty)
    return replace(
        draft,
        name=name,
        repeat_days=_repeat_days(draft.repeat_days),
        snooze_limit=_snooze_limit(draft.snooze_limit),
        sound_id=_sound_id(draft.sound_id),
        enabled=bool(draft.enabled),
    )


def validate_timer_draft(draft: TimerDraft) -> TimerDraft:
    name = clean_text(draft.name, "name")
    _int_in(draft.duration_seconds, "duration_seconds", 1, MAX_TIMER_SECONDS)
    return replace(draft, name=name, sound_id=_sound_id(draft.sound_id))


def _row(value: object, section: str, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(section, f"Backup section '{section}' holds a malformed entry.")
    missing = [key for key in keys if key not in value]
    if missing:
        raise ValidationError(section, f"Backup section '{section}' is missing '{missing[0]}'.")
    return value


def _backup_id(value: object, section: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValidationError(section, f"Backup section '{section}' holds an invalid id.")
    return value


def _optional_id(value: object, section: str) -> str | None:
    return None if value is None else _backup_id(value, section)


def _timestamp(value: object, section: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.match(value):
        raise ValidationError(section, f"Backup section '{section}' holds an invalid timestamp.")
    return value


def _flag(value: object, section: str) -> int:
    if value in (0, 1, True, False):
        return int(value)
    raise ValidationError(section, f"Backup section '{section}' holds an invalid flag.")


def _one_of(value: object, section: str, choices: tuple) -> str:
    if value not in choices:
        raise ValidationError(section, f"Backup section '{section}' holds an unknown value.")
    return value


def _backup_sound(raw: object) -> dict:
    row = _row(raw, "sounds", ("id", "display_name", "source_type", "file_path"))
    if row["source_type"] != "custom":
        raise ValidationError("sounds", "Only custom sounds can be restored from a backup.")
    path = row["file_path"]
    # A bare generated filename only: restored rows must never point outside the sounds folder.
    if not isinstance(path, str) or not _SOUND_FILE_RE.match(path):
        raise ValidationError("sounds", "Backup holds an unsafe sound file name.")
    duration = row.get("duration_seconds", 0)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ValidationError("sounds", "Backup holds an invalid sound duration.")
    return {
        "id": _backup_id(row["id"], "sounds"),
        "display_name": clean_text(row["display_name"], "sounds"),
        "source_type": "custom",
        "file_path": path,
        "duration_seconds": float(duration),
    }


def _backup_batch(raw: object) -> dict:
    section = "targets"
    row = _row(
        raw,
        section,
        (
            "id",
            "batch_index",
            "subtask_title",
            "planned_work_minutes",
            "planned_break_minutes",
            "break_type",
            "status",
        ),
    )
    completed_at = row.get("completed_at")
    return {
        "id": _backup_id(row["id"], section),
        "batch_index": _int_in(row["batch_index"], section, 1, MAX_BATCHES),
        "subtask_title": clean_text(row["subtask_title"], section),
        "planned_work_minutes": _int_in(row["planned_work_minutes"], section, 1, MAX_WORK_MINUTES),
        "planned_break_minutes": _int_in(row["planned_break_minutes"], section, 0, MAX_BREAK_MINUTES),
        "break_type": _one_of(row["break_type"], section, ("none", "short", "long")),
        "status": _one_of(row["status"], section, ("pending", "active", "completed", "skipped")),
        "actual_work_seconds": _int_in(row.get("actual_work_seconds", 0), section, 0, 10**7),
        "snooze_seconds_used": _int_in(row.get("snooze_seconds_used", 0), section, 0, 10**7),
        "snooze_count": _int_in(row.get("snooze_count", 0), section, 0, 10**5),
        "completed_at": None if completed_at is None else _timestamp(completed_at, section),
    }


def _backup_target(raw: object) -> dict:
    section = "targets"
    row = _row(
        raw,
        section,
        (
            "id",
            "name",
            "tag_label",
            "target_total_minutes",
            "work_minutes",
            "short_break_minutes",
            "long_rest_enabled",
            "long_rest_every",
            "long_rest_minutes",
            "snooze_limit",
            "sound_id",
            "status",
            "batches",
        ),
    )
    batches_raw = row["batches"]
    if not isinstance(batches_raw, list) or not 1 <= len(batches_raw) <= MAX_BATCHES:
        raise ValidationError(section, "Backup holds a target without valid batches.")
    batches = [_backup_batch(item) for item in batches_raw]
    indexes = [b["batch_index"] for b in batches]
    if len(set(indexes)) != len(indexes) or len({b["id"] for b in batches}) != len(batches):
        raise ValidationError(section, "Backup holds duplicate batches for one target.")
    return {
        "id": _backup_id(row["id"], section),
        "name": clean_text(row["name"], section),
        "tag_label": clean_text(row["tag_label"], section, 24),
        "tag_color": clean_text(row.get("tag_color", "purple"), section, 24),
        "target_total_minutes": _int_in(row["target_total_minutes"], section, 1, MAX_TARGET_MINUTES),
        "work_minutes": _int_in(row["work_minutes"], section, 1, MAX_WORK_MINUTES),
        "short_break_minutes": _int_in(row["short_break_minutes"], section, 0, MAX_BREAK_MINUTES),
        "long_rest_enabled": _flag(row["long_rest_enabled"], section),
        "long_rest_every": _int_in(row["long_rest_every"], section, 1, MAX_LONG_REST_EVERY),
        "long_rest_minutes": _int_in(row["long_rest_minutes"], section, 1, MAX_BREAK_MINUTES),
        "snooze_limit": _backup_snooze_limit(row["snooze_limit"], section),
        "sound_id": _backup_id(row["sound_id"], section),
        "status": _one_of(row["status"], section, ("planned", "in_progress", "completed")),
        "created_at": _timestamp(row.get("created_at", "1970-01-01 00:00:00"), section),
        "updated_at": _timestamp(row.get("updated_at", "1970-01-01 00:00:00"), section),
        "batches": sorted(batches, key=lambda b: b["batch_index"]),
    }


def _backup_snooze_limit(value: object, section: str) -> int | None:
    try:
        return _snooze_limit(value)
    except ValidationError as error:
        message = f"Backup section '{section}' holds an invalid snooze limit."
        raise ValidationError(section, message) from error


def _backup_alarm(raw: object) -> dict:
    section = "alarms"
    row = _row(
        raw,
        section,
        (
            "id",
            "name",
            "hour_24",
            "minute",
            "repeat_days",
            "sound_id",
            "snooze_interval_minutes",
            "snooze_limit",
            "mission_mode",
            "mission_count",
            "mission_difficulty",
            "enabled",
        ),
    )
    try:
        days = _repeat_days(row["repeat_days"])
        mode, count, difficulty = _mission(
            row["mission_mode"], row["mission_count"], row["mission_difficulty"]
        )
    except ValidationError as error:
        raise ValidationError(section, f"Backup section '{section}' holds an invalid alarm.") from error
    if row["snooze_interval_minutes"] not in ALARM_SNOOZE_INTERVALS or isinstance(
        row["snooze_interval_minutes"], bool
    ):
        raise ValidationError(section, "Backup holds an invalid snooze interval.")
    return {
        "id": _backup_id(row["id"], section),
        "name": clean_text(row["name"], section),
        "hour_24": _int_in(row["hour_24"], section, 0, 23),
        "minute": _int_in(row["minute"], section, 0, 59),
        "repeat_days": list(days),
        "sound_id": _backup_id(row["sound_id"], section),
        "snooze_interval_minutes": row["snooze_interval_minutes"],
        "snooze_limit": _backup_snooze_limit(row["snooze_limit"], section),
        "mission_mode": mode,
        "mission_count": count,
        "mission_difficulty": difficulty,
        "enabled": _flag(row["enabled"], section),
        "created_at": _timestamp(row.get("created_at", "1970-01-01 00:00:00"), section),
    }


def _backup_timer(raw: object) -> dict:
    section = "timers"
    row = _row(raw, section, ("id", "name", "duration_seconds", "sound_id"))
    return {
        "id": _backup_id(row["id"], section),
        "name": clean_text(row["name"], section),
        "duration_seconds": _int_in(row["duration_seconds"], section, 1, MAX_TIMER_SECONDS),
        "sound_id": _backup_id(row["sound_id"], section),
        "created_at": _timestamp(row.get("created_at", "1970-01-01 00:00:00"), section),
    }


def _backup_log(raw: object) -> dict:
    section = "focus_logs"
    row = _row(
        raw,
        section,
        (
            "id",
            "source_type",
            "title",
            "started_at",
            "ended_at",
            "duration_seconds",
            "subtask_completed",
            "local_date_key",
        ),
    )
    date_key = row["local_date_key"]
    if not isinstance(date_key, str) or not _DATE_RE.match(date_key):
        raise ValidationError(section, "Backup holds an invalid report date.")
    return {
        "id": _backup_id(row["id"], section),
        "source_type": _one_of(row["source_type"], section, ("target_batch", "timer")),
        "target_id": _optional_id(row.get("target_id"), section),
        "batch_id": _optional_id(row.get("batch_id"), section),
        "title": clean_text(row["title"], section),
        "tag_label": clean_text(row.get("tag_label", "Focus"), section, 24),
        "started_at": _timestamp(row["started_at"], section),
        "ended_at": _timestamp(row["ended_at"], section),
        "duration_seconds": _int_in(row["duration_seconds"], section, 0, 10**7),
        "subtask_completed": _flag(row["subtask_completed"], section),
        "local_date_key": date_key,
    }


_SECTION_PARSERS = {
    "sounds": _backup_sound,
    "targets": _backup_target,
    "alarms": _backup_alarm,
    "timers": _backup_timer,
    "focus_logs": _backup_log,
}


def validate_backup(payload: object) -> dict:
    """Return a normalized copy of a backup, or raise before anything touches the database."""
    if not isinstance(payload, dict):
        raise ValidationError("format", "This file is not a Productime backup.")
    if payload.get("format") != BACKUP_FORMAT:
        raise ValidationError("format", "This file is not a Productime backup.")
    version = payload.get("version")
    if isinstance(version, bool) or version != BACKUP_VERSION:
        raise ValidationError("version", "This backup was made by an unsupported version.")

    cleaned: dict = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION}
    for section, parse in _SECTION_PARSERS.items():
        rows = payload.get(section, [])
        if not isinstance(rows, list) or len(rows) > MAX_ROWS[section]:
            raise ValidationError(section, f"Backup section '{section}' is malformed.")
        parsed = [parse(item) for item in rows]
        if len({row["id"] for row in parsed}) != len(parsed):
            raise ValidationError(section, f"Backup section '{section}' holds duplicate ids.")
        cleaned[section] = parsed

    preferences = payload.get("preferences", {})
    if not isinstance(preferences, dict):
        raise ValidationError("preferences", "Backup preferences are malformed.")
    kept = {}
    for key, value in preferences.items():
        rule = PREFERENCE_RULES.get(key)
        if rule is None:
            continue
        if not isinstance(value, str) or not rule(value):
            raise ValidationError("preferences", f"Backup holds an invalid '{key}' preference.")
        kept[key] = value
    cleaned["preferences"] = kept
    return cleaned
