import unittest

import _bootstrap  # noqa: F401

from productime.ui.views.report.productivity_chart import (
    GRID_LINES,
    LABEL_GAP,
    PAD_LEFT,
    plot_left,
    scale_top,
)


class ChartScaleTests(unittest.TestCase):
    def test_every_grid_line_lands_on_a_whole_minute(self):
        for max_seconds in (1, 60, 899, 901, 1800, 3601, 7300, 20000, 50000, 90000, 200000):
            top = scale_top(max_seconds)
            with self.subTest(max_seconds=max_seconds):
                self.assertGreaterEqual(top, max_seconds)
                self.assertEqual(top % (GRID_LINES * 60), 0)


    def test_the_plot_starts_right_of_the_widest_axis_label(self):
        self.assertEqual(plot_left([18, 24]), PAD_LEFT)
        left = plot_left([20, 61, 30])
        self.assertGreaterEqual(left - LABEL_GAP, 61)


if __name__ == "__main__":
    unittest.main()
