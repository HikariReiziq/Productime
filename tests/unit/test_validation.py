import copy
import unittest

import _bootstrap  # noqa: F401

from productime.domain import validation as v
from productime.domain.models import AlarmDraft, TargetConfig, TargetDraft, TimerDraft


def target_draft(**overrides):
    values = {
        "name": "Website Profile Company",
        "tag_label": "Coding",
        "config": TargetConfig(360, 50, 10, True, 3, 30),
        "snooze_limit": 3,
        "sound_id": "bundled-digital",
        "subtask_titles": ("Membuat PRD", "Membuat desain"),
    }
    values.update(overrides)
    return TargetDraft(**values)


def alarm_draft(**overrides):
    values = {
        "name": "Morning class",
        "hour_24": 6,
        "minute": 30,
        "repeat_days": (1, 2, 3, 4, 5),
        "sound_id": "bundled-digital",
        "snooze_interval_minutes": 5,
        "snooze_limit": None,
        "mission_mode": "typing",
        "mission_count": 2,
        "mission_difficulty": "easy",
        "enabled": True,
    }
    values.update(overrides)
    return AlarmDraft(**values)


def backup_payload():
    return {
        "format": "productime-backup",
        "version": 1,
        "exported_at": "2026-11-16T08:00:00",
        "sounds": [
            {
                "id": "custom-0123456789abcdef0123456789abcdef",
                "display_name": "My song",
                "source_type": "custom",
                "file_path": "0123456789abcdef0123456789abcdef.mp3",
                "duration_seconds": 12.5,
            }
        ],
        "targets": [
            {
                "id": "t1",
                "name": "Belajar Pemrograman Mobile",
                "tag_label": "Study",
                "tag_color": "purple",
                "target_total_minutes": 240,
                "work_minutes": 25,
                "short_break_minutes": 5,
                "long_rest_enabled": 1,
                "long_rest_every": 4,
                "long_rest_minutes": 30,
                "snooze_limit": None,
                "sound_id": "bundled-digital",
                "status": "planned",
                "created_at": "2026-11-16 08:00:00",
                "updated_at": "2026-11-16 08:00:00",
                "batches": [
                    {
                        "id": "b1",
                        "batch_index": 1,
                        "subtask_title": "Belajar fundamental",
                        "planned_work_minutes": 25,
                        "planned_break_minutes": 5,
                        "break_type": "short",
                        "status": "pending",
                        "actual_work_seconds": 0,
                        "snooze_seconds_used": 0,
                        "snooze_count": 0,
                        "completed_at": None,
                    }
                ],
            }
        ],
        "alarms": [
            {
                "id": "a1",
                "name": "Morning class",
                "hour_24": 6,
                "minute": 30,
                "repeat_days": [1, 2, 3],
                "sound_id": "bundled-digital",
                "snooze_interval_minutes": 5,
                "snooze_limit": 3,
                "mission_mode": "math",
                "mission_count": 2,
                "mission_difficulty": "hard",
                "enabled": 1,
                "created_at": "2026-11-16 08:00:00",
            }
        ],
        "timers": [
            {
                "id": "tm1",
                "name": "Tea",
                "duration_seconds": 180,
                "sound_id": "bundled-funny",
                "created_at": "2026-11-16 08:00:00",
            }
        ],
        "focus_logs": [
            {
                "id": "l1",
                "source_type": "target_batch",
                "target_id": "t1",
                "batch_id": "b1",
                "title": "Belajar fundamental",
                "tag_label": "Study",
                "started_at": "2026-11-16T08:00:00",
                "ended_at": "2026-11-16T08:25:00",
                "duration_seconds": 1500,
                "subtask_completed": 1,
                "local_date_key": "2026-11-16",
            }
        ],
        "preferences": {"theme": "dark", "volume": "80"},
    }


class TargetValidationTests(unittest.TestCase):
    def test_valid_draft_is_trimmed_and_subtasks_fill_every_batch(self):
        cleaned = v.validate_target_draft(target_draft(name="  Website Profile Company  "))

        self.assertEqual(cleaned.name, "Website Profile Company")
        self.assertEqual(len(cleaned.subtask_titles), 6)
        self.assertEqual(cleaned.subtask_titles[:2], ("Membuat PRD", "Membuat desain"))
        self.assertEqual(cleaned.subtask_titles[5], "Batch 6")

    def test_blank_name_is_rejected_with_the_field_named(self):
        with self.assertRaises(v.ValidationError) as caught:
            v.validate_target_draft(target_draft(name="   "))

        self.assertEqual(caught.exception.field, "name")

    def test_control_characters_are_stripped_from_names(self):
        cleaned = v.validate_target_draft(target_draft(name="Deep\x00 work\x1b[31m"))

        self.assertEqual(cleaned.name, "Deep work[31m")

    def test_out_of_range_numbers_are_rejected(self):
        bad_configs = [
            TargetConfig(0, 25, 5, False, 3, 30),
            TargetConfig(240, 0, 5, False, 3, 30),
            TargetConfig(240, 25, -1, False, 3, 30),
            TargetConfig(240, 25, 5, True, 0, 30),
            TargetConfig(240, 25, 5, True, 3, 0),
            TargetConfig(100_000, 25, 5, False, 3, 30),
        ]
        for config in bad_configs:
            with self.subTest(config=config), self.assertRaises(v.ValidationError):
                v.validate_target_draft(target_draft(config=config))

    def test_snooze_limit_must_be_a_configured_choice(self):
        for limit in (None, 1, 2, 3, 5, 10):
            self.assertEqual(v.validate_target_draft(target_draft(snooze_limit=limit)).snooze_limit, limit)
        for limit in (0, 4, 11, -1):
            with self.subTest(limit=limit), self.assertRaises(v.ValidationError):
                v.validate_target_draft(target_draft(snooze_limit=limit))

    def test_a_custom_category_name_is_accepted_and_trimmed(self):
        cleaned = v.validate_target_draft(target_draft(tag_label="  Skripsi\x07 Bab 2 "))

        self.assertEqual(cleaned.tag_label, "Skripsi Bab 2")

    def test_custom_category_must_have_a_short_non_blank_name(self):
        for bad in ("", "   ", "x" * (v.MAX_TAG_LENGTH + 1)):
            with self.subTest(tag=bad), self.assertRaises(v.ValidationError) as caught:
                v.validate_target_draft(target_draft(tag_label=bad))
            self.assertEqual(caught.exception.field, "tag_label")

    def test_category_colour_is_fixed_for_presets_and_neutral_for_custom_names(self):
        self.assertEqual(v.tag_color("Coding"), "blue")
        self.assertEqual(v.tag_color("Work"), "red")
        self.assertEqual(v.tag_color("Skripsi"), "gray")

    def test_snooze_minutes_are_bounded(self):
        self.assertEqual(v.validate_snooze_minutes(7), 7)
        for minutes in (0, -5, 121, 2.5, "5"):
            with self.subTest(minutes=minutes), self.assertRaises(v.ValidationError):
                v.validate_snooze_minutes(minutes)


class AlarmAndTimerValidationTests(unittest.TestCase):
    def test_valid_alarm_sorts_and_dedupes_repeat_days(self):
        cleaned = v.validate_alarm_draft(alarm_draft(repeat_days=(5, 1, 1, 3)))

        self.assertEqual(cleaned.repeat_days, (1, 3, 5))

    def test_alarm_rejects_invalid_fields(self):
        bad = [
            {"hour_24": 24},
            {"minute": 60},
            {"repeat_days": (0,)},
            {"repeat_days": (8,)},
            {"snooze_interval_minutes": 7},
            {"snooze_limit": 4},
            {"mission_mode": "puzzle"},
            {"mission_count": 0},
            {"mission_count": 4},
            {"mission_difficulty": "extreme"},
            {"name": ""},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(v.ValidationError):
                v.validate_alarm_draft(alarm_draft(**overrides))

    def test_timer_needs_a_name_and_a_positive_duration(self):
        cleaned = v.validate_timer_draft(TimerDraft(" Tea ", 180, "bundled-funny"))
        self.assertEqual((cleaned.name, cleaned.duration_seconds), ("Tea", 180))

        for draft in (
            TimerDraft("Tea", 0, "bundled-funny"),
            TimerDraft("", 60, "bundled-funny"),
            TimerDraft("Tea", 100 * 3600, "bundled-funny"),
            TimerDraft("Tea", 60, "../../etc/passwd"),
        ):
            with self.subTest(draft=draft), self.assertRaises(v.ValidationError):
                v.validate_timer_draft(draft)


class BackupValidationTests(unittest.TestCase):
    def test_well_formed_backup_is_accepted(self):
        cleaned = v.validate_backup(backup_payload())

        self.assertEqual(len(cleaned["targets"]), 1)
        self.assertEqual(cleaned["targets"][0]["batches"][0]["subtask_title"], "Belajar fundamental")
        self.assertEqual(cleaned["alarms"][0]["repeat_days"], [1, 2, 3])
        self.assertEqual(cleaned["preferences"], {"theme": "dark", "volume": "80"})

    def test_wrong_format_or_version_is_rejected(self):
        for key, value in (("format", "other-app"), ("version", 99), ("version", "1")):
            payload = backup_payload()
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(v.ValidationError):
                v.validate_backup(payload)

    def test_non_object_payloads_are_rejected(self):
        for payload in (None, [], "text", 5):
            with self.subTest(payload=payload), self.assertRaises(v.ValidationError):
                v.validate_backup(payload)

    def test_corrupt_rows_are_rejected(self):
        mutations = [
            lambda p: p["targets"][0].update(work_minutes="25"),
            lambda p: p["targets"][0].update(status="deleted"),
            lambda p: p["targets"][0].update(id="../escape"),
            lambda p: p["targets"][0].pop("name"),
            lambda p: p["targets"][0]["batches"][0].update(break_type="nap"),
            lambda p: p["targets"][0]["batches"][0].update(batch_index=0),
            lambda p: p["alarms"][0].update(hour_24=25),
            lambda p: p["alarms"][0].update(repeat_days=[9]),
            lambda p: p["alarms"][0].update(snooze_interval_minutes=3),
            lambda p: p["timers"][0].update(duration_seconds=0),
            lambda p: p["focus_logs"][0].update(source_type="web"),
            lambda p: p["focus_logs"][0].update(local_date_key="16/11/2026"),
            lambda p: p["focus_logs"][0].update(duration_seconds=-1),
            lambda p: p["sounds"][0].update(file_path="../../.bashrc"),
            lambda p: p["sounds"][0].update(file_path="/etc/passwd"),
            lambda p: p["sounds"][0].update(source_type="bundled"),
            lambda p: p["preferences"].update(theme="neon"),
            lambda p: p.update(targets="none"),
        ]
        for index, mutate in enumerate(mutations):
            payload = copy.deepcopy(backup_payload())
            mutate(payload)
            with self.subTest(mutation=index), self.assertRaises(v.ValidationError):
                v.validate_backup(payload)

    def test_unknown_preference_keys_are_dropped(self):
        payload = backup_payload()
        payload["preferences"]["shell_command"] = "rm -rf ~"

        self.assertNotIn("shell_command", v.validate_backup(payload)["preferences"])

    def test_settings_preferences_round_trip_through_a_backup(self):
        payload = backup_payload()
        payload["preferences"].update(
            theme="system", notify_alarms="0", notify_timers="1", notify_targets="0", onboarding_done="1"
        )

        kept = v.validate_backup(payload)["preferences"]

        self.assertEqual(kept["theme"], "system")
        self.assertEqual(
            (kept["notify_alarms"], kept["notify_targets"], kept["onboarding_done"]), ("0", "0", "1")
        )
        for bad in ({"notify_alarms": "yes"}, {"onboarding_done": "2"}, {"theme": "blue"}):
            payload = backup_payload()
            payload["preferences"].update(bad)
            with self.subTest(bad=bad), self.assertRaises(v.ValidationError):
                v.validate_backup(payload)

    def test_duplicate_batch_indexes_are_rejected(self):
        payload = backup_payload()
        duplicate = dict(payload["targets"][0]["batches"][0], id="b2")
        payload["targets"][0]["batches"].append(duplicate)

        with self.assertRaises(v.ValidationError):
            v.validate_backup(payload)


if __name__ == "__main__":
    unittest.main()
