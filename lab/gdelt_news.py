"""Historical news attention + tone per S&P 500 company from GDELT's free daily GKG files.

Our own article table only goes back about a month, which makes any walk-forward test of news signals
impossible. GDELT publishes one GKG file per day (https://data.gdeltproject.org/gkg/YYYYMMDD.gkg.csv.zip,
~33 MB zipped, one record per article with the organizations mentioned and a tone score). This module
streams each day, counts mentions per company and the average tone, and keeps ONLY those aggregates in
data/gdelt_daily.parquet (date, symbol, n_art, tone, pos, neg). Raw files are never stored.

Matching is by normalised company name against GKG's organization field (no tickers there), so it is
noisy: generic names are skipped (GENERIC) and a few are hand-mapped (ALIASES). Noise only costs power; it
cannot create a false edge by itself because the test is always against future returns.

Knowledge time: a record's DATE is its UTC day, which is only complete at the END of that day. Consumers
must therefore use day D's numbers no earlier than the next session (the ablation lags by one session).

Run: python gdelt_news.py [--start 2023-01-01] [--end YYYY-MM-DD]   (resumable; safe to re-run)
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402

log = get_logger("lab.gdelt_news")
ROOT = Path(__file__).parent.parent
OUT = ROOT / "data" / "gdelt_daily.parquet"
URL = "https://data.gdeltproject.org/gkg/{d}.gkg.csv.zip"
UA = {"User-Agent": "market-intel-homelab/1.0 (personal research)"}
SUFFIX = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "holdings", "holding", "group", "plc",
          "ltd", "limited", "the", "class", "a", "b", "c", "llc", "lp", "sa", "nv", "ag", "de", "new", "trust", "international", "technologies",
          "technology", "enterprises", "industries", "financial", "bancorp", "&", "and"}
# alias -> symbol; a name that is also an everyday word and would swamp the signal with noise
GENERIC = {"target", "ball", "gap", "block", "fox", "general", "american", "united", "national", "first", "southern", "western",
           "eastern", "texas", "pacific", "global", "digital", "capital", "energy", "health", "life", "regency", "iron", "mountain",
           "amcor", "paramount", "match", "live", "norwegian", "royal", "express", "monster", "wynn", "ross", "kroger"}
ALIASES = {"alphabet": "GOOGL", "google": "GOOGL", "facebook": "META", "meta platforms": "META", "amazon": "AMZN",
           "berkshire hathaway": "BRK-B", "jpmorgan": "JPM", "jp morgan": "JPM", "jpmorgan chase": "JPM", "walmart": "WMT",
           "wal-mart": "WMT", "mcdonalds": "MCD", "coca-cola": "KO", "coca cola": "KO", "j&j": "JNJ", "johnson & johnson": "JNJ",
           "procter & gamble": "PG", "at&t": "T", "exxon mobil": "XOM", "exxonmobil": "XOM", "general motors": "GM",
           "general electric": "GE", "ge aerospace": "GE", "morgan stanley": "MS", "goldman sachs": "GS", "tesla": "TSLA",
           "nvidia": "NVDA", "microsoft": "MSFT", "apple": "AAPL", "netflix": "NFLX", "intel": "INTC", "boeing": "BA",
           "pfizer": "PFE", "visa": "V", "mastercard": "MA", "disney": "DIS", "walt disney": "DIS", "oracle": "ORCL", "asml": "ASML"}


def norm_name(s):
    s = re.sub(r"[^\w&\.\- ]", " ", str(s).lower())
    toks = [t for t in s.split() if t.strip(".") not in SUFFIX]
    return " ".join(toks).strip()


def alias_map(sp500):
    """normalised alias -> symbol. First symbol wins on collisions; dual share classes keep the first."""
    out = {}
    for x in sp500:
        a = norm_name(x["name"])
        if not a or (a in GENERIC) or (len(a) < 4 and a not in ALIASES):
            continue
        out.setdefault(a, x["symbol"])
    out.update(ALIASES)
    return out


def parse_day(fileobj, amap):
    """Stream one GKG file -> ({symbol: [n_art, tone_sum, pos_sum, neg_sum]}, total_records)."""
    agg, total = {}, 0
    for line in io.TextIOWrapper(fileobj, encoding="utf-8", errors="ignore"):
        p = line.rstrip("\n").split("\t")
        if len(p) < 8 or p[0] == "DATE":
            continue
        try:
            n = int(p[1] or 1)
            tone = [float(v) for v in p[7].split(",")[:3]]
        except ValueError:
            continue
        total += n
        seen = set()
        for o in p[6].split(";"):
            sym = amap.get(norm_name(o)) if o else None
            if sym and sym not in seen:
                seen.add(sym)
                a = agg.setdefault(sym, [0, 0.0, 0.0, 0.0])
                a[0] += n
                a[1] += tone[0] * n
                a[2] += tone[1] * n
                a[3] += tone[2] * n
    return agg, total


def fetch_day(d, amap):
    url = URL.format(d=d.strftime("%Y%m%d"))
    err = None
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=180)
            if r.status_code == 404:
                return None, "missing"
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    return parse_day(f, amap), None
        except Exception as e:  # noqa: BLE001
            err = str(e)[:80]
            time.sleep(5 * (attempt + 1))
    return None, err


def to_rows(d, agg, total):
    rows = [{"date": pd.Timestamp(d), "symbol": "__ALL__", "n_art": total, "tone": 0.0, "pos": 0.0, "neg": 0.0}]
    for sym, (n, t, p, ng) in agg.items():
        rows.append({"date": pd.Timestamp(d), "symbol": sym, "n_art": n, "tone": t / n, "pos": p / n, "neg": ng / n})
    return rows


def backfill(start, end):
    amap = alias_map(json.load(open(ROOT / "data" / "sp500.json")))
    have = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame(columns=["date", "symbol", "n_art", "tone", "pos", "neg"])
    done = set(pd.to_datetime(have["date"]).dt.date.unique())
    todo = [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)) not in done]
    log.info(f"gdelt_news: {len(todo)} days to fetch ({len(done)} already stored), {len(amap)} company aliases")
    buf, missing, t0 = [], [], time.time()
    for k, d in enumerate(todo, 1):
        res, err = fetch_day(d, amap)
        if err:
            missing.append((str(d), err))
        else:
            buf += to_rows(d, *res)
        if len(buf) > 40000 or k == len(todo):
            if buf:
                have = pd.concat([have, pd.DataFrame(buf)]).drop_duplicates(["date", "symbol"], keep="last")
                OUT.parent.mkdir(exist_ok=True)
                have.to_parquet(OUT, index=False)
                buf = []
            log.info(f"gdelt_news: {k}/{len(todo)} days, {len(missing)} missing, {time.time() - t0:.0f}s")
        time.sleep(1.0)
    return missing


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=(datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat())
    a = ap.parse_args()
    miss = backfill(date.fromisoformat(a.start), date.fromisoformat(a.end))
    print(f"done, {len(miss)} days missing/failed: {miss[:10]}")
