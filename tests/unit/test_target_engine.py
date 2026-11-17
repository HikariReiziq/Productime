import unittest
from datetime import datetime

import _bootstrap  # noqa: F401

from productime.domain import target_engine as engine
from productime.domain.formatters import format_clock_range, format_hm, format_range
from productime.domain.models import TargetConfig, TargetPhase

MIN = 60_000

EXAMPLE_1 = TargetConfig(
    target_total_minutes=360,
    work_minutes=50,
    short_break_minutes=10,
    long_rest_enabled=True,
    long_rest_every=3,
    long_rest_minutes=30,
)
EXAMPLE_2 = TargetConfig(
    target_total_minutes=240,
    work_minutes=25,
    short_break_minutes=5,
    long_rest_enabled=True,
    long_rest_every=4,
    long_rest_minutes=30,
)


def config(total, work, short, long_every=None, long_minutes=30):
    return TargetConfig(
        target_total_minutes=total,
        work_minutes=work,
        short_break_minutes=short,
        long_rest_enabled=long_every is not None,
        long_rest_every=long_every or 3,
        long_rest_minutes=long_minutes,
    )


def session(cfg, snooze_limit=None):
    slots = engine.build_timeline(cfg)
    return engine.start_session(engine.plans_from_slots(slots), snooze_limit)


class TimelineProjectionTests(unittest.TestCase):
    def test_example_1_six_hours_50_10_long_rest_every_3_totals_6h_40m(self):
        slots = engine.build_timeline(EXAMPLE_1)

        work = [(s.cumulative_start_minute, s.cumulative_end_minute) for s in slots]
        rests = [(s.cumulative_end_minute, s.break_end_minute, s.break_type) for s in slots]
        self.assertEqual(work, [(0, 50), (60, 110), (120, 170), (200, 250), (260, 310), (320, 370)])
        self.assertEqual(
            rests,
            [
                (50, 60, "short"),
                (110, 120, "short"),
                (170, 200, "long"),
                (250, 260, "short"),
                (310, 320, "short"),
                (370, 400, "long"),
            ],
        )
        self.assertEqual(engine.total_minutes(slots), 400)
        self.assertEqual(format_hm(engine.total_minutes(slots)), "6h 40m")
        self.assertEqual(format_range(370, 400), "06:10 -> 06:40")

    def test_example_2_four_hours_25_5_long_rest_every_4_totals_4h_50m(self):
        slots = engine.build_timeline(EXAMPLE_2)

        self.assertEqual(len(slots), 8)
        self.assertEqual(
            [(s.cumulative_start_minute, s.cumulative_end_minute) for s in slots],
            [(0, 25), (30, 55), (60, 85), (90, 115), (145, 170), (175, 200), (205, 230), (235, 260)],
        )
        self.assertEqual([s.break_type for s in slots].count("long"), 2)
        self.assertEqual((slots[3].break_type, slots[3].break_minutes), ("long", 30))
        self.assertEqual((slots[7].break_type, slots[7].break_end_minute), ("long", 290))
        self.assertEqual(format_hm(engine.total_minutes(slots)), "4h 50m")
        self.assertEqual(format_range(260, 290), "04:20 -> 04:50")

    def test_subtask_titles_attach_to_their_batches(self):
        titles = ["Membuat PRD", "Membuat desain", "Coding backend"]
        slots = engine.build_timeline(EXAMPLE_1, titles)

        self.assertEqual([s.subtask_title for s in slots[:3]], titles)
        self.assertEqual(slots[5].subtask_title, "")
        self.assertEqual([s.batch_index for s in slots], [1, 2, 3, 4, 5, 6])

    def test_remainder_appends_one_partial_final_batch(self):
        slots = engine.build_timeline(config(100, 25, 5))

        self.assertEqual([s.work_minutes for s in slots], [25, 25, 25, 10])
        self.assertEqual(slots[-1].break_type, "short")
        self.assertEqual(engine.total_minutes(slots), 105)

    def test_remainder_longer_than_work_is_capped_at_work_minutes(self):
        slots = engine.build_timeline(config(58, 25, 5))

        self.assertEqual([s.work_minutes for s in slots], [25, 25])

    def test_target_shorter_than_one_cycle_still_gets_one_batch(self):
        slots = engine.build_timeline(config(20, 25, 5))

        self.assertEqual([s.work_minutes for s in slots], [20])

    def test_zero_short_break_has_no_break_slot(self):
        slots = engine.build_timeline(config(100, 50, 0))

        self.assertEqual([(s.break_type, s.break_minutes) for s in slots], [("none", 0)] * 2)
        self.assertEqual(engine.total_minutes(slots), 100)

    def test_long_rest_applies_even_when_short_break_is_zero(self):
        slots = engine.build_timeline(config(100, 50, 0, long_every=2, long_minutes=15))

        self.assertEqual([s.break_type for s in slots], ["none", "long"])
        self.assertEqual(engine.total_minutes(slots), 115)

    def test_long_rest_every_one_replaces_every_break(self):
        slots = engine.build_timeline(config(60, 25, 5, long_every=1, long_minutes=20))

        self.assertEqual([s.break_minutes for s in slots], [20, 20])


class ClockRangeTests(unittest.TestCase):
    def test_clock_ranges_follow_the_desktop_clock_format(self):
        start, end = datetime(2026, 11, 17, 13, 5), datetime(2026, 11, 17, 13, 55)

        self.assertEqual(format_clock_range(start, end), "13:05 -> 13:55")
        self.assertEqual(format_clock_range(start, end, twenty_four=False), "1:05 PM -> 1:55 PM")
        self.assertEqual(format_clock_range(datetime(2026, 1, 1, 0, 7), end, False), "12:07 AM -> 1:55 PM")


class SubtaskGateTests(unittest.TestCase):
    def test_work_expiry_waits_at_the_gate_instead_of_resting(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)

        self.assertEqual(state.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(state.worked_ms, 50 * MIN)

    def test_time_passing_at_the_gate_never_starts_the_rest(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)

        state = engine.advance(state, 6 * 60 * MIN)

        self.assertEqual(state.phase, TargetPhase.AWAITING_SUBTASK_GATE)

    def test_checking_off_early_moves_straight_into_rest(self):
        state = engine.advance(session(EXAMPLE_1), 12 * MIN)

        state = engine.complete_subtask(state)

        self.assertEqual(state.phase, TargetPhase.RESTING)
        self.assertEqual(state.remaining_ms, 10 * MIN)
        self.assertEqual(state.batch_index, 1)

    def test_checking_off_at_the_gate_starts_the_rest(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)

        state = engine.complete_subtask(state)

        self.assertEqual(state.phase, TargetPhase.RESTING)

    def test_rest_expiry_waits_for_the_user_before_the_next_batch(self):
        state = engine.complete_subtask(session(EXAMPLE_1))

        state = engine.advance(state, 10 * MIN)

        self.assertEqual((state.phase, state.batch_index), (TargetPhase.READY_FOR_NEXT, 1))
        self.assertEqual(engine.advance(state, 60 * MIN), state)
        state = engine.start_next_batch(state)
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.WORKING, 2))
        self.assertEqual((state.worked_ms, state.snooze_count, state.remaining_ms), (0, 0, 50 * MIN))

    def test_third_batch_gets_the_long_rest(self):
        state = session(EXAMPLE_1)
        for _ in range(2):
            state = engine.start_next_batch(engine.advance(engine.complete_subtask(state), 10 * MIN))

        state = engine.complete_subtask(state)

        self.assertEqual((state.batch_index, state.remaining_ms), (3, 30 * MIN))

    def test_snoozing_cannot_reach_rest_without_check_off(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)

        state = engine.snooze(state, 5)
        self.assertEqual((state.phase, state.remaining_ms), (TargetPhase.SNOOZING, 5 * MIN))
        state = engine.advance(state, 5 * MIN)

        self.assertEqual(state.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(state.snoozed_ms, 5 * MIN)

    def test_custom_snooze_minutes_are_honoured(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)

        state = engine.snooze(state, 7)

        self.assertEqual(state.remaining_ms, 7 * MIN)

    def test_snooze_is_only_offered_at_the_gate(self):
        with self.assertRaises(engine.InvalidTransition):
            engine.snooze(session(EXAMPLE_1), 5)

    def test_snooze_limit_is_enforced_for_every_configured_value(self):
        for limit in (1, 2, 3, 5, 10):
            with self.subTest(limit=limit):
                state = engine.advance(session(EXAMPLE_1, snooze_limit=limit), 50 * MIN)
                for _ in range(limit):
                    self.assertTrue(state.can_snooze)
                    state = engine.advance(engine.snooze(state, 1), MIN)

                self.assertFalse(state.can_snooze)
                self.assertEqual(state.snoozes_left, 0)
                with self.assertRaises(engine.InvalidTransition):
                    engine.snooze(state, 1)

    def test_unlimited_snooze_never_locks(self):
        state = engine.advance(session(EXAMPLE_1), 50 * MIN)
        for _ in range(25):
            state = engine.advance(engine.snooze(state, 1), MIN)

        self.assertTrue(state.can_snooze)
        self.assertIsNone(state.snoozes_left)

    def test_snooze_count_resets_for_the_next_batch(self):
        state = engine.advance(session(EXAMPLE_1, snooze_limit=1), 50 * MIN)
        state = engine.advance(engine.snooze(state, 1), MIN)

        state = engine.advance(engine.complete_subtask(state), 10 * MIN)
        state = engine.advance(engine.start_next_batch(state), 50 * MIN)

        self.assertTrue(state.can_snooze)

    def test_pause_and_resume_keep_the_remaining_time(self):
        state = engine.advance(session(EXAMPLE_1), 20 * MIN)

        paused = engine.pause(state)
        self.assertEqual(paused.phase, TargetPhase.PAUSED_WORK)
        self.assertEqual(engine.advance(paused, 30 * MIN).remaining_ms, 30 * MIN)
        resumed = engine.resume(paused)

        self.assertEqual((resumed.phase, resumed.remaining_ms), (TargetPhase.WORKING, 30 * MIN))

    def test_paused_snooze_resumes_as_snooze(self):
        state = engine.snooze(engine.advance(session(EXAMPLE_1), 50 * MIN), 5)

        resumed = engine.resume(engine.pause(state))

        self.assertEqual(resumed.phase, TargetPhase.SNOOZING)

    def test_rest_can_be_paused_and_resumed(self):
        state = engine.complete_subtask(session(EXAMPLE_1))

        paused = engine.pause(state)
        self.assertEqual(paused.phase, TargetPhase.PAUSED_REST)

        self.assertEqual(engine.resume(paused).phase, TargetPhase.RESTING)

    def test_resume_restores_a_session_from_saved_work(self):
        plans = engine.plans_from_slots(engine.build_timeline(EXAMPLE_1))

        state = engine.start_session(plans, None, batch_index=2, worked_ms=20 * MIN)

        self.assertEqual((state.batch_index, state.remaining_ms), (2, 30 * MIN))


class NextBatchTests(unittest.TestCase):
    def resting(self, snooze_limit=None):
        return engine.complete_subtask(session(EXAMPLE_1, snooze_limit))

    def test_checking_during_a_rest_skips_the_rest_of_the_break(self):
        for state in (self.resting(), engine.pause(self.resting())):
            state = engine.advance(state, 3 * MIN)

            state = engine.start_next_batch(state)

            self.assertEqual(
                (state.phase, state.batch_index, state.remaining_ms), (TargetPhase.WORKING, 2, 50 * MIN)
            )

    def test_next_batch_cannot_start_from_work_or_the_gate(self):
        working = session(EXAMPLE_1)
        for state in (working, engine.pause(working), engine.advance(working, 50 * MIN)):
            with self.assertRaises(engine.InvalidTransition):
                engine.start_next_batch(state)

    def test_extending_the_rest_counts_against_the_snooze_limit(self):
        ready = engine.advance(self.resting(snooze_limit=2), 10 * MIN)
        self.assertEqual((ready.extensions_left, ready.can_extend_rest), (2, True))

        state = engine.extend_rest(ready, 5)
        self.assertEqual(
            (state.phase, state.remaining_ms, state.phase_total_ms), (TargetPhase.RESTING, 5 * MIN, 5 * MIN)
        )
        state = engine.extend_rest(engine.advance(state, 5 * MIN), 5)
        state = engine.advance(state, 5 * MIN)

        self.assertEqual(
            (state.phase, state.extensions_left, state.can_extend_rest),
            (TargetPhase.READY_FOR_NEXT, 0, False),
        )
        with self.assertRaises(engine.InvalidTransition):
            engine.extend_rest(state, 5)

    def test_unlimited_snooze_allows_unlimited_extensions(self):
        state = engine.advance(self.resting(), 10 * MIN)
        for _ in range(12):
            state = engine.advance(engine.extend_rest(state, 5), 5 * MIN)

        self.assertTrue(state.can_extend_rest)
        self.assertIsNone(state.extensions_left)

    def test_rest_can_only_be_extended_once_it_is_over(self):
        with self.assertRaises(engine.InvalidTransition):
            engine.extend_rest(self.resting(), 5)
        with self.assertRaises(engine.InvalidTransition):
            engine.extend_rest(engine.advance(self.resting(), 10 * MIN), 0)

    def test_extensions_start_again_at_the_next_break(self):
        state = engine.extend_rest(engine.advance(self.resting(snooze_limit=1), 10 * MIN), 5)
        state = engine.start_next_batch(state)

        state = engine.advance(engine.complete_subtask(state), 10 * MIN)

        self.assertEqual(state.extensions_left, 1)

    def test_waiting_to_start_cannot_be_paused_or_checked_off(self):
        ready = engine.advance(self.resting(), 10 * MIN)
        for event in (engine.pause, engine.complete_subtask, engine.finish_target):
            with self.assertRaises(engine.InvalidTransition):
                event(ready)


class SuspendForRestartTests(unittest.TestCase):
    """What a session looks like when the app reopens: everything exact, nothing counting."""

    def test_work_and_snooze_keep_their_exact_remaining_time(self):
        working = engine.advance(session(EXAMPLE_1), 12 * MIN + 345)
        snoozing = engine.advance(engine.snooze(engine.advance(working, 38 * MIN - 345), 10), 4 * MIN)

        for state, resume_phase, remaining in (
            (working, TargetPhase.WORKING, 38 * MIN - 345),
            (snoozing, TargetPhase.SNOOZING, 6 * MIN),
        ):
            frozen = engine.suspend(state)
            self.assertEqual((frozen.phase, frozen.remaining_ms), (TargetPhase.PAUSED_WORK, remaining))
            self.assertEqual(engine.resume(frozen).phase, resume_phase)

    def test_a_waiting_gate_reopens_quietly_and_returns_to_the_gate(self):
        gate = engine.advance(session(EXAMPLE_1), 50 * MIN)

        frozen = engine.suspend(gate)

        self.assertEqual(frozen.phase, TargetPhase.PAUSED_WORK)
        self.assertEqual(engine.resume(frozen).phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(engine.complete_subtask(frozen).phase, TargetPhase.RESTING)

    def test_a_rest_keeps_its_remaining_time(self):
        state = engine.advance(engine.complete_subtask(session(EXAMPLE_1)), 4 * MIN)

        frozen = engine.suspend(state)

        self.assertEqual((frozen.phase, frozen.remaining_ms), (TargetPhase.PAUSED_REST, 6 * MIN))

    def test_waiting_to_start_reopens_as_the_next_batch_paused(self):
        ready = engine.advance(engine.complete_subtask(session(EXAMPLE_1)), 10 * MIN)

        frozen = engine.suspend(ready)

        self.assertEqual(
            (frozen.phase, frozen.batch_index, frozen.remaining_ms), (TargetPhase.PAUSED_WORK, 2, 50 * MIN)
        )

    def test_paused_and_completed_sessions_stay_as_they_are(self):
        paused = engine.pause(session(EXAMPLE_1))
        self.assertEqual(engine.suspend(paused), paused)


class FinalBatchTests(unittest.TestCase):
    def final_batch_state(self, cfg=EXAMPLE_1):
        state = session(cfg)
        for _ in range(state.batch_count - 1):
            state = engine.complete_subtask(state)
            state = (
                engine.start_next_batch(engine.advance(state, state.remaining_ms))
                if state.phase is TargetPhase.RESTING
                else state
            )
        return state

    def test_final_check_off_enters_the_scheduled_final_rest(self):
        state = engine.complete_subtask(self.final_batch_state())

        self.assertEqual(state.phase, TargetPhase.RESTING)
        self.assertTrue(state.in_final_rest)
        self.assertEqual(state.remaining_ms, 30 * MIN)

    def test_finish_target_skips_the_final_rest(self):
        state = engine.complete_subtask(self.final_batch_state())

        self.assertEqual(engine.finish_target(state).phase, TargetPhase.COMPLETED)

    def test_final_rest_running_out_completes_the_target(self):
        state = engine.complete_subtask(self.final_batch_state())

        self.assertEqual(engine.advance(state, 30 * MIN).phase, TargetPhase.COMPLETED)

    def test_final_batch_without_break_completes_on_check_off(self):
        state = engine.complete_subtask(self.final_batch_state(config(100, 50, 0)))

        self.assertEqual(state.phase, TargetPhase.COMPLETED)

    def test_finish_target_is_rejected_before_the_final_rest(self):
        with self.assertRaises(engine.InvalidTransition):
            engine.finish_target(engine.complete_subtask(session(EXAMPLE_1)))
        with self.assertRaises(engine.InvalidTransition):
            engine.finish_target(self.final_batch_state())

    def test_the_final_rest_cannot_be_skipped_into_a_batch_that_does_not_exist(self):
        with self.assertRaises(engine.InvalidTransition):
            engine.start_next_batch(engine.complete_subtask(self.final_batch_state()))

    def test_completed_session_rejects_further_events(self):
        done = engine.finish_target(engine.complete_subtask(self.final_batch_state()))

        with self.assertRaises(engine.InvalidTransition):
            engine.complete_subtask(done)
        self.assertEqual(engine.advance(done, MIN), done)


if __name__ == "__main__":
    unittest.main()
