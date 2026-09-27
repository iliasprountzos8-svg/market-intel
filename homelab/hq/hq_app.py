#!/usr/bin/env python3
"""Market Intel HQ: the live, phone-first app served from the homelab.

Reads the local Postgres directly through a READ-ONLY role (it can never change data), plus the
signals SQLite, the price parquet and the lab JSON reports. Tailscale-only bind, secret-path token,
no elevated privileges. The only actions are two allow-listed jobs (pull data, generate digest).

Run with the lab venv (needs psycopg, pandas, pyarrow, yfinance).
"""
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import psycopg
from psycopg.rows import dict_row

# joblock.py sits next to hq_app.py in the deployed layout (~/services/hq/) and one level up
# in the repo layout (homelab/joblock.py, shared with homelab/sync_daemon.py) -- try both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from joblock import is_running as _is_running, start_job as _start_job  # noqa: E402

HOME = Path.home()
MI = HOME / "market-intel"
LOGS = MI / "logs"
HQ = HOME / "services" / "hq"
SIG = MI / "data" / "signals.db"
PRICES = MI / "data" / "prices.parquet"
TOKEN = (MI / ".control-token").read_text().strip()
PW = dict(l.split("=", 1) for l in (HQ / ".env").read_text().splitlines() if "=" in l)["HQ_DB_PASSWORD"]
BIND = ("100.83.128.73", 8095)
HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"]
MODELS = ("ens", "finbert", "lex", "old")
SP = json.loads((MI / "data" / "sp500.json").read_text())
NAMES = {x["symbol"]: (x["name"], x["sector"]) for x in SP}
NAMES.update({"ASML": ("ASML Holding", "Information Technology"), "TSM": ("Taiwan Semiconductor", "Information Technology"), "ARM": ("Arm Holdings", "Information Technology")})
ALIAS = {"NVDA": "nvidia|nvda", "MSFT": "microsoft|msft", "GOOGL": "alphabet|google|googl", "ASML": "asml", "AMZN": "amazon|amzn", "META": "meta platforms|facebook",
         "AAPL": "apple|aapl", "TSLA": "tesla|tsla"}
HOLD_RX = "(nvidia|nvda|microsoft|msft|alphabet|google|asml)"
SYM_RX = re.compile(r"^[A-Z0-9.\-^=]{1,10}$")
JOBS = {"pull": ["/bin/bash", str(MI / "run_pull.sh")], "digest": ["/bin/bash", str(MI / "run_digest.sh")]}
LOCKS = {"pull": "/tmp/mi-cycle.lock", "digest": "/tmp/mi-digest.lock"}
GREEK = re.compile(r"[Α-Ωα-ωά-ώ]")


def jdefault(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def db():
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="hq_ro", password=PW, row_factory=dict_row,
                           connect_timeout=5, options="-c default_transaction_read_only=on")


def q(sql, params=()):
    with db() as c:
        return c.execute(sql, params).fetchall()


def sig():
    return sqlite3.connect(f"file:{SIG}?mode=ro", uri=True, timeout=20)


def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def read_text(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- prices / quotes (cached)
_pc = {"t": 0, "wide": None}
_qc = {"t": 0, "data": {}}
_qlock = threading.Lock()


def wide_prices():
    if _pc["wide"] is None or time.time() - _pc["t"] > 300:
        df = pd.read_parquet(PRICES)
        _pc["wide"] = df.pivot(index="date", columns="symbol", values="close").sort_index()
        _pc["t"] = time.time()
    return _pc["wide"]


def perf(sym):
    w = wide_prices()
    if sym not in w.columns:
        return None
    s = w[sym].dropna()
    if len(s) < 25:
        return None
    ch = lambda n: float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100)
    return {"last": float(s.iloc[-1]), "d1": ch(1), "d5": ch(5), "d20": ch(20), "asof": str(s.index[-1].date())}


MARKET = [("NVDA", "NVDA"), ("MSFT", "MSFT"), ("GOOGL", "GOOGL"), ("ASML", "ASML"), ("VWCE.DE", "VWCE"), ("SPY", "S&P 500"), ("^TNX", "US 10y"), ("CL=F", "Oil"), ("GC=F", "Gold"), ("DX-Y.NYB", "USD")]


def quotes_cached():
    """Never blocks a page load: returns cached live quotes (or the nightly file) and refreshes in the background."""
    if time.time() - _qc["t"] >= 90 and not _qlock.locked():
        threading.Thread(target=quotes, daemon=True).start()
    if _qc["data"]:
        return _qc["data"]
    out = {}
    for s, name in MARKET:
        p = perf(s)
        if p:
            out[s] = {"name": name, "last": p["last"], "d1": p["d1"], "live": False, "asof": p["asof"]}
    return out


def quotes():
    """Live-ish quotes (Yahoo fast_info, cached 90 s); falls back to the nightly price file."""
    with _qlock:
        if time.time() - _qc["t"] < 90 and _qc["data"]:
            return _qc["data"]
        out = {}
        try:
            import yfinance as yf
            tk = yf.Tickers(" ".join(s for s, _ in MARKET))
            for s, name in MARKET:
                try:
                    fi = tk.tickers[s].fast_info
                    last, prev = float(fi["last_price"]), float(fi["previous_close"])
                    out[s] = {"name": name, "last": last, "d1": (last / prev - 1) * 100, "live": True}
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        for s, name in MARKET:
            if s not in out:
                p = perf(s)
                if p:
                    out[s] = {"name": name, "last": p["last"], "d1": p["d1"], "live": False, "asof": p["asof"]}
        _qc.update(t=time.time(), data=out)
        return out


# ---------------------------------------------------------------- job control (allow-listed)
def is_running(job):
    return _is_running(LOCKS[job])


def start_job(job):
    return _start_job(JOBS[job], LOCKS[job], MI)


# ---------------------------------------------------------------- API handlers
def api_live(_):
    c = q("""select count(*) total, count(*) filter (where published_at > now() - interval '1 hour') h1,
                    count(*) filter (where published_at > now() - interval '24 hours') h24, max(published_at) newest from articles""")[0]
    sc = q("""select count(*) filter (where s.article_id is not null) scored, count(*) n from articles a
              left join article_scores s on s.article_id=a.id and s.model='finbert' where a.published_at > now() - interval '24 hours'""")[0]
    newest = q("select id, title, source, published_at from articles order by published_at desc limit 6")
    return {"now": datetime.now(timezone.utc), "pull": {"running": is_running("pull"), "text": read_text(LOGS / "cycle-status.txt")},
            "fast": read_text(LOGS / "fast-status.txt"), "digest": {"running": is_running("digest"), "text": read_text(LOGS / "digest-status.txt")},
            "counts": c, "finbert_24h": {"scored": sc["scored"], "of": sc["n"]}, "newest": newest}


def signals_now(limit=14):
    con = sig()
    last = con.execute("select max(ts) from snapshots2 where model='ens'").fetchone()[0]
    rows = con.execute("select symbol, idx, z, n_lead, att_z from snapshots2 where ts=? and model='ens' and n_lead>=4 order by abs(coalesce(z,0)) desc limit ?", (last, limit)).fetchall()
    held = con.execute("select symbol, model, idx, z, n_lead from snapshots2 where ts=? and symbol in (?,?,?,?)", (last, *HOLDINGS)).fetchall()
    alerts = con.execute("select ts, symbol, detail from alerts order by ts desc limit 6").fetchall()
    con.close()
    hm = {}
    for s, m, i, z, n in held:
        hm.setdefault(s, {})[m] = {"idx": i, "z": z, "n": n}
    return {"as_of": last, "top": [dict(zip(("symbol", "idx", "z", "n", "att_z"), r)) for r in rows], "holdings": hm,
            "alerts": [dict(zip(("ts", "symbol", "detail"), r)) for r in alerts]}


def api_home(_):
    dg = q("select created_at, summary, guidance, key_themes, watchlist_notes from digests order by created_at desc limit 1")
    heads = q(f"""select a.id, a.title, a.url, a.source, a.published_at, s.score fb from articles a
                  left join article_scores s on s.article_id=a.id and s.model='finbert'
                  where a.published_at > now() - interval '36 hours' and a.title ~* %s order by a.published_at desc limit 14""", (HOLD_RX,))
    calls = q("select created_at, ticker_or_theme, call, confidence, horizon_days, outcome from ai_calls_log order by created_at desc limit 6")
    tr = read_json(LOGS / "track-record.json")
    lab = read_json(LOGS / "lab-daily.json")
    return {"digest": dg[0] if dg else None, "signals": signals_now(), "headlines": heads, "quotes": quotes_cached(), "calls": calls,
            "track": {"n": tr.get("n_decided"), "hit": tr.get("hit_rate"), "verdict": tr.get("verdict")} if tr else None,
            "lab": {"as_of": lab.get("as_of"), "top5": lab.get("top5"), "open": lab.get("open_paper_positions")} if lab else None}


def api_news(p):
    g = lambda k, d=None: (p.get(k) or [d])[0]
    where, params = ["a.published_at > now() - make_interval(hours => %s)"], [int(g("hours", 72))]
    t = g("ticker")
    if t:
        if not SYM_RX.match(t):
            return {"items": []}
        alias = ALIAS.get(t) or re.escape((NAMES.get(t, (t,))[0].split(" ")[0]).lower())
        where.append("((a.source = 'SEC EDGAR' and %s = any(a.tickers_raw)) or a.title ~* %s)")
        params += [t, r"\y(" + alias + r")\y"]
    if g("q"):
        where.append("a.title ilike %s")
        params.append("%" + g("q")[:60] + "%")
    f = g("f")
    if f == "pos":
        where.append("s.score > 0.15")
    elif f == "neg":
        where.append("s.score < -0.15")
    elif f == "claude":
        where.append("a.ai_summary is not null")
    elif f == "holdings":
        where.append("a.title ~* %s")
        params.append(HOLD_RX)
    elif f == "filings":
        where.append("a.source = 'SEC EDGAR'")
    if g("before"):
        where.append("a.published_at < %s")
        params.append(g("before"))
    rows = q(f"""select a.id, a.title, a.url, a.source, a.published_at, a.tickers_raw, a.ai_sentiment, a.ai_relevance_score, (a.ai_summary is not null) claude, s.score fb
                 from articles a left join article_scores s on s.article_id=a.id and s.model='finbert'
                 where {' and '.join(where)} order by a.published_at desc limit %s""", params + [min(int(g("limit", 30)), 60)])
    seen, out = set(), []
    for r in rows:
        key = re.sub(r"\W+", " ", (r["title"] or "").lower()).strip()
        if key in seen:
            continue  # the same story from several outlets shows once
        seen.add(key)
        r["greek"] = bool(GREEK.search(r["title"] or ""))
        r["tickers_raw"] = (r["tickers_raw"] or [])[:4]
        out.append(r)
    return {"items": out}


def api_symbols(_):
    return {"symbols": [{"s": s, "n": n[0], "sec": n[1]} for s, n in sorted(NAMES.items())]}


def api_ticker(p, sym):
    if not SYM_RX.match(sym):
        return {"error": "bad symbol"}
    con = sig()
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    ser = {m: [] for m in MODELS}
    for ts, m, idx in con.execute("select ts, model, idx from snapshots2 where symbol=? and ts>=? and idx is not null order by ts", (sym, since)):
        if m in ser:
            ser[m].append([ts, idx])
    last = con.execute("select max(ts) from snapshots2 where model='ens'").fetchone()[0]
    cur = {m: {"idx": i, "z": z, "n": n, "att_z": a} for m, i, z, n, a in con.execute("select model, idx, z, n_lead, att_z from snapshots2 where ts=? and symbol=?", (last, sym))}
    con.close()
    w = wide_prices()
    price = [[str(d.date()), float(v)] for d, v in w[sym].dropna().tail(90).items()] if sym in w.columns else []
    news = api_news({"ticker": [sym], "hours": ["240"], "limit": ["25"]})["items"]
    lab = q("select book, rank, score from lab_predictions where symbol=%s and pred_date=(select max(pred_date) from lab_predictions) order by book", (sym,))
    pos = q("select book, side, signal_date, status, net_excess_pct from lab_positions where symbol=%s order by signal_date desc limit 12", (sym,))
    nm = NAMES.get(sym, (sym, ""))
    return {"symbol": sym, "name": nm[0], "sector": nm[1], "perf": perf(sym), "series": ser, "current": cur, "price": price, "news": news, "lab": lab, "positions": pos}


def api_lab(_):
    rep = read_json(LOGS / "lab-daily.json") or {}
    d = q("select max(pred_date) d from lab_predictions")[0]["d"]
    top = q("select book, symbol, rank, score from lab_predictions where pred_date=%s and rank<=10 order by book, rank", (d,))
    avoid = q("""select book, symbol, rank, score from (select *, max(rank) over (partition by book) mr from lab_predictions where pred_date=%s) t
                 where rank > mr - 10 order by book, rank desc""", (d,))
    ledger = q("""select book, side, count(*) filter (where status='closed') n, avg(net_excess_pct) filter (where status='closed') net,
                  avg((excess_pct>0)::int) filter (where status='closed') hit, count(*) filter (where status='pending') open from lab_positions group by 1,2 order by 1,2""")
    wf = {h: read_json(LOGS / f"lab-walkforward-h{h}.json") for h in (5, 20)}
    se = read_json(LOGS / "signal-eval.json")
    return {"as_of": d, "top": top, "avoid": avoid, "ledger": ledger, "walkforward": wf, "signal_eval": (se or {}).get("report"), "note": rep.get("note")}


def api_system(_):
    kinds = q("select kind, count(*) n, count(*) filter (where enabled) en, count(*) filter (where fail_count>=3) failing from sources group by 1 order by 2 desc")
    failing = q("select name, fail_count, last_status, last_success from sources where fail_count>=3 order by fail_count desc limit 8")
    runs = q("select created_at, entries_fetched, errors from source_health where source='INGEST_ALL' and created_at > now() - interval '24 hours' order by created_at")
    perday = q("select published_at::date d, count(*) n from articles where published_at > now() - interval '14 days' group by 1 order by 1")
    size = q("select pg_size_pretty(pg_database_size('marketintel')) s")[0]["s"]
    calls = q("select created_at, ticker_or_theme, call, confidence, horizon_days, outcome, asset_return_pct from ai_calls_log order by created_at desc limit 15")
    tr = read_json(LOGS / "track-record.json")
    try:
        dq = q("select name, value from dq_metrics where ts = (select max(ts) from dq_metrics) order by name")
        stories = q("select s.title, s.event, s.n_sources, s.priority, r.result->>'takeaway' as takeaway from stories s "
                    "left join story_reads r on r.story_id = s.story_id where not s.is_backfill and s.first_seen > now() - interval '24 hours' "
                    "order by s.priority desc limit 8")
    except Exception:  # noqa: BLE001 - tables missing on a fresh install: the rest of the page still works
        dq, stories = [], []
    return {"dq": dq, "stories": stories, "kinds": kinds, "failing": failing, "runs": runs, "perday": perday, "db_size": size, "calls": calls,
            "track": tr, "pull": read_text(LOGS / "cycle-status.txt"), "fast": read_text(LOGS / "fast-status.txt"), "digest": read_text(LOGS / "digest-status.txt")}


ROUTES = {"live": api_live, "home": api_home, "news": api_news, "symbols": api_symbols, "lab": api_lab, "system": api_system}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        u = urlparse(self.path)
        prefix = "/" + TOKEN + "/"
        if u.path == "/health":
            return "health", "", {}
        if len(u.path) >= len(prefix) and hmac.compare_digest(u.path[:len(prefix)], prefix):
            return "ok", u.path[len(prefix):], parse_qs(u.query)
        return None, None, None

    def _json(self, obj):
        self._send(200, json.dumps(obj, default=jdefault).encode(), "application/json")

    def do_GET(self):
        kind, rest, qs = self._auth()
        if kind == "health":
            return self._send(200, b"ok")
        if kind != "ok":
            return self._send(404)
        try:
            if rest in ("", "index.html"):
                return self._send(200, (HQ / "index.html").read_bytes(), "text/html; charset=utf-8")
            if rest == "manifest.json":
                return self._send(200, json.dumps({"name": "Market Intel", "short_name": "Market Intel", "start_url": "./", "display": "standalone",
                                                   "background_color": "#0f1216", "theme_color": "#0f1216"}).encode(), "application/manifest+json")
            if rest.startswith("api/"):
                name = rest[4:]
                if name.startswith("ticker/"):
                    return self._json(api_ticker(qs, name[7:].upper()))
                if name in ROUTES:
                    return self._json(ROUTES[name](qs))
        except Exception as e:  # noqa: BLE001
            return self._send(500, json.dumps({"error": str(e)[:200]}).encode(), "application/json")
        self._send(404)

    def do_POST(self):
        kind, rest, _ = self._auth()
        if kind != "ok" or not rest.startswith("api/run/") or rest[8:] not in JOBS:
            return self._send(404)
        self._json({"result": start_job(rest[8:])})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=quotes, daemon=True).start()  # warm the cache
    ThreadingHTTPServer(BIND, H).serve_forever()
