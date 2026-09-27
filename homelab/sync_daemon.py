#!/usr/bin/env python3
"""Homelab -> site sync daemon (runs 24/7 as a systemd service).

The homelab is the primary system. The site (Vercel) can't reach it, so this daemon does the
talking, outbound only:

  push (every ~30 s)   worthy articles (new or re-scored), digests, calls   -> Supabase (site updates live via realtime)
  push (every 15 s)    pipeline_status heartbeat + progress                  -> Supabase
  push (on change)     ticker_signals (S&P 500 sentiment), lab_report        -> Supabase
  pull (every 5 min)   dashboard-owned settings (holdings, app_settings...)  <- Supabase
  poll (every 5 s)     `commands` queue: 'sync' and 'digest' requested from the site (phone)

Only two command kinds exist and both map to allow-listed local scripts, with rate limits.
Missing remote tables/columns (migration 005 not yet applied) are tolerated: it logs and keeps going.
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row
from supabase import create_client

sys.path.insert(0, str(Path(__file__).parent))
from joblock import is_running as _is_running, start_job as _start_job  # noqa: E402

MI = Path(__file__).parent
LOGS = MI / "logs"
DATA = MI / "data"
ENV = dotenv_values(MI / ".env")
for _k in ("SUPABASE_MIRROR_URL", "SUPABASE_MIRROR_KEY"):  # allow a test run against another REST endpoint
    if os.environ.get(_k):
        ENV[_k] = os.environ[_k]
HQENV = dotenv_values(Path.home() / "services" / "hq" / ".env")
STATE_F = Path(os.environ.get("SYNC_STATE") or (DATA / "sync-state.json"))
HOLD_RX = "(nvidia|nvda|microsoft|msft|alphabet|google|asml)"
SP = json.loads((DATA / "sp500.json").read_text())
NAMES = {x["symbol"]: (x["name"], x["sector"]) for x in SP}
NAMES.update({"ASML": ("ASML Holding", "Information Technology"), "TSM": ("Taiwan Semiconductor", "Information Technology"), "ARM": ("Arm Holdings", "Information Technology")})
WORTHY = 40
ART_COLS = ("id,source,url,title,left(summary_raw,600) as summary_raw,author,published_at,category,tickers_raw,ai_processed,ai_processed_at,ai_sentiment,"
            "ai_relevance_score,ai_affected_tickers,ai_summary,ai_suggested_action,ai_risk_flag,ai_confidence,fb_score,event,lang")
JOBS = {"sync": ["/bin/bash", str(MI / "run_sync.sh")], "digest": ["/bin/bash", str(MI / "run_digest.sh")]}
LOCKS = {"sync": "/tmp/mi-cycle.lock", "digest": "/tmp/mi-digest.lock", "fast": "/tmp/mi-fast.lock"}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def now():
    return datetime.now(timezone.utc)


def jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


def clean(row, drop=()):
    return {k: jsonable(v) for k, v in row.items() if k not in drop}


def load_state():
    try:
        return json.loads(STATE_F.read_text())
    except Exception:
        return {}


def save_state(st):
    STATE_F.write_text(json.dumps(st))


def local():
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="hq_ro", password=HQENV["HQ_DB_PASSWORD"], row_factory=dict_row, connect_timeout=5)


def rd(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def running(name):
    return _is_running(LOCKS[name])


class Remote:
    def __init__(self):
        self.c = create_client(ENV["SUPABASE_MIRROR_URL"], ENV["SUPABASE_MIRROR_KEY"])
        self.local_c = create_client(ENV["SUPABASE_URL"], ENV["SUPABASE_SERVICE_KEY"])
        self.missing_cols = {}     # table -> set(columns the remote doesn't have yet)
        self.missing_tables = {}   # table -> last warned ts

    def table_ok(self, t):
        w = self.missing_tables.get(t)
        return not (w and time.time() - w < 300)  # retry a missing table every 5 min

    def upsert(self, table, rows, on_conflict, chunk=100):
        if not rows or not self.table_ok(table):
            return 0
        drop = self.missing_cols.setdefault(table, set())
        sent = 0
        for i in range(0, len(rows), chunk):
            part = rows[i:i + chunk]
            for _ in range(30):
                try:
                    self.c.table(table).upsert([{k: v for k, v in r.items() if k not in drop} for r in part], on_conflict=on_conflict).execute()
                    sent += len(part)
                    break
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    m = re.search(r"Could not find the '(\w+)' column", msg)
                    if m:
                        drop.add(m.group(1))
                        log(f"remote {table} lacks column {m.group(1)} (apply migration 005); sending without it")
                        continue
                    if "PGRST205" in msg or "Could not find the table" in msg:
                        self.missing_tables[table] = time.time()
                        log(f"remote table {table} missing (apply migration 005)")
                        return sent
                    raise
        return sent

    def select(self, table, cols="*", **filters):
        if not self.table_ok(table):
            return []
        try:
            q = self.c.table(table).select(cols)
            for k, v in filters.items():
                q = q.eq(k, v)
            return q
        except Exception:
            return []


R = None


# ------------------------------------------------------------------ pushes
def push_articles(st):
    wm = st.get("art_wm", (now() - timedelta(days=2)).isoformat())
    with local() as c:
        rows = c.execute(f"""select {ART_COLS}, greatest(coalesce(ai_processed_at,'epoch'), coalesce(scraped_at,'epoch')) as wm from articles
                             where published_at > now() - interval '5 days'
                               and (ai_relevance_score >= %s or ai_summary is not null or title ~* %s)
                               and greatest(coalesce(ai_processed_at,'epoch'), coalesce(scraped_at,'epoch')) > %s
                             order by wm asc limit 800""", (WORTHY, HOLD_RX, wm)).fetchall()
    if not rows:
        return 0
    new_wm = max(r["wm"] for r in rows).isoformat()
    sent = R.upsert("articles", [clean(r, drop=("wm",)) for r in rows], "url")
    st["art_wm"] = new_wm
    if sent:
        log(f"pushed {sent} articles")
    return sent


def push_digests_calls(st):
    wm = st.get("dg_wm", (now() - timedelta(days=3)).isoformat())
    with local() as c:
        dg = c.execute("select * from digests where created_at > %s order by created_at limit 50", (wm,)).fetchall()
        calls = c.execute("select * from ai_calls_log where created_at > %s or outcome_checked_at > %s order by created_at limit 100", (wm, wm)).fetchall()
    n = R.upsert("digests", [clean(r) for r in dg], "id") + R.upsert("ai_calls_log", [clean(r) for r in calls], "id")
    if dg or calls:
        st["dg_wm"] = max([r["created_at"] for r in dg + calls]).isoformat()
        log(f"pushed {len(dg)} digests, {len(calls)} calls")
    return n


_heavy = {"t": 0, "data": {}}


def status_data():
    heavy = _heavy
    if time.time() - heavy["t"] > 60:
        with local() as c:
            cnt = c.execute("""select count(*) total, count(*) filter (where published_at > now() - interval '1 hour') h1, count(*) filter (where published_at > now() - interval '24 hours') h24,
                                      count(*) filter (where published_at > now() - interval '24 hours' and ai_relevance_score >= %s) worthy24, max(published_at) newest from articles""", (WORTHY,)).fetchone()
            fb = c.execute("""select count(*) filter (where s.article_id is not null) scored, count(*) n from articles a left join article_scores s on s.article_id=a.id and s.model='finbert'
                              where a.published_at > now() - interval '24 hours' and a.lang = 'en'""").fetchone()
            kinds = c.execute("select kind, count(*) n, count(*) filter (where enabled) en, count(*) filter (where fail_count>=3) failing from sources group by 1 order by 2 desc").fetchall()
            ing = c.execute("select created_at, entries_fetched from source_health where source='INGEST_ALL' and created_at > now() - interval '12 hours' order by created_at").fetchall()
            perday = c.execute("select published_at::date d, count(*) n from articles where published_at > now() - interval '14 days' group by 1 order by 1").fetchall()
            size = c.execute("select pg_size_pretty(pg_database_size('marketintel')) s").fetchone()["s"]
            mix = c.execute("select ai_sentiment s, count(*) n from articles where published_at > now() - interval '24 hours' group by 1").fetchall()
        heavy.update(t=time.time(), data={"counts": clean(cnt), "finbert": clean(fb), "sources": [clean(k) for k in kinds],
                                          "ingest": [{"t": jsonable(r["created_at"]), "new": r["entries_fetched"]} for r in ing],
                                          "per_day": [{"d": str(r["d"]), "n": r["n"]} for r in perday], "db_size": size,
                                          "mix24": {r["s"] or "none": r["n"] for r in mix}})
    d = dict(heavy["data"])
    d.update({"updated_at": now().isoformat(), "host": "homelab",
              "cycle": {"text": rd(LOGS / "cycle-status.txt"), "running": running("sync")},
              "fast": {"text": rd(LOGS / "fast-status.txt"), "running": running("fast")},
              "sync": {"text": rd(LOGS / "sync-status.txt")},
              "digest": {"text": rd(LOGS / "digest-status.txt"), "running": running("digest")},
              "lab_as_of": (json.loads(rd(LOGS / "lab-daily.json") or "{}")).get("as_of"),
              "schedule": {"cycle_minutes": 30, "fast_minutes": 5, "sync_seconds": 30}})
    return d


def push_status(extra=None):
    d = status_data()
    if extra:
        d.update(extra)
    R.upsert("pipeline_status", [{"id": 1, "updated_at": now().isoformat(), "data": d}], "id")


def push_signals(st):
    p = DATA / "signals.db"
    mt = p.stat().st_mtime if p.exists() else 0
    if mt <= st.get("sig_mtime", 0) and time.time() - st.get("sig_push", 0) < 900:
        return 0
    import sqlite3
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    last = con.execute("select max(ts) from snapshots2 where model='ens'").fetchone()[0]
    if not last:
        return 0
    cur = {}
    for sym, model, idx, z, n, att in con.execute("select symbol, model, idx, z, n_lead, att_z from snapshots2 where ts=?", (last,)):
        cur.setdefault(sym, {})[model] = (idx, z, n, att)
    since = (now() - timedelta(days=14)).isoformat()
    series = {}
    for sym, ts, idx in con.execute("select symbol, ts, idx from snapshots2 where model='ens' and ts>=? and idx is not null order by ts", (since,)):
        series.setdefault(sym, []).append((ts, idx))
    con.close()
    perf = {}
    try:
        w = pd.read_parquet(DATA / "prices.parquet").pivot(index="date", columns="symbol", values="close").sort_index()
        for s in w.columns:
            x = w[s].dropna()
            if len(x) > 6:
                perf[s] = {"last": float(x.iloc[-1]), "d1": float((x.iloc[-1] / x.iloc[-2] - 1) * 100), "d5": float((x.iloc[-1] / x.iloc[-6] - 1) * 100)}
    except Exception:  # noqa: BLE001
        pass
    rows = []
    for sym, m in cur.items():
        ens = m.get("ens")
        if not ens or not ens[2]:
            continue
        nm = NAMES.get(sym, (sym, "Macro" if sym in ("RATES", "OIL", "GOLD", "USD", "MARKET") else ""))
        s = series.get(sym, [])
        s = [[t, round(v, 3)] for i, (t, v) in enumerate(s) if i % 6 == 0][-60:]
        rows.append({"symbol": sym, "name": nm[0], "sector": nm[1], "updated_at": last, "ens": ens[0], "ens_z": ens[1], "stories_24h": ens[2], "attention_z": ens[3],
                     "finbert": (m.get("finbert") or (None,))[0], "lex": (m.get("lex") or (None,))[0], "old": (m.get("old") or (None,))[0],
                     "series": s, "perf": perf.get(sym)})
    sent = R.upsert("ticker_signals", rows, "symbol", chunk=100)
    if sent:
        st["sig_mtime"], st["sig_push"] = mt, time.time()
        log(f"pushed {sent} ticker signals")
    return sent


def push_lab(st):
    f = LOGS / "lab-daily.json"
    if not f.exists():
        return 0
    mt = f.stat().st_mtime
    if mt <= st.get("lab_mtime", 0):
        return 0
    rep = json.loads(f.read_text())
    with local() as c:
        d = c.execute("select max(pred_date) d from lab_predictions").fetchone()["d"]
        top = c.execute("select book, symbol, rank, score from lab_predictions where pred_date=%s and rank<=10 order by book, rank", (d,)).fetchall()
        avoid = c.execute("""select book, symbol, rank, score from (select *, max(rank) over (partition by book) mr from lab_predictions where pred_date=%s) t
                             where rank > mr - 10 order by book, rank desc""", (d,)).fetchall()
        ledger = c.execute("""select book, side, count(*) filter (where status='closed') n, avg(net_excess_pct) filter (where status='closed') net,
                              avg((excess_pct>0)::int) filter (where status='closed') hit, count(*) filter (where status='pending') open from lab_positions group by 1,2 order by 1,2""").fetchall()
        calls = c.execute("select created_at, ticker_or_theme, call, confidence, horizon_days, outcome from ai_calls_log order by created_at desc limit 12").fetchall()
    tr = json.loads(rd(LOGS / "track-record.json") or "{}")
    se = json.loads(rd(LOGS / "signal-eval.json") or "{}")
    report = {"as_of": str(d), "top": [clean(r) for r in top], "avoid": [clean(r) for r in avoid], "ledger": [clean(r) for r in ledger],
              "walkforward": {h: json.loads(rd(LOGS / f"lab-walkforward-h{h}.json") or "null") for h in (5, 20)},
              "signal_eval": (se.get("report") or "").splitlines()[-1:] or [], "track": {"n": tr.get("n_decided"), "hit": tr.get("hit_rate"), "ci": tr.get("ci95"), "verdict": tr.get("verdict")},
              "calls": [clean(r) for r in calls], "open_positions": rep.get("open_paper_positions"),
              "note": "Research and paper trading only. Not investment advice. Survivorship bias: the universe is today's S&P 500."}
    n = R.upsert("lab_report", [{"as_of": str(d), "updated_at": now().isoformat(), "report": json.loads(json.dumps(report, default=str))}], "as_of")
    if n:
        st["lab_mtime"] = mt
        log("pushed lab report")
    return n


def pull_settings(st):
    if time.time() - st.get("settings_t", 0) < 300:
        return
    for table, key in (("holdings", "symbol"), ("app_settings", "id"), ("portfolio_meta", "id"), ("push_subscriptions", "id"), ("source_overrides", "name")):
        try:
            rows = R.c.table(table).select("*").execute().data or []
            if rows:
                R.local_c.table(table).upsert(rows, on_conflict=key).execute()
        except Exception as e:  # noqa: BLE001
            log(f"pull {table} failed: {str(e)[:90]}")
    st["settings_t"] = time.time()


# ------------------------------------------------------------------ commands from the site
cur_cmd = {"row": None, "proc": None, "t0": None}
LAST = {"sync": 0, "digest": []}


def finish(status, result):
    row = cur_cmd["row"]
    try:
        R.c.table("commands").update({"status": status, "result": result[:300], "finished_at": now().isoformat()}).eq("id", row["id"]).execute()
    except Exception as e:  # noqa: BLE001
        log("command update failed:", str(e)[:100])
    cur_cmd.update(row=None, proc=None, t0=None)
    log(f"command {row['kind']} -> {status}: {result}")


def handle_commands(st):
    if not R.table_ok("commands"):
        return False
    if cur_cmd["row"]:
        proc = cur_cmd["proc"]
        if proc is not None and proc.poll() is None:
            if time.time() - cur_cmd["t0"] > 1500:
                proc.kill()
                finish("failed", "timed out after 25 min")
            return True
        rc = proc.returncode if proc is not None else 0
        kind = cur_cmd["row"]["kind"]
        if kind == "sync":
            txt = rd(LOGS / "sync-status.txt")
            LAST["sync"] = time.time()
            finish("done" if rc == 0 else "failed", (txt or f"rc={rc}") if rc != 3 else "a full cycle was already running; new data will appear when it finishes")
        else:
            LAST["digest"].append(time.time())
            with local() as c:
                d = c.execute("select created_at from digests order by created_at desc limit 1").fetchone()
            fresh = d and d["created_at"] > datetime.fromtimestamp(cur_cmd["t0"], timezone.utc)
            finish("done" if rc == 0 and fresh else "failed", "digest written" if rc == 0 and fresh else f"digest job rc={rc}")
        push_articles(st)
        push_digests_calls(st)
        push_status()
        return False
    try:
        res = R.c.table("commands").select("*").eq("status", "pending").order("requested_at").limit(5).execute().data or []
    except Exception as e:  # noqa: BLE001
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            R.missing_tables["commands"] = time.time()
            log("remote table commands missing (apply migration 005)")
        return False
    for row in res:
        age = (now() - datetime.fromisoformat(row["requested_at"].replace("Z", "+00:00"))).total_seconds()
        claim = lambda status, result=None: R.c.table("commands").update({"status": status, "started_at": now().isoformat(), "result": result}).eq("id", row["id"]).eq("status", "pending").execute()
        if row["kind"] not in JOBS:
            claim("rejected", "unknown command")
            continue
        if age > 900:
            claim("expired", "older than 15 minutes")
            continue
        if row["kind"] == "sync" and time.time() - LAST["sync"] < 45:
            claim("done", "just synced; data is current")
            continue
        LAST["digest"] = [t for t in LAST["digest"] if time.time() - t < 86400]
        if row["kind"] == "digest" and (len(LAST["digest"]) >= 8 or (LAST["digest"] and time.time() - LAST["digest"][-1] < 180)):
            claim("rejected", "digest rate limit (max 8/day, 3 min apart)")
            continue
        got = claim("running")
        if not got.data:
            continue  # someone else claimed it
        cur_cmd.update(row=row, proc=subprocess.Popen(JOBS[row["kind"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(MI)), t0=time.time())
        log(f"command {row['kind']} started")
        push_status({"command": {"kind": row["kind"], "status": "running"}})
        return True
    return False


# ------------------------------------------------------------------ main loop
def main():
    global R
    R = Remote()
    st = load_state()
    t = {"cmd": 0, "art": 0, "status": 0, "dg": 0, "sig": 0}
    fails = 0
    log("sync daemon started")
    while True:
        try:
            tm = time.time()
            if tm - t["cmd"] >= 5:
                t["cmd"] = tm
                handle_commands(st)
            if tm - t["status"] >= 15:
                t["status"] = tm
                push_status({"command": ({"kind": cur_cmd["row"]["kind"], "status": "running"} if cur_cmd["row"] else None)})
            if tm - t["art"] >= 30:
                t["art"] = tm
                push_articles(st)
                push_digests_calls(st)
                pull_settings(st)
            if tm - t["sig"] >= 120:
                t["sig"] = tm
                push_signals(st)
                push_lab(st)
            save_state(st)
            fails = 0
        except Exception as e:  # noqa: BLE001
            fails += 1
            log("loop error:", str(e)[:200])
            if fails <= 2:
                traceback.print_exc()
            time.sleep(min(60, 2 ** min(fails, 6)))
        time.sleep(2)


if __name__ == "__main__":
    main()
