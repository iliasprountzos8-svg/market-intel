"""Export a stratified random sample of recent articles for labeling (gold set).

    python eval_export.py --n 150 --seed 7 > eval/sample_unlabeled.jsonl
Stratified so that per-ticker feed items do not swamp outlets and SEC filings: 45% outlets, 35% per-ticker feeds,
20% SEC. The system's own tags are exported separately as 'pred_*' fields for the evaluator, never shown to the labeler.
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect  # noqa: E402

PER_TICKER = ("Yahoo Finance", "Seeking Alpha", "Nasdaq.com")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--exclude", default=None, help="jsonl of an earlier sample whose ids must not be reused")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    skip = {json.loads(l)["id"] for l in open(a.exclude, encoding="utf-8")} if a.exclude else set()
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select id, source, title, left(summary_raw, 220), tickers_raw, event, event2, fb_score, ai_sentiment, story_id "
                    "from articles where published_at > %s and not is_backfill and title is not null and length(title) > 15", (since,))
        rows = cur.fetchall()
    def group(r):
        if (r[1] or "").startswith("SEC EDGAR"):
            return "sec"
        return "ticker" if r[1] in PER_TICKER else "outlet"
    buckets = {"outlet": [], "ticker": [], "sec": []}
    for r in rows:
        if str(r[0]) not in skip:
            buckets[group(r)].append(r)
    quota = {"outlet": int(a.n * 0.45), "ticker": int(a.n * 0.35), "sec": a.n - int(a.n * 0.45) - int(a.n * 0.35)}
    out = []
    for g, q in quota.items():
        pool = buckets[g]
        rnd.shuffle(pool)
        out += pool[:q]
    rnd.shuffle(out)
    for i, r in enumerate(out):
        print(json.dumps({"i": i, "id": str(r[0]), "source": r[1], "title": r[2], "summary": r[3], "feed_tickers": r[4] or [],
                          "pred_event_old": r[5], "pred_event_v2": r[6], "pred_fb": r[7], "pred_ai_sentiment": r[8]}, ensure_ascii=False))
    print(f"buckets available: { {k: len(v) for k, v in buckets.items()} }", file=sys.stderr)


if __name__ == "__main__":
    main()
