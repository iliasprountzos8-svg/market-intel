"""Pull articles from RSS feeds (+ optional NewsAPI) and upsert them into Supabase.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY   (service role key -- bypasses RLS, used only server-side)
Optional:
  NEWSAPI_KEY

Run: python scrape.py
"""

import os
import re
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests
from dateutil import parser as dateparser
from dotenv import load_dotenv
from supabase import create_client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from rapidfuzz import fuzz

import json
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

from sec_filings import scrape_sec_filings
from tickers import extract_tickers

load_dotenv()
log = get_logger("scraper.scrape")

# Promotional/ad-copy junk that occasionally leaks into RSS feeds (seen from
# Investing.com specifically) -- not news, skip outright rather than let it
# pollute the DB and waste an AI-analysis pass.
SPAM_PATTERNS = re.compile(
    r"\b(last chance|% off|subscribe now|sign up now|limited time|"
    r"exclusive offer|hurry|don't miss out)\b",
    re.IGNORECASE,
)


def is_spam(title: str, summary: str) -> bool:
    return bool(SPAM_PATTERNS.search(f"{title} {summary}"))


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


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


def parse_published(entry):
    for field in ("published", "updated", "pubDate"):
        val = entry.get(field)
        if val:
            try:
                return dateparser.parse(val).astimezone(timezone.utc).isoformat()
            except Exception:
                continue
    return datetime.now(timezone.utc).isoformat()


def load_config(client=None):
    config_path = Path(__file__).parent.parent / "dashboard" / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)

    # Per-source enabled/disabled overrides set from the dashboard's admin
    # page (stored in Supabase, not config.json -- see migration
    # 002_notifications_and_settings.sql for why: a git-committed file can't
    # be toggled at runtime from a Vercel serverless function).
    if client is not None:
        try:
            res = client.table("source_overrides").select("name,enabled").execute()
            overrides = {row["name"]: row["enabled"] for row in res.data}
            for src in config.get("sources", []):
                if src["name"] in overrides:
                    src["enabled"] = overrides[src["name"]]
        except Exception as e:
            log.warning(f"Failed to load source_overrides, using config.json as-is: {e}")

    return config

def scrape_rss(client=None):
    config = load_config(client)
    rows = []
    health_metrics = []
    for src in config.get("sources", []):
        if not src.get("enabled", True):
            continue
        name = src["name"]
        feed_url = src["url"]
        category = src["category"]
        
        errors = 0
        try:
            resp = robust_get(feed_url, headers={"User-Agent": "MarketIntel Scraper"})
            feed = feedparser.parse(resp.content)
        except Exception as e:
            log.warning(f"failed to parse {name}: {e}")
            errors += 1
            health_metrics.append({"source": name, "entries_fetched": 0, "errors": errors})
            continue

        entries_added = 0
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = entry.get("summary", "") or entry.get("description", "")
            if is_spam(title, summary):
                continue
            text_for_tickers = f"{title} {summary}"
            rows.append({
                "source": name,
                "url": link,
                "title": title,
                "summary_raw": summary[:2000] if summary else None,
                "author": entry.get("author"),
                "published_at": parse_published(entry),
                "category": category,
                "tickers_raw": extract_tickers(text_for_tickers),
            })
            entries_added += 1
        log.info(f"{name}: {entries_added} entries")
        health_metrics.append({"source": name, "entries_fetched": entries_added, "errors": errors})
        time.sleep(0.5)
    return rows, health_metrics


def scrape_newsapi():
    api_key = os.environ.get("NEWSAPI_KEY")
    if not api_key:
        log.info("NEWSAPI_KEY not set, skipping NewsAPI")
        return [], []
    
    config = load_config()
    newsapi_query = config.get("newsapi_query", "")
    
    errors = 0
    try:
        resp = robust_get(
            "https://newsapi.org/v2/everything",
            params={
                "q": newsapi_query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 50,
                "apiKey": api_key,
            }
        )
        data = resp.json()
    except Exception as e:
        log.warning(f"NewsAPI error: {e}")
        return [], [{"source": "NewsAPI", "entries_fetched": 0, "errors": 1}]

    articles = data.get("articles", [])
    rows = []
    for a in articles:
        title = (a.get("title") or "").strip()
        url = (a.get("url") or "").strip()
        if not title or not url:
            continue
        desc = a.get("description") or ""
        rows.append({
            "source": a.get("source", {}).get("name", "NewsAPI"),
            "url": url,
            "title": title,
            "summary_raw": desc[:2000] if desc else None,
            "author": a.get("author"),
            "published_at": a.get("publishedAt") or datetime.now(timezone.utc).isoformat(),
            "category": "general",
            "tickers_raw": extract_tickers(f"{title} {desc}"),
        })
    log.info(f"NewsAPI: {len(rows)} entries")
    return rows, [{"source": "NewsAPI", "entries_fetched": len(rows), "errors": 0}]


def main():
    client = get_client()
    rss_rows, rss_health = scrape_rss(client)
    newsapi_rows, newsapi_health = scrape_newsapi()
    sec_rows, sec_health = scrape_sec_filings()
    
    rows = rss_rows + newsapi_rows + sec_rows
    health_metrics = rss_health + newsapi_health + sec_health

    if not rows:
        log.info("No rows scraped.")
        return

    # Data validation
    valid_rows = []
    current_year = datetime.now(timezone.utc).year
    for row in rows:
        if not row.get("title"): continue
        try:
            dt = dateparser.parse(row["published_at"])
            if dt.year < 2000 or dt.year > current_year + 1:
                continue
        except Exception:
            continue
        valid_rows.append(row)
    rows = valid_rows

    # De-dupe by url
    deduped = {}
    for row in rows:
        deduped[row["url"]] = row
    rows = list(deduped.values())

    # Fuzzy deduplication by title against recent DB articles and within the batch
    log.info("Deduplicating similar titles...")
    try:
        recent_res = client.table("articles").select("title").order("published_at", desc=True).limit(200).execute()
        existing_titles = [r["title"] for r in recent_res.data]
    except Exception as e:
        log.warning(f"Failed to fetch recent titles for deduplication: {e}")
        existing_titles = []

    final_rows = []
    for row in rows:
        title = row["title"]
        is_duplicate = False
        # Check against DB
        for ext_title in existing_titles:
            if fuzz.ratio(title, ext_title) > 85:
                is_duplicate = True
                break
        if not is_duplicate:
            # Check against final_rows (already processed in this batch)
            for f_row in final_rows:
                if fuzz.ratio(title, f_row["title"]) > 85:
                    is_duplicate = True
                    break
        if not is_duplicate:
            final_rows.append(row)
            existing_titles.append(title)  # add to check list for subsequent rows
    rows = final_rows

    # Upsert
    inserted = 0
    for i in range(0, len(rows), 100):
        batch = rows[i:i + 100]
        try:
            client.table("articles").upsert(batch, on_conflict="url").execute()
            inserted += len(batch)
        except Exception as e:
            log.error(f"upsert batch failed: {e}")

    log.info(f"Done. Upserted ~{inserted} rows (duplicates by url/title are skipped).")

    # Insert source health
    if health_metrics:
        try:
            client.table("source_health").insert(health_metrics).execute()
            log.info("Source health metrics recorded.")
        except Exception as e:
            log.warning(f"Failed to record source health: {e}")


if __name__ == "__main__":
    main()
