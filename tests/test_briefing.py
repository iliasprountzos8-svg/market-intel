import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import briefing  # noqa: E402

WATCH = [{"label": "export controls", "keywords": ["export control", "china"], "events": ["legal_reg"]},
         {"label": "results", "keywords": [], "events": ["earnings"]}]


class Thesis(unittest.TestCase):
    def test_keyword_and_event_matches(self):
        stories = [{"title": "US tightens export controls on AI chips", "event": "other"},
                   {"title": "Nvidia posts record quarter", "event": "earnings", "read": {"event": "earnings"}},
                   {"title": "Nvidia unveils new GPU", "event": "product"}]
        hits = dict(briefing.thesis_hits(WATCH, stories))
        self.assertIn("export controls", hits)
        self.assertEqual(hits["results"], "Nvidia posts record quarter")

    def test_weak_rule_tag_alone_does_not_raise_a_flag(self):
        self.assertEqual(briefing.thesis_hits(WATCH, [{"title": "He slept on the factory floor", "event": "earnings"}]), [])

    def test_sec_filing_event_counts(self):
        hits = briefing.thesis_hits(WATCH, [{"title": "NVDA files 8-K", "event": "earnings", "first_source": "SEC EDGAR 8-K"}])
        self.assertEqual([h[0] for h in hits], ["results"])

    def test_no_match_no_hit(self):
        self.assertEqual(briefing.thesis_hits(WATCH, [{"title": "Nvidia unveils new GPU", "event": "product"}]), [])


class Matching(unittest.TestCase):
    def test_word_start_not_substring(self):
        self.assertFalse(briefing.has_word("A basic guide to chips", "asic"))
        self.assertTrue(briefing.has_word("Custom ASIC demand grows", "asic"))

    def test_company_match_rules(self):
        kw = ["nvidia", "nvda"]
        self.assertTrue(briefing.about_holding({"title": "Nvidia beats estimates", "tickers": ["NVDA", "AMD", "MSFT", "AAPL"]}, "NVDA", kw))
        self.assertFalse(briefing.about_holding({"title": "TikTok settles state claims", "tickers": ["GOOGL", "META", "MSFT", "SNAP"]}, "GOOGL", ["google", "alphabet"]))
        self.assertTrue(briefing.about_holding({"title": "Chipmaker news", "tickers": ["ASML", "TSM"]}, "ASML", ["asml"]))
        self.assertTrue(briefing.about_holding({"title": "Deal news", "tickers": [], "read": {"about": "MSFT"}}, "MSFT", ["microsoft"]))
        self.assertFalse(briefing.about_holding({"title": "Deal news", "tickers": ["MSFT"], "read": {"about": "NVDA"}}, "MSFT", ["microsoft"]))


class Format(unittest.TestCase):
    def test_story_line_includes_reader_takeaway(self):
        s = {"event": "m&a", "n_sources": 2, "priority": 82.3, "title": "Nvidia weighs Anthropic stake",
             "read": {"takeaway": "Nvidia may invest $10B.", "direction": 1}}
        line = briefing.fmt_story(s)
        self.assertIn("[m&a, 2 src, prio 82]", line)
        self.assertIn("read: Nvidia may invest $10B. (positive)", line)

    def test_story_line_without_reader(self):
        self.assertNotIn("read:", briefing.fmt_story({"event": None, "n_sources": 1, "priority": 10, "title": "x", "read": None}))


if __name__ == "__main__":
    unittest.main()
