"""Prediction lab data layer: prices (S&P 500 + benchmarks) and cross-sectional features.

prices.parquet : long table (date, symbol, close, volume), incrementally refreshed from Yahoo.
build_panel()  : features per (date, symbol) + forward excess-return labels (vs SPY).

No look-ahead: features at date t use data up to t's close; labels use entry at the NEXT close (t+1)
and exit h days later, i.e. what a person acting on the signal could actually get.
"""
import json
import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
PRICES = DATA / "prices.parquet"
BENCH = ["SPY", "VT", "QQQ", "^VIX", "^TNX", "CL=F", "GC=F", "DX-Y.NYB"]
EXTRA = ["ASML", "TSM", "ARM"]
HORIZONS = (1, 5, 20)


def universe():
    sp = json.load(open(DATA / "sp500.json"))
    syms = [x["symbol"] for x in sp] + EXTRA
    sector = {x["symbol"]: x["sector"] for x in sp}
    sector.update({"ASML": "Information Technology", "TSM": "Information Technology", "ARM": "Information Technology"})
    return syms, sector


def _download(tickers, period):
    df = yf.download(tickers, period=period, interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)
    out = []
    for t in tickers:
        try:
            sub = df[t][["Close", "Volume"]].dropna(subset=["Close"])
        except (KeyError, TypeError):
            continue
        if sub.empty:
            continue
        sub = sub.reset_index().rename(columns={"Date": "date", "Close": "close", "Volume": "volume"})
        sub["symbol"] = t
        out.append(sub[["date", "symbol", "close", "volume"]])
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
    """News-sentiment features from signals.db (only recent history exists; NaN elsewhere)."""
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
