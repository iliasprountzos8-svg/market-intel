import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gdelt_news as gn  # noqa: E402

SP = [{"symbol": "AAPL", "name": "Apple Inc."}, {"symbol": "MSFT", "name": "Microsoft"}, {"symbol": "TGT", "name": "Target Corporation"},
      {"symbol": "GOOGL", "name": "Alphabet Inc. (Class A)"}, {"symbol": "GS", "name": "Goldman Sachs Group"}]


def rec(orgs, tone, n=1):
    return "\t".join(["20260901", str(n), "", "", "", "", orgs, tone, "", "x.com", "http://x"]) + "\n"


class Names(unittest.TestCase):
    def test_normalise_strips_corporate_suffixes(self):
        self.assertEqual(gn.norm_name("Apple Inc."), "apple")
        self.assertEqual(gn.norm_name("Goldman Sachs Group"), "goldman sachs")

    def test_generic_names_skipped_and_aliases_added(self):
        m = gn.alias_map(SP)
        self.assertNotIn("target", m)
        self.assertEqual(m["google"], "GOOGL")
        self.assertEqual(m["apple"], "AAPL")


class Parse(unittest.TestCase):
    def test_counts_tone_and_total(self):
        data = rec("apple inc;microsoft", "-2.0,1.0,3.0,4.0") + rec("apple", "4.0,5.0,1.0,6.0", n=3) + rec("someone else", "0,0,0,0")
        agg, total = gn.parse_day(io.BytesIO(data.encode()), gn.alias_map(SP))
        self.assertEqual(total, 5)
        self.assertEqual(agg["AAPL"][0], 4)                         # 1 + 3 articles
        self.assertAlmostEqual(agg["AAPL"][1] / agg["AAPL"][0], (-2.0 * 1 + 4.0 * 3) / 4)
        self.assertEqual(agg["MSFT"][0], 1)

    def test_org_counted_once_per_record(self):
        agg, _ = gn.parse_day(io.BytesIO(rec("apple;apple inc;apple", "1,1,1,1").encode()), gn.alias_map(SP))
        self.assertEqual(agg["AAPL"][0], 1)

    def test_bad_rows_skipped(self):
        agg, total = gn.parse_day(io.BytesIO(b"DATE\tNUMARTS\nbroken\n"), gn.alias_map(SP))
        self.assertEqual((agg, total), ({}, 0))


if __name__ == "__main__":
    unittest.main()
