"""Historical 8-K events from SEC EDGAR, for event studies and the ablation.

EDGAR's submissions API lists every filing with its exact acceptance timestamp and the decoded 8-K item
numbers, back to 2001: free, primary-source and point-in-time (nothing is revised after the fact). The
live pipeline already reads the "recent" block (ingest/sec8k.py); this module also walks the older pages
(`filings.files`) to build a history in data/edgar_8k.parquet: symbol, accepted (UTC), form, items.

Run: python edgar_events.py [--since 2015-01-01] [--only AAPL,MSFT]
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402

log = get_logger("lab.edgar_events")
ROOT = Path(__file__).parent.parent
OUT = ROOT / "data" / "edgar_8k.parquet"
HEADERS = {"User-Agent": "MarketIntel personal research project (contact: iliasprountzos8@gmail.com)"}
FORMS = {"8-K", "8-K/A"}
EXTRA = [("ASML", "0000937966")]


def split_items(s):
    return re.findall(r"\d\.\d\d", s or "")


def rows_from_block(block, symbol, since_iso="2000-01-01"):
    """One `filings.recent`-shaped block (parallel lists) -> event rows for 8-K forms on/after since."""
    block = block or {}
    forms = block.get("form") or []
    acc = block.get("acceptanceDateTime") or [None] * len(forms)
    fdate = block.get("filingDate") or [None] * len(forms)
    items = block.get("items") or [""] * len(forms)
    out = []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        accepted = acc[i] or (f"{fdate[i]}T00:00:00.000Z" if fdate[i] else None)
        if not accepted or accepted[:10] < since_iso:
            continue
        out.append({"symbol": symbol, "accepted": accepted, "form": form, "items": ",".join(split_items(items[i]))})
    return out


def _get(url):
    err = None
    for attempt in range(4):
        time.sleep(0.15 if attempt == 0 else 2.0 * attempt)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code in (403, 429):
                err = f"http {r.status_code}"
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            err = str(e)[:80]
    raise RuntimeError(err)


def fetch_symbol(item, since_iso):
    symbol, cik = item
    try:
        js = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        rows = rows_from_block((js.get("filings") or {}).get("recent"), symbol, since_iso)
        for f in (js.get("filings") or {}).get("files") or []:
            if (f.get("filingTo") or "9999")[:10] >= since_iso:
                rows += rows_from_block(_get(f"https://data.sec.gov/submissions/{f['name']}"), symbol, since_iso)
        return symbol, rows, None
    except Exception as e:  # noqa: BLE001
        return symbol, [], str(e)


def universe(only=None):
    sp = json.load(open(ROOT / "data" / "sp500.json"))
    rows = [(x["symbol"], x["cik"]) for x in sp if x.get("cik")] + EXTRA
    if only:
        want = {s.strip().upper() for s in only.split(",")}
        rows = [r for r in rows if r[0].upper() in want]
    return rows


def backfill(since_iso="2015-01-01", only=None):
    items = universe(only)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(lambda it: fetch_symbol(it, since_iso), items))
    errs = [(s, e) for s, _, e in res if e]
    rows = [r for _, rs, _ in res for r in rs]
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("no events fetched")
    df["accepted"] = pd.to_datetime(df["accepted"], utc=True, format="ISO8601")
    df = df.drop_duplicates(["symbol", "accepted", "items"]).sort_values(["symbol", "accepted"])
    OUT.parent.mkdir(exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info(f"edgar_events: {len(df)} 8-K events for {df.symbol.nunique()} symbols since {since_iso} in {time.time() - t0:.0f}s, {len(errs)} errors {errs[:5]}")
    return df, errs


def load():
    df = pd.read_parquet(OUT)
    df["accepted"] = pd.to_datetime(df["accepted"], utc=True)
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2015-01-01")
    ap.add_argument("--only")
    a = ap.parse_args()
    df, errs = backfill(a.since, a.only)
    print(f"{len(df)} events, {df.symbol.nunique()} symbols, {len(errs)} errors")
    sys.exit(1 if len(errs) > len(universe(a.only)) * 0.1 else 0)
