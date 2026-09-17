"""Link articles that mention the same ticker within a rolling time window,
writing article ids into each other's ai_correlated_article_ids.

This surfaces clusters -- e.g. five articles about NVDA in the same 48 hours
usually means something is actually happening, versus one lone mention. Purely
mechanical (ticker overlap + time window), no AI call needed, so it's cheap to
run on every scrape cycle.

Uses ai_affected_tickers when present (AI-verified), falling back to
tickers_raw (regex-extracted) for unprocessed articles.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run: python correlate.py [--window-hours 72] [--limit 500]
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

log = get_logger("analysis.correlate")

load_dotenv()

# Tickers too generic to be a useful correlation signal on their own.
STOPWORDS = {
    "US", "UK", "EU", "AI", "CEO", "CFO", "GDP", "CPI", "FED", "ECB", "IPO",
    "ETF", "SEC", "UN", "NYSE", "NASDAQ", "FOMC", "PM", "TV", "CNBC", "WSJ",
    "MPC", "QE", "QT", "AG", "DB", "CA", "SF", "FS", "IV", "RED",
    # thematic tags used in early hand-written ai_affected_tickers entries --
    # not real ticker symbols, would otherwise pollute clusters
    "MACRO", "RATES", "ENERGY", "TECH", "TRADE", "GEOPOLITICS", "REGULATORY",
    "CAPEX", "CHINA", "SEMICONDUCTORS", "AI INFRA", "POWER", "POWER/AI INFRA",
}


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-hours", type=int, default=72)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    client = get_client()
    since = (datetime.now(timezone.utc) - timedelta(hours=args.window_hours)).isoformat()

    res = (
        client.table("articles")
        .select("id,title,published_at,ai_affected_tickers,tickers_raw")
        .gte("published_at", since)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    articles = res.data
    if not articles:
        log.info("No articles in window.")
        return

    # ticker -> list of article ids
    by_ticker = defaultdict(list)
    for a in articles:
        tickers = a.get("ai_affected_tickers") or a.get("tickers_raw") or []
        for t in tickers:
            t = t.strip().upper()
            if t and t not in STOPWORDS and len(t) <= 6:
                by_ticker[t].append(a["id"])

    # article id -> set of correlated article ids (excluding itself)
    correlated = defaultdict(set)
    clusters = 0
    for ticker, ids in by_ticker.items():
        if len(ids) < 2:
            continue
        clusters += 1
        for aid in ids:
            correlated[aid].update(i for i in ids if i != aid)

    updated = 0
    for aid, others in correlated.items():
        client.table("articles").update({
            "ai_correlated_article_ids": list(others)[:20],  # cap to keep payload sane
        }).eq("id", aid).execute()
        updated += 1

    log.info(f"Found {clusters} ticker clusters (2+ articles) in the last {args.window_hours}h.")
    log.info(f"Updated correlation links on {updated} articles.")

    # Show the biggest clusters as a sanity check
    top = sorted(by_ticker.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]
    log.info("Top clusters:")
    for ticker, ids in top:
        if len(ids) >= 2:
            log.info(f"  {ticker:8s} {len(ids)} articles")


if __name__ == "__main__":
    main()
