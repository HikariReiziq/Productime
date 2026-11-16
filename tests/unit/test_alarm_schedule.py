import unittest
from datetime import datetime, timedelta

import _bootstrap  # noqa: F401

from productime.domain import alarm_schedule as schedule
from productime.domain.formatters import format_alarm_time, format_minutes, format_snooze_limit

# 2026-11-16 is a Monday (ISO weekday 1).
MONDAY_0600 = datetime(2026, 11, 16, 6, 0, 0)


class TwelveHourConversionTests(unittest.TestCase):
    def test_am_pm_converts_to_24_hour(self):
        cases = {
            (12, "AM"): 0,
            (1, "AM"): 1,
            (11, "AM"): 11,
            (12, "PM"): 12,
            (1, "PM"): 13,
            (11, "PM"): 23,
        }
        for (hour, period), expected in cases.items():
            with self.subTest(hour=hour, period=period):
                self.assertEqual(schedule.to_24h(hour, period), expected)

    def test_24_hour_converts_back_to_am_pm(self):
        for hour_24 in range(24):
            hour_12, period = schedule.to_12h(hour_24)

            self.assertEqual(schedule.to_24h(hour_12, period), hour_24)
            self.assertIn(hour_12, range(1, 13))

    def test_out_of_range_input_is_rejected(self):
        for hour, period in ((0, "AM"), (13, "PM"), (6, "XM")):
            with self.assertRaises(ValueError):
                schedule.to_24h(hour, period)

    def test_alarm_time_is_shown_in_12_hour_form(self):
        self.assertEqual(format_alarm_time(6, 30), "06:30 AM")
        self.assertEqual(format_alarm_time(0, 5), "12:05 AM")
        self.assertEqual(format_alarm_time(18, 0), "06:00 PM")


class SnoozeTextTests(unittest.TestCase):
    def test_snooze_limits_read_as_counts(self):
        self.assertEqual(
            [format_snooze_limit(limit) for limit in (None, 1, 2, 10)],
            ["Unlimited", "1 time", "2 times", "10 times"],
        )

    def test_minutes_use_the_singular_for_one(self):
        self.assertEqual([format_minutes(m) for m in (1, 5, 30)], ["1 minute", "5 minutes", "30 minutes"])


class NextRingTests(unittest.TestCase):
    def test_one_shot_alarm_rings_later_today(self):
        self.assertEqual(schedule.next_ring(6, 30, [], MONDAY_0600), datetime(2026, 11, 16, 6, 30))

    def test_one_shot_alarm_already_passed_rings_tomorrow(self):
        self.assertEqual(schedule.next_ring(5, 0, [], MONDAY_0600), datetime(2026, 11, 17, 5, 0))

    def test_the_current_minute_is_not_rung_again(self):
        now = datetime(2026, 11, 16, 6, 30, 20)

        self.assertEqual(schedule.next_ring(6, 30, [], now), datetime(2026, 11, 17, 6, 30))

    def test_repeat_days_pick_the_next_selected_weekday(self):
        self.assertEqual(schedule.next_ring(6, 30, [3, 5], MONDAY_0600), datetime(2026, 11, 18, 6, 30))

    def test_weekend_alarm_skips_weekdays(self):
        self.assertEqual(schedule.next_ring(9, 0, [6, 7], MONDAY_0600), datetime(2026, 11, 21, 9, 0))

    def test_same_weekday_wraps_to_next_week_once_passed(self):
        now = datetime(2026, 11, 16, 7, 0)

        self.assertEqual(schedule.next_ring(6, 30, [1], now), datetime(2026, 11, 23, 6, 30))

    def test_every_day_alarm_rings_within_24_hours(self):
        ring = schedule.next_ring(6, 0, [1, 2, 3, 4, 5, 6, 7], MONDAY_0600)

        self.assertEqual(ring - MONDAY_0600, timedelta(days=1))


class DueWindowTests(unittest.TestCase):
    def test_alarm_is_due_when_its_minute_falls_inside_the_window(self):
        start = datetime(2026, 11, 16, 6, 29, 50)
        end = datetime(2026, 11, 16, 6, 30, 5)

        self.assertEqual(schedule.due_between(6, 30, [], start, end), datetime(2026, 11, 16, 6, 30))

    def test_alarm_is_not_due_before_its_minute(self):
        start = datetime(2026, 11, 16, 6, 29, 0)
        end = datetime(2026, 11, 16, 6, 29, 59)

        self.assertIsNone(schedule.due_between(6, 30, [], start, end))

    def test_alarm_missed_during_sleep_is_found_in_the_wake_window(self):
        slept_at = datetime(2026, 11, 16, 23, 0)
        woke_at = datetime(2026, 11, 17, 8, 15)

        self.assertEqual(schedule.due_between(6, 30, [2], slept_at, woke_at), datetime(2026, 11, 17, 6, 30))

    def test_repeat_days_exclude_other_weekdays_from_the_window(self):
        start = datetime(2026, 11, 16, 6, 29, 50)
        end = datetime(2026, 11, 16, 6, 30, 5)

        self.assertIsNone(schedule.due_between(6, 30, [2, 3], start, end))

    def test_minute_key_is_identical_for_checks_inside_one_minute(self):
        first = schedule.minute_key("alarm-1", datetime(2026, 11, 16, 6, 30, 0))
        second = schedule.minute_key("alarm-1", datetime(2026, 11, 16, 6, 30, 59))
        next_minute = schedule.minute_key("alarm-1", datetime(2026, 11, 16, 6, 31, 0))

        self.assertEqual(first, second)
        self.assertNotEqual(first, next_minute)
        self.assertNotEqual(first, schedule.minute_key("alarm-2", datetime(2026, 11, 16, 6, 30)))


class RelativeLabelTests(unittest.TestCase):
    def test_relative_label_describes_time_until_ring(self):
        self.assertEqual(schedule.format_relative(timedelta(minutes=30)), "in 30m")
        self.assertEqual(schedule.format_relative(timedelta(hours=7, minutes=5)), "in 7h 5m")
        self.assertEqual(schedule.format_relative(timedelta(days=2, hours=3)), "in 2d 3h")
        self.assertEqual(schedule.format_relative(timedelta(seconds=20)), "in under 1m")


if __name__ == "__main__":
    unittest.main()
