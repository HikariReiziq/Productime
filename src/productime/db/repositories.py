"""Parameterized CRUD and report aggregation over the Productime database."""

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta

from ..domain import target_engine, validation
from ..domain.alarm_schedule import WEEKDAY_SHORT
from ..domain.models import (
    DEFAULT_SOUND_ID,
    TAG_COLORS,
    Alarm,
    AlarmDraft,
    FocusLog,
    ReportSummary,
    Sound,
    Target,
    TargetBatch,
    TargetConfig,
    TargetDraft,
    TimerDraft,
    TimerPreset,
)
from ..domain.validation import ValidationError

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def new_id() -> str:
    return uuid.uuid4().hex


def _now_text() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_sounds(self) -> list[Sound]:
        rows = self.conn.execute("SELECT * FROM sounds ORDER BY source_type = 'custom', rowid").fetchall()
        return [self._sound(row) for row in rows]

    def get_sound(self, sound_id: str) -> Sound | None:
        row = self.conn.execute("SELECT * FROM sounds WHERE id = ?", (sound_id,)).fetchone()
        return self._sound(row) if row else None

    def add_sound(self, sound: Sound) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sounds (id, display_name, source_type, file_path, duration_seconds)"
                " VALUES (?, ?, ?, ?, ?)",
                (sound.id, sound.display_name, sound.source_type, sound.file_path, sound.duration_seconds),
            )

    def delete_sound(self, sound_id: str) -> None:
        """Remove a custom sound, first moving everything that used it to the default sound."""
        with self.conn:
            row = self.conn.execute(
                "SELECT 1 FROM sounds WHERE id = ? AND source_type = 'custom'", (sound_id,)
            ).fetchone()
            if not row:
                return
            for statement in (
                "UPDATE targets SET sound_id = ? WHERE sound_id = ?",
                "UPDATE alarms SET sound_id = ? WHERE sound_id = ?",
                "UPDATE timers SET sound_id = ? WHERE sound_id = ?",
            ):
                self.conn.execute(statement, (DEFAULT_SOUND_ID, sound_id))
            self.conn.execute("DELETE FROM sounds WHERE id = ?", (sound_id,))

    def _require_sound(self, sound_id: str) -> None:
        if self.get_sound(sound_id) is None:
            raise ValidationError("sound_id", "That sound is no longer available. Choose another.")

    @staticmethod
    def _sound(row: sqlite3.Row) -> Sound:
        return Sound(
            row["id"],
            row["display_name"],
            row["source_type"],
            row["file_path"],
            float(row["duration_seconds"] or 0),
        )

    def list_targets(self) -> list[Target]:
        rows = self.conn.execute("SELECT * FROM targets ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._target(row) for row in rows]

    def get_target(self, target_id: str) -> Target | None:
        row = self.conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        return self._target(row) if row else None

    def create_target(self, draft: TargetDraft) -> Target:
        draft = validation.validate_target_draft(draft)
        self._require_sound(draft.sound_id)
        target_id = new_id()
        config = draft.config
        with self.conn:
            self.conn.execute(
                "INSERT INTO targets (id, name, tag_label, tag_color, target_total_minutes,"
                " work_minutes, short_break_minutes, long_rest_enabled, long_rest_every,"
                " long_rest_minutes, snooze_limit, sound_id, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
                (
                    target_id,
                    draft.name,
                    draft.tag_label,
                    TAG_COLORS[draft.tag_label],
                    config.target_total_minutes,
                    config.work_minutes,
                    config.short_break_minutes,
                    int(config.long_rest_enabled),
                    config.long_rest_every,
                    config.long_rest_minutes,
                    draft.snooze_limit,
                    draft.sound_id,
                    _now_text(),
                    _now_text(),
                ),
            )
            self._insert_batches(target_id, draft)
        return self.get_target(target_id)

    def update_target(self, target_id: str, draft: TargetDraft) -> Target:
        draft = validation.validate_target_draft(draft)
        self._require_sound(draft.sound_id)
        config = draft.config
        with self.conn:
            self.conn.execute(
                "UPDATE targets SET name = ?, tag_label = ?, tag_color = ?, target_total_minutes = ?,"
                " work_minutes = ?, short_break_minutes = ?, long_rest_enabled = ?,"
                " long_rest_every = ?, long_rest_minutes = ?, snooze_limit = ?, sound_id = ?,"
                " status = 'planned', updated_at = ? WHERE id = ?",
                (
                    draft.name,
                    draft.tag_label,
                    TAG_COLORS[draft.tag_label],
                    config.target_total_minutes,
                    config.work_minutes,
                    config.short_break_minutes,
                    int(config.long_rest_enabled),
                    config.long_rest_every,
                    config.long_rest_minutes,
                    draft.snooze_limit,
                    draft.sound_id,
                    _now_text(),
                    target_id,
                ),
            )
            self.conn.execute("DELETE FROM target_batches WHERE target_id = ?", (target_id,))
            self._insert_batches(target_id, draft)
        return self.get_target(target_id)

    def _insert_batches(self, target_id: str, draft: TargetDraft) -> None:
        slots = target_engine.build_timeline(draft.config, draft.subtask_titles)
        self.conn.executemany(
            "INSERT INTO target_batches (id, target_id, batch_index, subtask_title,"
            " planned_work_minutes, planned_break_minutes, break_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    new_id(),
                    target_id,
                    slot.batch_index,
                    slot.subtask_title,
                    slot.work_minutes,
                    slot.break_minutes,
                    slot.break_type,
                )
                for slot in slots
            ],
        )

    def delete_target(self, target_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))

    def set_target_status(self, target_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE targets SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_text(), target_id),
            )

    def save_batch_progress(self, batch: TargetBatch) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE target_batches SET status = ?, actual_work_seconds = ?,"
                " snooze_seconds_used = ?, snooze_count = ?, completed_at = ? WHERE id = ?",
                (
                    batch.status,
                    batch.actual_work_seconds,
                    batch.snooze_seconds_used,
                    batch.snooze_count,
                    batch.completed_at,
                    batch.id,
                ),
            )

    def reset_target_progress(self, target_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE target_batches SET status = 'pending', actual_work_seconds = 0,"
                " snooze_seconds_used = 0, snooze_count = 0, completed_at = NULL WHERE target_id = ?",
                (target_id,),
            )
            self.conn.execute(
                "UPDATE targets SET status = 'planned', updated_at = ? WHERE id = ?",
                (_now_text(), target_id),
            )

    def _target(self, row: sqlite3.Row) -> Target:
        batches = self.conn.execute(
            "SELECT * FROM target_batches WHERE target_id = ? ORDER BY batch_index", (row["id"],)
        ).fetchall()
        return Target(
            id=row["id"],
            name=row["name"],
            tag_label=row["tag_label"],
            tag_color=row["tag_color"],
            config=TargetConfig(
                row["target_total_minutes"],
                row["work_minutes"],
                row["short_break_minutes"],
                bool(row["long_rest_enabled"]),
                row["long_rest_every"],
                row["long_rest_minutes"],
            ),
            snooze_limit=row["snooze_limit"],
            sound_id=row["sound_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            batches=[
                TargetBatch(
                    id=b["id"],
                    target_id=b["target_id"],
                    batch_index=b["batch_index"],
                    subtask_title=b["subtask_title"],
                    planned_work_minutes=b["planned_work_minutes"],
                    planned_break_minutes=b["planned_break_minutes"],
                    break_type=b["break_type"],
                    status=b["status"],
                    actual_work_seconds=b["actual_work_seconds"],
                    snooze_seconds_used=b["snooze_seconds_used"],
                    snooze_count=b["snooze_count"],
                    completed_at=b["completed_at"],
                )
                for b in batches
            ],
        )

    def list_alarms(self) -> list[Alarm]:
        rows = self.conn.execute("SELECT * FROM alarms ORDER BY hour_24, minute, rowid").fetchall()
        return [self._alarm(row) for row in rows]

    def get_alarm(self, alarm_id: str) -> Alarm | None:
        row = self.conn.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        return self._alarm(row) if row else None

    def create_alarm(self, draft: AlarmDraft) -> Alarm:
        draft = validation.validate_alarm_draft(draft)
        self._require_sound(draft.sound_id)
        alarm_id = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO alarms (id, name, hour_24, minute, repeat_days_json, sound_id,"
                " snooze_interval_minutes, snooze_limit, mission_mode, mission_count,"
                " mission_difficulty, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (alarm_id, *self._alarm_values(draft), _now_text()),
            )
        return self.get_alarm(alarm_id)

    def update_alarm(self, alarm_id: str, draft: AlarmDraft) -> Alarm:
        draft = validation.validate_alarm_draft(draft)
        self._require_sound(draft.sound_id)
        with self.conn:
            self.conn.execute(
                "UPDATE alarms SET name = ?, hour_24 = ?, minute = ?, repeat_days_json = ?,"
                " sound_id = ?, snooze_interval_minutes = ?, snooze_limit = ?, mission_mode = ?,"
                " mission_count = ?, mission_difficulty = ?, enabled = ? WHERE id = ?",
                (*self._alarm_values(draft), alarm_id),
            )
        return self.get_alarm(alarm_id)

    @staticmethod
    def _alarm_values(draft: AlarmDraft) -> tuple:
        return (
            draft.name,
            draft.hour_24,
            draft.minute,
            json.dumps(list(draft.repeat_days)),
            draft.sound_id,
            draft.snooze_interval_minutes,
            draft.snooze_limit,
            draft.mission_mode,
            draft.mission_count,
            draft.mission_difficulty,
            int(draft.enabled),
        )

    def set_alarm_enabled(self, alarm_id: str, enabled: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE alarms SET enabled = ? WHERE id = ?", (int(enabled), alarm_id))

    def delete_alarm(self, alarm_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))

    @staticmethod
    def _alarm(row: sqlite3.Row) -> Alarm:
        try:
            days = tuple(int(d) for d in json.loads(row["repeat_days_json"]) if 1 <= int(d) <= 7)
        except ValueError, TypeError:
            days = ()
        return Alarm(
            id=row["id"],
            name=row["name"],
            hour_24=row["hour_24"],
            minute=row["minute"],
            repeat_days=days,
            sound_id=row["sound_id"],
            snooze_interval_minutes=row["snooze_interval_minutes"],
            snooze_limit=row["snooze_limit"],
            mission_mode=row["mission_mode"],
            mission_count=row["mission_count"],
            mission_difficulty=row["mission_difficulty"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def list_timers(self) -> list[TimerPreset]:
        rows = self.conn.execute("SELECT * FROM timers ORDER BY created_at, rowid").fetchall()
        return [self._timer(row) for row in rows]

    def get_timer(self, timer_id: str) -> TimerPreset | None:
        row = self.conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        return self._timer(row) if row else None

    def create_timer(self, draft: TimerDraft) -> TimerPreset:
        draft = validation.validate_timer_draft(draft)
        self._require_sound(draft.sound_id)
        timer_id = new_id()
        with self.conn:
            self.conn.execute(
                "INSERT INTO timers (id, name, duration_seconds, sound_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (timer_id, draft.name, draft.duration_seconds, draft.sound_id, _now_text()),
            )
        return self.get_timer(timer_id)

    def update_timer(self, timer_id: str, draft: TimerDraft) -> TimerPreset:
        draft = validation.validate_timer_draft(draft)
        self._require_sound(draft.sound_id)
        with self.conn:
            self.conn.execute(
                "UPDATE timers SET name = ?, duration_seconds = ?, sound_id = ? WHERE id = ?",
                (draft.name, draft.duration_seconds, draft.sound_id, timer_id),
            )
        return self.get_timer(timer_id)

    def delete_timer(self, timer_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM timers WHERE id = ?", (timer_id,))

    @staticmethod
    def _timer(row: sqlite3.Row) -> TimerPreset:
        return TimerPreset(
            row["id"], row["name"], row["duration_seconds"], row["sound_id"], row["created_at"]
        )

    def add_focus_log(
        self,
        *,
        source_type: str,
        title: str,
        tag_label: str,
        started_at: datetime,
        ended_at: datetime,
        duration_seconds: int,
        subtask_completed: bool,
        target_id: str | None = None,
        batch_id: str | None = None,
    ) -> FocusLog:
        log = FocusLog(
            id=new_id(),
            source_type=source_type,
            target_id=target_id,
            batch_id=batch_id,
            title=title,
            tag_label=tag_label,
            started_at=started_at.strftime(LOG_TIMESTAMP_FORMAT),
            ended_at=ended_at.strftime(LOG_TIMESTAMP_FORMAT),
            duration_seconds=max(0, int(duration_seconds)),
            subtask_completed=bool(subtask_completed),
            local_date_key=ended_at.strftime("%Y-%m-%d"),
        )
        with self.conn:
            self.conn.execute(
                "INSERT INTO focus_logs (id, source_type, target_id, batch_id, title, tag_label,"
                " started_at, ended_at, duration_seconds, subtask_completed, local_date_key)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    log.id,
                    log.source_type,
                    log.target_id,
                    log.batch_id,
                    log.title,
                    log.tag_label,
                    log.started_at,
                    log.ended_at,
                    log.duration_seconds,
                    int(log.subtask_completed),
                    log.local_date_key,
                ),
            )
        return log

    def report(self, period: str, anchor: date) -> ReportSummary:
        if period == "week":
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
            labels = WEEKDAY_SHORT
        elif period == "day":
            start = end = anchor
            labels = tuple(f"{hour:02d}" for hour in range(24))
        else:
            raise ValueError(f"Unknown report period: {period}")

        rows = self.conn.execute(
            "SELECT l.*, COALESCE(t.name, '') AS target_name FROM focus_logs l"
            " LEFT JOIN targets t ON t.id = l.target_id"
            " WHERE l.local_date_key BETWEEN ? AND ? ORDER BY l.ended_at DESC, l.rowid DESC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        logs = tuple(self._log(row) for row in rows)

        buckets = [0] * len(labels)
        for log in logs:
            if period == "day":
                index = int(log.ended_at[11:13])
            else:
                index = (date.fromisoformat(log.local_date_key) - start).days
            buckets[index] += log.duration_seconds
        return ReportSummary(
            period=period,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            task_completed=sum(1 for log in logs if log.subtask_completed),
            total_seconds=sum(log.duration_seconds for log in logs),
            bucket_labels=tuple(labels),
            bucket_seconds=tuple(buckets),
            logs=logs,
        )

    @staticmethod
    def _log(row: sqlite3.Row) -> FocusLog:
        keys = row.keys()
        return FocusLog(
            id=row["id"],
            source_type=row["source_type"],
            target_id=row["target_id"],
            batch_id=row["batch_id"],
            title=row["title"],
            tag_label=row["tag_label"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_seconds=row["duration_seconds"],
            subtask_completed=bool(row["subtask_completed"]),
            local_date_key=row["local_date_key"],
            target_name=row["target_name"] if "target_name" in keys else "",
        )

    def get_pref(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM app_preferences WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_pref(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO app_preferences (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def export_rows(self) -> dict:
        """Plain rows for a JSON backup. Bundled sounds are reseeded, so only custom ones go out."""

        def fetch(sql: str) -> list[dict]:
            return [dict(row) for row in self.conn.execute(sql).fetchall()]

        targets = fetch("SELECT * FROM targets ORDER BY created_at, rowid")
        for target in targets:
            batches = self.conn.execute(
                "SELECT id, batch_index, subtask_title, planned_work_minutes, planned_break_minutes,"
                " break_type, status, actual_work_seconds, snooze_seconds_used, snooze_count,"
                " completed_at FROM target_batches WHERE target_id = ? ORDER BY batch_index",
                (target["id"],),
            ).fetchall()
            target["batches"] = [dict(row) for row in batches]
        alarms = fetch("SELECT * FROM alarms ORDER BY rowid")
        for alarm in alarms:
            alarm["repeat_days"] = json.loads(alarm.pop("repeat_days_json") or "[]")
        return {
            "sounds": fetch(
                "SELECT id, display_name, source_type, file_path, duration_seconds FROM sounds"
                " WHERE source_type = 'custom' ORDER BY rowid"
            ),
            "targets": targets,
            "alarms": alarms,
            "timers": fetch("SELECT * FROM timers ORDER BY rowid"),
            "focus_logs": fetch("SELECT * FROM focus_logs ORDER BY ended_at, rowid"),
            "preferences": {
                row["key"]: row["value"]
                for row in self.conn.execute("SELECT key, value FROM app_preferences").fetchall()
            },
        }

    def replace_all(self, data: dict, sound_file_exists: Callable[[dict], bool]) -> None:
        """Swap in validated backup rows. One transaction: any failure leaves the old data."""
        with self.conn:
            for statement in (
                "DELETE FROM focus_logs",
                "DELETE FROM target_batches",
                "DELETE FROM targets",
                "DELETE FROM alarms",
                "DELETE FROM timers",
            ):
                self.conn.execute(statement)

            for sound in data["sounds"]:
                if sound_file_exists(sound):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO sounds (id, display_name, source_type, file_path,"
                        " duration_seconds) VALUES (?, ?, 'custom', ?, ?)",
                        (sound["id"], sound["display_name"], sound["file_path"], sound["duration_seconds"]),
                    )
            known = {row["id"] for row in self.conn.execute("SELECT id FROM sounds").fetchall()}

            def usable(sound_id: str) -> str:
                return sound_id if sound_id in known else DEFAULT_SOUND_ID

            for t in data["targets"]:
                self.conn.execute(
                    "INSERT INTO targets (id, name, tag_label, tag_color, target_total_minutes,"
                    " work_minutes, short_break_minutes, long_rest_enabled, long_rest_every,"
                    " long_rest_minutes, snooze_limit, sound_id, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        t["id"],
                        t["name"],
                        t["tag_label"],
                        t["tag_color"],
                        t["target_total_minutes"],
                        t["work_minutes"],
                        t["short_break_minutes"],
                        t["long_rest_enabled"],
                        t["long_rest_every"],
                        t["long_rest_minutes"],
                        t["snooze_limit"],
                        usable(t["sound_id"]),
                        t["status"],
                        t["created_at"],
                        t["updated_at"],
                    ),
                )
                self.conn.executemany(
                    "INSERT INTO target_batches (id, target_id, batch_index, subtask_title,"
                    " planned_work_minutes, planned_break_minutes, break_type, status,"
                    " actual_work_seconds, snooze_seconds_used, snooze_count, completed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            b["id"],
                            t["id"],
                            b["batch_index"],
                            b["subtask_title"],
                            b["planned_work_minutes"],
                            b["planned_break_minutes"],
                            b["break_type"],
                            b["status"],
                            b["actual_work_seconds"],
                            b["snooze_seconds_used"],
                            b["snooze_count"],
                            b["completed_at"],
                        )
                        for b in t["batches"]
                    ],
                )
            self.conn.executemany(
                "INSERT INTO alarms (id, name, hour_24, minute, repeat_days_json, sound_id,"
                " snooze_interval_minutes, snooze_limit, mission_mode, mission_count,"
                " mission_difficulty, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        a["id"],
                        a["name"],
                        a["hour_24"],
                        a["minute"],
                        json.dumps(a["repeat_days"]),
                        usable(a["sound_id"]),
                        a["snooze_interval_minutes"],
                        a["snooze_limit"],
                        a["mission_mode"],
                        a["mission_count"],
                        a["mission_difficulty"],
                        a["enabled"],
                        a["created_at"],
                    )
                    for a in data["alarms"]
                ],
            )
            self.conn.executemany(
                "INSERT INTO timers (id, name, duration_seconds, sound_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (t["id"], t["name"], t["duration_seconds"], usable(t["sound_id"]), t["created_at"])
                    for t in data["timers"]
                ],
            )
            self.conn.executemany(
                "INSERT INTO focus_logs (id, source_type, target_id, batch_id, title, tag_label,"
                " started_at, ended_at, duration_seconds, subtask_completed, local_date_key)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        log["id"],
                        log["source_type"],
                        log["target_id"],
                        log["batch_id"],
                        log["title"],
                        log["tag_label"],
                        log["started_at"],
                        log["ended_at"],
                        log["duration_seconds"],
                        log["subtask_completed"],
                        log["local_date_key"],
                    )
                    for log in data["focus_logs"]
                ],
            )
            for key, value in data["preferences"].items():
                self.conn.execute(
                    "INSERT INTO app_preferences (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
