import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import calibration as cal  # noqa: E402


class Brier(unittest.TestCase):
    def test_perfect_and_worst(self):
        self.assertEqual(cal.brier([(1.0, 1), (0.0, 0)]), 0.0)
        self.assertEqual(cal.brier([(1.0, 0), (0.0, 1)]), 1.0)

    def test_always_fifty_percent_scores_a_quarter(self):
        self.assertAlmostEqual(cal.brier([(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]), 0.25)

    def test_empty(self):
        self.assertIsNone(cal.brier([]))


class Bins(unittest.TestCase):
    def test_overconfidence_is_visible(self):
        pairs = [(0.9, 1), (0.9, 0), (0.9, 0), (0.9, 0), (0.3, 0)]
        b = {r["range"]: r for r in cal.bins(pairs)}
        self.assertAlmostEqual(b["80%-100%"]["stated"], 0.9)
        self.assertAlmostEqual(b["80%-100%"]["hit_rate"], 0.25)

    def test_report_warns_when_sample_is_small(self):
        txt = cal.report([(0.7, 1), (0.6, 0)])
        self.assertIn("anecdote", txt)
        self.assertIn("Brier", txt)

    def test_report_with_nothing_scoreable(self):
        self.assertIn("no scoreable calls yet", cal.report([], n_unclear=3, n_no_conf=17))


if __name__ == "__main__":
    unittest.main()
