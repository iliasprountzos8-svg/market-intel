"""Run from the repo root:  python -m unittest discover -s tests -v   (no dependencies)"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "ingest"))

import sec_items as sec  # noqa: E402
import story_logic as sl  # noqa: E402

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def art(i, title, src="Reuters", dh=0, tickers=None, seen_after_min=5):
    pub = T0 + timedelta(hours=dh)
    return {"id": f"a{i}", "title": title, "published_at": pub, "scraped_at": pub + timedelta(minutes=seen_after_min),
            "source": src, "tickers": tickers or []}


class Normalize(unittest.TestCase):
    def test_source_tail_stripped(self):
        self.assertEqual(sl.normalize_title("Nvidia jumps on AI demand - Reuters"), "nvidia jumps on ai demand")
        self.assertEqual(sl.normalize_title("Nvidia jumps on AI demand | Yahoo Finance"), "nvidia jumps on ai demand")

    def test_real_dash_content_kept(self):
        self.assertIn("here's why", sl.normalize_title("Tesla falls 5% - here's why"))

    def test_ticker_tag_and_prefix_removed(self):
        self.assertEqual(sl.normalize_title("BREAKING: Apple (NASDAQ:AAPL) unveils new iPhone"), "apple unveils new iphone")

    def test_tokens_drop_stopwords(self):
        toks = sl.title_tokens("Why the stock market could fall after the Fed decision")
        self.assertIn("fed", toks)
        self.assertNotIn("stock", toks)
        self.assertNotIn("the", toks)

    def test_hash_is_order_insensitive(self):
        self.assertEqual(sl.title_hash("Nvidia beats revenue estimates"), sl.title_hash("Revenue estimates beaten Nvidia beats"[:0] + "estimates revenue beats Nvidia"))


class Freshness(unittest.TestCase):
    def test_backfill_flag(self):
        self.assertTrue(sl.is_backfill(T0 - timedelta(days=3), T0))
        self.assertFalse(sl.is_backfill(T0 - timedelta(hours=2), T0))
        self.assertFalse(sl.is_backfill(None, T0))


class Clustering(unittest.TestCase):
    def setUp(self):
        self.ix = sl.StoryIndex()

    def test_exact_duplicates_merge_across_sources(self):
        a, _ = self.ix.assign(art(1, "Nvidia beats quarterly revenue estimates on AI chip demand", "Yahoo Finance", tickers=["NVDA"]))
        b, new = self.ix.assign(art(2, "Nvidia beats quarterly revenue estimates on AI chip demand - Reuters", "Reuters", dh=1, tickers=["NVDA", "AMD"]))
        self.assertEqual(a, b)
        self.assertFalse(new)
        s = self.ix.stories[a]
        self.assertEqual(s.n_articles, 2)
        self.assertEqual(s.sources, {"Yahoo Finance", "Reuters"})
        self.assertEqual(s.tickers, {"NVDA", "AMD"})

    def test_near_duplicates_merge(self):
        a, _ = self.ix.assign(art(1, "Microsoft announces $10 billion investment in Azure data centers in Europe"))
        b, _ = self.ix.assign(art(2, "Microsoft announces $10 billion Azure data center investment in Europe", "CNBC", dh=2))
        self.assertEqual(a, b)

    def test_unrelated_stories_stay_separate(self):
        a, _ = self.ix.assign(art(1, "Nvidia beats quarterly revenue estimates on AI chip demand"))
        b, _ = self.ix.assign(art(2, "Nvidia faces new export restrictions on chips to China", dh=1))
        self.assertNotEqual(a, b)

    def test_same_words_days_later_is_a_new_story(self):
        a, _ = self.ix.assign(art(1, "Fed holds rates steady as inflation cools", dh=0))
        b, _ = self.ix.assign(art(2, "Fed holds rates steady as inflation cools", dh=24 * 10))
        self.assertNotEqual(a, b)

    def test_templated_headlines_for_different_companies_do_not_merge(self):
        names = ["RTX Corporation", "McKesson Corporation", "Starbucks Corporation", "Honeywell International", "Paychex Incorporated",
                 "Realty Income", "Broadridge Financial", "Willis Towers Watson", "Zimmer Biomet", "Johnson Controls", "Wells Fargo",
                 "Targa Resources", "Regency Centers", "Monolithic Power", "Las Vegas Sands", "United Airlines", "Jack Henry", "Deckers Outdoor",
                 "Labcorp Holdings", "Brown Brown", "Kraft Heinz", "Revvity Incorporated", "Henry Schein", "Fifth Third", "Marsh McLennan"]
        titles = [f"{n} is Attracting Investor Attention: Here is What You Should Know" for n in names]
        df, n_docs = sl.build_df(titles * 1 + [f"Filler headline number {i} about something unrelated" for i in range(50)])
        em = sl.EntityMatcher({n: n.split()[0].upper() + "_" for n in names})
        self.ix = sl.StoryIndex(df=df, n_docs=n_docs, entity_fn=em)
        ids = []
        for i, n in enumerate(names):  # no tickers: only the rarity weighting can keep these apart
            sid, _ = self.ix.assign(art(i, f"{n} is Attracting Investor Attention: Here is What You Should Know", "Yahoo Finance", dh=i * 0.1))
            ids.append(sid)
        self.assertEqual(len(set(ids)), len(names))

    def test_disjoint_feed_tickers_never_merge(self):
        a, _ = self.ix.assign(art(1, "Options begin trading November sixth", "Nasdaq.com", tickers=["DHI"]))
        b, _ = self.ix.assign(art(2, "Options begin trading November sixth", "Nasdaq.com", dh=1, tickers=["WFC"]))
        # identical headline text is one story (exact hash), but a near-duplicate with another company's ticker is not:
        c, _ = self.ix.assign(art(3, "Options begin trading November sixth again", "Nasdaq.com", dh=2, tickers=["DECK"]))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_identical_generic_headline_merges_across_tickers(self):
        a, _ = self.ix.assign(art(1, "Aggregate assets under management rise in August", "Seeking Alpha", tickers=["BLK"]))
        b, _ = self.ix.assign(art(2, "Aggregate assets under management rise in August", "Seeking Alpha", dh=1, tickers=["BX"]))
        self.assertEqual(a, b)
        self.assertEqual(self.ix.stories[a].tickers, {"BLK", "BX"})

    def test_entity_matcher_reads_tickers_and_names(self):
        em = sl.EntityMatcher({"Microsoft": "MSFT", "NVIDIA": "NVDA"})
        self.assertEqual(em("Microsoft Corp (MSFT) rises"), {"MSFT"})
        self.assertEqual(em("Why NVIDIA and Microsoft rallied"), {"NVDA", "MSFT"})
        self.assertEqual(em("Apple (NASDAQ:AAPL) unveils phone"), {"AAPL"})
        self.assertEqual(em("Stocks rise on Fed hopes"), set())

    def test_paraphrases_naming_the_same_company_still_merge_with_entities(self):
        self.ix = sl.StoryIndex(entity_fn=sl.EntityMatcher({"Microsoft": "MSFT"}))
        a, _ = self.ix.assign(art(1, "Microsoft announces $10 billion investment in Azure data centers in Europe"))
        b, _ = self.ix.assign(art(2, "Microsoft announces $10 billion Azure data center investment in Europe", "CNBC", dh=2))
        self.assertEqual(a, b)

    def test_recurring_headline_five_days_apart_is_a_new_story(self):
        a, _ = self.ix.assign(art(1, "Weekly jobless claims fall to lowest level this year", dh=0))
        b, _ = self.ix.assign(art(2, "Weekly jobless claims fall to lowest level this year", dh=24 * 5))
        self.assertNotEqual(a, b)

    def test_first_reporter_is_the_earliest_article(self):
        a, _ = self.ix.assign(art(1, "Apple recalls chargers over overheating risk", "Seeking Alpha", dh=3))
        self.ix.assign(art(2, "Apple recalls chargers over overheating risk", "Reuters", dh=1))
        s = self.ix.stories[a]
        self.assertEqual(s.first_source, "Reuters")
        self.assertEqual(s.first_article_id, "a2")

    def test_short_titles_need_real_overlap(self):
        a, _ = self.ix.assign(art(1, "Tesla recall"))
        b, _ = self.ix.assign(art(2, "Ford recall"))
        self.assertNotEqual(a, b)


class Events(unittest.TestCase):
    def test_common_events(self):
        cases = {
            "Nvidia reports Q3 earnings, EPS beats estimates": "earnings",
            "Microsoft raises full-year guidance after strong Azure growth": "guidance",
            "Broadcom to acquire VMware in $61 billion deal": "m&a",
            "Alphabet hit with new antitrust lawsuit by the DOJ": "legal_reg",
            "Morgan Stanley upgrades Apple to overweight, raises price target": "analyst",
            "Intel CEO steps down after board pressure": "leadership",
            "Company discloses data breach affecting customers": "cyber",
            "Firm to restate last three years of financials": "restatement",
            "Fed signals rate cut as inflation eases": "macro",
            "Amazon announces $20 billion share buyback": "capital",
            "Apple launches new iPhone with faster chip": "product",
            "Weather is nice in Athens today": "other",
        }
        for title, want in cases.items():
            self.assertEqual(sl.event_v2(title), want, title)

    def test_sec_items_override_text(self):
        self.assertEqual(sl.event_v2("NVDA files 8-K", sec_items=["2.02", "9.01"]), "earnings")
        self.assertEqual(sl.event_v2("XYZ files 8-K", sec_items=["5.02"]), "leadership")
        self.assertEqual(sl.event_v2("XYZ files 8-K", sec_items=["4.02", "2.02"]), "restatement")

    def test_exhibits_only_falls_back_to_text(self):
        self.assertEqual(sl.event_v2("Apple launches new iPhone", sec_items=["9.01"]), "product")


class Opinion(unittest.TestCase):
    def test_clickbait_is_opinion_not_earnings(self):
        for t in ("Should You Buy Nike Stock Before Oct. 1?", "3 Stocks to Buy and Hold for the Next Decade",
                  "Can AMD Stock Reach $600?", "Here's How Many Shares of This 15%-Yielding Dividend Stock You'd Need",
                  "Realty Income vs. Regency Centers: Which REIT Is Better for Investors?", "Honeywell (HON) Stock Moves 2.60%: What You Should Know"):
            self.assertEqual(sl.event_v2(t), "opinion", t)

    def test_dividend_clickbait_is_opinion_but_dividend_news_is_capital(self):
        for t in ("This Dividend Stock Pays You 12 Times Per Year. Here's What a $25,000 Investment Would Earn",
                  "If You Invest $100 Per Month in Procter & Gamble Stock, Here's the Passive Dividend Income",
                  "VIG's Strange Rule: The Highest-Yielding Dividend Growers Aren't Allowed In"):
            self.assertEqual(sl.event_v2(t), "opinion", t)
        self.assertEqual(sl.event_v2("Procter & Gamble raises quarterly dividend by 5%"), "capital")
        self.assertEqual(sl.event_v2("Intel suspends dividend to preserve cash"), "capital")

    def test_real_news_is_not_opinion(self):
        self.assertEqual(sl.event_v2("Nike reports quarterly earnings, revenue beats estimates"), "earnings")
        self.assertEqual(sl.event_v2("Nvidia weighing $10 billion stake in Anthropic"), "m&a")
        self.assertEqual(sl.event_v2("China, US agree to $30 billion tariff cut"), "legal_reg")

    def test_summary_no_longer_drives_events(self):
        self.assertEqual(sl.event_v2("Should You Buy Nike Stock Before Oct. 1?", "Nike will report earnings and EPS estimates"), "opinion")
        self.assertEqual(sl.event_v2("Company update", "Q3 earnings and EPS results"), "other")

    def test_opinion_pieces_rank_low(self):
        self.assertLess(sl.priority(False, "opinion", "outlet", 1, False, 1), sl.priority(False, "other", "outlet", 1, False, 1))

    def test_syndication_is_not_confirmation(self):
        self.assertEqual(sl.independent_sources({"Motley Fool", "Yahoo Finance", "Nasdaq.com"}), 1)
        self.assertEqual(sl.independent_sources({"Reuters", "Bloomberg", "Yahoo Finance"}), 2)
        self.assertEqual(sl.independent_sources({"Yahoo Finance", "Nasdaq.com"}), 1)


class Priority(unittest.TestCase):
    def test_held_primary_earnings_is_high(self):
        p = sl.priority(True, "earnings", "primary", 3, False, 1)
        self.assertGreater(p, 80)

    def test_backfill_is_crushed(self):
        fresh = sl.priority(True, "earnings", "primary", 3, False, 1)
        old = sl.priority(True, "earnings", "primary", 3, True, 1)
        self.assertLess(old, fresh * 0.15)

    def test_held_beats_unheld_and_events_beat_noise(self):
        self.assertGreater(sl.priority(True, "other", "outlet", 1, False, 2), sl.priority(False, "other", "outlet", 1, False, 2))
        self.assertGreater(sl.priority(False, "guidance", "outlet", 1, False, 2), sl.priority(False, "other", "outlet", 1, False, 2))

    def test_roundups_are_discounted_and_range_is_bounded(self):
        self.assertLess(sl.priority(False, "macro", "outlet", 1, False, 1, n_tickers=12), sl.priority(False, "macro", "outlet", 1, False, 1))
        self.assertLessEqual(sl.priority(True, "restatement", "primary", 9, False, 0), 100.0)

    def test_source_class(self):
        self.assertEqual(sl.source_class("Yahoo Finance"), "ticker_feed")
        self.assertEqual(sl.source_class("SEC EDGAR 8-K"), "primary")
        self.assertEqual(sl.source_class("Federal Reserve Press Releases"), "primary")
        self.assertEqual(sl.source_class("CNBC"), "outlet")


SAMPLE = {"filings": {"recent": {
    "accessionNumber": ["0001045810-26-000101", "0001045810-26-000100", "0001045810-26-000099", "0001045810-26-000098"],
    "filingDate": ["2026-09-25", "2026-09-24", "2026-09-20", "2026-09-19"],
    "acceptanceDateTime": ["2026-09-25T20:15:33.000Z", "2026-09-24T21:00:01.000Z", "2026-09-20T13:00:00.000Z", "2026-09-19T10:00:00.000Z"],
    "form": ["8-K", "4", "8-K", "8-K"],
    "items": ["2.02,9.01", "", "9.01", "5.02"],
    "primaryDocDescription": ["8-K", "FORM 4", "8-K", "8-K"],
}}}


class SecItems(unittest.TestCase):
    def test_parses_only_material_8k(self):
        rows = sec.parse_submissions(SAMPLE, "NVDA", "NVIDIA Corp", "0001045810")
        self.assertEqual(len(rows), 2)  # Form 4 skipped, exhibits-only 8-K skipped
        first = rows[0]
        self.assertIn("NVDA files 8-K", first["title"])
        self.assertIn("Earnings", first["title"])
        self.assertEqual(first["url"], "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/0001045810-26-000101-index.htm")
        self.assertEqual(first["published_at"], "2026-09-25T20:15:33.000+00:00")
        self.assertEqual(first["tickers_raw"], ["NVDA"])
        self.assertIn("Items: 2.02, 9.01", first["summary_raw"])

    def test_since_filter(self):
        rows = sec.parse_submissions(SAMPLE, "NVDA", "NVIDIA Corp", "0001045810", since_iso="2026-09-24T00:00:00.000Z")
        self.assertEqual(len(rows), 1)

    def test_event_from_summary_items(self):
        rows = sec.parse_submissions(SAMPLE, "NVDA", "NVIDIA Corp", "0001045810")
        items = sec.split_items(rows[1]["summary_raw"].split("Items:")[1])
        self.assertEqual(sl.event_v2(rows[1]["title"], sec_items=items), "leadership")

    def test_empty_input(self):
        self.assertEqual(sec.parse_submissions({}, "X", "X", "0000000001"), [])


if __name__ == "__main__":
    unittest.main()
