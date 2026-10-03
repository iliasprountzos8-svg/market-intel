"""Data-quality metrics for the news pipeline: written to dq_metrics (one row per metric per run) and printed.

    python dq_metrics.py            # compute, store and print
    python dq_metrics.py --print    # compute and print only
Baseline measured before the news-reading upgrade (2026-09-27): duplicate titles 11%, full text 22% of articles,
event tags on about 7% of articles, 39 SEC items per week, 80% of volume from three per-ticker feeds.
"""
import argparse
import sys
from pathlib import Path

METRICS = {
    "articles_7d": "select count(*) from articles where scraped_at > now() - interval '7 days'",
    "stories_7d": "select count(*) from stories where first_seen > now() - interval '7 days' and not is_backfill",
    "stories_per_article_7d": "select round((count(*)::numeric / nullif((select count(*) from articles where scraped_at > now() - interval '7 days' and not is_backfill), 0)), 3) "
                              "from stories where first_seen > now() - interval '7 days' and not is_backfill",
    "exact_duplicate_title_share_7d": "select round(100.0 * (count(*) - count(distinct lower(title))) / nullif(count(*), 0), 1) from articles where scraped_at > now() - interval '7 days'",
    "backfill_share_scraped_24h": "select round(100.0 * count(*) filter (where is_backfill) / nullif(count(*), 0), 1) from articles where scraped_at > now() - interval '24 hours'",
    "ingest_lag_median_min_fresh": "select round((percentile_cont(0.5) within group (order by extract(epoch from scraped_at - published_at) / 60))::numeric, 0) "
                                   "from articles where scraped_at > now() - interval '6 hours' and published_at <= scraped_at and not is_backfill",
    "fulltext_share_all_30d": "select round(100.0 * count(*) filter (where full_text is not null and length(full_text) > 200) / nullif(count(*), 0), 1) from articles where scraped_at > now() - interval '30 days'",
    "fulltext_share_material_stories_3d": "select round(100.0 * count(*) filter (where a.full_text is not null and length(a.full_text) > 200) / nullif(count(*), 0), 1) "
                                          "from stories s join articles a on a.id = s.first_article_id where s.priority >= 50 and not s.is_backfill and s.first_seen > now() - interval '3 days'",
    "event_tagged_share_stories_7d": "select round(100.0 * count(*) filter (where coalesce(event, 'other') <> 'other') / nullif(count(*), 0), 1) from stories where first_seen > now() - interval '7 days' and not is_backfill",
    "opinion_share_stories_7d": "select round(100.0 * count(*) filter (where event = 'opinion') / nullif(count(*), 0), 1) from stories where first_seen > now() - interval '7 days' and not is_backfill",
    "reader_coverage_top50_48h": "select round(100.0 * count(r.story_id) / nullif(count(*), 0), 1) from (select story_id from stories where not is_backfill and first_seen > now() - interval '48 hours' "
                                 "order by priority desc limit 50) t left join story_reads r on r.story_id = t.story_id",
    "sec_filing_stories_7d": "select count(*) from stories where first_source ilike 'SEC EDGAR%' and first_seen > now() - interval '7 days'",
    "ticker_feed_share_articles_7d": "select round(100.0 * count(*) filter (where source in ('Yahoo Finance','Seeking Alpha','Nasdaq.com')) / nullif(count(*), 0), 1) from articles where scraped_at > now() - interval '7 days'",
    "unstoried_articles": "select count(*) from articles where story_id is null",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="only_print", action="store_true")
    a = ap.parse_args()
    import psycopg
    from dotenv import dotenv_values
    env = dotenv_values(Path.home() / "services" / "marketdb" / ".env")
    out = {}
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="postgres", password=env["POSTGRES_PASSWORD"]) as conn, conn.cursor() as cur:
        for name, sql in METRICS.items():
            try:
                cur.execute(sql)
                v = cur.fetchone()[0]
                out[name] = None if v is None else float(v)
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                out[name] = None
                print(f"metric {name} failed: {str(e)[:80]}")
        if not a.only_print:
            cur.execute("select now()")
            ts = cur.fetchone()[0]
            cur.executemany("insert into dq_metrics (ts, name, value) values (%s, %s, %s)", [(ts, k, v) for k, v in out.items() if v is not None])
            conn.commit()
    w = max(len(k) for k in out)
    for k, v in out.items():
        print(f"{k:<{w}}  {'n/a' if v is None else (f'{v:,.1f}' if v != int(v) else f'{int(v):,}')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
