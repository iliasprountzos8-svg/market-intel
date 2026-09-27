"""Per-holding morning briefing: what changed, what is primary-source, and which thesis watch-items fired.

    python briefing.py            # write logs/briefing-latest.md and print it
    python briefing.py --send     # also push a short version to the phone via ntfy
Research tooling only; nothing here is investment advice. Edit analysis/theses.json to make the watch-items yours.
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import re

sys.path.insert(0, str(Path(__file__).parent))
from db import connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).parent


def has_word(text, kw):
    """Case-insensitive match at a word start, so 'asic' does not fire inside 'basic'."""
    return re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()), text.lower()) is not None


def about_holding(story, sym, kws):
    """Company match: a keyword in the title, or the LLM reader says it is about the company, or a feed tag on at most 2 tickers."""
    if any(has_word(story["title"], k) for k in (kws or []) if len(k) >= 4) or has_word(story["title"], sym):
        return True
    about = ((story.get("read") or {}).get("about") or "").split(".")[0].upper()
    if about:
        return about == sym
    tk = story.get("tickers") or []
    return sym in tk and len(tk) <= 2


def thesis_hits(watch, stories):
    """stories: list of dicts with title, event. Returns [(label, title)] for the first story matching each watch-item."""
    hits = []
    for w in watch:
        for s in stories:
            t = s["title"].lower()
            # A weak rule-based tag alone is not enough to raise a thesis flag: it needs the LLM reader or a primary source behind it.
            ev = (s.get("read") or {}).get("event") or (s["event"] if (s.get("first_source") or "").startswith("SEC EDGAR") else None)
            if (ev is not None and ev in (w.get("events") or [])) or any(has_word(t, k) for k in (w.get("keywords") or [])):
                hits.append((w["label"], s["title"]))
                break
    return hits


def fmt_story(s):
    read = s.get("read") or {}
    d = {-1: "negative", 0: "neutral", 1: "positive"}.get(read.get("direction"), "")
    take = f" | read: {read['takeaway']} ({d})" if read.get("takeaway") else ""
    return f"[{s['event'] or 'other'}, {s['n_sources']} src, prio {s['priority']:.0f}] {s['title'][:110]}{take}"


def signal_line(sym):
    try:
        con = sqlite3.connect(f"file:{ROOT / 'data' / 'signals.db'}?mode=ro", uri=True, timeout=10)
        r = con.execute("select idx, z, n_lead from snapshots2 where symbol=? and model='ens' order by ts desc limit 1", (sym,)).fetchone()
        if r and r[0] is not None:
            z = "n/a" if r[1] is None else f"{r[1]:+.1f}"
            return f"news sentiment index {r[0]:+.2f} (z {z}, {r[2]} lead stories/24h)"
    except Exception:  # noqa: BLE001
        pass
    return "news sentiment: no data"


def build():
    theses = json.load(open(HERE / "theses.json", encoding="utf-8"))
    lines = [f"# Morning brief {datetime.now():%a %d %b %H:%M}", "Research only, not investment advice.", ""]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select symbol, keywords from holdings where coalesce(display_order, 1) > 0 order by display_order")
        holdings = cur.fetchall()
        cur.execute("select count(*), count(*) filter (where first_source ilike 'SEC EDGAR%%') from stories where not is_backfill and first_seen > now() - interval '24 hours'")
        n_st, n_sec = cur.fetchone()
        lines.append(f"Last 24h: {n_st} new stories ({n_sec} SEC filings).")
        shown = set()
        for sym, kws in holdings:
            base = sym.split(".")[0].upper()
            pats = [f"%{k.lower()}%" for k in (kws or []) if len(k) >= 4] or ["%" + base.lower() + "%"]
            cur.execute("select s.story_id, s.title, s.event, s.n_sources, s.priority, s.first_source, r.result, s.tickers "
                        "from stories s left join story_reads r on r.story_id = s.story_id "
                        "where not s.is_backfill and s.first_seen > now() - interval '48 hours' "
                        "and (%s = any(s.tickers) or lower(s.title) like any(%s) or r.result->>'about' = %s) order by s.priority desc limit 80", (base, pats, base))
            rows = [dict(story_id=r[0], title=r[1], event=r[2], n_sources=r[3], priority=r[4], first_source=r[5], read=r[6], tickers=r[7]) for r in cur.fetchall()]
            rows = [r for r in rows if about_holding(r, base, kws)]
            if base == "VWCE" or not rows and base not in theses:
                continue
            lines += ["", f"## {base}", signal_line(base)]
            top = [r for r in rows if r["event"] not in ("opinion", "other") or (r.get("read") or {}).get("relevant")][:3]
            for s in top:
                shown.add(s["story_id"])
                lines.append("- " + fmt_story(s))
            filings = [r for r in rows if (r["first_source"] or "").startswith("SEC EDGAR")]
            for f in filings[:2]:
                lines.append(f"- SEC: {f['title'][:120]}")
            if base in theses:
                hits = thesis_hits(theses[base]["watch"], [r for r in rows if r["event"] != "opinion"])
                if hits:
                    lines.append("- THESIS WATCH (draft rules, edit theses.json): " + "; ".join(f"{lab}: {t[:70]}" for lab, t in hits))
        cur.execute("select s.story_id, s.title, s.event, s.n_sources, s.priority, s.first_source, r.result "
                    "from stories s left join story_reads r on r.story_id = s.story_id "
                    "where not s.is_backfill and s.first_seen > now() - interval '24 hours' and coalesce(s.event,'other') not in ('opinion') "
                    "order by s.priority desc limit 30")
        rest = [dict(story_id=r[0], title=r[1], event=r[2], n_sources=r[3], priority=r[4], first_source=r[5], read=r[6]) for r in cur.fetchall() if r[0] not in shown][:5]
        if rest:
            lines += ["", "## Also moving (top stories not about your holdings)"]
            lines += ["- " + fmt_story(s) for s in rest]
        cur.execute("select count(*), coalesce(sum(cost_usd), 0) from story_reads where read_at > date_trunc('day', now())")
        n_read, cost = cur.fetchone()
        lines += ["", f"(LLM reader today: {n_read} stories, about ${cost:.3f} at list price)"]
    return "\n".join(lines)


def send(text):
    topic = (Path.home() / "services" / "ntfy" / "topic.txt").read_text().strip()
    req = urllib.request.Request(f"http://100.83.128.73:8090/{topic}", data=text[:3500].encode("utf-8"),
                                 headers={"Title": "Morning brief", "Priority": "default", "Tags": "newspaper"})
    urllib.request.urlopen(req, timeout=15).read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    text = build()
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "briefing-latest.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    if a.send:
        send(text)
        print("\n[sent to phone]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
