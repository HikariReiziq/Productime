import unittest

import _bootstrap  # noqa: F401

from productime.domain import stopwatch_engine as sw
from productime.domain.formatters import format_delta, format_stopwatch


class StopwatchStateTests(unittest.TestCase):
    def test_idle_offers_start_with_lap_disabled(self):
        state = sw.initial()

        self.assertEqual(sw.controls(state), ("start", "lap"))
        self.assertFalse(sw.can_lap(state))

    def test_running_offers_pause_and_lap(self):
        state = sw.start(sw.initial(), 1_000)

        self.assertEqual(sw.controls(state), ("pause", "lap"))
        self.assertTrue(sw.can_lap(state))

    def test_paused_offers_resume_and_clear(self):
        state = sw.pause(sw.start(sw.initial(), 1_000), 4_000)

        self.assertEqual(sw.controls(state), ("resume", "clear"))
        self.assertFalse(sw.can_lap(state))

    def test_elapsed_excludes_paused_time(self):
        state = sw.start(sw.initial(), 1_000)
        state = sw.pause(state, 4_000)
        self.assertEqual(sw.elapsed_ms(state, 90_000), 3_000)

        state = sw.resume(state, 100_000)

        self.assertEqual(sw.elapsed_ms(state, 102_500), 5_500)

    def test_clear_returns_to_idle_and_drops_laps(self):
        state = sw.lap(sw.start(sw.initial(), 0), 2_000)

        state = sw.clear(sw.pause(state, 3_000))

        self.assertEqual(state, sw.initial())

    def test_invalid_transitions_are_rejected(self):
        with self.assertRaises(sw.InvalidTransition):
            sw.lap(sw.initial(), 0)
        with self.assertRaises(sw.InvalidTransition):
            sw.pause(sw.initial(), 0)
        with self.assertRaises(sw.InvalidTransition):
            sw.clear(sw.start(sw.initial(), 0))
        with self.assertRaises(sw.InvalidTransition):
            sw.start(sw.start(sw.initial(), 0), 5)

    def test_sleep_gap_is_added_to_a_running_stopwatch_only(self):
        running = sw.start(sw.initial(), 0)
        paused = sw.pause(running, 1_000)

        self.assertEqual(sw.elapsed_ms(sw.add_gap(running, 60_000), 1_000), 61_000)
        self.assertEqual(sw.elapsed_ms(sw.add_gap(paused, 60_000), 5_000), 1_000)


class LapGapTests(unittest.TestCase):
    def test_first_lap_gap_is_measured_from_zero_with_no_delta(self):
        state = sw.lap(sw.start(sw.initial(), 0), 12_340)

        lap = state.laps[0]
        self.assertEqual((lap.number, lap.total_elapsed_ms, lap.lap_split_ms), (1, 12_340, 12_340))
        self.assertIsNone(lap.delta_vs_prev_ms)

    def test_each_lap_records_the_gap_to_the_previous_lap(self):
        state = sw.start(sw.initial(), 0)
        for moment in (10_000, 21_150, 31_880):
            state = sw.lap(state, moment)

        self.assertEqual([lap.lap_split_ms for lap in state.laps], [10_000, 11_150, 10_730])
        self.assertEqual([lap.delta_vs_prev_ms for lap in state.laps], [None, 1_150, -420])
        self.assertEqual([lap.total_elapsed_ms for lap in state.laps], [10_000, 21_150, 31_880])

    def test_lap_gap_ignores_time_spent_paused(self):
        state = sw.lap(sw.start(sw.initial(), 0), 5_000)
        state = sw.resume(sw.pause(state, 6_000), 60_000)

        state = sw.lap(state, 62_000)

        self.assertEqual(state.laps[1].lap_split_ms, 3_000)

    def test_formatting_matches_the_lap_table(self):
        self.assertEqual(format_stopwatch(0), "00:00:00.00")
        self.assertEqual(format_stopwatch(3_723_450), "01:02:03.45")
        self.assertEqual(format_delta(1_150), "+1.15s")
        self.assertEqual(format_delta(-420), "-0.42s")
        self.assertEqual(format_delta(0), "+0.00s")


if __name__ == "__main__":
    unittest.main()
