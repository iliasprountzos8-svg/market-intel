import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import lab_data as ld
    import lab_wf as lw
except ImportError:  # lab venv only (sklearn/scipy/joblib)
    lw = ld = None


@unittest.skipIf(lw is None, "lab dependencies only in the lab venv")
class LongHistory(unittest.TestCase):
    def test_panel_without_volume_has_h60_and_no_dvol(self):
        idx = pd.bdate_range("2020-01-01", periods=400)
        syms, _ = ld.universe()
        rng = np.random.default_rng(1)
        cols = syms[:5] + ["SPY"]
        close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (400, 6)), axis=0)), index=idx, columns=cols)
        panel = ld.build_panel(close, None, horizons=lw.LONG_HORIZONS)
        self.assertIn("y60", panel.columns)
        self.assertNotIn("f_dvol_ratio", panel.columns)
        self.assertEqual(lw.ranked(panel, lw.FEATS_LONG).shape[1], len(lw.FEATS_LONG))
        # the label at t is the return from t+1 to t+61, so the last 61 days have none
        last = panel["y60"].unstack().iloc[-61:]
        self.assertTrue(last.isna().all().all())

    def test_family_fdr_corrects_across_horizons(self):
        r = lambda p: {"name": "x", "ic_mean": 0.05, "ic_t": 2.0, "p_value": p, "spread_net_pct": 0.3}  # noqa: E731
        # alone, p=0.04 passes q=0.10; among 12 tests the BH threshold (rank 1 of 12) is 0.10/12
        self.assertTrue(lw.family_fdr({5: [r(0.04)]})[0]["skill_fdr_family"])
        fam = lw.family_fdr({5: [r(0.04)] + [r(0.5)] * 5, 20: [r(0.6)] * 3, 60: [r(0.7)] * 3})
        self.assertFalse(any(t["skill_fdr_family"] for t in fam))

    def test_negative_net_spread_never_passes(self):
        r = {"name": "x", "ic_mean": 0.05, "ic_t": 4.0, "p_value": 0.0001, "spread_net_pct": -0.1}
        self.assertFalse(lw.family_fdr({5: [r]})[0]["skill_fdr_family"])


if __name__ == "__main__":
    unittest.main()
