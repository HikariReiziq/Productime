import csv
import io
import json
import stat
import unittest
from datetime import date, datetime

import _bootstrap  # noqa: F401

from productime.services import backup_service as backup
from productime.services.audio_service import SoundLibrary
from support import ASSETS, EXAMPLE_1, EXAMPLE_2, TempData, alarm_draft, make_wav, timer_draft


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.repo = self.data.repo
        self.library = self.make_library(self.data)
        self.out = self.data.root / "exports"
        self.out.mkdir()

    def make_library(self, data):
        return SoundLibrary(data.repo, ASSETS, data.data_dir / "sounds")

    def populate(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.repo.create_target(EXAMPLE_2)
        self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3), mission_mode="typing", mission_count=2))
        self.repo.create_timer(timer_draft())
        self.repo.add_focus_log(
            source_type="target_batch",
            title="Membuat PRD",
            tag_label="Coding",
            started_at=datetime(2026, 11, 16, 9, 0),
            ended_at=datetime(2026, 11, 16, 9, 50),
            duration_seconds=3000,
            subtask_completed=True,
            target_id=target.id,
            batch_id=target.batches[0].id,
        )
        self.repo.set_pref("theme", "dark")
        return target

    def counts(self, repo):
        tables = ("targets", "target_batches", "alarms", "timers", "focus_logs")
        return {
            t: repo.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in tables
        }


class BackupRoundTripTests(BackupTestCase):
    def test_backup_file_restores_every_saved_item_into_an_empty_install(self):
        target = self.populate()
        path = self.out / "productime-backup.json"
        backup.write_backup(self.repo, path)

        fresh = TempData()
        self.addCleanup(fresh.close)
        backup.restore_backup(fresh.repo, self.make_library(fresh), backup.read_backup(path))

        self.assertEqual(
            self.counts(fresh.repo),
            {
                "targets": 2,
                "target_batches": 14,
                "alarms": 1,
                "timers": 1,
                "focus_logs": 1,
            },
        )
        restored = fresh.repo.get_target(target.id)
        self.assertEqual([b.subtask_title for b in restored.batches], list(EXAMPLE_1.subtask_titles))
        self.assertEqual(restored.total_timeline_minutes, 400)
        alarm = fresh.repo.list_alarms()[0]
        self.assertEqual(alarm.repeat_days, (1, 2, 3))
        self.assertEqual((alarm.mission_mode, alarm.mission_count), ("typing", 2))
        self.assertEqual(fresh.repo.list_timers()[0].name, "Tea")
        self.assertEqual(fresh.repo.report("day", date(2026, 11, 16)).total_seconds, 3000)
        self.assertEqual(fresh.repo.get_pref("theme", "light"), "dark")

    def test_restore_replaces_existing_data_instead_of_merging(self):
        self.populate()
        path = self.out / "backup.json"
        backup.write_backup(self.repo, path)
        self.repo.create_timer(timer_draft(name="Added after backup"))
        self.repo.delete_alarm(self.repo.list_alarms()[0].id)

        backup.restore_backup(self.repo, self.library, backup.read_backup(path))

        self.assertEqual([t.name for t in self.repo.list_timers()], ["Tea"])
        self.assertEqual(len(self.repo.list_alarms()), 1)

    def test_backup_file_is_private_and_labelled(self):
        self.populate()
        path = self.out / "backup.json"

        backup.write_backup(self.repo, path)

        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((payload["format"], payload["version"]), ("productime-backup", 1))
        self.assertEqual(list(self.out.iterdir()), [path])

    def test_corrupt_backup_is_rejected_and_the_database_is_untouched(self):
        self.populate()
        path = self.out / "backup.json"
        backup.write_backup(self.repo, path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["targets"][0]["work_minutes"] = "fifty"
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = self.counts(self.repo)

        with self.assertRaises(backup.BackupError):
            backup.read_backup(path)

        self.assertEqual(self.counts(self.repo), before)

    def test_files_that_are_not_backups_are_rejected(self):
        not_json = self.out / "notes.json"
        not_json.write_text("this is not json", encoding="utf-8")
        other_json = self.out / "other.json"
        other_json.write_text('{"hello": "world"}', encoding="utf-8")
        binary = self.out / "binary.json"
        binary.write_bytes(b"\xff\xfe\x00\x01")

        for path in (not_json, other_json, binary, self.out / "missing.json"):
            with self.subTest(path=path.name), self.assertRaises(backup.BackupError):
                backup.read_backup(path)

    def test_oversized_backup_is_rejected_before_parsing(self):
        huge = self.out / "huge.json"
        with open(huge, "wb") as handle:
            handle.truncate(backup.MAX_BACKUP_BYTES + 1)

        with self.assertRaises(backup.BackupError):
            backup.read_backup(huge)

    def test_custom_sound_without_its_file_falls_back_to_the_default_sound(self):
        sound = self.library.import_custom(make_wav(self.data.root / "chime.wav"))
        alarm = self.repo.create_alarm(alarm_draft(sound_id=sound.id))
        path = self.out / "backup.json"
        backup.write_backup(self.repo, path)

        fresh = TempData()
        self.addCleanup(fresh.close)
        backup.restore_backup(fresh.repo, self.make_library(fresh), backup.read_backup(path))

        self.assertEqual(fresh.repo.get_alarm(alarm.id).sound_id, "bundled-digital")
        self.assertIsNone(fresh.repo.get_sound(sound.id))

    def test_custom_sound_with_its_file_present_is_kept(self):
        sound = self.library.import_custom(make_wav(self.data.root / "chime.wav"))
        alarm = self.repo.create_alarm(alarm_draft(sound_id=sound.id))
        path = self.out / "backup.json"
        backup.write_backup(self.repo, path)
        self.repo.delete_alarm(alarm.id)

        backup.restore_backup(self.repo, self.library, backup.read_backup(path))

        self.assertEqual(self.repo.get_alarm(alarm.id).sound_id, sound.id)


class ReportExportTests(BackupTestCase):
    def report_with(self, title, target_name="Plan"):
        from dataclasses import replace

        target = self.repo.create_target(replace(EXAMPLE_1, name=target_name))
        self.repo.add_focus_log(
            source_type="target_batch",
            title=title,
            tag_label="Coding",
            started_at=datetime(2026, 11, 16, 9, 0),
            ended_at=datetime(2026, 11, 16, 9, 50),
            duration_seconds=3000,
            subtask_completed=True,
            target_id=target.id,
        )
        return self.repo.report("day", date(2026, 11, 16))

    def test_formula_like_cells_are_neutralised(self):
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            with self.subTest(prefix=repr(prefix)):
                self.assertEqual(backup.csv_cell(prefix + "SUM(A1:A9)"), "'" + prefix + "SUM(A1:A9)")
        self.assertEqual(backup.csv_cell("Coding backend"), "Coding backend")
        self.assertEqual(backup.csv_cell(3000), "3000")

    def test_csv_export_is_rfc_4180_and_keeps_hostile_titles_inert(self):
        title = '=HYPERLINK("http://evil.example","click"), then'
        text = backup.report_to_csv(self.report_with(title, target_name="@cmd"))

        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        rows = list(csv.reader(io.StringIO(text, newline="")))
        header, row = rows[0], rows[1]
        self.assertEqual(len(rows), 2)
        self.assertEqual(row[header.index("Subtask")], "'" + title)
        self.assertEqual(row[header.index("Target")], "'@cmd")
        self.assertEqual(row[header.index("Duration (seconds)")], "3000")
        self.assertEqual(row[header.index("Completed")], "yes")
        for line in text.strip("\r\n").split("\r\n"):
            self.assertTrue(line.startswith('"') and line.endswith('"'), line)

    def test_csv_and_json_exports_are_written_to_the_chosen_files(self):
        report = self.report_with("Membuat PRD", target_name="Website Profile Company")
        csv_path, json_path = self.out / "report.csv", self.out / "report.json"

        backup.write_report_csv(report, csv_path)
        backup.write_report_json(report, json_path)

        self.assertIn("Membuat PRD", csv_path.read_text(encoding="utf-8"))
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual((payload["task_completed"], payload["total_seconds"]), (1, 3000))
        self.assertEqual(payload["logs"][0]["title"], "Membuat PRD")
        self.assertEqual(payload["logs"][0]["target_name"], "Website Profile Company")
        self.assertEqual(len(payload["buckets"]), 24)

    def test_empty_report_exports_only_the_header(self):
        text = backup.report_to_csv(self.repo.report("day", date(2026, 11, 16)))

        self.assertEqual(len(list(csv.reader(io.StringIO(text, newline="")))), 1)


if __name__ == "__main__":
    unittest.main()
