import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import embed_logic as el  # noqa: E402


def st(title, t=0, tickers=(), ents=(), sid=0):
    return {"id": sid, "title": title, "first_pub": t, "tickers": set(tickers), "ents": set(ents)}


class Numbers(unittest.TestCase):
    def test_extraction_ignores_years_and_small_ordinals(self):
        self.assertEqual(el.numbers_of("Q3 2026 revenue rises 12% to $3.36B"), {"12", "3.36"})
        self.assertEqual(el.numbers_of("Top 3 stocks for 2027"), set())
        self.assertEqual(el.numbers_of("Raises $1,200 million"), {"1200"})


class Language(unittest.TestCase):
    def test_latin_share(self):
        self.assertGreater(el.latin_share("Nvidia beats estimates"), 0.99)
        self.assertLess(el.latin_share("Έκρηξη στην Πλάκα: Στους 6 οι αγνοούμενοι"), 0.2)
        self.assertGreater(el.latin_share("Micron (MU) beats: 12% – up"), 0.85)
        self.assertEqual(el.latin_share("1234 -- !!"), 0.0)


class Gates(unittest.TestCase):
    def test_same_event_passes(self):
        a = st("Nscale secures $3.36B convertible financing", 0, ["NSC"], ["NSC"])
        b = st("Ahead of IPO, Nscale raises $3.36B", 3600, ["NSC"], ["NSC"])
        self.assertEqual(el.gates(a, b), (True, "ok"))

    def test_time_window(self):
        self.assertEqual(el.gates(st("x", 0), st("x", 5 * 86400)), (False, "time"))

    def test_disjoint_companies_blocked(self):
        self.assertEqual(el.gates(st("a", 0, ents=["RTX"]), st("b", 0, ents=["MCK"])), (False, "entities"))
        self.assertEqual(el.gates(st("a", 0, tickers=["AAPL"]), st("b", 0, tickers=["MSFT"])), (False, "tickers"))

    def test_unknown_companies_do_not_block(self):
        self.assertTrue(el.gates(st("a", 0, ents=["RTX"]), st("b", 0))[0])

    def test_conflicting_figures_block(self):
        a = st("Stock moves 3.92% on Tuesday", 0)
        b = st("Stock moves 2.10% on Wednesday", 0)
        self.assertEqual(el.gates(a, b), (False, "numbers"))

    def test_rounded_figures_are_the_same_deal(self):
        self.assertTrue(el.gates(st("Akamai soars on $11.6 billion Anthropic deal", 0), st("Akamai rallies on $12 billion cloud deal", 0))[0])
        self.assertFalse(el.gates(st("Fund raises $15 billion", 0), st("Fund valued at $150 billion", 0))[0])

    def test_ticker_gate_is_strict(self):
        many = ["XOM", "CVX", "COP"]
        self.assertEqual(el.gates(st("Iran offers Hormuz deal", 0, many), st("Tehran floats Hormuz plan", 0, ["OXY", "MPC", "VLO"])), (False, "tickers"))
        self.assertEqual(el.gates(st("DHI options begin trading", 0, ["DHI"]), st("WFC options begin trading", 0, ["WFC"])), (False, "tickers"))
        self.assertTrue(el.gates(st("Iran offers Hormuz deal", 0, many), st("Tehran floats Hormuz plan", 0, ["OXY", "XOM"]))[0])

    def test_dates_and_small_integers_must_match_exactly(self):
        self.assertEqual(el.gates(st("Links 9/25/2026", 0), st("Links 9/26/2026", 0)), (False, "numbers"))
        self.assertFalse(el.numbers_compatible({"25"}, {"26"}))
        self.assertTrue(el.numbers_compatible({"11.6"}, {"12"}))
        self.assertTrue(el.numbers_compatible({"1000"}, {"1050"}))

    def test_preview_and_result_are_different_developments(self):
        self.assertEqual(el.gates(st("TD SYNNEX Q3 2026 Earnings Preview", 0), st("TD SYNNEX Q3 Earnings Beat Estimates on Distribution Strength", 0)),
                         (False, "preview_vs_result"))
        self.assertTrue(el.gates(st("TD SYNNEX Q3 Earnings Beat Estimates", 0), st("TD SYNNEX tops Q3 estimates", 0))[0])
        self.assertTrue(el.gates(st("Micron to report earnings next week", 0), st("What to expect from Micron's upcoming report", 0))[0])

    def test_one_side_without_figures_is_fine(self):
        self.assertTrue(el.gates(st("Company raises funding", 0), st("Company raises $5B funding", 0))[0])


class Pairs(unittest.TestCase):
    def test_candidate_pairs_by_cosine(self):
        v = np.array([[1, 0], [0.99, 0.14], [0, 1]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pairs = el.candidate_pairs(v, 0.9)
        self.assertEqual([(i, j) for i, j, _ in pairs], [(0, 1)])

    def test_gates_cannot_be_bypassed_by_chaining(self):
        v = np.array([[1, 0], [0.99, 0.1], [0.98, 0.15], [0, 1]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        stories = [st("A one", 100, sid=1), st("A two", 50, sid=2), st("A three", 200, sid=3), st("Other", 0, sid=4)]
        stories[0]["ents"] = {"Y"}   # story 1 names company Y, story 3 names company X: they may never share a group,
        stories[2]["ents"] = {"X"}   # even though story 2 (no entities) is similar to both
        groups = el.merge_groups(stories, el.candidate_pairs(v, 0.9), 0.9)
        ids = [[stories[i]["id"] for i in g] for g in groups]
        self.assertTrue(ids, "some merge should still happen")
        for g in ids:
            self.assertFalse({1, 3} <= set(g), g)
            self.assertNotIn(4, g)

    def test_group_keeps_earliest_story_first(self):
        v = np.array([[1, 0], [0.99, 0.1]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        stories = [st("late", 100, sid=1), st("early", 50, sid=2)]
        groups = el.merge_groups(stories, el.candidate_pairs(v, 0.9), 0.9)
        self.assertEqual([[stories[i]["id"] for i in g] for g in groups], [[2, 1]])

    def test_complete_link_blocks_topic_drift_chains(self):
        # a~b and b~c are close, but a and c are not: without complete-link all three would chain into one story
        v = np.array([[1.0, 0.0], [0.80, 0.60], [0.28, 0.96]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        stories = [st("a", 1, sid=1), st("b", 2, sid=2), st("c", 3, sid=3)]
        groups = el.merge_groups(stories, el.candidate_pairs(v, 0.78), 0.78)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)  # never all three

    def test_transitive_merge(self):
        v = np.array([[1, 0], [0.97, 0.24], [0.95, 0.31]], dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        stories = [st("a", 1, sid=1), st("b", 2, sid=2), st("c", 3, sid=3)]
        groups = el.merge_groups(stories, el.candidate_pairs(v, 0.9), 0.9)
        self.assertEqual([[stories[i]["id"] for i in g] for g in groups], [[1, 2, 3]])


if __name__ == "__main__":
    unittest.main()
