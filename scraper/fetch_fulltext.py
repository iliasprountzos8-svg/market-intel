"""Backfill full_text for articles that only have an RSS summary so far.

Runs as a separate, slower pass from scrape.py: fetching + extracting full
article bodies is much heavier per-item than polling RSS feeds, so it's kept
on its own schedule (see .github/workflows/fetch-fulltext.yml).

Marks every attempted row with full_text_fetch_attempted=true so a paywalled
or broken URL isn't retried forever -- full_text stays null in that case and
the AI analysis pass just falls back to the RSS summary for it.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run: python fetch_fulltext.py [--limit 100]
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests
import trafilatura
from dotenv import load_dotenv
from supabase import create_client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger
from tickers import extract_tickers

log = get_logger("scraper.fetch_fulltext")

load_dotenv()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Domains confirmed to block non-browser requests outright (Cloudflare/401) --
# see README "Known, accepted limitations". Skipping them outright saves a
# network round-trip per article instead of failing after a timeout.
BLOCKED_DOMAINS = ("investing.com",)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException)
)
def robust_get(url, **kwargs):
    kwargs.setdefault("timeout", 15)
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def resolve_url(url: str) -> str:
    """Google News RSS links are redirect wrappers; follow them to the real article URL."""
    if "news.google.com" not in url:
        return url
    try:
        resp = robust_get(url, headers=HEADERS, allow_redirects=True)
        return resp.url
    except Exception:
        return url


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between fetches, be polite")
    args = parser.parse_args()

    client = get_client()
    res = (
        client.table("articles")
        .select("id,url,source,title,tickers_raw")
        .eq("full_text_fetch_attempted", False)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    rows = res.data
    if not rows:
        log.info("Nothing to backfill.")
        return

    ok, failed, skipped = 0, 0, 0
    source_metrics = {}
    for row in rows:
        now = datetime.now(timezone.utc).isoformat()

        if any(domain in row["url"] for domain in BLOCKED_DOMAINS):
            client.table("articles").update({
                "full_text_fetch_attempted": True,
                "full_text_fetched_at": now,
            }).eq("id", row["id"]).execute()
            skipped += 1
            continue

        text = None
        try:
            real_url = resolve_url(row["url"])
            # Use our own browser-like headers directly -- trafilatura's built-in
            # fetcher gets 401/403'd by several sources (MarketWatch, Seeking Alpha)
            # that a normal browser UA sails through.
            resp = robust_get(real_url, headers=HEADERS)
            downloaded = resp.text
            text = trafilatura.extract(downloaded) if downloaded else None
            if text and len(text) < 200:
                text = None  # too short to be the real article body (likely a stub/consent page)
        except Exception as e:
            log.warning(f"fetch failed for {row['url']}: {e}")
            text = None

        payload = {
            "full_text_fetch_attempted": True,
            "full_text_fetched_at": now,
        }
        if text:
            payload["full_text"] = text
            # Re-derive tickers now that we have the real article body --
            # the title/RSS-snippet pass at scrape time misses almost every
            # company-name mention (see tickers.py docstring).
            existing = set(row.get("tickers_raw") or [])
            found = set(extract_tickers(f"{row.get('title', '')} {text}"))
            merged = sorted(existing | found)
            if merged != sorted(existing):
                payload["tickers_raw"] = merged
            ok += 1
        else:
            failed += 1

        try:
            client.table("articles").update(payload).eq("id", row["id"]).execute()
        except Exception as e:
            log.error(f"db update failed for {row['id']}: {e}")

        # Track hit rate per source
        source = row.get("source", "Unknown")
        if source not in source_metrics:
            source_metrics[source] = {"source": source, "full_text_fetched": 0, "full_text_failed": 0, "skipped_domains": 0}
        if any(domain in row["url"] for domain in BLOCKED_DOMAINS):
            source_metrics[source]["skipped_domains"] += 1
        elif text:
            source_metrics[source]["full_text_fetched"] += 1
        else:
            source_metrics[source]["full_text_failed"] += 1

        time.sleep(args.delay)

    log.info(f"Done. Full text fetched: {ok}, failed/unavailable: {failed}, "
          f"skipped (known-blocked domain): {skipped}, total: {len(rows)}")

    health_metrics = list(source_metrics.values())
    if health_metrics:
        try:
            client.table("source_health").insert(health_metrics).execute()
            log.info("Source health metrics recorded for full text backfill.")
        except Exception as e:
            log.warning(f"Failed to record source health: {e}")


if __name__ == "__main__":
    main()
