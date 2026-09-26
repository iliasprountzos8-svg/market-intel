"""Merge stories that report the same event in different words, using free local embeddings. Run with the nlp venv.

    python merge_stories.py                      # merge (cosine >= EMBED_MERGE_COS, all guards)
    python merge_stories.py --dry-run            # show what would merge, write nothing
    python merge_stories.py --explore 1          # write eval/pairs_sample1.jsonl: random candidate pairs by cosine bin, for labeling
Cost: none (22M-parameter model on the CPU, about 200 headlines/second). Guards: see embed_logic.py.
"""
import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nlp"))
import embed_logic as el  # noqa: E402
import story_logic as sl  # noqa: E402

EMBED_MERGE_COS = 0.78  # frozen after calibrating on eval/pairs_v1.txt; blind test on pairs_v2.txt: precision 94% (33/35), recall 60% (33/55)
LOCK_ID = 7461          # shared with stories.py so the two never write at once
BINS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]


def connect():
    import psycopg
    from dotenv import dotenv_values
    env = dotenv_values(Path.home() / "services" / "marketdb" / ".env")
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="postgres", password=env["POSTGRES_PASSWORD"])


def load_recent(cur, days):
    cur.execute("select story_id, title, first_published, tickers, event, priority from stories "
                "where not is_backfill and first_seen > now() - %s * interval '1 day' and coalesce(first_source, '') not ilike 'SEC EDGAR%%' "
                "order by story_id", (days,))
    return [r for r in cur.fetchall() if el.latin_share(r[1]) >= 0.85]  # English only: see embed_logic.latin_share


def embeddings_for(cur, rows, embedder):
    ids = [r[0] for r in rows]
    cur.execute("select story_id, emb from story_emb where story_id = any(%s)", (ids,))
    have = {sid: np.frombuffer(bytes(b), dtype=np.float32) for sid, b in cur.fetchall()}
    missing = [r for r in rows if r[0] not in have]
    if missing:
        vecs = embedder.encode([sl.normalize_title(r[1]) or r[1] for r in missing])
        for r, v in zip(missing, vecs):
            cur.execute("insert into story_emb (story_id, emb) values (%s, %s) on conflict (story_id) do nothing", (r[0], v.tobytes()))
            have[r[0]] = v
    return np.vstack([have[i] for i in ids]) if ids else np.zeros((0, 384), dtype=np.float32), len(missing)


def build_story_dicts(rows, entity_fn):
    out = []
    for sid, title, fpub, tickers, event, prio in rows:
        out.append({"id": sid, "title": title, "first_pub": sl.parse_ts(fpub).timestamp(), "tickers": set(tickers or []),
                    "ents": entity_fn(title), "event": event, "priority": prio or 0.0})
    return out


def apply_group(cur, group, stories, cos_of):
    canon = stories[group[0]]
    others = [stories[i] for i in group[1:]]
    oids = [o["id"] for o in others]
    cur.execute("update articles set story_id = %s where story_id = any(%s)", (canon["id"], oids))
    cur.execute("select count(*), count(distinct source), min(coalesce(published_at, scraped_at)), max(coalesce(published_at, scraped_at)), "
                "max(scraped_at), array_agg(distinct source) from articles where story_id = %s", (canon["id"],))
    n, ns, fpub, lpub, lseen, srcs = cur.fetchone()
    cur.execute("select id, source from articles where story_id = %s order by coalesce(published_at, scraped_at) limit 1", (canon["id"],))
    fa, fsrc = cur.fetchone()
    tickers = sorted(set().union(canon["tickers"], *[o["tickers"] for o in others]))
    best = max([canon] + others, key=lambda s: sl.EVENT_WEIGHT.get(s["event"] or "other", 0))
    prio = max(s["priority"] for s in [canon] + others)
    cur.execute("update stories set n_articles=%s, n_sources=%s, first_article_id=%s, first_source=%s, first_published=%s, last_published=%s, "
                "last_seen=%s, tickers=%s, sources=%s, event=%s, priority=%s, updated_at=now() where story_id=%s",
                (n, ns, fa, fsrc, fpub, lpub, lseen, tickers, sorted(srcs), best["event"], prio, canon["id"]))
    for o in others:
        cur.execute("insert into story_merges (canon_id, absorbed_id, cos, canon_title, absorbed_title) values (%s,%s,%s,%s,%s)",
                    (canon["id"], o["id"], cos_of.get((canon["id"], o["id"])) or cos_of.get((o["id"], canon["id"])), canon["title"][:300], o["title"][:300]))
    cur.execute("update story_reads r set story_id = %s where story_id = any(%s) and not exists "
                "(select 1 from story_reads x where x.story_id = %s and x.model = r.model)", (canon["id"], oids, canon["id"]))
    cur.execute("delete from story_reads where story_id = any(%s)", (oids,))
    cur.execute("delete from story_emb where story_id = any(%s)", (oids,))
    cur.execute("delete from stories where story_id = any(%s)", (oids,))
    return len(oids)


def pair_fields(a, b):
    """Everything the guards need, stored with the pair so rules can be re-evaluated later against the same labels."""
    return {"ta": sorted(a["tickers"]), "tb": sorted(b["tickers"]), "xa": sorted(a["ents"]), "xb": sorted(b["ents"]), "pa": a["first_pub"], "pb": b["first_pub"]}


def enrich(n, entity_fn):
    """Add guard fields to an existing pairs file (labels are bound to its row order, so it must not be re-sampled)."""
    path = HERE / "eval" / f"pairs_sample{n}.jsonl"
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    with connect() as conn, conn.cursor() as cur:
        ids = sorted({r["ida"] for r in rows} | {r["idb"] for r in rows})
        cur.execute("select story_id, title, first_published, tickers, event, priority from stories where story_id = any(%s)", (ids,))
        st = {d["id"]: d for d in build_story_dicts(cur.fetchall(), entity_fn)}
    for r in rows:
        r.update(pair_fields(st[r["ida"]], st[r["idb"]]))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"enriched {len(rows)} pairs in {path}")


QUOTA = {0.55: 6, 0.60: 6, 0.65: 8, 0.70: 16, 0.75: 16, 0.80: 16, 0.85: 16, 0.90: 12}  # most pairs where the decision is made


def explore(n, stories, pairs, seed, skip=frozenset()):
    rnd = random.Random(seed)
    by_bin = {}
    for i, j, c in pairs:
        if abs(stories[i]["first_pub"] - stories[j]["first_pub"]) > el.WINDOW_S:
            continue
        if (stories[i]["id"], stories[j]["id"]) in skip or (stories[j]["id"], stories[i]["id"]) in skip:
            continue
        for lo, hi in zip(BINS, BINS[1:]):
            if lo <= c < hi:
                by_bin.setdefault(lo, []).append((i, j, c))
    out = []
    for lo in BINS[:-1]:
        pool = by_bin.get(lo, [])
        rnd.shuffle(pool)
        for i, j, c in pool[:QUOTA[lo]]:
            ok, why = el.gates(stories[i], stories[j])
            out.append({"a": stories[i]["title"], "b": stories[j]["title"], "cos": round(c, 3), "gate": why, "ida": stories[i]["id"], "idb": stories[j]["id"],
                        "ea": stories[i]["event"], "eb": stories[j]["event"], **pair_fields(stories[i], stories[j])})
    rnd.shuffle(out)
    path = HERE / "eval" / f"pairs_sample{n}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for k, o in enumerate(out):
            o["i"] = k
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"candidate pairs per bin: { {round(lo, 2): len(by_bin.get(lo, [])) for lo in BINS[:-1]} }")
    print(f"wrote {len(out)} pairs -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=4)
    ap.add_argument("--min-cos", type=float, default=EMBED_MERGE_COS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--explore", type=int, default=0)
    ap.add_argument("--exclude-set", type=int, default=0, help="do not re-sample pairs already in this labeled set")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--enrich", type=int, default=0, help="add guard fields to an existing pairs file")
    a = ap.parse_args()
    t0 = time.time()
    from embed import Embedder
    names = sl.load_names(ROOT / "data" / "sp500.json")
    entity_fn = sl.EntityMatcher(names, ignore_case=True)
    if a.enrich:
        enrich(a.enrich, entity_fn); return 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(%s)", (LOCK_ID,))
        if not cur.fetchone()[0]:
            print("another story job holds the lock; skipping"); return 0
        cur.execute("create table if not exists story_emb (story_id bigint primary key, emb bytea not null)")
        rows = load_recent(cur, a.days)
        if len(rows) < 2:
            print("not enough stories"); return 0
        emb, n_new = embeddings_for(cur, rows, Embedder())
        conn.commit()
        stories = build_story_dicts(rows, entity_fn)
        floor = 0.55 if a.explore else a.min_cos
        pairs = el.candidate_pairs(emb, floor)
        print(f"{len(rows)} stories ({n_new} newly embedded), {len(pairs)} candidate pairs >= {floor}, {time.time() - t0:.1f}s")
        if a.explore:
            skip = set()
            if a.exclude_set:
                for line in open(HERE / "eval" / f"pairs_sample{a.exclude_set}.jsonl", encoding="utf-8"):
                    d = json.loads(line)
                    skip.add((d["ida"], d["idb"]))
            explore(a.explore, stories, pairs, a.seed, skip); return 0
        groups = el.merge_groups(stories, pairs, a.min_cos)
        cos_of = {(stories[i]["id"], stories[j]["id"]): c for i, j, c in pairs}
        print(f"{len(groups)} merge groups, {sum(len(g) - 1 for g in groups)} stories would be absorbed (threshold {a.min_cos})")
        if a.dry_run:
            for g in groups[:15]:
                print(f"  keep: {stories[g[0]]['title'][:80]}")
                for k in g[1:]:
                    print(f"     +   {stories[k]['title'][:80]}")
            return 0
        absorbed = sum(apply_group(cur, g, stories, cos_of) for g in groups)
        cur.execute("delete from story_emb e where not exists (select 1 from stories s where s.story_id = e.story_id)")
        conn.commit()
        cur.execute("select pg_advisory_unlock(%s)", (LOCK_ID,))
        print(f"merged: {absorbed} stories absorbed in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
