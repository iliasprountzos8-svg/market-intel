"""Keep the Vercel dashboard (which reads Supabase) fed while the homelab database is primary.

homelab -> Supabase : digests, ai_calls_log, and only the dashboard-worthy articles
                      (last 3 days AND (relevance >= 60 OR a holding OR Claude-written summary)).
Supabase -> homelab : dashboard-managed settings (holdings, app_settings, portfolio_meta,
                      push_subscriptions, source_overrides), so edits made in the dashboard take effect here.

Never deletes anything in Supabase. Stops mirroring articles if the remote table gets big
(free tier is 500 MB). Run at the end of every cycle: python mirror_to_supabase.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
from logger import get_logger  # noqa: E402

log = get_logger("mirror")
HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"]
REMOTE_ARTICLE_CAP = 150_000


def main():
    rurl, rkey = os.environ.get("SUPABASE_MIRROR_URL"), os.environ.get("SUPABASE_MIRROR_KEY")
    if not rurl or not rkey:
        log.info("no SUPABASE_MIRROR_* configured; nothing to mirror")
        return
    local = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    remote = create_client(rurl, rkey)
    since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    since2 = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    # settings the dashboard owns: remote -> local
    for table, key in (("holdings", "symbol"), ("app_settings", "id"), ("portfolio_meta", "id"), ("push_subscriptions", "id"), ("source_overrides", "name")):
        try:
            rows = remote.table(table).select("*").execute().data or []
            if rows:
                local.table(table).upsert(rows, on_conflict=key).execute()
        except Exception as e:  # noqa: BLE001
            log.warning(f"pull {table} failed: {str(e)[:100]}")

    # digests + calls: local -> remote (recent rows; upsert by id)
    cols_calls = None
    for table in ("digests", "ai_calls_log"):
        try:
            rows = local.table(table).select("*").gte("created_at", since2).execute().data or []
            if table == "ai_calls_log" and rows:
                # the remote table may not have migration-004 columns yet: send only columns it knows
                known = set(remote.table(table).select("*").limit(1).execute().data[0].keys())
                rows = [{k: v for k, v in r.items() if k in known} for r in rows]
            if rows:
                remote.table(table).upsert(rows, on_conflict="id").execute()
        except Exception as e:  # noqa: BLE001
            log.warning(f"push {table} failed: {str(e)[:120]}")

    # dashboard-worthy articles: local -> remote
    try:
        remote_n = remote.table("articles").select("id", count="exact").limit(1).execute().count or 0
        if remote_n >= REMOTE_ARTICLE_CAP:
            log.warning(f"remote articles {remote_n} >= cap; not mirroring articles")
            return
        cols = "id,source,url,title,summary_raw,author,published_at,category,tickers_raw,ai_processed,ai_processed_at,ai_sentiment,ai_relevance_score,ai_affected_tickers,ai_summary,ai_suggested_action,ai_risk_flag,ai_confidence"
        rows = (local.table("articles").select(cols).gte("published_at", since).gte("ai_relevance_score", 60)
                .order("published_at", desc=True).limit(1000).execute().data or [])
        rows += (local.table("articles").select(cols).gte("published_at", since).overlaps("tickers_raw", HOLDINGS)
                 .order("published_at", desc=True).limit(1000).execute().data or [])
        rows += (local.table("articles").select(cols).gte("published_at", since).not_.is_("ai_summary", "null").limit(500).execute().data or [])
        uniq = {r["url"]: r for r in rows}
        rows = list(uniq.values())
        sent = 0
        for i in range(0, len(rows), 200):
            remote.table("articles").upsert(rows[i:i + 200], on_conflict="url").execute()
            sent += len(rows[i:i + 200])
        log.info(f"mirrored {sent} articles; remote table now ~{remote_n}")
    except Exception as e:  # noqa: BLE001
        log.warning(f"push articles failed: {str(e)[:160]}")


if __name__ == "__main__":
    main()
