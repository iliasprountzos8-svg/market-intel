"""Point-in-time S&P 500 membership, to cut the survivorship bias of testing TODAY's members on past data.

Source: Wikipedia "Historical components of the S&P 500" (dated additions/removals, free). Cached in
data/sp500_changes.json and refreshed monthly.

What it fixes: a stock that was added to the index in 2024 after a huge run is no longer allowed to count
before its add date (the main way momentum-type tests get flattered). What it CANNOT fix: companies that
were removed (bought out, bankrupt, shrunk) have no price history in yfinance, so they are simply absent;
`coverage()` reports how many removals are missing so the residual bias is visible, not hidden.
"""
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "sp500_changes.json"
URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
UA = {"User-Agent": "market-intel-homelab/1.0 (personal research)"}
MAX_AGE_DAYS = 30


def norm(sym):
    return str(sym).strip().upper().replace(".", "-")


def parse_changes(df):
    """Wikipedia changes table (multi-level header) -> [{date, added, removed}] newest first."""
    df = df.copy()
    df.columns = [" ".join(str(x) for x in (c if isinstance(c, tuple) else (c,)) if "Unnamed" not in str(x)).strip() for c in df.columns]
    cols = {c.lower(): c for c in df.columns}
    date_c = next(c for k, c in cols.items() if "date" in k)
    add_c = next(c for k, c in cols.items() if k.startswith("added") and "ticker" in k)
    rem_c = next(c for k, c in cols.items() if k.startswith("removed") and "ticker" in k)
    out = []
    for _, r in df.iterrows():
        d = pd.to_datetime(r[date_c], errors="coerce")
        if pd.isna(d):
            continue  # repeated header rows etc.
        a = r[add_c] if isinstance(r[add_c], str) else None
        rm = r[rem_c] if isinstance(r[rem_c], str) else None
        out.append({"date": d.date().isoformat(), "added": norm(a) if a else None, "removed": norm(rm) if rm else None})
    return out


def fetch_changes(force=False):
    if CACHE.exists() and not force and (time.time() - CACHE.stat().st_mtime) < MAX_AGE_DAYS * 86400:
        return json.loads(CACHE.read_text())
    r = requests.get(URL, headers=UA, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    changes = parse_changes(tables[0])
    if len(changes) < 100:
        raise RuntimeError(f"only parsed {len(changes)} index changes; page layout probably changed")
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(changes))
    return changes


def membership_starts(changes):
    """symbol -> date it (last) joined the index, for symbols with a recorded addition."""
    starts = {}
    for c in changes:
        if c["added"]:
            starts[c["added"]] = max(starts.get(c["added"], ""), c["date"])
    return starts


def pit_mask(index, starts):
    """Boolean ndarray over a (date, symbol) MultiIndex: False where the symbol had not yet joined
    the index on that date. Symbols with no recorded addition are treated as members throughout."""
    dates = index.get_level_values("date")
    syms = index.get_level_values("symbol")
    start = pd.to_datetime(pd.Series(syms).map(lambda s: starts.get(norm(s))).values)
    return ~(pd.Series(start).notna().values & (dates.values < start.values))


def coverage(changes, priced_symbols, since):
    """How many index removals since `since` have no price history here (the survivorship residual)."""
    have = {norm(s) for s in priced_symbols}
    removed = {c["removed"] for c in changes if c["removed"] and c["date"] >= since}
    missing = sorted(removed - have)
    return {"removals_since": len(removed), "removed_without_prices": len(missing), "sample": missing[:12]}
