import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "money-intel"))
from categories import categorize, normalize_merchant  # noqa: E402
from forecast import forecast_month  # noqa: E402
from recurring import detect_recurring  # noqa: E402


class Categorize(unittest.TestCase):
    def test_known_merchant_matches(self):
        self.assertEqual(categorize("SKLAVENITIS 1234 THESSALONIKI"), "groceries")
        self.assertEqual(categorize("NETFLIX.COM"), "subscriptions")
        self.assertEqual(categorize("SALARY PAYROLL DEPOSIT"), "income")

    def test_unknown_merchant_is_uncategorized(self):
        self.assertEqual(categorize("XQZZY RANDOM STORE 998877"), "uncategorized")

    def test_normalize_strips_numeric_noise(self):
        self.assertEqual(normalize_merchant("SKLAVENITIS 00123456 25/09/2026"), "sklavenitis")


class Recurring(unittest.TestCase):
    def test_monthly_charge_detected_active(self):
        txns = [{"txn_date": date(2026, m, 5), "merchant_norm": "netflix", "amount": -12.99}
                for m in (6, 7, 8, 9)]
        out = detect_recurring(txns, as_of=date(2026, 9, 10))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["merchant_norm"], "netflix")
        self.assertEqual(out[0]["status"], "active")
        self.assertAlmostEqual(out[0]["typical_amount"], -12.99)

    def test_lapsed_subscription_flagged(self):
        txns = [{"txn_date": date(2026, m, 5), "merchant_norm": "gymfee", "amount": -30.0}
                for m in (3, 4, 5)]
        out = detect_recurring(txns, as_of=date(2026, 9, 10))
        self.assertEqual(out[0]["status"], "lapsed")

    def test_irregular_grocery_trips_not_flagged(self):
        txns = [
            {"txn_date": date(2026, 9, 1), "merchant_norm": "sklavenitis", "amount": -22.10},
            {"txn_date": date(2026, 9, 3), "merchant_norm": "sklavenitis", "amount": -8.40},
            {"txn_date": date(2026, 9, 9), "merchant_norm": "sklavenitis", "amount": -41.00},
        ]
        self.assertEqual(detect_recurring(txns, as_of=date(2026, 9, 10)), [])

    def test_too_few_occurrences_not_flagged(self):
        txns = [{"txn_date": date(2026, 7, 5), "merchant_norm": "netflix", "amount": -12.99},
                {"txn_date": date(2026, 8, 5), "merchant_norm": "netflix", "amount": -12.99}]
        self.assertEqual(detect_recurring(txns, as_of=date(2026, 9, 10)), [])


class Forecast(unittest.TestCase):
    def test_not_enough_data_says_so(self):
        result = forecast_month({date(2026, 9, 1): 10.0}, today=date(2026, 9, 3))
        self.assertIn("note", result)

    def test_projects_with_confidence_band(self):
        daily = {date(2026, 9, d): 10.0 for d in range(1, 11)}
        result = forecast_month(daily, today=date(2026, 9, 10))
        self.assertNotIn("note", result)
        self.assertEqual(result["mtd_total"], 100.0)
        self.assertGreater(result["projected_total"], result["mtd_total"])
        lo, hi = result["projected_ci95"]
        self.assertLessEqual(lo, result["projected_total"])
        self.assertGreaterEqual(hi, result["projected_total"])


if __name__ == "__main__":
    unittest.main()
