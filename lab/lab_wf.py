"""Walk-forward validation of candidate strategies (weekly job).

Every strategy is scored ONLY on data it never saw: models are refit on data up to a cutoff
(purged by the label horizon) and evaluated on the following quarter, repeated through time.

Strategies:
  mom      12-1 month momentum rank
  rev      short-term reversal (-5d return) rank
  lowvol   low 60d volatility rank
  hgb      gradient-boosted trees on the cross-sectional price features (price-only ML)

Metrics per strategy and horizon: mean daily rank-IC (Spearman of the score vs realised excess
return over SPY), IC t-stat, and the top-decile minus bottom-decile spread (raw and after a 10 bps
per-side trading cost, assuming the book turns over once per holding period).
"Skill" is only claimed if IC t-stat > 2 AND the net spread is positive. Everything is written
to logs/lab-walkforward.json.

Run: python lab_wf.py [--h 5] [--start 2023-01-01]
"""
import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).parent))
import lab_data as ld  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = ld.ROOT
FEATS = ["f_r1", "f_r5", "f_r20", "f_r60", "f_mom_12_1", "f_vol20", "f_vol60", "f_dd_high", "f_dvol_ratio", "f_rel5", "f_rel20", "f_sec_rel20"]
COST_BPS = 10.0


def ranked(panel):
    r = ld.cs_rank(panel, FEATS)
    r.columns = [c.replace("f_", "r_") for c in r.columns]
    return r


def daily_ic(score, y):
    df = pd.DataFrame({"s": score, "y": y}).dropna()
    def ic(g):
        return g["s"].corr(g["y"], method="spearman") if len(g) >= 50 else np.nan
    return df.groupby(level="date").apply(ic).dropna()


def decile_spread(score, y):
    df = pd.DataFrame({"s": score, "y": y}).dropna()
    def sp(g):
        if len(g) < 100:
            return np.nan
        q = g["s"].rank(pct=True)
        return g.loc[q >= 0.9, "y"].mean() - g.loc[q <= 0.1, "y"].mean()
    return df.groupby(level="date").apply(sp).dropna()


def summarise(name, score, y, h):
    ic = daily_ic(score, y)
    sp = decile_spread(score, y)
    if len(ic) < 30:
        return {"name": name, "n_days": int(len(ic)), "note": "too few days"}
    tstat = ic.mean() / (ic.std() / np.sqrt(len(ic))) * (1 / np.sqrt(h))  # overlapping horizons: shrink the t-stat
    gross = float(sp.mean())
    cost = 2 * COST_BPS / 1e4 * 2  # long+short legs, entry+exit, per holding period
    return {"name": name, "n_days": int(len(ic)), "ic_mean": float(ic.mean()), "ic_t": float(tstat), "spread_gross_pct": gross * 100,
            "spread_net_pct": (gross - cost) * 100, "hit_days": float((sp > 0).mean()),
            "skill": bool(tstat > 2 and (gross - cost) > 0)}


def fit_hgb(Xtr, ytr):
    m = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=400, l2_regularization=1.0, random_state=7)
    m.fit(Xtr, ytr)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--refresh", action="store_true", help="re-download prices first")
    args = ap.parse_args()
    t0 = time.time()
    if args.refresh or not ld.PRICES.exists():
        ld.update_prices(full=not ld.PRICES.exists())
    close, vol = ld.load_wide()
    panel = ld.build_panel(close, vol)
    ycol = f"y{args.h}"
    R = ranked(panel)
    Rc = list(R.columns)
    data = pd.concat([R, panel[[ycol]]], axis=1).dropna(subset=Rc)
    yrank = data[ycol].groupby(level="date").rank(pct=True)
    dates = data.index.get_level_values("date").unique().sort_values()
    start = pd.Timestamp(args.start)
    folds = pd.date_range(start, dates.max(), freq="QS")
    scores = {k: [] for k in ("mom", "rev", "lowvol", "hgb")}
    for k, f0 in enumerate(folds):
        f1 = folds[k + 1] if k + 1 < len(folds) else dates.max() + pd.Timedelta(days=1)
        test = data[(data.index.get_level_values("date") >= f0) & (data.index.get_level_values("date") < f1)]
        cutoff = f0 - pd.Timedelta(days=int(args.h * 1.6) + 4)  # purge overlapping label windows
        trmask = (data.index.get_level_values("date") <= cutoff) & data[ycol].notna().values
        train = data[trmask]
        if len(train) < 50000 or test.empty:
            continue
        tr_sub = train.iloc[::3]  # every 3rd row: overlapping labels are highly redundant
        model = fit_hgb(tr_sub[Rc].values, yrank.loc[tr_sub.index].values)
        scores["hgb"].append(pd.Series(model.predict(test[Rc].values), index=test.index))
        scores["mom"].append(test["r_mom_12_1"])
        scores["rev"].append(-test["r_r5"])
        scores["lowvol"].append(-test["r_vol60"])
    results = []
    for name, parts in scores.items():
        if not parts:
            continue
        s = pd.concat(parts)
        results.append(summarise(name, s, data.loc[s.index, ycol], args.h))
    # final model on everything, for the daily predictor
    full = data[data[ycol].notna()].iloc[::3]
    final = fit_hgb(full[Rc].values, yrank.loc[full.index].values)
    (ld.DATA / "models").mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final, "features": Rc, "h": args.h, "trained": datetime.now(timezone.utc).isoformat()}, ld.DATA / "models" / f"hgb_h{args.h}.joblib")
    out = {"generated": datetime.now(timezone.utc).isoformat(), "horizon_days": args.h, "test_from": args.start, "cost_bps_per_side": COST_BPS,
           "results": results, "rows": int(len(data)), "symbols": int(data.index.get_level_values("symbol").nunique()),
           "note": "skill requires IC t-stat > 2 and positive net spread; overlapping windows make t-stats optimistic, so shrunk by 1/sqrt(h). CAVEAT: the universe is TODAY's S&P 500 members over past years (survivorship bias: stocks that did well are more likely to be in the index today, which flatters momentum-type strategies). Treat any 'skill' as a hypothesis to confirm with the forward paper ledger, not proof."}
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / f"lab-walkforward-h{args.h}.json").write_text(json.dumps(out, indent=1))
    print("  CAVEAT: universe = today's S&P 500 members (survivorship bias); confirm with the forward paper ledger.")
    print(f"walk-forward h={args.h}d, {out['symbols']} symbols, {out['rows']} rows, {time.time() - t0:.0f}s")
    for r in results:
        if "ic_mean" in r:
            print(f"  {r['name']:7s} IC {r['ic_mean']:+.3f} (t={r['ic_t']:+.1f}) | top-bottom decile {r['spread_gross_pct']:+.2f}% gross, {r['spread_net_pct']:+.2f}% net | days {r['n_days']} | skill={r['skill']}")
        else:
            print("  ", r)


if __name__ == "__main__":
    main()
