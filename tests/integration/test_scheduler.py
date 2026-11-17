import random
import unittest
from datetime import date

import _bootstrap  # noqa: F401

from productime.domain import target_engine
from productime.domain.models import TargetPhase
from productime.services.clock import FakeClock
from productime.services.scheduler_service import Scheduler, SchedulerError
from support import (
    EXAMPLE_1,
    MONDAY_0800,
    FakeAudio,
    RecordingNotifier,
    TempData,
    alarm_draft,
    short_target,
    timer_draft,
)

MIN = 60


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.repo = self.data.repo
        self.clock = FakeClock(MONDAY_0800)
        self.audio = FakeAudio()
        self.notifier = RecordingNotifier()
        self.scheduler = self.make_scheduler()

    def make_scheduler(self):
        return Scheduler(self.repo, self.clock, self.audio, self.notifier, rng=random.Random(11))

    def run_for(self, seconds, step=1.0):
        remaining = float(seconds)
        while remaining > 1e-9:
            chunk = min(step, remaining)
            self.clock.advance(chunk)
            self.scheduler.poll()
            remaining -= chunk

    @property
    def phase(self):
        return self.scheduler.session.state.phase

    def report(self):
        return self.repo.report("day", date(2026, 11, 16))


class TargetSessionTests(SchedulerTestCase):
    def start(self, draft=EXAMPLE_1):
        target = self.repo.create_target(draft)
        self.scheduler.start_target(target.id)
        return target

    def test_work_countdown_reaches_the_gate_at_exactly_the_planned_time(self):
        self.start()

        for step in (0.37, 1.0, 13.9, 0.004, 240.0, 59.726):
            self.run_for(step, step=step)
        self.run_for(50 * MIN - 315.0 - 0.001, step=7.3)
        self.assertEqual(self.phase, TargetPhase.WORKING)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 1)
        self.run_for(0.001, step=0.001)

        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)

    def test_gate_loops_the_target_sound_and_never_rests_on_its_own(self):
        self.start()

        self.run_for(50 * MIN, step=30)
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")
        self.run_for(3 * 60 * MIN, step=600)

        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")
        self.assertEqual(self.report().task_completed, 0)

    def test_checking_off_at_the_gate_stops_the_sound_starts_rest_and_logs_the_work(self):
        target = self.start()
        self.run_for(50 * MIN, step=30)

        self.scheduler.complete_subtask()

        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertIsNone(self.audio.alert_sound_id)
        report = self.report()
        self.assertEqual((report.task_completed, report.total_seconds), (1, 3000))
        self.assertEqual((report.logs[0].title, report.logs[0].target_name), ("Membuat PRD", target.name))
        saved = self.repo.get_target(target.id)
        self.assertEqual((saved.status, saved.batches[0].status), ("in_progress", "completed"))
        self.assertEqual(saved.batches[0].actual_work_seconds, 3000)

    def test_checking_off_early_logs_only_the_time_actually_worked(self):
        self.start()
        self.run_for(12 * MIN, step=30)

        self.scheduler.complete_subtask()

        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertEqual(self.report().total_seconds, 12 * MIN)

    def test_rest_ends_by_asking_before_the_next_batch(self):
        target = self.start()
        self.scheduler.complete_subtask()

        self.run_for(10 * MIN, step=30)
        self.run_for(30 * MIN, step=60)

        state = self.scheduler.session.state
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.READY_FOR_NEXT, 1))
        alert = self.scheduler.current_alert()
        self.assertEqual((alert.kind, alert.title), ("ready", "Membuat desain"))
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")
        self.assertIn("Break is over", self.notifier.titles())
        self.assertTrue(self.scheduler.has_background_work())
        self.assertEqual(self.report().total_seconds, 0, "rest and waiting are not focus time")

        self.scheduler.start_next_batch()

        state = self.scheduler.session.state
        self.assertEqual(
            (state.phase, state.batch_index, state.remaining_ms), (TargetPhase.WORKING, 2, 50 * MIN * 1000)
        )
        self.assertIsNone(self.audio.alert_sound_id)
        self.assertNotIn("target-ready", self.notifier.active)
        self.assertEqual(self.repo.get_target(target.id).batches[1].status, "active")

    def test_checking_during_a_rest_starts_the_next_batch_at_once(self):
        self.start()
        self.scheduler.complete_subtask()
        self.run_for(3 * MIN, step=30)

        self.scheduler.start_next_batch()

        state = self.scheduler.session.state
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.WORKING, 2))
        self.run_for(MIN, step=30)
        self.assertEqual(state.remaining_ms, 50 * MIN * 1000)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 49 * MIN * 1000)

    def test_a_rest_can_be_extended_within_the_snooze_limit(self):
        self.start()
        self.scheduler.complete_subtask()
        self.run_for(10 * MIN, step=30)
        first_key = self.scheduler.current_alert().key

        self.scheduler.extend_rest(5)

        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertIsNone(self.audio.alert_sound_id)
        self.run_for(5 * MIN, step=30)
        self.assertEqual(self.phase, TargetPhase.READY_FOR_NEXT)
        self.assertNotEqual(self.scheduler.current_alert().key, first_key)
        for _ in range(2):
            self.scheduler.extend_rest(5)
            self.run_for(5 * MIN, step=30)
        with self.assertRaises(target_engine.InvalidTransition):
            self.scheduler.extend_rest(5)
        self.assertEqual(self.report().total_seconds, 0)

    def test_snooze_extends_work_and_its_time_is_logged(self):
        self.start()
        self.run_for(50 * MIN, step=30)

        self.scheduler.snooze_session(5)
        self.assertEqual(self.phase, TargetPhase.SNOOZING)
        self.assertIsNone(self.audio.alert_sound_id)
        self.run_for(5 * MIN, step=10)
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.scheduler.complete_subtask()

        self.assertEqual(self.report().total_seconds, 55 * MIN)
        self.assertEqual(self.repo.list_targets()[0].batches[0].snooze_seconds_used, 5 * MIN)

    def test_snooze_locks_once_the_limit_is_used(self):
        self.start(short_target(snooze_limit=1))
        self.run_for(1 * MIN, step=5)
        self.scheduler.snooze_session(1)
        self.run_for(1 * MIN, step=5)

        self.assertFalse(self.scheduler.session.state.can_snooze)
        with self.assertRaises(target_engine.InvalidTransition):
            self.scheduler.snooze_session(1)
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)

    def test_invalid_snooze_minutes_are_rejected(self):
        self.start()
        self.run_for(50 * MIN, step=30)

        for minutes in (0, -3, 500):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.scheduler.snooze_session(minutes)

    def test_final_check_off_enters_final_rest_and_finish_completes_the_target(self):
        target = self.start(short_target())
        self.scheduler.complete_subtask()
        self.run_for(1 * MIN, step=5)
        self.scheduler.start_next_batch()
        self.run_for(1 * MIN, step=5)

        self.scheduler.complete_subtask()
        self.assertTrue(self.scheduler.session.state.in_final_rest)
        self.scheduler.finish_target()

        self.assertIsNone(self.scheduler.session)
        self.assertEqual(self.repo.get_target(target.id).status, "completed")
        self.assertEqual(self.report().task_completed, 2)
        self.assertEqual(self.scheduler.last_completed_target.id, target.id)

    def test_final_rest_running_out_completes_the_target(self):
        target = self.start(short_target())
        self.scheduler.complete_subtask()
        self.run_for(1 * MIN, step=5)
        self.scheduler.start_next_batch()
        self.scheduler.complete_subtask()

        self.run_for(1 * MIN, step=5)

        self.assertEqual(self.repo.get_target(target.id).status, "completed")

    def test_suspend_during_work_lands_on_the_gate_after_wake(self):
        self.start()
        self.run_for(10 * MIN, step=30)

        self.clock.suspend(2 * 60 * MIN)
        self.scheduler.poll()

        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)

    def test_short_suspend_shortens_the_countdown_by_the_time_asleep(self):
        self.start()
        self.run_for(10 * MIN, step=30)

        self.clock.suspend(15 * MIN)
        self.scheduler.poll()

        self.assertEqual(self.scheduler.session.state.remaining_ms, 25 * MIN * 1000)

    def test_setting_the_system_clock_does_not_move_the_countdown(self):
        self.start()
        self.run_for(10 * MIN, step=30)

        self.clock.shift_wall(-3 * 60 * MIN)
        self.scheduler.poll()
        self.run_for(1 * MIN, step=30)

        self.assertEqual(self.scheduler.session.state.remaining_ms, 39 * MIN * 1000)

    def test_pause_freezes_and_resume_continues(self):
        self.start()
        self.run_for(10 * MIN, step=30)

        self.scheduler.pause_session()
        self.run_for(30 * MIN, step=60)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 40 * MIN * 1000)
        self.scheduler.resume_session()
        self.run_for(5 * MIN, step=60)

        self.assertEqual(self.scheduler.session.state.remaining_ms, 35 * MIN * 1000)

    def test_quit_keeps_progress_for_a_later_resume(self):
        target = self.start()
        self.run_for(20 * MIN, step=30)

        self.scheduler.quit_session()

        self.assertIsNone(self.scheduler.session)
        saved = self.repo.get_target(target.id)
        self.assertEqual((saved.status, saved.batches[0].actual_work_seconds), ("in_progress", 20 * MIN))
        self.assertEqual((self.report().task_completed, self.report().total_seconds), (0, 20 * MIN))
        self.scheduler.start_target(target.id)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 30 * MIN * 1000)
        self.run_for(30 * MIN, step=30)
        self.scheduler.complete_subtask()
        self.assertEqual((self.report().task_completed, self.report().total_seconds), (1, 50 * MIN))

    def test_unfinished_target_is_restored_paused_after_a_restart(self):
        target = self.start()
        self.run_for(20 * MIN, step=30)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        self.assertEqual(restarted.session.target.id, target.id)
        self.assertEqual(restarted.session.state.phase, TargetPhase.PAUSED_WORK)
        self.assertEqual(restarted.session.state.remaining_ms, 30 * MIN * 1000)
        self.assertFalse(restarted.has_background_work())

    def test_a_rest_reopens_paused_with_its_exact_time_left(self):
        target = self.start()
        self.scheduler.complete_subtask()
        self.run_for(3 * MIN + 17.25, step=0.75)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        state = restarted.session.state
        self.assertEqual(restarted.session.target.id, target.id)
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.PAUSED_REST, 1))
        self.assertEqual(state.remaining_ms, 10 * MIN * 1000 - 197_250)

    def test_a_snooze_reopens_paused_with_its_exact_time_left(self):
        self.start()
        self.run_for(50 * MIN, step=60)
        self.scheduler.snooze_session(10)
        self.run_for(4 * MIN + 0.5, step=0.5)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        state = restarted.session.state
        self.assertEqual(
            (state.phase, state.remaining_ms, state.snooze_count), (TargetPhase.PAUSED_WORK, 359_500, 1)
        )
        restarted.resume_session()
        self.assertEqual(restarted.session.state.phase, TargetPhase.SNOOZING)

    def test_a_waiting_gate_reopens_silently_and_resumes_at_the_gate(self):
        self.start()
        self.run_for(50 * MIN, step=60)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        self.assertEqual(restarted.session.state.phase, TargetPhase.PAUSED_WORK)
        self.assertIsNone(restarted.current_alert())
        restarted.resume_session()
        self.assertEqual(restarted.current_alert().kind, "gate")

    def test_waiting_to_start_reopens_as_the_next_batch_paused(self):
        self.start()
        self.scheduler.complete_subtask()
        self.run_for(10 * MIN, step=60)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        state = restarted.session.state
        self.assertEqual(
            (state.phase, state.batch_index, state.remaining_ms),
            (TargetPhase.PAUSED_WORK, 2, 50 * MIN * 1000),
        )
        self.assertIsNone(restarted.current_alert())

    def test_a_crash_keeps_the_time_left_from_the_last_regular_save(self):
        self.start()
        self.run_for(20 * MIN + 10, step=5)

        restarted = self.make_scheduler()

        remaining = restarted.session.state.remaining_ms
        self.assertEqual(restarted.session.state.phase, TargetPhase.PAUSED_WORK)
        self.assertLessEqual(remaining - (30 * MIN - 10) * 1000, 30_000)
        self.assertGreaterEqual(remaining, (30 * MIN - 10) * 1000)

    def test_quitting_the_target_drops_the_saved_rest(self):
        self.start()
        self.scheduler.complete_subtask()
        self.run_for(MIN, step=30)
        self.scheduler.quit_session()

        restarted = self.make_scheduler()

        state = restarted.session.state
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.PAUSED_WORK, 2))
        self.assertEqual(state.remaining_ms, 50 * MIN * 1000)

    def test_only_one_target_runs_at_a_time(self):
        self.start()
        other = self.repo.create_target(short_target())

        with self.assertRaises(SchedulerError):
            self.scheduler.start_target(other.id)

    def test_completed_target_can_be_run_again_from_the_start(self):
        target = self.start(short_target())
        self.scheduler.complete_subtask()
        self.run_for(1 * MIN, step=5)
        self.scheduler.start_next_batch()
        self.scheduler.complete_subtask()
        self.scheduler.finish_target()

        self.scheduler.start_target(target.id)

        state = self.scheduler.session.state
        self.assertEqual((state.batch_index, state.phase), (1, TargetPhase.WORKING))


class TimerTests(SchedulerTestCase):
    def setUp(self):
        super().setUp()
        self.timer = self.repo.create_timer(timer_draft())

    def run_state(self):
        return self.scheduler.timer_run(self.timer.id)

    def test_timer_counts_down_and_loops_its_sound_when_it_expires(self):
        self.scheduler.start_timer(self.timer.id)

        self.run_for(60, step=0.7)
        self.assertEqual((self.run_state().status, self.run_state().remaining_ms), ("running", 30_000))
        self.run_for(30, step=0.7)

        self.assertEqual(self.run_state().status, "expired")
        self.assertEqual(self.audio.alert_sound_id, "bundled-funny")
        self.assertIn("Timer finished", self.notifier.titles())

    def test_expired_timer_keeps_sounding_until_dismissed(self):
        self.scheduler.start_timer(self.timer.id)
        self.run_for(90 + 600, step=5)
        self.assertEqual(self.audio.alert_sound_id, "bundled-funny")

        self.scheduler.dismiss_timer(self.timer.id)

        self.assertIsNone(self.audio.alert_sound_id)
        self.assertIsNone(self.run_state())

    def test_running_and_paused_timers_reopen_paused_with_their_time_left(self):
        other = self.repo.create_timer(timer_draft(name="Laundry", duration_seconds=600))
        self.scheduler.start_timer(self.timer.id)
        self.scheduler.start_timer(other.id)
        self.run_for(20.5, step=0.5)
        self.scheduler.pause_timer(other.id)
        self.run_for(10, step=1)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        tea, laundry = restarted.timer_run(self.timer.id), restarted.timer_run(other.id)
        self.assertEqual((tea.status, tea.remaining_ms, tea.name), ("paused", 59_500, "Tea"))
        self.assertEqual((laundry.status, laundry.remaining_ms), ("paused", 579_500))
        self.assertIsNone(restarted.current_alert())
        restarted.resume_timer(self.timer.id)
        self.assertEqual(restarted.timer_run(self.timer.id).status, "running")

    def test_an_expired_or_deleted_timer_is_not_brought_back(self):
        short = self.repo.create_timer(timer_draft(name="Egg", duration_seconds=5))
        self.scheduler.start_timer(short.id)
        self.scheduler.start_timer(self.timer.id)
        self.run_for(6, step=1)
        self.repo.delete_timer(self.timer.id)
        self.scheduler.shutdown()

        restarted = self.make_scheduler()

        self.assertEqual(restarted.timers, {})

    def test_restart_from_the_expired_state_runs_the_full_duration_again(self):
        self.scheduler.start_timer(self.timer.id)
        self.run_for(90, step=5)

        self.scheduler.restart_timer(self.timer.id)

        self.assertEqual((self.run_state().status, self.run_state().remaining_ms), ("running", 90_000))
        self.assertIsNone(self.audio.alert_sound_id)

    def test_pause_resume_and_reset(self):
        self.scheduler.start_timer(self.timer.id)
        self.run_for(30, step=1)

        self.scheduler.pause_timer(self.timer.id)
        self.run_for(120, step=5)
        self.assertEqual((self.run_state().status, self.run_state().remaining_ms), ("paused", 60_000))
        self.scheduler.resume_timer(self.timer.id)
        self.run_for(10, step=1)
        self.assertEqual(self.run_state().remaining_ms, 50_000)
        self.scheduler.reset_timer(self.timer.id)

        self.assertIsNone(self.run_state())

    def test_timer_expires_on_wake_when_it_ran_out_during_suspend(self):
        self.scheduler.start_timer(self.timer.id)
        self.run_for(60, step=1)

        self.clock.suspend(40)
        self.scheduler.poll()

        self.assertEqual(self.run_state().status, "expired")

    def test_finished_timer_adds_time_to_the_report_but_no_completed_task(self):
        self.scheduler.start_timer(self.timer.id)

        self.run_for(90, step=5)

        self.assertEqual((self.report().task_completed, self.report().total_seconds), (0, 90))


class AlarmTests(SchedulerTestCase):
    def add(self, **overrides):
        alarm = self.repo.create_alarm(alarm_draft(**overrides))
        self.scheduler.refresh_alarms()
        return alarm

    def ringing_ids(self):
        return [r.alarm.id for r in self.scheduler.ringing]

    def test_alarm_rings_when_its_minute_arrives_and_loops_its_sound(self):
        alarm = self.add()

        self.run_for(4 * MIN + 59, step=1)
        self.assertEqual(self.ringing_ids(), [])
        self.run_for(1, step=1)

        self.assertEqual(self.ringing_ids(), [alarm.id])
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")
        self.assertIn("Morning class", self.notifier.titles())

    def test_alarm_rings_once_per_minute_however_often_it_is_checked(self):
        alarm = self.add()
        self.run_for(5 * MIN, step=1)

        self.scheduler.stop_alarm(alarm.id)
        self.run_for(59, step=0.25)

        self.assertEqual(self.ringing_ids(), [])
        self.assertEqual(self.audio.history.count("start:bundled-digital"), 1)

    def test_one_shot_alarm_disables_itself_after_ringing(self):
        alarm = self.add()

        self.run_for(5 * MIN, step=1)

        self.assertFalse(self.repo.get_alarm(alarm.id).enabled)

    def test_repeating_alarm_stays_enabled_and_skips_other_weekdays(self):
        monday = self.add(repeat_days=(1,))
        tuesday = self.add(name="Tuesday only", repeat_days=(2,), minute=6)

        self.run_for(10 * MIN, step=1)

        self.assertEqual(self.ringing_ids(), [monday.id])
        self.assertTrue(self.repo.get_alarm(monday.id).enabled)
        self.assertTrue(self.repo.get_alarm(tuesday.id).enabled)

    def test_disabled_alarm_does_not_ring(self):
        self.add(enabled=False)

        self.run_for(10 * MIN, step=1)

        self.assertEqual(self.ringing_ids(), [])

    def test_alarm_missed_during_suspend_rings_once_on_wake(self):
        alarm = self.add()

        self.clock.suspend(3 * 60 * MIN)
        self.scheduler.poll()
        self.run_for(30, step=1)

        self.assertEqual(self.ringing_ids(), [alarm.id])

    def test_clock_moved_back_across_the_alarm_minute_does_not_ring_twice(self):
        alarm = self.add()
        self.run_for(5 * MIN, step=1)
        self.scheduler.stop_alarm(alarm.id)
        self.repo.set_alarm_enabled(alarm.id, True)
        self.scheduler.refresh_alarms()

        self.clock.shift_wall(-30)
        self.scheduler.poll()
        self.run_for(40, step=1)

        self.assertEqual(self.ringing_ids(), [])

    def test_snooze_silences_and_rings_again_after_the_interval(self):
        alarm = self.add(snooze_interval_minutes=5, snooze_limit=1)
        self.run_for(5 * MIN, step=1)

        self.scheduler.snooze_alarm(alarm.id)
        self.assertEqual((self.ringing_ids(), self.audio.alert_sound_id), ([], None))
        self.run_for(5 * MIN - 1, step=1)
        self.assertEqual(self.ringing_ids(), [])
        self.run_for(1, step=1)

        self.assertEqual(self.ringing_ids(), [alarm.id])
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")

    def test_snooze_is_refused_once_the_limit_is_reached(self):
        alarm = self.add(snooze_limit=1)
        self.run_for(5 * MIN, step=1)
        self.scheduler.snooze_alarm(alarm.id)
        self.run_for(5 * MIN, step=1)

        self.assertFalse(self.scheduler.can_snooze_alarm(alarm.id))
        with self.assertRaises(SchedulerError):
            self.scheduler.snooze_alarm(alarm.id)
        self.assertEqual(self.ringing_ids(), [alarm.id])

    def test_unlimited_snooze_never_locks(self):
        alarm = self.add(snooze_limit=None, snooze_interval_minutes=1)
        self.run_for(5 * MIN, step=1)

        for _ in range(12):
            self.scheduler.snooze_alarm(alarm.id)
            self.run_for(1 * MIN, step=1)

        self.assertTrue(self.scheduler.can_snooze_alarm(alarm.id))

    def test_typing_mission_must_be_fully_passed_before_the_alarm_stops(self):
        alarm = self.add(mission_mode="typing", mission_count=3)
        self.run_for(5 * MIN, step=1)
        ringing = self.scheduler.ringing[0]

        self.assertEqual(ringing.mission_total, 3)
        self.assertLessEqual(len(ringing.pool), 10)
        with self.assertRaises(SchedulerError):
            self.scheduler.stop_alarm(alarm.id)
        wrong = self.scheduler.submit_mission(alarm.id, "definitely not the phrase")
        self.assertEqual((wrong.correct, wrong.finished, ringing.mission_index), (False, False, 0))
        for index in range(3):
            self.assertEqual(self.audio.alert_sound_id, "bundled-digital")
            result = self.scheduler.submit_mission(alarm.id, ringing.current_prompt)
            self.assertEqual((result.correct, result.finished), (True, index == 2))

        self.assertEqual((self.ringing_ids(), self.audio.alert_sound_id), ([], None))

    def test_math_mission_checks_the_answer_inside_the_scheduler(self):
        alarm = self.add(mission_mode="math", mission_count=2, mission_difficulty="hard")
        self.run_for(5 * MIN, step=1)
        ringing = self.scheduler.ringing[0]

        first = ringing.problems[0]
        self.assertEqual(ringing.current_prompt, first.text)
        self.assertFalse(self.scheduler.submit_mission(alarm.id, str(first.answer + 1)).correct)
        self.assertFalse(self.scheduler.submit_mission(alarm.id, "abc").correct)
        self.assertTrue(self.scheduler.submit_mission(alarm.id, str(first.answer)).correct)
        self.assertEqual(self.ringing_ids(), [alarm.id])
        result = self.scheduler.submit_mission(alarm.id, str(ringing.problems[1].answer))

        self.assertTrue(result.finished)
        self.assertEqual(self.ringing_ids(), [])

    def test_click_mission_stops_only_after_the_last_click(self):
        alarm = self.repo.create_alarm(alarm_draft(mission_mode="click", mission_clicks=20))
        self.scheduler.refresh_alarms()
        self.run_for(5 * MIN, step=1)

        with self.assertRaises(SchedulerError):
            self.scheduler.stop_alarm(alarm.id)
        for done in range(1, 20):
            result = self.scheduler.register_click(alarm.id)
            self.assertFalse(result.finished)
            self.assertEqual(self.scheduler.ringing[0].clicks_done, done)
        self.assertEqual(self.audio.alert_sound_id, "bundled-digital")

        self.assertTrue(self.scheduler.register_click(alarm.id).finished)
        self.assertEqual(self.scheduler.ringing, [])
        self.assertIsNone(self.audio.alert_sound_id)

    def test_a_snoozed_click_mission_starts_from_zero_when_it_rings_again(self):
        alarm = self.repo.create_alarm(alarm_draft(mission_mode="click", mission_clicks=10, snooze_limit=1))
        self.scheduler.refresh_alarms()
        self.run_for(5 * MIN, step=1)
        for _ in range(7):
            self.scheduler.register_click(alarm.id)

        self.scheduler.snooze_alarm(alarm.id)
        self.run_for(5 * MIN, step=1)

        entry = self.scheduler.ringing[0]
        self.assertEqual((entry.clicks_done, entry.clicks_needed), (0, 10))
        self.assertEqual(self.notifier.button_labels(f"alarm-{alarm.id}"), ["Open Mission"])

    def test_clicks_only_count_for_a_click_mission(self):
        alarm = self.repo.create_alarm(alarm_draft(mission_mode="math"))
        self.scheduler.refresh_alarms()
        self.run_for(5 * MIN, step=1)

        with self.assertRaises(SchedulerError):
            self.scheduler.register_click(alarm.id)

    def test_ringing_alarm_is_locked_against_edit_and_delete(self):
        alarm = self.add(mission_mode="typing")
        self.assertFalse(self.scheduler.is_alarm_locked(alarm.id))

        self.run_for(5 * MIN, step=1)

        self.assertTrue(self.scheduler.is_alarm_locked(alarm.id))

    def test_next_alarm_reports_the_soonest_enabled_alarm(self):
        self.add(name="Later", hour_24=9, minute=0)
        soon = self.add(name="Soon", hour_24=8, minute=30)
        self.add(name="Off", hour_24=8, minute=10, enabled=False)

        alarm, when = self.scheduler.next_alarm()

        self.assertEqual((alarm.id, when.hour, when.minute), (soon.id, 8, 30))


class AlertPriorityAndBackgroundTests(SchedulerTestCase):
    def test_alarm_outranks_the_gate_which_outranks_an_expired_timer(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=30))
        target = self.repo.create_target(short_target())
        alarm = self.repo.create_alarm(alarm_draft(minute=2, sound_id="bundled-biohazard"))
        self.scheduler.refresh_alarms()
        self.scheduler.start_timer(timer.id)
        self.scheduler.start_target(target.id)

        self.run_for(30, step=1)
        self.assertEqual(self.scheduler.current_alert().kind, "timer")
        self.run_for(30, step=1)
        self.assertEqual(self.scheduler.current_alert().kind, "gate")
        self.assertEqual(self.audio.alert_sound_id, "bundled-community")
        self.run_for(60, step=1)
        self.assertEqual(self.scheduler.current_alert().kind, "alarm")
        self.assertEqual(self.audio.alert_sound_id, "bundled-biohazard")

        self.scheduler.stop_alarm(alarm.id)
        self.assertEqual(self.scheduler.current_alert().kind, "gate")
        self.scheduler.complete_subtask()
        self.assertEqual(self.scheduler.current_alert().kind, "timer")
        self.scheduler.dismiss_timer(timer.id)

        self.assertIsNone(self.scheduler.current_alert())
        self.assertIsNone(self.audio.alert_sound_id)

    def test_background_work_is_reported_only_while_something_needs_the_process(self):
        self.assertFalse(self.scheduler.has_background_work())
        self.assertIsNone(self.scheduler.next_wakeup_ms())

        timer = self.repo.create_timer(timer_draft())
        self.scheduler.start_timer(timer.id)
        self.assertTrue(self.scheduler.has_background_work())
        self.assertLessEqual(self.scheduler.next_wakeup_ms(), 90_000)
        self.scheduler.reset_timer(timer.id)
        self.assertFalse(self.scheduler.has_background_work())

        self.repo.create_alarm(alarm_draft())
        self.scheduler.refresh_alarms()
        self.assertTrue(self.scheduler.has_background_work())

    def test_listeners_hear_about_state_changes(self):
        events = []
        self.scheduler.subscribe(lambda: events.append(1))
        timer = self.repo.create_timer(timer_draft(duration_seconds=10))

        self.scheduler.start_timer(timer.id)
        started = len(events)
        self.run_for(10, step=1)

        self.assertGreaterEqual(started, 1)
        self.assertGreater(len(events), started)

    def test_stopwatch_runs_on_the_monotonic_clock_and_counts_suspend_time(self):
        self.scheduler.stopwatch_start()
        self.run_for(10, step=0.5)
        self.scheduler.stopwatch_lap()
        self.clock.suspend(60)
        self.scheduler.poll()
        self.run_for(5, step=0.5)

        self.assertEqual(self.scheduler.stopwatch_elapsed_ms(), 75_000)
        self.assertEqual(self.scheduler.stopwatch.laps[0].lap_split_ms, 10_000)
        self.scheduler.stopwatch_pause()
        self.run_for(30, step=1)
        self.assertEqual(self.scheduler.stopwatch_elapsed_ms(), 75_000)
        self.scheduler.stopwatch_clear()
        self.assertEqual(self.scheduler.stopwatch_elapsed_ms(), 0)


class NotificationButtonTests(SchedulerTestCase):
    """Buttons on GNOME notifications act without opening the window."""

    def test_gate_notification_offers_check_off_and_a_snooze_while_snoozes_remain(self):
        target = self.repo.create_target(short_target(snooze_limit=1))
        self.scheduler.start_target(target.id)
        self.run_for(MIN, step=5)

        self.assertEqual(
            self.notifier.buttons["target-gate"],
            (("Check Off", "app.gate-complete", None), ("Snooze 5 min", "app.gate-snooze", 5)),
        )
        self.scheduler.snooze_session(1)
        self.run_for(MIN, step=5)

        self.assertEqual(self.notifier.button_labels("target-gate"), ["Check Off"])

    def test_alarm_without_a_mission_offers_stop_and_snooze(self):
        alarm = self.repo.create_alarm(alarm_draft(snooze_interval_minutes=10, snooze_limit=None))
        self.scheduler.refresh_alarms()

        self.run_for(5 * MIN, step=1)

        self.assertEqual(
            self.notifier.buttons[f"alarm-{alarm.id}"],
            (("Stop", "app.alarm-stop", alarm.id), ("Snooze 10 min", "app.alarm-snooze", alarm.id)),
        )

    def test_alarm_with_a_mission_opens_the_mission_instead_of_stopping(self):
        alarm = self.repo.create_alarm(alarm_draft(mission_mode="math", snooze_limit=1))
        self.scheduler.refresh_alarms()
        self.run_for(5 * MIN, step=1)

        self.assertEqual(self.notifier.button_labels(f"alarm-{alarm.id}"), ["Open Mission", "Snooze 5 min"])
        self.scheduler.snooze_alarm(alarm.id)
        self.run_for(5 * MIN, step=1)

        self.assertEqual(self.notifier.button_labels(f"alarm-{alarm.id}"), ["Open Mission"])

    def test_ready_notification_offers_start_and_a_rest_while_extensions_remain(self):
        target = self.repo.create_target(short_target(snooze_limit=1))
        self.scheduler.start_target(target.id)
        self.scheduler.complete_subtask()
        self.run_for(MIN, step=5)

        self.assertEqual(
            self.notifier.buttons["target-ready"],
            (("Start Batch 2", "app.ready-start", None), ("Rest 5 min", "app.ready-extend", 5)),
        )
        self.scheduler.extend_rest(1)
        self.assertNotIn("target-ready", self.notifier.active)
        self.run_for(MIN, step=5)

        self.assertEqual(self.notifier.button_labels("target-ready"), ["Start Batch 2"])

    def test_expired_timer_offers_stop_and_restart(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=10))
        self.scheduler.start_timer(timer.id)

        self.run_for(10, step=1)

        self.assertEqual(
            self.notifier.buttons[f"timer-{timer.id}"],
            (("Stop", "app.timer-stop", timer.id), ("Restart", "app.timer-restart", timer.id)),
        )


if __name__ == "__main__":
    unittest.main()
