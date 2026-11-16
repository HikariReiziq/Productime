import stat
import unittest
from datetime import date, datetime

import _bootstrap  # noqa: F401

from productime.domain.models import Sound, TargetConfig
from productime.domain.validation import ValidationError
from support import EXAMPLE_1, EXAMPLE_2, TempData, alarm_draft, timer_draft


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.repo = self.data.repo

    def count(self, table):
        allowed = {"targets", "target_batches", "alarms", "timers", "focus_logs", "sounds"}
        self.assertIn(table, allowed)
        return self.repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608

    def log(self, when, seconds, completed=True, title="Subtask", target_id=None, source="target_batch"):
        return self.repo.add_focus_log(
            source_type=source,
            title=title,
            tag_label="Study",
            started_at=when,
            ended_at=when,
            duration_seconds=seconds,
            subtask_completed=completed,
            target_id=target_id,
        )


class StorageSetupTests(DatabaseTestCase):
    def test_data_directory_and_database_are_private_to_the_user(self):
        db_path = self.data.data_dir / "productime.db"

        self.assertEqual(stat.S_IMODE(self.data.data_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(db_path.stat().st_mode), 0o600)

    def test_database_runs_in_wal_mode_with_foreign_keys(self):
        self.assertEqual(self.repo.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.repo.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_six_bundled_sounds_are_seeded_exactly_once(self):
        expected = {
            "bundled-8footdino": "8footdino_on_scratch-alarm.mp3",
            "bundled-biohazard": "biohazard-alarm.mp3",
            "bundled-community": "community-alarm.mp3",
            "bundled-digital": "digital-alarm-clock.mp3",
            "bundled-funny": "funny-alarm.mp3",
            "bundled-lofi": "lo-fi-alarm-clock.mp3",
        }

        repo = self.data.reopen()

        sounds = {s.id: s.file_path for s in repo.list_sounds()}
        self.assertEqual(sounds, expected)

    def test_first_run_has_no_user_data(self):
        for table in ("targets", "alarms", "timers", "focus_logs"):
            self.assertEqual(self.count(table), 0, table)


class TargetRepositoryTests(DatabaseTestCase):
    def test_target_round_trips_with_every_batch_and_subtask(self):
        created = self.repo.create_target(EXAMPLE_1)

        loaded = self.repo.get_target(created.id)

        self.assertEqual(loaded.name, "Website Profile Company")
        self.assertEqual(loaded.config, EXAMPLE_1.config)
        self.assertEqual((loaded.snooze_limit, loaded.sound_id), (3, "bundled-digital"))
        self.assertEqual(loaded.status, "planned")
        self.assertEqual([b.subtask_title for b in loaded.batches], list(EXAMPLE_1.subtask_titles))
        self.assertEqual([b.break_type for b in loaded.batches], ["short", "short", "long"] * 2)
        self.assertEqual(loaded.total_timeline_minutes, 400)

    def test_unlimited_snooze_is_stored_as_null(self):
        created = self.repo.create_target(EXAMPLE_2)

        self.assertIsNone(self.repo.get_target(created.id).snooze_limit)
        self.assertEqual(self.repo.get_target(created.id).total_timeline_minutes, 290)

    def test_update_replaces_the_batch_plan(self):
        created = self.repo.create_target(EXAMPLE_1)

        self.repo.update_target(created.id, EXAMPLE_2)

        loaded = self.repo.get_target(created.id)
        self.assertEqual(len(loaded.batches), 8)
        self.assertEqual(self.count("target_batches"), 8)
        self.assertEqual(loaded.name, "Belajar Pemrograman Mobile")

    def test_delete_cascades_to_batches(self):
        created = self.repo.create_target(EXAMPLE_1)

        self.repo.delete_target(created.id)

        self.assertEqual((self.count("targets"), self.count("target_batches")), (0, 0))

    def test_invalid_target_is_rejected_and_nothing_is_written(self):
        from dataclasses import replace

        bad = replace(EXAMPLE_1, config=TargetConfig(0, 50, 10))

        with self.assertRaises(ValidationError):
            self.repo.create_target(bad)
        with self.assertRaises(ValidationError):
            self.repo.create_target(replace(EXAMPLE_1, sound_id="bundled-missing"))

        self.assertEqual((self.count("targets"), self.count("target_batches")), (0, 0))

    def test_hostile_text_is_stored_literally(self):
        from dataclasses import replace

        name = "Robert'); DROP TABLE targets;--"

        created = self.repo.create_target(replace(EXAMPLE_1, name=name))

        self.assertEqual(self.repo.get_target(created.id).name, name)
        self.assertEqual(self.count("targets"), 1)

    def test_batch_progress_and_status_are_saved(self):
        target = self.repo.create_target(EXAMPLE_1)
        batch = target.batches[0]
        batch.status, batch.actual_work_seconds, batch.snooze_count = "active", 1200, 2

        self.repo.save_batch_progress(batch)
        self.repo.set_target_status(target.id, "in_progress")

        loaded = self.repo.get_target(target.id)
        self.assertEqual(loaded.status, "in_progress")
        self.assertEqual(
            (loaded.batches[0].status, loaded.batches[0].actual_work_seconds, loaded.batches[0].snooze_count),
            ("active", 1200, 2),
        )

    def test_reset_clears_progress_for_a_rerun(self):
        target = self.repo.create_target(EXAMPLE_1)
        batch = target.batches[0]
        batch.status, batch.actual_work_seconds = "completed", 3000
        self.repo.save_batch_progress(batch)
        self.repo.set_target_status(target.id, "completed")

        self.repo.reset_target_progress(target.id)

        loaded = self.repo.get_target(target.id)
        self.assertEqual(loaded.status, "planned")
        self.assertEqual({b.status for b in loaded.batches}, {"pending"})
        self.assertEqual(loaded.batches[0].actual_work_seconds, 0)


class AlarmAndTimerRepositoryTests(DatabaseTestCase):
    def test_alarm_round_trips_with_repeat_days_and_mission(self):
        draft = alarm_draft(
            repeat_days=(1, 3, 5), mission_mode="math", mission_count=3, mission_difficulty="hard"
        )

        created = self.repo.create_alarm(draft)

        loaded = self.repo.get_alarm(created.id)
        self.assertEqual(loaded.repeat_days, (1, 3, 5))
        self.assertEqual(
            (loaded.mission_mode, loaded.mission_count, loaded.mission_difficulty), ("math", 3, "hard")
        )
        self.assertEqual((loaded.hour_24, loaded.minute, loaded.enabled), (8, 5, True))

    def test_alarm_can_be_toggled_edited_and_deleted(self):
        created = self.repo.create_alarm(alarm_draft())

        self.repo.set_alarm_enabled(created.id, False)
        self.assertFalse(self.repo.get_alarm(created.id).enabled)
        self.repo.update_alarm(created.id, alarm_draft(name="Gym", hour_24=17))
        self.assertEqual(
            (self.repo.get_alarm(created.id).name, self.repo.get_alarm(created.id).hour_24), ("Gym", 17)
        )
        self.repo.delete_alarm(created.id)

        self.assertEqual(self.repo.list_alarms(), [])

    def test_timer_round_trips_and_can_be_edited_and_deleted(self):
        created = self.repo.create_timer(timer_draft())

        self.repo.update_timer(created.id, timer_draft(name="Laundry", duration_seconds=3600))
        loaded = self.repo.get_timer(created.id)
        self.assertEqual((loaded.name, loaded.duration_seconds), ("Laundry", 3600))
        self.assertEqual(loaded.sound_id, "bundled-funny")
        self.repo.delete_timer(created.id)

        self.assertEqual(self.repo.list_timers(), [])

    def test_deleting_a_custom_sound_moves_its_users_to_the_default_sound(self):
        self.repo.add_sound(Sound("custom-abc", "My song", "custom", "abc.mp3", 3.0))
        alarm = self.repo.create_alarm(alarm_draft(sound_id="custom-abc"))
        timer = self.repo.create_timer(timer_draft(sound_id="custom-abc"))

        self.repo.delete_sound("custom-abc")

        self.assertIsNone(self.repo.get_sound("custom-abc"))
        self.assertEqual(self.repo.get_alarm(alarm.id).sound_id, "bundled-digital")
        self.assertEqual(self.repo.get_timer(timer.id).sound_id, "bundled-digital")

    def test_bundled_sounds_cannot_be_deleted(self):
        self.repo.delete_sound("bundled-digital")

        self.assertIsNotNone(self.repo.get_sound("bundled-digital"))


class PersistenceTests(DatabaseTestCase):
    def test_saved_data_survives_closing_and_reopening_the_database(self):
        target = self.repo.create_target(EXAMPLE_1)
        alarm = self.repo.create_alarm(alarm_draft())
        timer = self.repo.create_timer(timer_draft())
        self.log(datetime(2026, 11, 16, 9, 0), 3000, title="Membuat PRD", target_id=target.id)
        self.repo.set_pref("theme", "dark")

        repo = self.data.reopen()

        self.assertEqual(repo.get_target(target.id).batches[5].subtask_title, "Finish selesai semua")
        self.assertEqual(repo.get_alarm(alarm.id).name, "Morning class")
        self.assertEqual(repo.get_timer(timer.id).duration_seconds, 90)
        self.assertEqual(repo.report("day", date(2026, 11, 16)).task_completed, 1)
        self.assertEqual(repo.get_pref("theme", "light"), "dark")

    def test_preferences_fall_back_to_the_default(self):
        self.assertEqual(self.repo.get_pref("volume", "100"), "100")
        self.repo.set_pref("volume", "40")
        self.repo.set_pref("volume", "55")

        self.assertEqual(self.repo.get_pref("volume", "100"), "55")


class ReportAggregationTests(DatabaseTestCase):
    def test_day_report_counts_completed_subtasks_and_sums_time_per_hour(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.log(datetime(2026, 11, 16, 9, 10), 3000, title="Membuat PRD", target_id=target.id)
        self.log(datetime(2026, 11, 16, 9, 50), 600, completed=False, title="Membuat desain")
        self.log(datetime(2026, 11, 16, 14, 0), 1500, title="Coding backend")
        self.log(datetime(2026, 11, 17, 9, 0), 999, title="Other day")

        report = self.repo.report("day", date(2026, 11, 16))

        self.assertEqual((report.task_completed, report.total_seconds), (2, 5100))
        self.assertEqual(len(report.bucket_seconds), 24)
        self.assertEqual((report.bucket_seconds[9], report.bucket_seconds[14]), (3600, 1500))
        self.assertEqual(sum(report.bucket_seconds), 5100)
        titles = [log.title for log in report.logs]
        self.assertEqual(titles, ["Coding backend", "Membuat desain", "Membuat PRD"])
        self.assertEqual(report.logs[2].target_name, "Website Profile Company")

    def test_week_report_groups_monday_to_sunday(self):
        self.log(datetime(2026, 11, 16, 9, 0), 1800)
        self.log(datetime(2026, 11, 18, 9, 0), 1200)
        self.log(datetime(2026, 11, 22, 23, 30), 600, completed=False)
        self.log(datetime(2026, 11, 23, 0, 30), 5000)
        self.log(datetime(2026, 11, 15, 12, 0), 7000)

        report = self.repo.report("week", date(2026, 11, 19))

        self.assertEqual(report.bucket_labels, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        self.assertEqual(report.bucket_seconds, (1800, 0, 1200, 0, 0, 0, 600))
        self.assertEqual((report.start_date, report.end_date), ("2026-11-16", "2026-11-22"))
        self.assertEqual((report.task_completed, report.total_seconds), (2, 3600))

    def test_empty_period_reports_zero_without_inventing_rows(self):
        report = self.repo.report("day", date(2026, 11, 16))

        self.assertEqual((report.task_completed, report.total_seconds, report.logs), (0, 0, ()))
        self.assertEqual(sum(report.bucket_seconds), 0)

    def test_timer_logs_add_time_but_not_completed_tasks(self):
        self.log(datetime(2026, 11, 16, 10, 0), 180, completed=False, title="Tea", source="timer")

        report = self.repo.report("day", date(2026, 11, 16))

        self.assertEqual((report.task_completed, report.total_seconds), (0, 180))


if __name__ == "__main__":
    unittest.main()
