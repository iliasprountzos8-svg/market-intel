"""Event study: do 8-K filings (by item type) predict what the stock does AFTER the market has reacted?

Discovery/confirmation split: effects are measured on events before CONFIRM_FROM (discovery) and again on
events from CONFIRM_FROM onward (confirmation, never used to pick anything). A group only counts as
"confirmed" if the sign repeats, the confirmation t-stat is > 2 after Benjamini-Hochberg FDR across all
group x horizon tests, and it is still positive net of trading costs.

Conservative timing so nothing needs to be faster than this system really is:
  reaction day D = first session whose 16:00 New York close is at/after the filing's acceptance time;
  entry = close of the session AFTER D, exit = h sessions later. The abnormal return is vs SPY.
  The reaction-day move itself is reported for context but is NOT assumed tradable.
Standard errors are clustered by entry date (earnings seasons bunch hundreds of events on a few days), and
repeat events of the same stock/group inside the holding window are dropped so windows do not overlap.

Caveat printed in the output: universe = index members that still have prices (point-in-time entry dates
applied; companies removed from the index and delisted are absent), so long-side drift is flattered.
survivorship_bound() quantifies this: it blends in the unseen names at assumed returns and reports the
breakeven return at which a result would stop being significant. It bounds the bias; it does not remove it.

Run: python event_study.py [--refresh-prices]   -> logs/event-study.json and logs/event-study.md
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))
import edgar_events as ee  # noqa: E402
import universe_pit as up  # noqa: E402
from fdr import bh_fdr  # noqa: E402
from logger import get_logger  # noqa: E402

log = get_logger("lab.event_study")
ROOT = Path(__file__).parent.parent
LONG = ROOT / "data" / "prices_long.parquet"
HORIZONS = (1, 5, 20)
CONFIRM_FROM = pd.Timestamp("2021-01-01")
COST = 0.20  # % round trip at 10 bps per side
MIN_EVENTS = 150
ITEM_NAMES = {"2.02": "earnings", "5.02": "officer/director change", "1.01": "material agreement", "8.01": "other events",
              "7.01": "reg FD", "2.03": "new debt", "5.07": "shareholder vote", "2.05": "restructuring",
              "2.01": "acquisition/disposal", "1.02": "agreement terminated", "3.02": "unregistered equity sale",
              "5.03": "bylaws change", "4.02": "restatement", "2.06": "impairment", "4.01": "auditor change",
              "3.01": "listing notice", "2.04": "obligation accelerated"}


def load_prices(refresh=False):
    """Long daily closes since 2015 for the universe + SPY (own cache; the lab's 5y parquet is untouched)."""
    if LONG.exists() and not refresh and (time.time() - LONG.stat().st_mtime) < 7 * 86400:
        return pd.read_parquet(LONG)
    import yfinance as yf
    import lab_data as ld
    syms, _ = ld.universe()
    tickers = sorted(set(syms + ["SPY"]))
    parts = []
    for i in range(0, len(tickers), 100):
        df = yf.download(tickers[i:i + 100], start="2014-12-01", interval="1d", auto_adjust=True,
                         group_by="ticker", threads=True, progress=False)
        for t in tickers[i:i + 100]:
            try:
                s = df[t]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if len(s):
                parts.append(pd.DataFrame({"date": s.index.tz_localize(None).normalize(), "symbol": t, "close": s.values}))
    out = pd.concat(parts)
    out.to_parquet(LONG, index=False)
    return out


def reaction_index(accepted_utc, sessions):
    """Position in `sessions` (sorted DatetimeIndex) of each event's reaction session: the first session
    whose 16:00 New York close is at or after the acceptance time."""
    ny = accepted_utc.dt.tz_convert("America/New_York")
    day = ny.dt.tz_localize(None).dt.normalize()
    after_close = (ny.dt.hour >= 16).astype(int)
    d = day + pd.to_timedelta(after_close, unit="D")  # after the bell -> the next calendar day's session
    return sessions.searchsorted(d.values, side="left")


def build_events(events, close, h_list=HORIZONS):
    """Long table: one row per (event, item group, horizon) with the abnormal return from entry to exit."""
    sessions = close.index
    ev = events[events.symbol.isin(close.columns)].copy()
    ev["ri"] = reaction_index(ev["accepted"], sessions)
    ev = ev[(ev.ri >= 1) & ((ev.ri + 1 + max(h_list)) < len(sessions))]
    cvals = close.values
    col = {s: i for i, s in enumerate(close.columns)}
    spyv = close["SPY"].values
    rows = []
    for r in ev.itertuples(index=False):
        ci, ri = col[r.symbol], r.ri
        p_react = cvals[ri, ci] / cvals[ri - 1, ci] - 1
        s_react = spyv[ri] / spyv[ri - 1] - 1
        e = ri + 1
        groups = {i for i in r.items.split(",") if i in ITEM_NAMES} | {"any"}
        for h in h_list:
            p0, p1 = cvals[e, ci], cvals[e + h, ci]
            if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0:
                continue
            ex = ((p1 / p0 - 1) - (spyv[e + h] / spyv[e] - 1)) * 100
            for g in groups:
                rows.append((r.symbol, g, h, sessions[e], ex, (p_react - s_react) * 100))
    return pd.DataFrame(rows, columns=["symbol", "group", "h", "entry", "excess", "react"])


def drop_overlaps(df):
    """Keep the first event per (symbol, group, h) inside any h-session window (calendar proxy: 1.5*h days)."""
    keep = []
    for (_, _, h), sub in df.sort_values("entry").groupby(["symbol", "group", "h"], sort=False):
        last = None
        for idx, e in zip(sub.index, sub["entry"]):
            if last is None or (e - last).days > h * 1.5:
                keep.append(idx)
                last = e
    return df.loc[keep]


def clustered_stats(x, clusters):
    """Mean, cluster-robust t-stat (clusters = entry dates) and two-sided p."""
    n = len(x)
    if n < 30:
        return None
    m = x.mean()
    cs = pd.Series(x - m).groupby(np.asarray(clusters)).sum()
    se = np.sqrt((cs ** 2).sum()) / n
    if se == 0:
        return None
    from scipy import stats as sstats  # lazy: only the lab venv has scipy
    t = m / se
    p = 2 * sstats.t.sf(abs(t), df=max(len(cs) - 1, 1))
    return {"n": int(n), "mean_pct": float(m), "t": float(t), "p": float(p),
            "pos_share": float((x > 0).mean()), "median_pct": float(np.median(x))}


def analyse(ev):
    results = []
    for (g, h), sub in ev.groupby(["group", "h"]):
        if len(sub) < MIN_EVENTS:
            continue
        disc, conf = sub[sub.entry < CONFIRM_FROM], sub[sub.entry >= CONFIRM_FROM]
        d = clustered_stats(disc["excess"].values, disc["entry"].values) if len(disc) else None
        c = clustered_stats(conf["excess"].values, conf["entry"].values) if len(conf) else None
        if not c or not d:
            continue
        direction = np.sign(d["mean_pct"])  # the side is chosen from discovery only
        results.append({"group": g, "name": ITEM_NAMES.get(g, g), "h": int(h), "discovery": d, "confirmation": c,
                        "side": "long" if direction > 0 else "short",
                        "confirm_net_pct": float(direction * c["mean_pct"] - COST),
                        "same_sign": bool(np.sign(d["mean_pct"]) == np.sign(c["mean_pct"])),
                        "reaction_day_pct": float(sub["react"].mean())})
    if results:
        rejected, _ = bh_fdr([r["confirmation"]["p"] for r in results], q=0.10)
        for r, rej in zip(results, rejected):
            r["confirmed"] = bool(rej and r["same_sign"] and r["confirm_net_pct"] > 0 and abs(r["confirmation"]["t"]) > 2)
    return results


BIAS_SCENARIOS = (0.0, -1.0, -2.0)  # assumed mean excess % per event for names missing from the data


def survivorship_bound(r, m, scenarios=BIAS_SCENARIOS):
    """How fragile is one result to the delisted names we cannot price? Missing names are a share `m` of all
    events; the observed mean is only the survivors' (1-m). For each assumed mean excess return R of the
    missing names the blended mean is (1-m)*mean + m*R, rescored with the observed standard error (an
    approximation: the missing names' variance is unknown). `breakeven_R` is the missing-name mean that
    would make the confirmation t fall to 2. Delisted names plausibly underperform, so R <= 0 is the
    realistic direction; for a long side this is the harmful one."""
    c = r["confirmation"]
    sign = 1.0 if r["side"] == "long" else -1.0
    se = abs(c["mean_pct"] / c["t"]) if c["t"] else None
    out = {"missing_share": m, "scenarios": []}
    for R in scenarios:
        blended = (1 - m) * c["mean_pct"] + m * R
        out["scenarios"].append({"R": R, "mean_pct": blended, "t": blended / se if se else None,
                                 "net_pct": sign * blended - COST})
    if se and m > 0:
        need = sign * 2 * se  # blended mean at which |t| = 2 on the chosen side
        out["breakeven_R"] = (need - (1 - m) * c["mean_pct"]) / m
    return out


def missing_share(n_symbols, n_missing):
    """Share of events assumed to come from unpriced (delisted) names: missing / (priced + missing)."""
    total = n_symbols + n_missing
    return n_missing / total if total else 0.0


def report(results, meta):
    L = [f"# 8-K event study, {datetime.now(timezone.utc):%Y-%m-%d}",
         f"Events {meta['events']}, symbols {meta['symbols']}, discovery before {CONFIRM_FROM.date()}, confirmation after. "
         "Excess return vs SPY from the close AFTER the reaction day; cluster-robust t; BH FDR q=0.10 across all tests; "
         "costs 20 bps round trip.",
         "CAVEAT: only index members that still have prices (survivorship), so long-side drift is flattered. Research, not advice.", ""]
    conf = [r for r in results if r["confirmed"]]
    L.append(f"Tests run: {len(results)}. Confirmed out-of-sample after FDR and costs: {len(conf)}.")
    for r in sorted(conf, key=lambda r: -abs(r["confirmation"]["t"])):
        c = r["confirmation"]
        L.append(f"  CONFIRMED {r['name']} h={r['h']}d {r['side']}: mean {c['mean_pct']:+.2f}% (t={c['t']:+.1f}, n={c['n']}), net {r['confirm_net_pct']:+.2f}%")
    L += ["", "Largest confirmation-sample effects (confirmed or not):"]
    for r in sorted(results, key=lambda r: -abs(r["confirmation"]["t"]))[:12]:
        d, c = r["discovery"], r["confirmation"]
        L.append(f"  {r['name']:26s} h={r['h']:>2}d disc {d['mean_pct']:+.2f}% (t={d['t']:+.1f}) | conf {c['mean_pct']:+.2f}% "
                 f"(t={c['t']:+.1f}, n={c['n']}) same_sign={r['same_sign']} confirmed={r['confirmed']}")
    bias = meta.get("bias")
    if bias:
        L += ["", f"Survivorship bound: {bias['missing']} of {bias['removals']} index removals since {CONFIRM_FROM.date()} have no prices, "
                  f"so about {bias['share']:.0%} of events are unseen. Blended confirmation mean if those names averaged R % excess per event "
                  f"(t uses the observed SE; breakeven R = value that drops |t| to 2; None if already below 2):"]
        held = [r for r in results if r["same_sign"]]
        L.append(f"  (only the {len(held)} of {len(results)} tests whose sign held out-of-sample; a flipped sign fails regardless of survivorship)")
        for r in sorted(held, key=lambda r: -abs(r["confirmation"]["t"]))[:8]:
            b = r.get("bias_bound")
            if not b:
                continue
            sc = "  ".join(f"R={s['R']:+.0f}: {s['mean_pct']:+.2f}% (t={s['t']:+.1f})" for s in b["scenarios"])
            be = b.get("breakeven_R")
            be = f"{be:+.1f}%" if be is not None and abs(r["confirmation"]["t"]) > 2 else "None"
            L.append(f"  {r['name']:26s} h={r['h']:>2}d {r['side']:5s} {sc} | breakeven R {be}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-prices", action="store_true")
    a = ap.parse_args()
    events = ee.load()
    prices = load_prices(a.refresh_prices)
    prices["date"] = pd.to_datetime(prices["date"])
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    close = close[close.notna().sum(axis=1) >= 300]  # real equity sessions only
    try:  # drop events from before a company joined the index
        starts = up.membership_starts(up.fetch_changes())
        st = events["symbol"].map(lambda s: starts.get(up.norm(s)))
        day = events["accepted"].dt.tz_localize(None).dt.strftime("%Y-%m-%d")
        keep = st.isna() | (day >= st.fillna(""))
        log.info(f"event_study: point-in-time filter dropped {int((~keep).sum())} events from before index entry")
        events = events[keep]
    except Exception as e:  # noqa: BLE001
        log.warning(f"event_study: point-in-time membership unavailable ({e})")
    ev = drop_overlaps(build_events(events, close))
    results = analyse(ev)
    meta = {"events": int(ev[ev.group == "any"].drop_duplicates(["symbol", "entry"]).shape[0]), "symbols": int(ev.symbol.nunique())}
    try:
        cov = up.coverage(up.fetch_changes(), close.columns, CONFIRM_FROM.date().isoformat())
        m = missing_share(meta["symbols"], cov["removed_without_prices"])
        for r in results:
            r["bias_bound"] = survivorship_bound(r, m)
        meta["bias"] = {"removals": cov["removals_since"], "missing": cov["removed_without_prices"], "share": m}
    except Exception as e:  # noqa: BLE001
        log.warning(f"event_study: survivorship bound unavailable ({e})")
    txt = report(results, meta)
    print(txt)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "event-study.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "meta": meta, "results": results}, indent=1, default=str))
    (ROOT / "logs" / "event-study.md").write_text(txt)


if __name__ == "__main__":
    main()
