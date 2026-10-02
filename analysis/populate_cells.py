"""AI populates the cells: fills the article fields the site reads (ai_sentiment, ai_relevance_score,
ai_affected_tickers, ai_confidence) plus new ones (fb_score, event, lang) from the local models.

Inputs (all local, no API cost): FinBERT score, finance lexicon, entity matching against the S&P 500,
event tagging, source credibility, duplicate clustering (from signals.py).
Output: better cells so the news reads better and the important items float to the top.

Rules of the road:
  * Rows that Claude analysed (ai_summary present) keep Claude's sentiment/relevance/tickers/action;
    only the new cells (fb_score, event, lang) are filled for them.
  * A row is only written if a value actually changed (keeps sync deltas small).
  * Non-English text is capped: the models are English-only.

Run: python populate_cells.py [--hours 72]
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
import signals as S  # noqa: E402  (entity extraction, events, clustering, standardisation)

GREEK = re.compile(r"[Α-Ωα-ωά-ώ]")
HOLD = set(S.HOLDINGS)
EVENT_PTS = {"guidance": 18, "earnings": 18, "m&a": 15, "filing": 12, "analyst": 10, "legal_reg": 8, "macro": 8, "supply": 8, "product": 4}
REAL = set(S.UNIVERSE_SYMS) - set(S.MACRO)


def connect():
    env = dotenv_values(Path.home() / "services" / "marketdb" / ".env")
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="postgres", password=env["POSTGRES_PASSWORD"])


def compute(it):
    title, syms = it["title"], it["syms"]
    greek = bool(GREEK.search(title))
    named = [s for s, r in syms.items() if r >= 0.9]          # in the headline itself
    tagged = [s for s, r in syms.items() if 0.7 <= r < 0.9]   # per-company feed tag only
    holding = any(s in HOLD for s in named)
    rel = 45 if holding else 25 if named else 10 if tagged else 6 if syms else 0
    rel += EVENT_PTS.get(it["event"], 0)
    sw = it["src_w"]
    rel += 8 if sw >= 1.2 else 4 if sw >= 1.0 else -4 if sw <= 0.7 else 0
    fb, lex = it["raw_fb"], it["raw_lex"]
    basis = fb if fb is not None else lex
    rel += round(abs(basis) * 15)
    if it["novelty"] < 1:
        rel -= 25                                              # a repeat of a story we already have
    if any(s in S.MACRO for s in named) and not holding:
        rel += 8                                               # rates/oil/gold matter to a global index holder
    if greek:
        rel = min(rel, 30)
    rel = max(0, min(100, rel))
    label = "neutral" if greek else "bullish" if basis > 0.25 else "bearish" if basis < -0.25 else "neutral"
    tickers = [s for s in sorted(syms, key=lambda x: -syms[x]) if s in REAL][:5]
    return {"ai_sentiment": label, "ai_relevance_score": rel, "ai_affected_tickers": tickers, "ai_confidence": int(min(100, abs(basis) * 100)),
            "fb_score": None if fb is None else round(float(fb), 4), "event": it["event"], "lang": "el" if greek else "en"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72)
    args = ap.parse_args()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""select a.id, a.source, a.title, a.summary_raw, a.published_at, a.tickers_raw, a.ai_sentiment, (a.ai_summary is not null) claude,
                              a.ai_relevance_score, a.ai_affected_tickers, a.ai_confidence, a.fb_score, a.event, a.lang, s.score
                       from articles a left join article_scores s on s.article_id = a.id and s.model = 'finbert'
                       where a.published_at > now() - make_interval(hours => %s)""", (args.hours,))
        rows = cur.fetchall()
    cols = ["id", "source", "title", "summary_raw", "published_at", "tickers_raw", "ai_sentiment", "claude", "rel", "aff", "conf", "fbcur", "eventcur", "langcur", "fb"]
    recs = [dict(zip(cols, r)) for r in rows]
    finbert = {r["id"]: r["fb"] for r in recs if r["fb"] is not None}
    items = S.score_articles([{"id": r["id"], "source": r["source"], "title": r["title"], "summary_raw": r["summary_raw"], "published_at": r["published_at"],
                               "tickers_raw": r["tickers_raw"], "ai_sentiment": r["ai_sentiment"]} for r in recs], finbert)
    byid = {it["id"]: it for it in items}
    updates, claude_updates = [], []
    for r in recs:
        it = byid.get(r["id"])
        if not it:
            continue
        v = compute(it)
        if r["claude"]:
            if (r["fbcur"], r["eventcur"], r["langcur"]) != (v["fb_score"], v["event"], v["lang"]):
                claude_updates.append((v["fb_score"], v["event"], v["lang"], r["id"]))
            continue
        cur = (r["ai_sentiment"], r["rel"], sorted(r["aff"] or []), r["conf"], r["fbcur"], r["eventcur"], r["langcur"])
        new = (v["ai_sentiment"], v["ai_relevance_score"], sorted(v["ai_affected_tickers"]), v["ai_confidence"], v["fb_score"], v["event"], v["lang"])
        if cur != new:
            updates.append((v["ai_sentiment"], v["ai_relevance_score"], v["ai_affected_tickers"], v["ai_confidence"], v["fb_score"], v["event"], v["lang"], r["id"]))
    with conn.cursor() as cur:
        cur.executemany("update articles set ai_sentiment=%s, ai_relevance_score=%s, ai_affected_tickers=%s, ai_confidence=%s, fb_score=%s, event=%s, lang=%s, "
                        "ai_processed=true, ai_processed_at=now() where id=%s", updates)
        cur.executemany("update articles set fb_score=%s, event=%s, lang=%s, ai_processed_at=now() where id=%s", claude_updates)
    conn.commit()
    lab = {}
    for it in items:
        v = compute(it)
        lab[v["ai_sentiment"]] = lab.get(v["ai_sentiment"], 0) + 1
    hi = sum(1 for it in items if compute(it)["ai_relevance_score"] >= 60)
    print(f"populate_cells: {len(recs)} articles ({args.hours}h) | rewrote {len(updates)} | new-cell-only (Claude rows) {len(claude_updates)} | "
          f"sentiment mix {lab} | relevance>=60: {hi}")


if __name__ == "__main__":
    main()
