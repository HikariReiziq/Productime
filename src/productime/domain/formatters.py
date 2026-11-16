"""Display formatting shared by widgets, exports, and tests."""

import math

from .alarm_schedule import to_12h


def format_hm(total_minutes: int) -> str:
    hours, minutes = divmod(max(0, int(total_minutes)), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def format_clock_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(max(0, int(total_minutes)), 60)
    return f"{hours:02d}:{minutes:02d}"


def format_range(start_minute: int, end_minute: int) -> str:
    return f"{format_clock_minutes(start_minute)} -> {format_clock_minutes(end_minute)}"


def format_hms(total_seconds: float) -> str:
    seconds = max(0, math.ceil(total_seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_countdown(total_seconds: float) -> str:
    """Ring countdown: MM:SS, widening to HH:MM:SS only when an hour or more remains."""
    seconds = max(0, math.ceil(total_seconds))
    if seconds >= 3600:
        return format_hms(seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def format_duration(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    if 0 < seconds < 60:
        return f"{seconds}s"
    return format_hm(seconds // 60)


def format_stopwatch(elapsed_ms: int) -> str:
    centis = max(0, int(elapsed_ms)) // 10
    seconds, cc = divmod(centis, 100)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{cc:02d}"


def format_delta(delta_ms: int) -> str:
    sign = "-" if delta_ms < 0 else "+"
    return f"{sign}{abs(delta_ms) / 1000:.2f}s"


def format_alarm_time(hour_24: int, minute: int) -> str:
    hour_12, period = to_12h(hour_24)
    return f"{hour_12:02d}:{minute:02d} {period}"


def format_minutes(minutes: int) -> str:
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def format_snooze_limit(limit: int | None) -> str:
    """How many snoozes are allowed, where None means no limit."""
    if limit is None:
        return "Unlimited"
    return "1 time" if limit == 1 else f"{limit} times"
