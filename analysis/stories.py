"""Cluster new articles into stories, apply the freshness gate, tag events (v2) and compute priority.

Run with the analysis venv (needs psycopg). Safe to run repeatedly; an advisory lock stops overlapping runs.

    python stories.py                 # process articles that have no story yet (newest cycle)
    python stories.py --limit 5000    # cap per run
    python stories.py --dry-run       # cluster in memory, write nothing, print stats
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import story_logic as sl  # noqa: E402
from db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCK_ID = 7461
_ITEMS = re.compile(r"Items?:\s*([0-9., ]+)")


def load_universe():
    try:
        sp = {x["symbol"] for x in json.load(open(ROOT / "data" / "sp500.json"))}
    except OSError:
        sp = set()
    return sp


def load_holdings(cur):
    cur.execute("select symbol, keywords from holdings where coalesce(display_order, 1) > 0")
    syms, kws = set(), set()
    for sym, kw in cur.fetchall():
        syms.add(sym.split(".")[0].upper())
        kws |= {k.lower() for k in (kw or []) if len(k) >= 4}
    return syms, kws


def sec_items_of(summary):
    m = _ITEMS.search(summary or "")
    return re.findall(r"\d\.\d\d", m.group(1)) if m else None


def art_from_row(r):
    aid, title, pub, seen, src, tickers = r[:6]
    pub = pub or seen
    return {"id": aid, "title": title or "", "published_at": sl.parse_ts(pub), "scraped_at": sl.parse_ts(seen), "source": src or "?", "tickers": list(tickers or [])}


def make_entity_fn():
    """Company-name matcher shared with signals.py (same S&P 500 name table)."""
    try:
        import signals as sg
        return sl.EntityMatcher(dict(sg.NAMES))
    except Exception as e:  # noqa: BLE001 - clustering still works, just without the entity veto
        print(f"entity matcher unavailable ({e}); continuing without it")
        return sl.EntityMatcher({})


def story_from_row(r, entity_fn=None):
    (sid, title, thash, faid, fsrc, fpub, fseen, lpub, lseen, n, tickers, sources, event) = r
    s = sl.Story.__new__(sl.Story)
    s.id, s.title = sid, title
    s.tokens, s.hash = sl.title_tokens(title), thash or sl.title_hash(title)
    s.first_pub, s.first_seen, s.last_pub, s.last_seen = fpub, fseen, lpub, lseen
    s.tickers, s.sources = set(tickers or []), set(sources or [])
    s.n_articles, s.first_source, s.first_article_id = n, fsrc, faid
    s.dirty, s.new = False, False
    s.ents = entity_fn(title) if entity_fn else set()
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    now = datetime.now(timezone.utc)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s)", (LOCK_ID,))
        if not cur.fetchone()[0]:
            print("another stories run holds the lock; skipping")
            return 0
        cur.execute("select id, title, published_at, scraped_at, source, tickers_raw, summary_raw from articles "
                    "where story_id is null order by coalesce(published_at, scraped_at) asc limit %s", (a.limit,))
        rows = cur.fetchall()
        if not rows:
            print("nothing to cluster")
            return 0
        oldest = min(sl.parse_ts(r[2] or r[3]) for r in rows) - timedelta(hours=sl.CLUSTER_WINDOW_H + 24)
        cur.execute("select story_id, title, title_hash, first_article_id, first_source, first_published, first_seen, last_published, "
                    "last_seen, n_articles, tickers, sources, event from stories where first_published >= %s", (oldest,))
        cur.execute("select title from articles order by scraped_at desc limit 60000")
        df, n_docs = sl.build_df(t[0] for t in cur.fetchall())
        entity_fn = make_entity_fn()
        ix = sl.StoryIndex(df=df, n_docs=n_docs, entity_fn=entity_fn)
        prev_event = {}
        for r in cur.fetchall():
            s = story_from_row(r[:13], entity_fn)
            prev_event[s.id] = r[12]
            ix.add_existing(s)
        cur.execute("select coalesce(max(story_id), 0) + 1 from stories")
        ix.next_id = max(ix.next_id, cur.fetchone()[0])

        sp500 = load_universe()
        held_syms, held_kws = load_holdings(cur)
        assign, ev_by_story, art_updates = {}, {}, []
        for r in rows:
            art = art_from_row(r)
            sid, _new = ix.assign(art)
            summary = r[6]
            ev = sl.event_v2(art["title"], summary, sec_items_of(summary))
            best = ev_by_story.get(sid)
            if best is None or sl.EVENT_WEIGHT.get(ev, 0) > sl.EVENT_WEIGHT.get(best, 0):
                ev_by_story[sid] = ev
            art_updates.append((sid, sl.title_hash(art["title"]), sl.is_backfill(art["published_at"], art["scraped_at"]), ev, art["id"]))
            assign[art["id"]] = sid

        touched = [s for s in ix.stories.values() if s.dirty]
        n_new = sum(1 for s in touched if s.new)
        n_art = len(rows)
        print(f"{n_art} articles -> {len(touched)} touched stories ({n_new} new, {len(touched) - n_new} extended); "
              f"dedupe ratio {n_art / max(len(touched), 1):.2f}")

        def story_priority(s):
            ev = ev_by_story.get(s.id) or prev_event.get(s.id) or "other"
            if sl.EVENT_WEIGHT.get(prev_event.get(s.id) or "other", 0) > sl.EVENT_WEIGHT.get(ev, 0):
                ev = prev_event[s.id]
            toks = set(sl.normalize_title(s.title).split())
            held = bool(s.tickers & held_syms) or bool(toks & held_kws)
            cls = min((sl.source_class(x) for x in s.sources), key=lambda c: {"primary": 0, "outlet": 1, "ticker_feed": 2}[c])
            age_h = (now - s.first_pub).total_seconds() / 3600
            bf = sl.is_backfill(s.first_pub, s.first_seen)
            return ev, bf, sl.priority(held, ev, cls, sl.independent_sources(s.sources), bf, age_h, in_sp500=bool(s.tickers & sp500) or not sp500, n_tickers=len(s.tickers))

        if a.dry_run:
            top = sorted(touched, key=lambda s: -s.n_articles)[:5]
            for s in top:
                print(f"  {s.n_articles:>3} articles / {len(s.sources)} sources: {s.title[:90]}")
            return 0

        for s in touched:
            ev, bf, prio = story_priority(s)
            if s.new:
                cur.execute("insert into stories (story_id, title, title_hash, first_article_id, first_source, first_published, first_seen, "
                            "last_published, last_seen, n_articles, n_sources, tickers, sources, event, is_backfill, priority) "
                            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (s.id, s.title[:500], s.hash, s.first_article_id, s.first_source, s.first_pub, s.first_seen, s.last_pub, s.last_seen,
                             s.n_articles, len(s.sources), sorted(s.tickers), sorted(s.sources), ev, bf, prio))
            else:
                cur.execute("update stories set first_article_id=%s, first_source=%s, first_published=%s, last_published=%s, last_seen=%s, "
                            "n_articles=%s, n_sources=%s, tickers=%s, sources=%s, event=%s, is_backfill=%s, priority=%s, updated_at=now() "
                            "where story_id=%s",
                            (s.first_article_id, s.first_source, s.first_pub, s.last_pub, s.last_seen, s.n_articles, len(s.sources),
                             sorted(s.tickers), sorted(s.sources), ev, bf, prio, s.id))
        cur.executemany("update articles set story_id=%s, title_hash=%s, is_backfill=%s, event2=%s where id=%s", art_updates)
        cur.execute("select setval(pg_get_serial_sequence('stories','story_id'), (select max(story_id) from stories))")

        # Decay: recompute priority for recent, not-yet-fetched stories so ordering follows current freshness.
        cur.execute("select story_id, title, title_hash, first_article_id, first_source, first_published, first_seen, last_published, last_seen, "
                    "n_articles, tickers, sources, event from stories where first_seen > now() - interval '3 days'")
        for r in cur.fetchall():
            s = story_from_row(r)
            prev_event[s.id] = r[12]
            ev, bf, prio = story_priority(s)
            cur.execute("update stories set priority=%s where story_id=%s and priority is distinct from %s", (prio, s.id, prio))
        cur.execute("update articles a set priority = s.priority from stories s where a.story_id = s.story_id "
                    "and a.full_text_fetch_attempted = false and a.priority is distinct from s.priority")
        conn.commit()
        cur.execute("select pg_advisory_unlock(%s)", (LOCK_ID,))
        print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
