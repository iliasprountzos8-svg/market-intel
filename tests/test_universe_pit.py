import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab"))
import universe_pit as up  # noqa: E402


class PointInTime(unittest.TestCase):
    def table(self):
        cols = pd.MultiIndex.from_tuples([("Effective Date", "Effective Date"), ("Added", "Ticker"), ("Added", "Security"),
                                          ("Removed", "Ticker"), ("Removed", "Security"), ("Reason", "Reason")])
        return pd.DataFrame([["Effective Date", "Ticker", "Security", "Ticker", "Security", "Reason"],
                             ["September 21, 2026", "BE", "Bloom", "TAP", "Molson", "x"],
                             ["March 3, 2024", "BRK.B", "Berkshire", None, None, "y"]], columns=cols)

    def test_parse_skips_header_rows_and_normalises(self):
        ch = up.parse_changes(self.table())
        self.assertEqual([c["added"] for c in ch], ["BE", "BRK-B"])
        self.assertEqual(ch[0]["removed"], "TAP")

    def test_mask_blocks_dates_before_add(self):
        starts = up.membership_starts(up.parse_changes(self.table()))
        idx = pd.MultiIndex.from_product([pd.to_datetime(["2026-09-01", "2026-10-01"]), ["BE", "AAPL"]], names=["date", "symbol"])
        got = dict(zip(idx, up.pit_mask(idx, starts)))
        self.assertFalse(got[(pd.Timestamp("2026-09-01"), "BE")])   # before it joined
        self.assertTrue(got[(pd.Timestamp("2026-10-01"), "BE")])
        self.assertTrue(got[(pd.Timestamp("2026-09-01"), "AAPL")])  # no recorded add -> always member

    def test_coverage_counts_missing_prices(self):
        cov = up.coverage(up.parse_changes(self.table()), ["BE"], "2020-01-01")
        self.assertEqual(cov["removed_without_prices"], 1)


if __name__ == "__main__":
    unittest.main()
