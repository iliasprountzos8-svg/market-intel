import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import scoring  # noqa: E402
import cli  # noqa: E402

# Mon 21 Sep .. Fri 2 Oct 2026 closes (trading days only)
DAYS = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25",
                       "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
SERIES = pd.Series([100, 101, 102, 103, 92.41, 91, 90, 90.42, 92.97, 93], index=DAYS)


def fake_history(symbol, start, end):
    return SERIES


class WeekendWindow(unittest.TestCase):
    """Two calls made on the same Saturday must share ONE window (was: 8h UTC and 22h UTC scored
    over different exits, so identical oil calls got opposite verdicts)."""

    def run_window(self, created):
        with patch.object(scoring, "_cached_history", fake_history), \
             patch.object(scoring, "datetime") as dt:
            dt.now.return_value = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
            dt.side_effect = datetime
            return scoring.window_return("CL=F", created, 5)

    def test_same_saturday_same_window(self):
        a = self.run_window(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc))
        b = self.run_window(datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(a["entry_date"], "2026-09-25")
        self.assertEqual((a["entry_date"], a["exit_date"], a["exit_price"]),
                         (b["entry_date"], b["exit_date"], b["exit_price"]))
        self.assertAlmostEqual(a["return_pct"], (90.42 - 92.41) / 92.41 * 100, places=3)

    def test_open_window_is_not_scored(self):
        self.assertIsNone(self.run_window(datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)))


class WhyNoResult(unittest.TestCase):
    """An open window is normal; missing prices for an elapsed window is a real failure and the only
    case check_outcomes should alert on (it used to cry 'yfinance problem' for every open call)."""

    def run_result(self, created, history):
        with patch.object(scoring, "_cached_history", history), patch.object(scoring, "datetime") as dt:
            dt.now.return_value = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
            dt.side_effect = datetime
            return scoring.window_result("CL=F", created, 5)

    def test_open_window_is_open_not_a_failure(self):
        r = self.run_result(datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc), fake_history)
        self.assertEqual(r["reason"], "open")

    def test_elapsed_window_without_prices_is_no_data(self):
        r = self.run_result(datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc), lambda *a, **k: None)
        self.assertEqual(r["reason"], "no_data")

    def test_recent_call_without_prices_is_not_flagged(self):
        r = self.run_result(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc), lambda *a, **k: None)
        self.assertEqual(r["reason"], "open")

    def test_elapsed_window_scores(self):
        r = self.run_result(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc), fake_history)
        self.assertEqual(r["reason"], "ok")


class Judge(unittest.TestCase):
    def w(self, ret, bp=0):
        return {"return_pct": ret, "change_bp": bp}

    def test_stock_judged_on_excess(self):
        # MSFT -0.63% looks right for a bearish call, but VT fell more: it outperformed.
        v, basis = scoring.judge("bearish", "MSFT", self.w(-0.63), self.w(-1.36))
        self.assertEqual((v, basis), ("incorrect", "excess"))

    def test_yield_judged_in_bp(self):
        self.assertEqual(scoring.judge("bullish", "^TNX", self.w(0.4, 2.0))[0], "unclear")
        self.assertEqual(scoring.judge("bullish", "^TNX", self.w(1.0, 5.2))[0], "correct")

    def test_commodity_absolute(self):
        self.assertEqual(scoring.judge("bullish", "CL=F", self.w(-2.15))[0], "incorrect")

    def test_symbol_resolution(self):
        self.assertEqual(scoring.resolve_symbol("^TNX"), "^TNX")
        self.assertEqual(scoring.resolve_symbol("LNG"), "LNG")


class DistinctViews(unittest.TestCase):
    def test_relogged_views_count_once(self):
        row = dict(symbol="MSFT", call="bearish", outcome="incorrect", entry_price=516.17, exit_price=512.9)
        rows = [dict(row, id=i) for i in range(4)] + [dict(symbol="MSFT", call="bearish", outcome=None)]
        self.assertEqual(len(scoring.distinct_views(rows)), 2)  # one scored view + the open one


class LogCallDuplicateGuard(unittest.TestCase):
    def client(self, rows):
        c = MagicMock()
        q = c.table.return_value.select.return_value.eq.return_value.gte.return_value
        q.execute.return_value.data = rows
        return c

    def test_open_duplicate_found(self):
        c = self.client([{"id": "x", "created_at": "2026-09-28T06:00", "symbol": "CL=F", "ticker_or_theme": "oil"}])
        self.assertIsNotNone(cli.find_recent_duplicate(c, "CL=F", "WTI oil", "bullish", 5))

    def test_other_symbol_not_duplicate(self):
        c = self.client([{"id": "x", "created_at": "2026-09-28T06:00", "symbol": "NVDA", "ticker_or_theme": "nvda"}])
        self.assertIsNone(cli.find_recent_duplicate(c, "CL=F", "WTI oil", "bullish", 5))


if __name__ == "__main__":
    unittest.main()
