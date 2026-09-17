"""CLI for Claude (or you) to read/write the market-intel DB during an on-demand
analysis pass. No separate AI API key needed -- Claude reads the fetched articles
itself in-conversation, reasons about them, and writes conclusions back via
mark-processed / write-digest.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Examples:
  python cli.py fetch-unprocessed --limit 50
  python cli.py fetch-recent --hours 24
  python cli.py mark-processed <article_id> \
      --sentiment bullish --relevance 80 \
      --tickers NVDA,MSFT --summary "..." \
      --action "watch" --risk none --confidence 70
  python cli.py write-digest --hours 24 --summary "..." \
      --themes "Fed rate path,AI capex" --guidance "..." \
      --watchlist '{"NVDA": "earnings beat, no action needed"}'
  python cli.py log-call --ticker NVDA --call bullish --rationale "..." --digest-id <id>
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client

try:
    from classify import score_article
except ImportError:
    score_article = None

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

log = get_logger("analysis.cli")

load_dotenv()


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def cmd_fetch_unprocessed(client, args):
    res = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,url")
        .eq("ai_processed", False)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    # Prefer full_text when available; fall back to the RSS summary otherwise
    # (some sources block scraping -- see fetch_fulltext.py). Keeps the payload
    # Claude reads free of a redundant near-duplicate field.
    rows = []
    for r in res.data:
        r["text"] = r.pop("full_text") or r.pop("summary_raw")
        r.pop("summary_raw", None)
        rows.append(r)
    print(json.dumps(rows, indent=2, default=str))


def cmd_fetch_recent(client, args):
    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    res = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,"
                "ai_sentiment,ai_relevance_score,ai_summary,url")
        .gte("published_at", since)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    rows = []
    for r in res.data:
        r["text"] = r.pop("full_text") or r.pop("summary_raw")
        r.pop("summary_raw", None)
        rows.append(r)
    print(json.dumps(rows, indent=2, default=str))


def cmd_mark_processed(client, args):
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else []
    payload = {
        "ai_processed": True,
        "ai_processed_at": datetime.now(timezone.utc).isoformat(),
        "ai_sentiment": args.sentiment,
        "ai_relevance_score": args.relevance,
        "ai_affected_tickers": tickers,
        "ai_summary": args.summary,
        "ai_suggested_action": args.action,
        "ai_risk_flag": args.risk,
        "ai_confidence": args.confidence,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    res = client.table("articles").update(payload).eq("id", args.article_id).execute()
    print(json.dumps(res.data, indent=2, default=str))


def cmd_bulk_classify_rule_based(client, args):
    if not score_article:
        log.error("vaderSentiment is not installed. Please pip install -r requirements.txt")
        sys.exit(1)

    res = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,url")
        .eq("ai_processed", False)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    
    rows = res.data
    if not rows:
        log.info("No unprocessed articles found.")
        return

    updated_count = 0
    for row in rows:
        payload = score_article(row)
        payload["ai_processed_at"] = datetime.now(timezone.utc).isoformat()
        
        try:
            client.table("articles").update(payload).eq("id", row["id"]).execute()
            updated_count += 1
        except Exception as e:
            log.error(f"Failed to update article {row['id']}: {e}")
            
    log.info(f"Bulk classified {updated_count} articles using rule-based heuristics.")


def cmd_write_digest(client, args):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    themes = [t.strip() for t in args.themes.split(",")] if args.themes else []
    watchlist = json.loads(args.watchlist) if args.watchlist else None

    # count articles covered in this window
    count_res = (
        client.table("articles")
        .select("id", count="exact")
        .gte("published_at", since.isoformat())
        .execute()
    )
    covered = count_res.count or 0

    payload = {
        "period_start": since.isoformat(),
        "period_end": now.isoformat(),
        "articles_covered": covered,
        "summary": args.summary,
        "key_themes": themes,
        "guidance": args.guidance,
        "watchlist_notes": watchlist,
    }
    res = client.table("digests").insert(payload).execute()
    print(json.dumps(res.data, indent=2, default=str))


def cmd_log_call(client, args):
    payload = {
        "digest_id": args.digest_id,
        "ticker_or_theme": args.ticker,
        "call": args.call,
        "rationale": args.rationale,
        "confidence": args.confidence,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    res = client.table("ai_calls_log").insert(payload).execute()
    print(json.dumps(res.data, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch-unprocessed")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_fetch_unprocessed)

    p = sub.add_parser("fetch-recent")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_fetch_recent)

    p = sub.add_parser("mark-processed")
    p.add_argument("article_id")
    p.add_argument("--sentiment", choices=["bullish", "bearish", "neutral", "mixed"])
    p.add_argument("--relevance", type=int)
    p.add_argument("--tickers", help="comma-separated tickers/sectors")
    p.add_argument("--summary")
    p.add_argument("--action")
    p.add_argument("--risk")
    p.add_argument("--confidence", type=int)
    p.set_defaults(func=cmd_mark_processed)

    p = sub.add_parser("write-digest")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--summary", required=True)
    p.add_argument("--themes", help="comma-separated")
    p.add_argument("--guidance")
    p.add_argument("--watchlist", help="JSON object string")
    p.set_defaults(func=cmd_write_digest)

    p = sub.add_parser("log-call")
    p.add_argument("--ticker", required=True)
    p.add_argument("--call", required=True, choices=["bullish", "bearish", "neutral"])
    p.add_argument("--rationale")
    p.add_argument("--digest-id")
    p.add_argument("--confidence", type=int, help="0-100 confidence score")
    p.set_defaults(func=cmd_log_call)
    
    p = sub.add_parser("bulk-classify")
    p.add_argument("--rule-based", action="store_true", help="Use local rule-based heuristics instead of LLM")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_bulk_classify_rule_based)

    args = parser.parse_args()
    client = get_client()
    args.func(client, args)


if __name__ == "__main__":
    main()
