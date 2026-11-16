"""12h/24h conversion and next-ring calculation. Weekdays are ISO: 1 = Monday .. 7 = Sunday."""

from collections.abc import Iterable
from datetime import datetime, timedelta

WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = (1, 2, 3, 4, 5)
WEEKEND = (6, 7)
EVERY_DAY = (1, 2, 3, 4, 5, 6, 7)


def to_24h(hour_12: int, period: str) -> int:
    if not 1 <= hour_12 <= 12:
        raise ValueError("Hour must be between 1 and 12.")
    if period not in ("AM", "PM"):
        raise ValueError("Period must be AM or PM.")
    base = hour_12 % 12
    return base + 12 if period == "PM" else base


def to_12h(hour_24: int) -> tuple[int, str]:
    if not 0 <= hour_24 <= 23:
        raise ValueError("Hour must be between 0 and 23.")
    return (hour_24 % 12 or 12, "PM" if hour_24 >= 12 else "AM")


def next_ring(hour_24: int, minute: int, repeat_days: Iterable[int], now: datetime) -> datetime:
    """First occurrence strictly after `now`. An empty repeat list means any day."""
    days = set(repeat_days)
    candidate = now.replace(hour=hour_24, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        if candidate > now and (not days or candidate.isoweekday() in days):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError("Repeat days contain no valid weekday.")


def due_between(
    hour_24: int, minute: int, repeat_days: Iterable[int], start: datetime, end: datetime
) -> datetime | None:
    """Occurrence inside (start, end], which also catches alarms missed during a suspend."""
    if end <= start:
        return None
    occurrence = next_ring(hour_24, minute, repeat_days, start)
    return occurrence if occurrence <= end else None


def minute_key(alarm_id: str, when: datetime) -> str:
    return f"{alarm_id}@{when.strftime('%Y-%m-%dT%H:%M')}"


def format_relative(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "in under 1m"
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {minutes}m"
    return f"in {minutes}m"


def describe_repeat(repeat_days: Iterable[int]) -> str:
    days = tuple(sorted(set(repeat_days)))
    if not days:
        return "Once"
    if days == EVERY_DAY:
        return "Every day"
    if days == WEEKDAYS:
        return "Weekdays"
    if days == WEEKEND:
        return "Weekends"
    return ", ".join(WEEKDAY_SHORT[d - 1] for d in days)
