import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edgar_events as ee  # noqa: E402


class Parse(unittest.TestCase):
    def test_only_8k_forms_after_since(self):
        block = {"form": ["8-K", "10-Q", "8-K/A", "8-K"],
                 "acceptanceDateTime": ["2024-05-02T20:15:00.000Z", "2024-05-03T10:00:00.000Z", "2024-06-01T12:00:00.000Z", "2012-01-01T12:00:00.000Z"],
                 "filingDate": ["2024-05-02", "2024-05-03", "2024-06-01", "2012-01-01"],
                 "items": ["2.02,9.01", "", "5.02", "1.01"]}
        rows = ee.rows_from_block(block, "AAPL", "2015-01-01")
        self.assertEqual([r["items"] for r in rows], ["2.02,9.01", "5.02"])

    def test_missing_acceptance_falls_back_to_filing_date(self):
        rows = ee.rows_from_block({"form": ["8-K"], "filingDate": ["2020-01-02"], "items": ["8.01"]}, "X")
        self.assertTrue(rows[0]["accepted"].startswith("2020-01-02"))

    def test_empty_block(self):
        self.assertEqual(ee.rows_from_block(None, "X"), [])


if __name__ == "__main__":
    unittest.main()
