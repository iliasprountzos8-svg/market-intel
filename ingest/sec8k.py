"""Poll SEC EDGAR for new 8-K / 6-K filings of every S&P 500 company (+ ASML) and store them as articles.

Primary-source events: the submissions API already decodes the 8-K item numbers (2.02 earnings, 5.02 executive
change, 4.02 restatement ...), so no document download is needed. Polite by design: SEC-required identifying
User-Agent (shared with scraper/sec_filings.py), 4 workers, small delay, stops on repeated 429/403.

    python sec8k.py                  # last 3 days of filings for all companies
    python sec8k.py --only NVDA,MSFT --days 30 --dry-run
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(Path(__file__).parent))
from logger import get_logger  # noqa: E402
from sec_filings import SEC_HEADERS  # noqa: E402
import sec_items  # noqa: E402

log = get_logger("ingest.sec8k")
load_dotenv(ROOT / ".env")

EXTRA = [("ASML", "ASML Holding", "0000937966")]  # foreign private issuer: files 6-K


def universe(only=None):
    sp = json.load(open(ROOT / "data" / "sp500.json"))
    rows = [(x["symbol"], x["name"], x["cik"]) for x in sp if x.get("cik")] + EXTRA
    if only:
        want = {s.strip().upper() for s in only.split(",")}
        rows = [r for r in rows if r[0].upper() in want]
    return rows


def fetch_one(item, since_iso):
    """One company's submissions. On a rate limit (403/429) or a network hiccup, back off and retry
    instead of abandoning the rest of the universe: a short SEC throttle used to turn into hundreds of
    'skipped' companies and fail the whole cycle."""
    symbol, name, cik = item
    err = None
    for attempt in range(4):
        time.sleep(0.12 if attempt == 0 else 2.0 * attempt)
        try:
            r = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=SEC_HEADERS, timeout=20)
            if r.status_code in (403, 429):
                err = f"http {r.status_code}"
                continue
            r.raise_for_status()
            return item, sec_items.parse_submissions(r.json(), symbol, name, cik, since_iso), None
        except Exception as e:  # noqa: BLE001
            err = str(e)[:100]
    return item, None, err


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--only", default=None, help="comma separated symbols")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    items = universe(a.only)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda it: fetch_one(it, since), items))
    rows, errs = [], []
    for item, parsed, err in results:
        if err:
            errs.append((item[0], err))
        elif parsed:
            rows += parsed
    print(f"sec8k: polled {len(items)} companies in {time.time() - t0:.0f}s, {len(rows)} filings in window, {len(errs)} errors")
    for sym, e in errs[:5]:
        print(f"  error {sym}: {e}")
    if a.dry_run:
        for r in sorted(rows, key=lambda r: r["published_at"], reverse=True)[:12]:
            print(f"  {r['published_at'][:16]}  {r['title'][:100]}")
        return 0
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return 1
    client = create_client(url, key)
    before = client.table("articles").select("id", count="exact").eq("category", "filing").ilike("source", "SEC EDGAR%").execute().count
    for i in range(0, len(rows), 200):
        client.table("articles").upsert(rows[i:i + 200], on_conflict="url", ignore_duplicates=True).execute()
    after = client.table("articles").select("id", count="exact").eq("category", "filing").ilike("source", "SEC EDGAR%").execute().count
    print(f"sec8k: {after - before} new filing articles stored")
    return 0 if len(errs) < len(items) * 0.2 else 1


if __name__ == "__main__":
    sys.exit(main())
