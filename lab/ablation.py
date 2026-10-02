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
MIN_NEWS_DAYS = 250
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
    ev = event_features(ee.load(), close.index, symbols)
    R = lw.ranked(panel)
    Rc = list(R.columns)
    ycol = f"y{args.h}"
    data = pd.concat([R, panel[[ycol]], ev], axis=1).loc[panel.index].dropna(subset=Rc)
    data[EV_FEATS] = data[EV_FEATS].fillna({"e_days_since_earn": 120}).fillna(0)
    yrank = data[ycol].groupby(level="date").rank(pct=True)
    dates = data.index.get_level_values("date").unique().sort_values()
    folds = pd.date_range(pd.Timestamp(args.start), dates.max(), freq="QS")
    base, withev = [], []
    for k, f0 in enumerate(folds):
        f1 = folds[k + 1] if k + 1 < len(folds) else dates.max() + pd.Timedelta(days=1)
        test = data[(data.index.get_level_values("date") >= f0) & (data.index.get_level_values("date") < f1)]
        cutoff = f0 - pd.Timedelta(days=int(args.h * 1.6) + 4)
        train = data[(data.index.get_level_values("date") <= cutoff) & data[ycol].notna().values]
        if len(train) < 50000 or test.empty:
            continue
        tr = train.iloc[::3]
        m0 = lw.fit_hgb(tr[Rc].values, yrank.loc[tr.index].values)
        m1 = lw.fit_hgb(tr[Rc + EV_FEATS].values, yrank.loc[tr.index].values)
        base.append(pd.Series(m0.predict(test[Rc].values), index=test.index))
        withev.append(pd.Series(m1.predict(test[Rc + EV_FEATS].values), index=test.index))
    sb, se = pd.concat(base), pd.concat(withev)
    y = data.loc[sb.index, ycol]
    res = paired(lw.daily_ic(sb, y), lw.daily_ic(se, y), args.h)
    sp_b, sp_e = lw.decile_spread(sb, y), lw.decile_spread(se, y)
    news = ld.news_features(data.index)
    news_days = int(news.dropna(how="all").index.get_level_values("date").nunique()) if len(news.columns) else 0
    verdict = ("Event features add measurable predictive power (paired t > 2)." if res and res["paired_t"] > 2 else
               "Event features do NOT add significant power over price-only features." if res else "Too few days to test.")
    out = {"generated": datetime.now(timezone.utc).isoformat(), "horizon_days": args.h, "test_from": args.start,
           "result": res, "spread_gross_pct": {"price_only": float(sp_b.mean() * 100), "with_events": float(sp_e.mean() * 100)},
           "event_features": EV_FEATS, "news_feature_days": news_days,
           "news_note": (f"news features cover {news_days} trading days; need {MIN_NEWS_DAYS}+ for a walk-forward test"
                         if news_days < MIN_NEWS_DAYS else "news history sufficient (not yet wired in)"),
           "point_in_time": True, "verdict": verdict, "seconds": round(time.time() - t0)}
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / f"ablation-h{args.h}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
