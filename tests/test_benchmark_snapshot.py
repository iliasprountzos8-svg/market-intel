import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import benchmark_snapshot as bs  # noqa: E402


class LoadJsonArtifact(unittest.TestCase):
    def test_missing_file(self):
        data, note = bs.load_json_artifact(Path("/does/not/exist.json"))
        self.assertIsNone(data)
        self.assertIn("not yet generated", note)

    def test_reads_fresh_file(self):
        p = Path(__file__).resolve().parent / "_tmp_artifact.json"
        p.write_text(json.dumps({"a": 1}))
        try:
            data, note = bs.load_json_artifact(p)
            self.assertEqual(data, {"a": 1})
            self.assertIsNone(note)
        finally:
            p.unlink()

    def test_stale_file_is_rejected(self):
        p = Path(__file__).resolve().parent / "_tmp_stale.json"
        p.write_text(json.dumps({"a": 1}))
        old = time.time() - 9 * 86400
        import os
        os.utime(p, (old, old))
        try:
            data, note = bs.load_json_artifact(p, stale_after_days=8)
            self.assertIsNone(data)
            self.assertIn("stale", note)
        finally:
            p.unlink()

    def test_unreadable_json(self):
        p = Path(__file__).resolve().parent / "_tmp_bad.json"
        p.write_text("{not valid json")
        try:
            data, note = bs.load_json_artifact(p)
            self.assertIsNone(data)
            self.assertIn("unreadable", note)
        finally:
            p.unlink()


class BootCi(unittest.TestCase):
    def test_deterministic_with_seed(self):
        vals = [1.0, 2.0, 1.5, -0.5, 3.0, 0.2, 1.1, -1.0, 2.2, 0.9, 1.3, -0.2, 0.7, 1.8, 2.5]
        lo1, hi1 = bs.boot_ci(vals, n=500, seed=7)
        lo2, hi2 = bs.boot_ci(vals, n=500, seed=7)
        self.assertEqual((lo1, hi1), (lo2, hi2))
        self.assertLess(lo1, hi1)

    def test_ci_brackets_the_mean_for_tight_data(self):
        vals = [1.0] * 20
        lo, hi = bs.boot_ci(vals, n=500, seed=7)
        self.assertAlmostEqual(lo, 1.0)
        self.assertAlmostEqual(hi, 1.0)


class ToMarkdown(unittest.TestCase):
    def _base_snapshot(self, **overrides):
        snap = {
            "generated": "2026-09-27T12:00:00",
            "calibration": {"n_scoreable": 0, "n_unclear": 0, "n_no_confidence": 5, "brier": None, "bins": [], "min_n_for_signal": 30},
            "ledger": {"pending_positions": 10, "settled_positions": 0, "note": "nothing settled yet", "by_book_side": []},
            "data_quality": {"note": "no dq_metrics rows yet"},
            "track_record": {"note": "not yet generated"},
            "signal_eval": {"note": "not yet generated"},
            "walkforward": {"note": "not yet generated"},
        }
        snap.update(overrides)
        return snap

    def test_handles_all_missing_gracefully(self):
        md = bs.to_markdown(self._base_snapshot())
        self.assertIn("N/A", md)
        self.assertIn("nothing settled yet", md)
        self.assertIn("no dq_metrics rows yet", md)
        self.assertIn("not yet generated", md)

    def test_renders_calibration_when_present(self):
        snap = self._base_snapshot(calibration={
            "n_scoreable": 40, "n_unclear": 2, "n_no_confidence": 1, "brier": 0.18,
            "bins": [{"range": "80%-100%", "n": 10, "stated": 0.9, "hit_rate": 0.6}],
            "min_n_for_signal": 30,
        })
        md = bs.to_markdown(snap)
        self.assertIn("0.180", md)
        self.assertIn("80%-100%", md)

    def test_renders_ledger_table_when_present(self):
        snap = self._base_snapshot(ledger={
            "pending_positions": 3, "settled_positions": 20,
            "by_book_side": [{"book": "momentum", "side": "long", "n_dates": 16, "mean_net_excess_pct": 0.14,
                               "hit_rate": 0.55, "ci95": [0.01, 0.27], "verdict": "CI above 0 (still needs multiple-test caution)"}],
        })
        md = bs.to_markdown(snap)
        self.assertIn("momentum/long", md)
        self.assertIn("CI above 0", md)

    def test_signal_eval_significance_count(self):
        snap = self._base_snapshot(signal_eval={
            "modelA|lex|1d|raw": {"rho": 0.1, "p": 0.01, "n": 100},
            "modelA|lex|5d|raw": {"rho": 0.02, "p": 0.6, "n": 100},
        })
        md = bs.to_markdown(snap)
        self.assertIn("1/2 tests significant", md)


if __name__ == "__main__":
    unittest.main()
