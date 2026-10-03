"""Walk-forward validation of candidate strategies (weekly job).

Every strategy is scored ONLY on data it never saw: models are refit on data up to a cutoff
(purged by the label horizon) and evaluated on the following quarter, repeated through time.

Strategies:
  mom      12-1 month momentum rank
  rev      short-term reversal (-5d return) rank
  lowvol   low 60d volatility rank
  hgb      gradient-boosted trees on the cross-sectional price features (price-only ML)
  high52   proximity to 52-week high (George & Hwang anomaly: momentum works better measured
           relative to a salient anchor price than as a raw return)
  secmom   12-1 month momentum, sector-neutralised (is momentum a stock-picking signal or just a
           sector bet in disguise?)
  momvol   12-1 month momentum, confirmed by above-average dollar volume (is a volume-backed move
           more persistent than a quiet one?)

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
from scipy import stats as sstats
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))
import lab_data as ld  # noqa: E402
import universe_pit as up  # noqa: E402
from fdr import bh_fdr  # noqa: E402
from logger import get_logger  # noqa: E402

warnings.filterwarnings("ignore")
logger = get_logger("lab_wf")
ROOT = ld.ROOT
FEATS = ["f_r1", "f_r5", "f_r20", "f_r60", "f_mom_12_1", "f_vol20", "f_vol60", "f_dd_high", "f_dvol_ratio", "f_rel5", "f_rel20", "f_sec_rel20", "f_sec_mom_12_1"]
COST_BPS = 10.0
FDR_Q = 0.10
FEATS_LONG = [f for f in FEATS if f != "f_dvol_ratio"]  # the 2015+ cache has closes only
LONG_HORIZONS = (1, 5, 20, 60)
LONG_START = "2018-01-01"  # mom_12_1 needs a year of warm-up after 2014-12, plus training rows before the first fold
MOM_STRATEGIES = ("mom", "secmom", "momvol")  # momentum-family strategies most exposed to survivorship bias
N_SUBSAMPLES = 5
SUBSAMPLE_FRAC = 0.7


def ranked(panel, feats=FEATS):
    r = ld.cs_rank(panel, feats)
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


def subsample_robustness(score, y, n_resamples=N_SUBSAMPLES, frac=SUBSAMPLE_FRAC, seed=7):
    """Poor-man's out-of-universe robustness check (mitigates, does not fix, survivorship bias):
    re-run the IC computation on random subsamples of the ticker universe and require the sign
    of the mean IC to be stable across resamples. Returns (stable: bool, frac_same_sign: float)."""
    symbols = score.index.get_level_values("symbol").unique()
    if len(symbols) < 20:
        return False, 0.0
    rng = np.random.default_rng(seed)
    full_sign = np.sign(daily_ic(score, y).mean())
    if full_sign == 0 or np.isnan(full_sign):
        return False, 0.0
    same = 0
    used = 0
    for _ in range(n_resamples):
        keep = rng.choice(symbols, size=int(len(symbols) * frac), replace=False)
        mask = score.index.get_level_values("symbol").isin(keep)
        sub_ic = daily_ic(score[mask], y[mask])
        if len(sub_ic) < 30:
            continue
        used += 1
        if np.sign(sub_ic.mean()) == full_sign:
            same += 1
    if used == 0:
        return False, 0.0
    frac_same = same / used
    return frac_same >= 0.8, frac_same


def summarise(name, score, y, h):
    ic = daily_ic(score, y)
    sp = decile_spread(score, y)
    if len(ic) < 30:
        return {"name": name, "n_days": int(len(ic)), "note": "too few days"}
    tstat = ic.mean() / (ic.std() / np.sqrt(len(ic))) * (1 / np.sqrt(h))  # overlapping horizons: shrink the t-stat
    gross = float(sp.mean())
    cost = 2 * COST_BPS / 1e4 * 2  # long+short legs, entry+exit, per holding period
    # two-sided p-value for the (shrunk) t-stat, df = n_days - 1
    pval = float(2 * sstats.t.sf(abs(tstat), df=max(len(ic) - 1, 1)))
    skill_raw = bool(tstat > 2 and (gross - cost) > 0)  # legacy, uncorrected cutoff - kept for continuity
    result = {"name": name, "n_days": int(len(ic)), "ic_mean": float(ic.mean()), "ic_t": float(tstat), "p_value": pval,
              "spread_gross_pct": gross * 100, "spread_net_pct": (gross - cost) * 100, "hit_days": float((sp > 0).mean()),
              "skill": skill_raw, "skill_fdr": None}  # skill_fdr filled after FDR correction across the family, below
    if name in MOM_STRATEGIES:
        stable, frac_same = subsample_robustness(score, y)
        result["survivorship_bias_warning"] = True
        result["subsample_robustness_stable"] = stable
        result["subsample_robustness_frac_same_sign"] = round(frac_same, 2)
    return result


def fit_hgb(Xtr, ytr):
    m = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=400, l2_regularization=1.0, random_state=7)
    m.fit(Xtr, ytr)
    return m


def family_fdr(runs, q=FDR_Q):
    """BH-FDR over every (horizon, strategy) test at once. `runs` = {h: [result, ...]}. Testing 3 horizons x 6
    strategies separately and keeping each run's own correction would understate the multiple-testing burden."""
    rows = [(h, r) for h, rs in sorted(runs.items()) for r in rs if "p_value" in r]
    if not rows:
        return []
    rejected, crit = bh_fdr([r["p_value"] for _, r in rows], q=q)
    return [{"h": h, "name": r["name"], "ic_mean": r["ic_mean"], "ic_t": r["ic_t"], "p_value": r["p_value"],
             "spread_net_pct": r["spread_net_pct"], "skill_fdr_family": bool(rej and r["spread_net_pct"] > 0), "critical": c}
            for (h, r), rej, c in zip(rows, rejected, crit)]


def family_report():
    runs = {}
    for h in LONG_HORIZONS:
        f = ROOT / "logs" / f"lab-walkforward-long-h{h}.json"
        if f.exists():
            runs[h] = json.loads(f.read_text())["results"]
    fam = family_fdr(runs)
    (ROOT / "logs" / "lab-walkforward-long-family.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "horizons": sorted(runs), "tests": fam}, indent=1))
    print(f"long-history family: {len(fam)} tests over horizons {sorted(runs)}, BH q={FDR_Q}; passing: {sum(t['skill_fdr_family'] for t in fam)}")
    for t in sorted(fam, key=lambda t: t["p_value"])[:8]:
        print(f"  h={t['h']:>2} {t['name']:7s} IC {t['ic_mean']:+.3f} (t={t['ic_t']:+.1f}, p={t['p_value']:.3f}) net {t['spread_net_pct']:+.2f}% pass={t['skill_fdr_family']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--start", default=None, help="first test quarter (default 2023-01-01, or 2018-01-01 with --long)")
    ap.add_argument("--long", action="store_true", help="2015+ closes from event_study's cache (no volume features); writes lab-walkforward-long-h*.json and leaves the daily model and weekly outputs untouched")
    ap.add_argument("--family", action="store_true", help="only combine the lab-walkforward-long-h*.json files into one BH-FDR family and exit")
    ap.add_argument("--refresh", action="store_true", help="re-download prices first")
    ap.add_argument("--no-pit", action="store_true", help="skip the point-in-time S&P membership filter (reproduces the old, survivorship-flattered numbers)")
    args = ap.parse_args()
    if args.family:
        return family_report()
    args.start = args.start or (LONG_START if args.long else "2023-01-01")
    t0 = time.time()
    feats = FEATS_LONG if args.long else FEATS
    if args.long:
        close = ld.load_wide_long()
        panel = ld.build_panel(close, None, horizons=LONG_HORIZONS)
    else:
        if args.refresh or not ld.PRICES.exists():
            ld.update_prices(full=not ld.PRICES.exists())
        close, vol = ld.load_wide()
        panel = ld.build_panel(close, vol)
    pit = {"applied": False}
    if not args.no_pit:
        try:
            changes = up.fetch_changes()
            keep = up.pit_mask(panel.index, up.membership_starts(changes))
            pit = {"applied": True, "rows_dropped": int((~keep).sum()), "rows_kept": int(keep.sum()),
                   "residual": up.coverage(changes, panel.index.get_level_values("symbol").unique(), args.start)}
            panel = panel[keep]
            logger.info(f"lab_wf: point-in-time membership applied, dropped {pit['rows_dropped']} rows from before index entry; "
                        f"{pit['residual']['removed_without_prices']}/{pit['residual']['removals_since']} removed names have no prices (residual bias)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"lab_wf: point-in-time membership unavailable ({e}); running with today's members (survivorship-flattered)")
    ycol = f"y{args.h}"
    R = ranked(panel, feats)
    Rc = list(R.columns)
    data = pd.concat([R, panel[[ycol]]], axis=1).dropna(subset=Rc)
    yrank = data[ycol].groupby(level="date").rank(pct=True)
    dates = data.index.get_level_values("date").unique().sort_values()
    start = pd.Timestamp(args.start)
    folds = pd.date_range(start, dates.max(), freq="QS")
    scores = {k: [] for k in ("mom", "rev", "lowvol", "hgb", "high52", "secmom", "momvol")}
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
        scores["high52"].append(test["r_dd_high"])
        scores["secmom"].append(test["r_sec_mom_12_1"])
        if not args.long:  # momvol needs volume, which the long cache lacks
            scores["momvol"].append((test["r_mom_12_1"] + test["r_dvol_ratio"]) / 2)
    results = []
    for name, parts in scores.items():
        if not parts:
            continue
        s = pd.concat(parts)
        results.append(summarise(name, s, data.loc[s.index, ycol], args.h))
    # Benjamini-Hochberg FDR correction across this run's family of strategy tests (multiple-testing
    # correction: testing 7 strategies x cutoff>2 inflates false positives; BH controls expected FDR at q).
    testable = [r for r in results if "p_value" in r]
    rejected, crit = bh_fdr([r["p_value"] for r in testable], q=FDR_Q)
    for r, rej, c in zip(testable, rejected, crit):
        net_positive = r["spread_net_pct"] > 0
        r["skill_fdr"] = bool(rej and net_positive)
        r["fdr_q"] = FDR_Q
        r["fdr_critical_value"] = c
        if r["skill_fdr"] != r["skill"]:
            logger.warning(f"lab_wf: skill/skill_fdr disagree for {r['name']} h={args.h}: "
                            f"skill(raw t>2)={r['skill']} skill_fdr(BH q={FDR_Q})={r['skill_fdr']} "
                            f"p={r['p_value']:.4f} t={r['ic_t']:.2f}")
    # final model on everything, for the daily predictor
    if not args.long:  # the long run is research only; it must not replace the daily predictor's model
        full = data[data[ycol].notna()].iloc[::3]
        final = fit_hgb(full[Rc].values, yrank.loc[full.index].values)
        (ld.DATA / "models").mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": final, "features": Rc, "h": args.h, "trained": datetime.now(timezone.utc).isoformat()}, ld.DATA / "models" / f"hgb_h{args.h}.joblib")
    out = {"generated": datetime.now(timezone.utc).isoformat(), "horizon_days": args.h, "test_from": args.start, "cost_bps_per_side": COST_BPS,
           "results": results, "rows": int(len(data)), "symbols": int(data.index.get_level_values("symbol").nunique()),
           "survivorship_bias_warning": True, "point_in_time": pit,
           "note": "skill = legacy uncorrected cutoff (IC t-stat > 2 and positive net spread), kept for continuity. "
                   "skill_fdr = Benjamini-Hochberg FDR-corrected significance at q=0.10 across this run's family of "
                   f"{len(testable)} strategy tests - prefer skill_fdr. Overlapping windows make t-stats/p-values "
                   "optimistic, so t-stat is shrunk by 1/sqrt(h). CAVEAT: the universe is TODAY's S&P 500 members over "
                   "past years (survivorship bias: stocks that did well are more likely to be in the index today, "
                   "which flatters momentum-type strategies) - this is NOT fixed here, only labeled, plus a cheap "
                   "70%-subsample stability check on momentum-family strategies (subsample_robustness_stable). "
                   "Treat any 'skill'/'skill_fdr' as a hypothesis to confirm with the forward paper ledger, not proof."}
    (ROOT / "logs").mkdir(exist_ok=True)
    out["long_history"] = bool(args.long)
    tag = "long-" if args.long else ""
    (ROOT / "logs" / f"lab-walkforward-{tag}h{args.h}.json").write_text(json.dumps(out, indent=1))
    print("  CAVEAT: universe = today's S&P 500 members (survivorship bias); confirm with the forward paper ledger.")
    print(f"walk-forward h={args.h}d, {out['symbols']} symbols, {out['rows']} rows, {time.time() - t0:.0f}s")
    for r in results:
        if "ic_mean" in r:
            extra = ""
            if "subsample_robustness_stable" in r:
                extra = f" | subsample_stable={r['subsample_robustness_stable']}({r['subsample_robustness_frac_same_sign']})"
            print(f"  {r['name']:7s} IC {r['ic_mean']:+.3f} (t={r['ic_t']:+.1f}, p={r['p_value']:.3f}) | top-bottom decile {r['spread_gross_pct']:+.2f}% gross, {r['spread_net_pct']:+.2f}% net | days {r['n_days']} | skill={r['skill']} skill_fdr={r['skill_fdr']}{extra}")
        else:
            print("  ", r)


if __name__ == "__main__":
    main()
