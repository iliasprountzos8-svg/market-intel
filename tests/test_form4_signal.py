import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import form4_signal as fs  # noqa: E402


def txn(symbol, code, shares, price):
    return {"symbol": symbol, "transaction_code": code, "shares": shares, "price_per_share": price}


class ComputeDailyIndex(unittest.TestCase):
    def test_all_buys_gives_plus_one(self):
        out = fs.compute_daily_index([txn("NVDA", "P", 100, 10.0), txn("NVDA", "P", 50, 20.0)])
        self.assertAlmostEqual(out["NVDA"][0], 1.0)
        self.assertEqual(out["NVDA"][1], 2)

    def test_all_sells_gives_minus_one(self):
        out = fs.compute_daily_index([txn("NVDA", "S", 100, 10.0)])
        self.assertAlmostEqual(out["NVDA"][0], -1.0)

    def test_mixed_buy_sell_dollar_weighted(self):
        # buy $1000, sell $500 -> (1000-500)/1500
        out = fs.compute_daily_index([txn("NVDA", "P", 100, 10.0), txn("NVDA", "S", 50, 10.0)])
        self.assertAlmostEqual(out["NVDA"][0], 500 / 1500)
        self.assertEqual(out["NVDA"][1], 2)

    def test_non_ps_codes_are_ignored(self):
        out = fs.compute_daily_index([txn("NVDA", "A", 1000, 0.0), txn("NVDA", "G", 500, 0.0)])
        self.assertEqual(out, {})

    def test_missing_shares_or_price_ignored(self):
        out = fs.compute_daily_index([txn("NVDA", "P", None, 10.0), txn("NVDA", "P", 100, None)])
        self.assertEqual(out, {})

    def test_multiple_symbols_kept_separate(self):
        out = fs.compute_daily_index([txn("NVDA", "P", 100, 10.0), txn("MSFT", "S", 100, 10.0)])
        self.assertAlmostEqual(out["NVDA"][0], 1.0)
        self.assertAlmostEqual(out["MSFT"][0], -1.0)

    def test_empty_input(self):
        self.assertEqual(fs.compute_daily_index([]), {})


if __name__ == "__main__":
    unittest.main()
