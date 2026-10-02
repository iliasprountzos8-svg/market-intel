"""Prediction lab data layer: prices (S&P 500 + benchmarks) and cross-sectional features.

prices.parquet : long table (date, symbol, close, volume), incrementally refreshed from Yahoo.
build_panel()  : features per (date, symbol) + forward excess-return labels (vs SPY).

No look-ahead: features at date t use data up to t's close; labels use entry at the NEXT close (t+1)
and exit h days later, i.e. what a person acting on the signal could actually get.
"""
import json
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402

warnings.filterwarnings("ignore")
log = get_logger("lab.lab_data")
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
PRICES = DATA / "prices.parquet"
BENCH = ["SPY", "VT", "QQQ", "^VIX", "^TNX", "CL=F", "GC=F", "DX-Y.NYB"]
EXTRA = ["ASML", "TSM", "ARM"]
HORIZONS = (1, 5, 20)


def universe():
    """CAVEAT (survivorship bias): this is TODAY's S&P 500 membership, applied uniformly across the
    whole backtest history in lab_wf.py. There is no point-in-time constituent history vendored or
    fetchable here (checked: not available via yfinance or any installed package) -- a real fix is out
    of scope for a homelab research box. This is NOT corrected, only labeled (survivorship_bias_warning
    in lab-walkforward-*.json) plus a cheap 70%-ticker-subsample stability check on momentum-family
    strategies (see lab_wf.subsample_robustness). Treat 'skill'/'skill_fdr' on mom/secmom/momvol as a
    weaker claim than on strategies without this exposure."""
    sp = json.load(open(DATA / "sp500.json"))
    syms = [x["symbol"] for x in sp] + EXTRA
    sector = {x["symbol"]: x["sector"] for x in sp}
    sector.update({"ASML": "Information Technology", "TSM": "Information Technology", "ARM": "Information Technology"})
    return syms, sector


def _download(tickers, period, attempts=3, backoff=2):
    """Batch download with retry + exponential backoff (3 attempts: 2s/4s/8s). Per-symbol failures
    within a successful batch response are logged (not silently swallowed); if more than 20% of the
    batch has no usable data, that's logged as a loud warning so run_cycle can surface it."""
    df = None
    last_err = None
    for attempt in range(attempts):
        try:
            df = yf.download(tickers, period=period, interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)
            if df is not None and not df.empty:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
            df = None
        if attempt < attempts - 1:
            wait = backoff * (2 ** attempt)
            log.warning(f"lab_data._download: yfinance batch download failed/empty (attempt {attempt + 1}/{attempts}, {len(tickers)} tickers): {last_err}; retrying in {wait}s")
            time.sleep(wait)
    if df is None or df.empty:
        log.warning(f"lab_data._download: yfinance batch download FAILED after {attempts} attempts for {len(tickers)} tickers: {last_err}")
        return pd.DataFrame(columns=["date", "symbol", "close", "volume"])
    out = []
    failed = []
    for t in tickers:
        try:
            sub = df[t][["Close", "Volume"]].dropna(subset=["Close"])
        except (KeyError, TypeError):
            failed.append(t)
            continue
        if sub.empty:
            failed.append(t)
            continue
        sub = sub.reset_index().rename(columns={"Date": "date", "Close": "close", "Volume": "volume"})
        sub["symbol"] = t
        out.append(sub[["date", "symbol", "close", "volume"]])
    fail_frac = len(failed) / len(tickers) if tickers else 0.0
    if failed:
        log.warning(f"lab_data._download: {len(failed)}/{len(tickers)} symbols had no usable data this batch: {failed[:20]}{'...' if len(failed) > 20 else ''}")
    if fail_frac > 0.20:
        log.warning(f"lab_data._download: {fail_frac:.0%} of batch ({len(failed)}/{len(tickers)} symbols) failed -- this looks like a yfinance/network problem, not a handful of delisted tickers")
    return pd.concat(out) if out else pd.DataFrame(columns=["date", "symbol", "close", "volume"])


def update_prices(full=False):
    """Refresh the parquet: 5y history on first run/full, otherwise the last 15 days merged in."""
    syms, _ = universe()
    tickers = sorted(set(syms + BENCH))
    period = "5y" if (full or not PRICES.exists()) else "15d"
    parts = []
    for i in range(0, len(tickers), 100):
        parts.append(_download(tickers[i:i + 100], period))
    new = pd.concat(parts)
    new["date"] = pd.to_datetime(new["date"]).dt.tz_localize(None).dt.normalize()
    if PRICES.exists() and period != "5y":
        old = pd.read_parquet(PRICES)
        new = pd.concat([old, new]).drop_duplicates(["date", "symbol"], keep="last")
    DATA.mkdir(exist_ok=True)
    new.sort_values(["symbol", "date"]).to_parquet(PRICES, index=False)
    return new


def load_wide():
    df = pd.read_parquet(PRICES)
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    vol = df.pivot(index="date", columns="symbol", values="volume").sort_index()
    # keep only real equity trading days: holidays and half-loaded days (where only futures/indices traded)
    # would otherwise leave all-empty rows that blank out rolling features for weeks
    syms, _ = universe()
    eq = [c for c in close.columns if c in set(syms)]
    valid = close[eq].notna().sum(axis=1) >= 400
    return close[valid], vol[valid]


def _stack(df):
    try:
        return df.stack(future_stack=True)
    except TypeError:  # pandas 3: the new implementation is the only one
        return df.stack()


def build_panel(close, vol, horizons=HORIZONS):
    """Returns long DataFrame indexed (date, symbol) with features f_*, forward excess returns y_h."""
    syms, sector = universe()
    cols = [c for c in close.columns if c in set(syms)]
    c = close[cols]
    spy = close["SPY"]
    ret = c.pct_change()
    feats = {}
    feats["r1"] = ret
    feats["r5"] = c.pct_change(5)
    feats["r20"] = c.pct_change(20)
    feats["r60"] = c.pct_change(60)
    feats["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    feats["vol20"] = ret.rolling(20).std()
    feats["vol60"] = ret.rolling(60).std()
    feats["dd_high"] = c / c.rolling(252, min_periods=120).max() - 1
    dv = (c * vol[cols])
    feats["dvol_ratio"] = dv / dv.rolling(60).mean()
    spy5, spy20 = spy.pct_change(5), spy.pct_change(20)
    feats["rel5"] = feats["r5"].sub(spy5, axis=0)
    feats["rel20"] = feats["r20"].sub(spy20, axis=0)
    sec = pd.Series({s: sector.get(s, "?") for s in cols})
    r20 = feats["r20"]
    feats["sec_rel20"] = r20 - r20.T.groupby(sec).transform("mean").T
    mom = feats["mom_12_1"]
    feats["sec_mom_12_1"] = mom - mom.T.groupby(sec).transform("mean").T
    labels = {}
    for h in horizons:
        entry = c.shift(-1)
        exit_ = c.shift(-1 - h)
        fwd = exit_ / entry - 1
        sfwd = spy.shift(-1 - h) / spy.shift(-1) - 1
        labels[f"y{h}"] = fwd.sub(sfwd, axis=0)
    parts = {f"f_{k}": _stack(v) for k, v in feats.items()}
    parts.update({k: _stack(v) for k, v in labels.items()})
    panel = pd.DataFrame(parts)
    panel.index.names = ["date", "symbol"]
    panel = panel.replace([np.inf, -np.inf], np.nan)
    return panel


def cs_rank(panel, cols):
    """Cross-sectional percentile rank of each column per date (robust to regime and outliers)."""
    return panel[cols].groupby(level="date").rank(pct=True)


def news_features(dates_symbols_index):
    """News-sentiment features from signals.db (only recent history exists; NaN elsewhere).

    Knowledge time: snapshots are computed live from articles already scraped, so they are point-in-time.
    Any FUTURE backfill of article features must key on articles.scraped_at (when we could first know
    it), never published_at, or late-scraped articles leak into the past."""
    db = DATA / "signals.db"
    if not db.exists():
        return pd.DataFrame(index=dates_symbols_index)
    con = sqlite3.connect(db, timeout=120)
    df = pd.read_sql_query("select ts, symbol, model, idx, z, att_z from snapshots2 where ts is not null", con)
    if df.empty:
        return pd.DataFrame(index=dates_symbols_index)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df = df[df["ts"].dt.hour <= 20]
    df["date"] = df["ts"].dt.tz_localize(None).dt.normalize()
    df = df.sort_values("ts").groupby(["date", "symbol", "model"]).tail(1)
    wide = df.pivot_table(index=["date", "symbol"], columns="model", values=["idx", "z", "att_z"])
    wide.columns = [f"n_{a}_{b}" for a, b in wide.columns]
    return wide
