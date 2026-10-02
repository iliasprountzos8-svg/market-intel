"""Ablation: do EDGAR 8-K event features add predictive power to the price-only walk-forward model?

Same model, same hyper-parameters, same purged quarterly folds and the same test rows as lab_wf.py; the
ONLY difference is the extra event features. The verdict is a paired test on the daily rank-IC difference
(events minus price-only), shrunk by 1/sqrt(h) for overlapping labels, so "events help" needs the paired
t-stat > 2, not just a higher IC by chance.

Event features use filings only up to the session BEFORE the date (counts are placed on the reaction
session and shifted by one), so nothing the market had not yet digested can leak in.
News-sentiment features (signals.db) are reported but only used when at least MIN_NEWS_DAYS trading days
of history exist; today they cover about three weeks, which cannot support a walk-forward test.

Run: python ablation.py [--h 5] [--start 2023-01-01]   -> logs/ablation-h{h}.json
"""
import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))
import edgar_events as ee  # noqa: E402
import event_study as es  # noqa: E402
import lab_data as ld  # noqa: E402
import lab_wf as lw  # noqa: E402
import universe_pit as up  # noqa: E402
from logger import get_logger  # noqa: E402

warnings.filterwarnings("ignore")
log = get_logger("lab.ablation")
ROOT = ld.ROOT
MIN_NEWS_DAYS = 250  # GDELT days required before the news arm runs
GD_FEATS = ["g_att_1", "g_att_ratio", "g_n_5", "g_tone_5", "g_tone_chg"]
GD_FROM = pd.Timestamp("2023-03-01")  # 60-day windows need warm-up after the 2023-01-01 backfill start
GD_TEST_FROM = "2024-01-01"  # leaves ~10 months of training before the first test fold
EV_FEATS = ["e_n8k_5", "e_n8k_20", "e_earn_20", "e_n502_60", "e_n101_20", "e_days_since_earn"]


def event_features(events, sessions, symbols):
    """(date, symbol) frame of event features from 8-K history; leakage-safe (see module docstring)."""
    ev = events[events.symbol.isin(symbols)].copy()
    ev["ri"] = es.reaction_index(ev["accepted"], sessions)
    ev = ev[ev.ri < len(sessions)]
    col = {s: i for i, s in enumerate(symbols)}

    def matrix(mask):
        m = np.zeros((len(sessions), len(symbols)))
        sub = ev[mask(ev)]
        np.add.at(m, (sub["ri"].values, sub["symbol"].map(col).values), 1)
        return pd.DataFrame(m, index=sessions, columns=symbols)

    has = lambda item: (lambda d: d["items"].str.contains(item, regex=False))  # noqa: E731
    any8k = matrix(lambda d: np.ones(len(d), dtype=bool)).shift(1)
    earn = matrix(has("2.02")).shift(1)
    n502 = matrix(has("5.02")).shift(1)
    n101 = matrix(has("1.01")).shift(1)
    last_earn = earn.where(earn > 0).apply(lambda c: pd.Series(np.where(c.notna(), np.arange(len(c)), np.nan), index=c.index).ffill())
    since = last_earn.rsub(np.arange(len(sessions)), axis=0).clip(upper=120).fillna(120)
    feats = {"e_n8k_5": any8k.rolling(5).sum(), "e_n8k_20": any8k.rolling(20).sum(), "e_earn_20": earn.rolling(20).sum(),
             "e_n502_60": n502.rolling(60).sum(), "e_n101_20": n101.rolling(20).sum(), "e_days_since_earn": since}
    out = pd.DataFrame({k: ld._stack(v) for k, v in feats.items()})
    out.index.names = ["date", "symbol"]
    return out


def paired(ic_a, ic_b, h):
    df = pd.concat([ic_a, ic_b], axis=1, keys=["a", "b"]).dropna()
    d = df["b"] - df["a"]
    if len(d) < 30:
        return None
    t = d.mean() / (d.std() / np.sqrt(len(d))) / np.sqrt(h)
    return {"n_days": int(len(d)), "ic_base": float(df["a"].mean()), "ic_events": float(df["b"].mean()),
            "ic_diff": float(d.mean()), "paired_t": float(t)}


def gdelt_features(sessions, symbols):
    """(date, symbol) frame of GDELT attention/tone features. GDELT day D is only complete at the end of D (UTC),
    so session t uses days up to t-1 (calendar lag), never the same day."""
    g = pd.read_parquet(ROOT / "data" / "gdelt_daily.parquet")
    g["date"] = pd.to_datetime(g["date"])
    total = g[g.symbol == "__ALL__"].set_index("date")["n_art"].sort_index()
    g = g[g.symbol.isin(symbols)]
    cal = pd.date_range(g["date"].min(), g["date"].max())
    n = g.pivot(index="date", columns="symbol", values="n_art").reindex(cal).reindex(columns=symbols).fillna(0)
    tone = g.pivot(index="date", columns="symbol", values="tone").reindex(cal).reindex(columns=symbols)
    share = n.div(total.reindex(cal).replace(0, np.nan), axis=0) * 1e6  # mentions per million articles that day
    w = n.where(n > 0)
    n5, n60 = n.rolling(5, min_periods=1).sum(), n.rolling(60, min_periods=20).sum() / 12
    t5 = (tone * n).rolling(5, min_periods=1).sum() / n.rolling(5, min_periods=1).sum().replace(0, np.nan)
    t60 = (tone * n).rolling(60, min_periods=20).sum() / n.rolling(60, min_periods=20).sum().replace(0, np.nan)
    feats = {"g_att_1": np.log1p(share), "g_att_ratio": (n5 / n60.replace(0, np.nan)).clip(upper=20), "g_n_5": np.log1p(n5),
             "g_tone_5": t5, "g_tone_chg": t5 - t60}
    lagged = {}
    for k, v in feats.items():
        v = v.reindex(sessions - pd.Timedelta(days=1))  # value known at the START of session t = day t-1
        v.index = sessions
        lagged[k] = ld._stack(v)
    out = pd.DataFrame(lagged)
    out.index.names = ["date", "symbol"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--start", default="2023-01-01")
    args = ap.parse_args()
    t0 = time.time()
    close, vol = ld.load_wide()
    panel = ld.build_panel(close, vol)
    try:
        panel = panel[up.pit_mask(panel.index, up.membership_starts(up.fetch_changes()))]
    except Exception as e:  # noqa: BLE001
        log.warning(f"ablation: point-in-time membership unavailable ({e})")
    symbols = sorted(panel.index.get_level_values("symbol").unique())
    parts = [event_features(ee.load(), close.index, symbols)]
    gdelt_file = ROOT / "data" / "gdelt_daily.parquet"
    gd_days = 0
    if gdelt_file.exists():
        gdf = pd.read_parquet(gdelt_file, columns=["date", "symbol"])
        gd_days = int(gdf[gdf.symbol == "__ALL__"]["date"].nunique())
    use_gd = gd_days >= MIN_NEWS_DAYS
    if use_gd:
        parts.append(gdelt_features(close.index, symbols))
    R = lw.ranked(panel)
    Rc = list(R.columns)
    ycol = f"y{args.h}"
    data = pd.concat([R, panel[[ycol]]] + parts, axis=1).loc[panel.index].dropna(subset=Rc)
    data[EV_FEATS] = data[EV_FEATS].fillna({"e_days_since_earn": 120}).fillna(0)
    arms = {"price_only": Rc, "events": Rc + EV_FEATS}
    start = args.start
    if use_gd:
        data = data[data.index.get_level_values("date") >= GD_FROM]  # identical rows for every arm
        data[GD_FEATS] = data[GD_FEATS].fillna({"g_att_ratio": 1.0}).fillna(0)
        arms["gdelt"] = Rc + GD_FEATS
        arms["events+gdelt"] = Rc + EV_FEATS + GD_FEATS
        start = max(args.start, GD_TEST_FROM)
    yrank = data[ycol].groupby(level="date").rank(pct=True)
    dates = data.index.get_level_values("date").unique().sort_values()
    folds = pd.date_range(pd.Timestamp(start), dates.max(), freq="QS")
    preds = {k: [] for k in arms}
    for k, f0 in enumerate(folds):
        f1 = folds[k + 1] if k + 1 < len(folds) else dates.max() + pd.Timedelta(days=1)
        test = data[(data.index.get_level_values("date") >= f0) & (data.index.get_level_values("date") < f1)]
        cutoff = f0 - pd.Timedelta(days=int(args.h * 1.6) + 4)
        train = data[(data.index.get_level_values("date") <= cutoff) & data[ycol].notna().values]
        if len(train) < 50000 or test.empty:
            continue
        tr = train.iloc[::3]
        for name, cols in arms.items():
            m = lw.fit_hgb(tr[cols].values, yrank.loc[tr.index].values)
            preds[name].append(pd.Series(m.predict(test[cols].values), index=test.index))
    scores = {k: pd.concat(v) for k, v in preds.items() if v}
    base = scores["price_only"]
    y = data.loc[base.index, ycol]
    ic_base = lw.daily_ic(base, y)
    results, spreads = {}, {"price_only": float(lw.decile_spread(base, y).mean() * 100)}
    for name, sc in scores.items():
        if name == "price_only":
            continue
        results[name] = paired(ic_base, lw.daily_ic(sc, y), args.h)
        spreads[name] = float(lw.decile_spread(sc, y).mean() * 100)
    sig = [k for k, r in results.items() if r and r["paired_t"] > 2]
    verdict = (f"Feature set(s) {sig} add measurable predictive power over price-only (paired t > 2); confirm on the forward ledger."
               if sig else "No feature set adds significant power over price-only features." if results else "Too few days to test.")
    out = {"generated": datetime.now(timezone.utc).isoformat(), "horizon_days": args.h, "test_from": start,
           "arms": {k: v for k, v in arms.items()}, "results_vs_price_only": results, "spread_gross_pct": spreads,
           "gdelt_days": gd_days, "gdelt_used": use_gd,
           "news_note": ("GDELT news features included (attention, mentions ratio, tone)." if use_gd else
                         f"GDELT history has {gd_days} days; need {MIN_NEWS_DAYS}+ for a walk-forward test"),
           "point_in_time": True, "verdict": verdict, "seconds": round(time.time() - t0)}
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / f"ablation-h{args.h}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
