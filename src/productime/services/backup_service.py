"""JSON backup and restore, plus CSV and JSON report export."""

import csv
import io
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from ..db.repositories import Repository
from ..domain import validation
from ..domain.formatters import format_duration
from ..domain.models import ReportSummary
from ..domain.validation import BACKUP_FORMAT, BACKUP_VERSION, ValidationError

MAX_BACKUP_BYTES = 64 * 1024 * 1024
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_HEADER = (
    "Date",
    "Started",
    "Ended",
    "Source",
    "Target",
    "Subtask",
    "Tag",
    "Duration (seconds)",
    "Duration",
    "Completed",
)


class BackupError(Exception):
    """A backup or export could not be read or written. The message is safe to show."""


def _atomic_write(path: Path, text: str) -> None:
    """Write through a private temp file so a failed export never leaves a half-written file."""
    path = Path(path)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as error:
        temp.unlink(missing_ok=True)
        raise BackupError("The file could not be written. Check the folder and free space.") from error


def build_backup(repo: Repository, exported_at: datetime | None = None) -> dict:
    stamp = (exported_at or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    return {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": stamp, **repo.export_rows()}


def write_backup(repo: Repository, path: Path, exported_at: datetime | None = None) -> None:
    payload = build_backup(repo, exported_at)
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_backup(path: Path) -> dict:
    """Load and fully validate a backup. Nothing is written while this runs."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BACKUP_BYTES:
            raise BackupError("That file is too large to be a Productime backup.")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BackupError("That file could not be read as a backup.") from error
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as error:
        raise BackupError("That file is not valid JSON.") from error
    try:
        return validation.validate_backup(payload)
    except ValidationError as error:
        raise BackupError(error.message) from error


def restore_backup(repo: Repository, library, data: dict) -> None:
    """Replace stored data with a validated backup. Custom sounds whose files are
    missing on this machine are dropped and their users fall back to the default sound."""
    repo.replace_all(data, library.file_exists)


def csv_cell(value: object) -> str:
    """Prefix cells a spreadsheet would run as a formula, so exported text stays text."""
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def report_to_csv(report: ReportSummary) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for log in report.logs:
        row = (
            log.local_date_key,
            log.started_at[11:],
            log.ended_at[11:],
            "Target" if log.source_type == "target_batch" else "Timer",
            log.target_name,
            log.title,
            log.tag_label,
            log.duration_seconds,
            format_duration(log.duration_seconds),
            "yes" if log.subtask_completed else "no",
        )
        writer.writerow([csv_cell(cell) for cell in row])
    return buffer.getvalue()


def report_to_dict(report: ReportSummary) -> dict:
    return {
        "format": "productime-report",
        "period": report.period,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "task_completed": report.task_completed,
        "total_seconds": report.total_seconds,
        "buckets": [
            {"label": label, "seconds": seconds}
            for label, seconds in zip(report.bucket_labels, report.bucket_seconds, strict=True)
        ],
        "logs": [
            {
                "date": log.local_date_key,
                "started_at": log.started_at,
                "ended_at": log.ended_at,
                "source_type": log.source_type,
                "target_name": log.target_name,
                "title": log.title,
                "tag_label": log.tag_label,
                "duration_seconds": log.duration_seconds,
                "subtask_completed": log.subtask_completed,
            }
            for log in report.logs
        ],
    }


def write_report_csv(report: ReportSummary, path: Path) -> None:
    _atomic_write(path, report_to_csv(report))


def write_report_json(report: ReportSummary, path: Path) -> None:
    _atomic_write(path, json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n")
