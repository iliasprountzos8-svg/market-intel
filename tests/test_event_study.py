import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import event_study as es  # noqa: E402


class Timing(unittest.TestCase):
    def test_after_close_filing_reacts_next_session(self):
        sessions = pd.to_datetime(["2024-05-01", "2024-05-02", "2024-05-03"])
        # 20:15 UTC = 16:15 New York (after the bell) on May 1 -> reaction session is May 2
        # 13:00 UTC = 09:00 New York (before the open) on May 1 -> reaction session is May 1
        acc = pd.Series(pd.to_datetime(["2024-05-01T20:15:00Z", "2024-05-01T13:00:00Z"], utc=True))
        self.assertEqual(list(es.reaction_index(acc, sessions)), [1, 0])


try:
    import scipy  # noqa: F401
except ImportError:
    scipy = None


@unittest.skipIf(scipy is None, "scipy only in the lab venv")
class Stats(unittest.TestCase):
    def test_cluster_se_larger_when_events_bunch(self):
        rng = np.random.default_rng(0)
        shock = rng.normal(0, 1.0, 4).repeat(100)  # events on the same date share a common move
        x = 0.3 + shock + rng.normal(0, 0.3, 400)
        spread = es.clustered_stats(x, np.arange(400))          # every event its own date
        bunched = es.clustered_stats(x, np.arange(400) // 100)  # 4 dates only
        self.assertLess(abs(bunched["t"]), abs(spread["t"]))

    def test_too_few_events_returns_none(self):
        self.assertIsNone(es.clustered_stats(np.ones(10), np.arange(10)))


class Overlaps(unittest.TestCase):
    def test_repeat_inside_window_dropped(self):
        df = pd.DataFrame({"symbol": ["A", "A", "A"], "group": ["any"] * 3, "h": [5] * 3,
                           "entry": pd.to_datetime(["2024-01-02", "2024-01-04", "2024-02-01"]), "excess": [1, 1, 1], "react": [0, 0, 0]})
        self.assertEqual(len(es.drop_overlaps(df)), 2)


if __name__ == "__main__":
    unittest.main()
