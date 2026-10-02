"""LLM reader: structured reading of the most important stories, in batches, with a daily cap.

Regex tags are a cheap first pass (analysis/event_rules.py). This step asks a small Claude model to read the top
stories by priority and return: event type, company the news is about, direction for that company, magnitude,
relevance and a one-line takeaway. Results go to story_reads. Batching matters: each headless call carries a fixed
~6.5k-token overhead, so 20 stories per call is far cheaper than 20 calls.

    python reader.py                     # read the next batch of top unread stories (respects the daily cap)
    python reader.py --dry-run           # show what would be read and the prompt size, no model call
    python reader.py --eval-set 2        # read the gold sample (title+summary only) for evaluation
Controls: MI_READER=0 disables; MI_READER_DAILY_MAX (default 60 stories/day); MI_READER_MODEL (default haiku).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EVENTS = ("earnings guidance m&a legal_reg analyst macro product supply leadership capital layoffs cyber opinion restatement "
          "agreement insider geopolitics listing governance disclosure other_filing other").split()
HERE = Path(__file__).parent

PROMPT_HEAD = """You are a careful financial news reader. For each numbered item return ONE JSON object.
Fields:
  "i": the item number,
  "event": one of %(events)s
     (opinion = commentary, listicle, "should you buy", price-move explainer or preview with no new facts;
      insider = executive/director share sales or purchases; geopolitics = war, sanctions, diplomacy, summits),
  "about": the single US-listed ticker the news is mainly about, or "" if none or several,
  "direction": -1, 0 or 1 = likely effect of THIS news on that company's (or, if no company, the market's) value; 0 if unclear or neutral,
  "magnitude": 1 (minor), 2 (moderate) or 3 (major, market-moving),
  "relevant": 1 if useful to someone following US equities and the macro backdrop, else 0,
  "takeaway": at most 18 words stating what actually happened (no advice, no hype).
Judge only from the text given. If the text is too thin to tell, use event "other", direction 0, magnitude 1.
Return ONLY a JSON array with one object per item, nothing else.

"""


def build_prompt(items):
    """items: list of dicts with i, title, source, summary, text (optional)."""
    lines = []
    for it in items:
        body = (it.get("text") or it.get("summary") or "").replace("\n", " ").strip()
        lines.append(f'[{it["i"]}] ({it.get("source", "?")}) {it["title"].strip()}' + (f"\n    {body[:900]}" if body else ""))
    return PROMPT_HEAD % {"events": ", ".join(EVENTS)} + "\n".join(lines)


def parse_response(text, expected):
    """Extract the JSON array from a model reply; returns {i: cleaned dict}. Bad rows are dropped, not guessed."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for o in arr:
        try:
            i = int(o["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if i not in expected:
            continue
        ev = str(o.get("event", "other")).strip().lower()
        out[i] = {
            "event": ev if ev in EVENTS else "other",
            "about": str(o.get("about") or "").strip().upper()[:8],
            "direction": max(-1, min(1, int(o.get("direction") or 0))),
            "magnitude": max(1, min(3, int(o.get("magnitude") or 1))),
            "relevant": 1 if int(o.get("relevant") or 0) else 0,
            "takeaway": str(o.get("takeaway") or "")[:200],
        }
    return out


def call_claude(prompt, model):
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    proc = subprocess.run([claude, "-p", "--strict-mcp-config", "--tools", "", "--model", model, "--output-format", "json", prompt],
                          capture_output=True, text=True, timeout=300)
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude returned non-JSON (rc={proc.returncode}): {proc.stdout[:200]!r} {proc.stderr[:200]!r}")
    if d.get("is_error"):
        raise RuntimeError(f"claude error: {str(d.get('result'))[:200]}")
    u = d.get("usage") or {}
    tokens = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0))
    return d.get("result", ""), tokens, float(d.get("total_cost_usd") or 0.0)


def eval_set(n, model, batch=18):
    src = HERE / "eval" / ("sample_unlabeled.jsonl" if n == 1 else f"sample{n}_unlabeled.jsonl")
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    preds, cost, toks = {}, 0.0, [0, 0]
    for k in range(0, len(rows), batch):
        chunk = rows[k:k + batch]
        items = [{"i": r["i"], "title": r["title"], "source": r["source"], "summary": r["summary"]} for r in chunk]
        text, (ti, to), c = call_claude(build_prompt(items), model)
        got = parse_response(text, {r["i"] for r in chunk})
        preds.update(got)
        cost += c; toks[0] += ti; toks[1] += to
        print(f"batch {k // batch + 1}: parsed {len(got)}/{len(chunk)}  cost ${c:.4f}")
    out = HERE / "eval" / f"reader_pred_set{n}.json"
    json.dump({"model": model, "cost_usd": round(cost, 4), "tokens_in": toks[0], "tokens_out": toks[1], "n": len(rows),
               "pred": {str(k): v for k, v in preds.items()}}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"read {len(preds)}/{len(rows)} items, total cost ${cost:.4f} (list price basis), {toks[0]} in / {toks[1]} out tokens -> {out}")


def run_live(k, model, dry):
    import psycopg
    from dotenv import dotenv_values
    env = dotenv_values(Path.home() / "services" / "marketdb" / ".env")
    daily = int(os.environ.get("MI_READER_DAILY_MAX", "60"))
    with psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="postgres", password=env["POSTGRES_PASSWORD"]) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from story_reads where read_at > date_trunc('day', now())")
        used = cur.fetchone()[0]
        room = max(0, daily - used)
        if room == 0:
            print(f"daily cap reached ({used}/{daily})"); return 0
        cur.execute(
            "select s.story_id, s.title, s.first_source, a.summary_raw, left(a.full_text, 1500) "
            "from stories s join articles a on a.id = s.first_article_id "
            "where not s.is_backfill and s.first_seen > now() - interval '36 hours' and s.priority >= 40 "
            "and coalesce(s.event, 'other') <> 'opinion' "
            "and not exists (select 1 from story_reads r where r.story_id = s.story_id and r.model = %s) "
            "order by s.priority desc limit %s", (model, min(k, room)))
        rows = cur.fetchall()
        if not rows:
            print("nothing worth reading right now"); return 0
        items = [{"i": n, "title": r[1], "source": r[2], "summary": r[3], "text": r[4]} for n, r in enumerate(rows)]
        prompt = build_prompt(items)
        print(f"{len(items)} stories, prompt about {len(prompt) // 4} tokens; used today {used}/{daily}")
        if dry:
            for it in items:
                print(f"  - {it['title'][:100]}")
            return 0
        text, (ti, to), cost = call_claude(prompt, model)
        got = parse_response(text, set(range(len(rows))))
        for n, r in enumerate(rows):
            if n in got:
                cur.execute("insert into story_reads (story_id, model, result, input_tokens, output_tokens, cost_usd) values (%s,%s,%s,%s,%s,%s) "
                            "on conflict (story_id, model) do update set result = excluded.result, read_at = now()",
                            (r[0], model, json.dumps(got[n], ensure_ascii=False), ti // len(rows), to // len(rows), cost / len(rows)))
        conn.commit()
        print(f"stored {len(got)}/{len(rows)} readings, tokens {ti} in / {to} out, cost ${cost:.4f} (list price basis)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--eval-set", type=int, default=0)
    a = ap.parse_args(argv)
    if os.environ.get("MI_READER", "0") != "1":
        print("reader is OFF by default: it uses Claude subscription usage. Enable deliberately with MI_READER=1."); return 0
    model = os.environ.get("MI_READER_MODEL", "haiku")
    if a.eval_set:
        eval_set(a.eval_set, model); return 0
    return run_live(a.k, model, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
