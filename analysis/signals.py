"""Signals engine v2: runs every cycle on the homelab. Scores the news three ways and keeps a
per-ticker sentiment index for the whole S&P 500 (+ macro themes), so we can MEASURE which
model actually predicts anything instead of guessing.

Models tracked side by side (snapshots2.model):
  lex      finance lexicon (negation-aware) + VADER, standardised per source
  finbert  local FinBERT (ProsusAI/finbert) score from article_scores, standardised per source
  ens      average of the two when FinBERT exists, else lex

Per article: event tag/weight, source weight, duplicate/syndication clustering (same story from
many outlets counts once, with a small confirmation bonus).
Per ticker/model: recency-decayed weighted mean (half-life 12h), z-scored vs its own 14-day
history, and an attention (story volume) z-score.
Alerts (ens model, holdings + macro + mega caps, cooldown 6h) go to ntfy; optional auto-digest
(MI_AUTO_DIGEST=1). Snapshots live in SQLite (data/signals.db) for signal_eval.py.

Run: python signals.py [--backfill DAYS] [--dry-run] [--no-alert]
"""

import argparse
import bisect
import json
import math
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402

log = get_logger("analysis.signals")
load_dotenv()
ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "signals.db"

HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"]
MODELS = ("lex", "finbert", "ens", "old")
OLD_MAP = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "mixed": 0.0}

# hand-tuned aliases (case-insensitive unless wrapped in (?-i:...)) for the names people actually write
BASE_UNIVERSE = {
    "NVDA": r"nvidia|\bnvda\b", "MSFT": r"microsoft|\bmsft\b", "GOOGL": r"alphabet|google|\bgoogl?\b",
    "ASML": r"\basml\b", "AMZN": r"amazon|\bamzn\b", "META": r"meta platforms|(?-i:\bMeta\b)|facebook",
    "AAPL": r"(?-i:\bApple\b)|\baapl\b", "TSLA": r"tesla|\btsla\b", "AVGO": r"broadcom|\bavgo\b",
    "AMD": r"\bamd\b|advanced micro", "MU": r"micron", "TSM": r"tsmc|taiwan semiconductor",
    "INTC": r"(?-i:\bIntel\b)", "ORCL": r"oracle", "NFLX": r"netflix", "ARM": r"(?-i:\bArm Holdings\b)",
    "RATES": r"\bfed\b|federal reserve|fomc|powell|treasury yield|10-year yield|rate (hike|cut)|interest rate|barr\b",
    "OIL": r"\boil\b|crude|\bbrent\b|\bwti\b|opec|strait of hormuz",
    "GOLD": r"\bgold\b|bullion", "USD": r"dollar index|\bdxy\b|the dollar|us dollar",
    "MARKET": r"wall street|s&p 500|\bdow\b|nasdaq|stocks (rise|fall|slip|rally)|stock market",
}
AMBIG = {"Target", "Ford", "Gap", "Match", "Ball", "Hess", "Amgen", "Dow", "Visa", "Southern", "General", "United", "American", "First", "National", "Lowe"}
SUFFIX = re.compile(r"[,\.]?\s+(Inc\.?|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|plc|Holdings?|Group|Incorporated|Class [ABC]|& Co\.?|\(The\)|The)\s*$", re.I)


def build_universe():
    """Returns (symbols, names_map): hand-tuned BASE_UNIVERSE handles the big names with regexes;
    every other S&P 500 company is matched by exact-case company name via ONE combined regex."""
    syms = set(BASE_UNIVERSE)
    names = {}
    try:
        sp = json.load(open(ROOT / "data" / "sp500.json"))
    except OSError:
        return syms, names
    for x in sp:
        sym = x["symbol"]
        syms.add(sym)
        if sym in BASE_UNIVERSE:
            continue
        name = x["name"]
        for _ in range(3):
            name = SUFFIX.sub("", name).strip()
        if name not in AMBIG and len(name) >= 4 and (" " in name or len(name) >= 5):
            names[name] = sym
    return syms, names


UNIVERSE_SYMS, NAMES = build_universe()
COMPILED = {s: re.compile(p, re.I) for s, p in BASE_UNIVERSE.items()}
NAME_RX = re.compile(r"(?-i:(" + "|".join(re.escape(n) for n in sorted(NAMES, key=len, reverse=True)) + r"))") if NAMES else None
DOLLAR_RX = re.compile(r"\$([A-Z]{1,5})")
MACRO = {"RATES", "OIL", "GOLD", "USD", "MARKET"}
ALERT_SYMBOLS = set(HOLDINGS) | MACRO | {"AMZN", "META", "AAPL", "TSLA", "AVGO", "AMD", "MU", "TSM", "ORCL", "NFLX", "INTC"}

POS = {w: 1.0 for w in """beat beats surge surges surged soar soars soared rally rallies rallied jump jumps jumped record
upgrade upgraded upgrades raises raised outperform outperforms strong stronger strength growth profit profits profitable gain gains
gained rebound rebounds boost boosts boosted optimism optimistic bullish approval approved approves wins win won breakthrough
expands expansion buyback tops topped exceeds exceeded accelerates accelerating robust resilient upside momentum recovery
recovers partnership awarded lifts lifted climbs climbed advances advanced rises rose highest""".split()}
NEG = {w: 1.0 for w in """miss misses missed plunge plunges plunged tumble tumbles tumbled slump slumps slumped drop drops dropped
fall falls fell slide slides slid downgrade downgraded downgrades cuts warning warns warned weak weaker weakness loss losses lawsuit
sues sued probe investigation fine fined ban bans banned recall layoffs bankruptcy default fraud decline declines declined
slowdown recession risk risks fears fear concern concerns selloff crash crisis tariff tariffs sanctions shortage delay delays
delayed halts halted resigns hawkish worst lowest sinks sank slips slipped stall stalls""".split()}
NEGATORS = {"not", "no", "never", "without", "fails", "failed", "unable", "lack", "lacks"}

EVENTS = [
    ("guidance", 1.6, r"guidance|outlook|forecast|expects? (revenue|sales|profit)"),
    ("earnings", 1.5, r"earnings|quarterly results|revenue (rose|fell|beat|miss)|\beps\b|profit (rose|fell)"),
    ("analyst", 1.3, r"upgrade|downgrade|price target|initiates coverage|reiterat"),
    ("m&a", 1.4, r"acquire|acquisition|takeover|merger|buyout|to buy .* for \$"),
    ("filing", 1.3, r"\bfiled 8-K|\bfiled 6-K|\bfiled 10-[KQ]"),
    ("legal_reg", 1.2, r"lawsuit|sues|antitrust|regulator|\bftc\b|\bsec\b charges|fine[ds]?\b|probe|ban\b|sanction"),
    ("macro", 1.2, r"\bfed\b|inflation|\bcpi\b|jobs report|payrolls|gdp|rate (hike|cut)|treasury yield|tariff"),
    ("supply", 1.1, r"shortage|supply chain|export controls?|chip ban|strait of hormuz"),
    ("product", 1.0, r"launch|unveil|announces? (new|its)|release[sd]?|partnership"),
]
EVENTS = [(n, w, re.compile(p, re.I)) for n, w, p in EVENTS]

SOURCE_W = {"reuters": 1.2, "bloomberg": 1.2, "financial times": 1.2, "wsj": 1.2, "wall street journal": 1.2, "federal reserve": 1.3, "fed ": 1.3,
            "ecb": 1.3, "bls": 1.3, "sec edgar": 1.3, "cnbc": 1.0, "marketwatch": 1.0, "guardian": 0.9, "oilprice": 0.9, "nasdaq": 0.9,
            "yahoo": 0.8, "investing.com": 0.8, "business insider": 0.8, "seeking alpha": 0.7}

_vader = SentimentIntensityAnalyzer()
TOKEN = re.compile(r"[a-z']+")


def source_weight(src):
    s = (src or "").lower()
    return next((w for k, w in SOURCE_W.items() if k in s), 0.8)


def fin_score(text):
    toks = TOKEN.findall(text.lower())
    pos = neg = 0.0
    for i, t in enumerate(toks):
        s = 1.0 if t in POS else -1.0 if t in NEG else 0.0
        if not s:
            continue
        if any(x in NEGATORS for x in toks[max(0, i - 3):i]):
            s = -s
        pos, neg = (pos + 1, neg) if s > 0 else (pos, neg + 1)
    return math.tanh((pos - neg) / math.sqrt(pos + neg + 2.0))


def event_of(text):
    for name, w, rx in EVENTS:
        if rx.search(text):
            return name, w
    return "other", 0.8


def symbols_of(title, summary, tickers_raw):
    out = {}
    for sym, rx in COMPILED.items():
        if rx.search(title):
            out[sym] = 1.0
        elif summary and rx.search(summary):
            out[sym] = 0.5
    if NAME_RX:
        for m in NAME_RX.finditer(title):
            out[NAMES[m.group(1)]] = max(out.get(NAMES[m.group(1)], 0), 1.0)
        if summary:
            for m in NAME_RX.finditer(summary):
                out.setdefault(NAMES[m.group(1)], 0.5)
    for m in DOLLAR_RX.finditer(f"{title} {summary or ''}"):
        if m.group(1) in UNIVERSE_SYMS:
            out[m.group(1)] = max(out.get(m.group(1), 0), 0.9)
    for t in tickers_raw or []:
        if t in UNIVERSE_SYMS and out.get(t, 0) < 0.8:
            out[t] = 0.8  # a per-company feed / exchange tag is a strong link
    return out


def norm_title(t):
    return frozenset(w for w in TOKEN.findall((t or "").lower()) if len(w) > 3)


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def fetch_all(client, table, cols, since=None, since_col="published_at", extra=None):
    rows, off = [], 0
    while True:
        q = client.table(table).select(cols)
        if since:
            q = q.gte(since_col, since.isoformat())
        if extra:
            q = extra(q)
        r = q.order(since_col if since else cols.split(",")[0]).range(off, off + 999).execute().data
        rows += r
        if len(r) < 1000:
            return rows
        off += 1000


def standardise(items, key_in, key_out, min_n=8):
    by_src = defaultdict(list)
    for it in items:
        if it.get(key_in) is not None:
            by_src[it["source"]].append(it[key_in])
    allv = [v for vs in by_src.values() for v in vs]
    if not allv:
        return
    fb = (statistics.fmean(allv), statistics.pstdev(allv) or 0.25)
    stats = {s: (statistics.fmean(v), statistics.pstdev(v) or 0.25) for s, v in by_src.items() if len(v) >= min_n}
    for it in items:
        if it.get(key_in) is None:
            it[key_out] = None
            continue
        mu, sd = stats.get(it["source"], fb)
        it[key_out] = max(-1.0, min(1.0, (it[key_in] - mu) / (sd * 2.5)))


def score_articles(rows, finbert):
    items = []
    for r in rows:
        title, summ = r.get("title") or "", (r.get("summary_raw") or "")[:600]
        text = f"{title}. {summ}"
        ev, evw = event_of(text)
        items.append({"id": r["id"], "ts": parse_ts(r["published_at"]), "source": r.get("source"), "title": title,
                      "raw_lex": 0.6 * fin_score(text) + 0.4 * _vader.polarity_scores(text)["compound"],
                      "raw_fb": finbert.get(r["id"]), "old": OLD_MAP.get(r.get("ai_sentiment")), "event": ev, "event_w": evw, "src_w": source_weight(r.get("source")),
                      "syms": symbols_of(title, summ, r.get("tickers_raw")), "ntitle": norm_title(title)})
    standardise(items, "raw_lex", "lex")
    standardise(items, "raw_fb", "finbert")
    for it in items:
        parts = [v for v in (it["lex"], it["finbert"]) if v is not None]
        it["ens"] = sum(parts) / len(parts) if parts else None
    items.sort(key=lambda x: x["ts"])
    leaders = []
    for it in items:
        it["novelty"] = 1.0
        for ld in reversed(leaders[-500:]):
            if (it["ts"] - ld["ts"]).total_seconds() > 48 * 3600:
                break
            if jaccard(it["ntitle"], ld["ntitle"]) >= 0.6:
                it["novelty"] = 0.25
                ld["confirm"] = ld.get("confirm", 0) + 1
                break
        else:
            leaders.append(it)
    return items


def group_by_symbol(items):
    by = defaultdict(list)
    for it in items:  # items are time-sorted
        for s in it["syms"]:
            by[s].append(it)
    return by, {s: [i["ts"] for i in v] for s, v in by.items()}


def index_at(sym_items, sym_ts, sym, model, t, half_life_h=12.0):
    hi = bisect.bisect_right(sym_ts, t)
    lo = bisect.bisect_left(sym_ts, t - timedelta(hours=48))
    num = den = 0.0
    n_lead = 0
    for it in sym_items[lo:hi]:
        sc = it[model]
        if sc is None:
            continue
        age_h = (t - it["ts"]).total_seconds() / 3600
        w = it["src_w"] * it["event_w"] * it["syms"][sym] * it["novelty"] * (1.0 + min(it.get("confirm", 0), 4) * 0.1) * 0.5 ** (age_h / half_life_h)
        num += w * sc
        den += w
        if it["novelty"] == 1.0 and age_h <= 24:
            n_lead += 1
    return (num / den if den else None), n_lead, den


def attention(sym_items, sym_ts, t):
    hi = bisect.bisect_right(sym_ts, t)
    r0 = bisect.bisect_left(sym_ts, t - timedelta(hours=6))
    b0 = bisect.bisect_left(sym_ts, t - timedelta(days=14))
    recent = sum(1 for it in sym_items[r0:hi] if it["novelty"] == 1.0)
    prior = sum(1 for it in sym_items[b0:r0] if it["novelty"] == 1.0)
    lam = prior / (14 * 24 - 6) * 6
    return recent, (recent - lam) / math.sqrt(lam + 1.0)


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=180)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma busy_timeout=180000")
    con.execute("create table if not exists snapshots2(ts text, symbol text, model text, idx real, z real, n_lead int, wsum real, att_n int, att_z real, primary key(ts, symbol, model))")
    con.execute("create table if not exists alerts(ts text, symbol text, kind text, detail text)")
    con.execute("create index if not exists idx_snap2_sym_model_ts on snapshots2(symbol, model, ts)")  # zscore() filters by symbol+model; without this each of ~1,800 lookups scanned the whole table
    return con


MEM = None  # in-memory history used during --backfill (thousands of SQLite lookups are far too slow)


def zscore(con, sym, model, idx, now):
    if MEM is not None:
        lo = now - timedelta(days=14)
        hist = [v for (tt, v) in MEM[(sym, model)] if lo <= tt < now]
        MEM[(sym, model)].append((now, idx))
        if len(hist) < 20:
            return None
        sd = statistics.pstdev(hist)
        return (idx - statistics.fmean(hist)) / sd if sd > 1e-6 else None
    since = (now - timedelta(days=14)).isoformat()
    hist = [r[0] for r in con.execute("select idx from snapshots2 where symbol=? and model=? and ts>=? and ts<? and idx is not null", (sym, model, since, now.isoformat()))]
    if len(hist) < 20:
        return None
    sd = statistics.pstdev(hist)
    return (idx - statistics.fmean(hist)) / sd if sd > 1e-6 else None


def ntfy(title, msg, priority=3, tags=None):
    server, topic = os.environ.get("NTFY_SERVER"), os.environ.get("NTFY_TOPIC")
    if not server or not topic:
        return
    body = {"topic": topic, "title": title, "message": msg, "priority": priority, "tags": tags or ["mag"], "click": os.environ.get("NTFY_CONTROL_URL", "")}
    try:
        urllib.request.urlopen(urllib.request.Request(server.rstrip("/"), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=10).read()
    except Exception as e:  # noqa: BLE001
        log.warning(f"ntfy failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-alert", action="store_true")
    ap.add_argument("--z-alert", type=float, default=2.0)
    args = ap.parse_args()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    client = create_client(url, key)
    now = datetime.now(timezone.utc)
    lookback = (args.backfill + 16) if args.backfill else 16
    since = now - timedelta(days=lookback)
    rows = fetch_all(client, "articles", "id,source,title,summary_raw,published_at,tickers_raw,ai_sentiment", since=since)
    fb = {r["article_id"]: r["score"] for r in fetch_all(client, "article_scores", "article_id,score,scored_at", since=since - timedelta(days=1),
                                                       since_col="scored_at", extra=lambda q: q.eq("model", "finbert"))}
    items = score_articles(rows, fb)
    by_sym, ts_by_sym = group_by_symbol(items)
    con = db()

    def snapshot(t, write=True):
        made = []
        for sym, sitems in by_sym.items():
            sts = ts_by_sym[sym]
            att_n, att_z = attention(sitems, sts, t)
            for model in MODELS:
                idx, n_lead, wsum = index_at(sitems, sts, sym, model, t)
                if idx is None:
                    continue
                z = zscore(con, sym, model, idx, t)
                made.append((t.isoformat(), sym, model, idx, z, n_lead, wsum, att_n, att_z))
        if write and made:
            con.executemany("insert or replace into snapshots2 values(?,?,?,?,?,?,?,?,?)", made)
        return made

    if args.backfill:
        global MEM
        MEM = defaultdict(list)
        t = (now - timedelta(days=args.backfill)).replace(minute=0, second=0, microsecond=0)
        total = 0
        while t <= now:
            total += len(snapshot(t))
            con.commit()  # release the write lock every hour-step so the live cycle is never blocked
            t += timedelta(hours=1)
        log.info(f"backfill: {total} snapshots from {len(items)} scored articles ({len(fb)} with FinBERT)")

    MEM = None  # the live snapshot reads its history from SQLite
    made = snapshot(now, write=not args.dry_run)
    if not args.dry_run:
        con.commit()
    alerts = []
    for ts, sym, model, idx, z, n_lead, wsum, att_n, att_z in made:
        if model != "ens" or sym not in ALERT_SYMBOLS or z is None or abs(z) < args.z_alert or n_lead < 4:
            continue
        last = con.execute("select max(ts) from alerts where symbol=? and kind='sentiment'", (sym,)).fetchone()[0]
        if last and (now - parse_ts(last)).total_seconds() < 6 * 3600:
            continue
        top = sorted([i for i in by_sym[sym] if i["novelty"] == 1.0 and (now - i["ts"]).total_seconds() < 24 * 3600 and i["ens"] is not None],
                     key=lambda i: -abs(i["ens"]) * i["event_w"])[:2]
        alerts.append((sym, z, f"{sym} news sentiment {'up' if z > 0 else 'down'} vs its own 14-day norm (z={z:+.1f}, {n_lead} stories/24h, attention z={att_z:+.1f}). "
                       + " | ".join(f"[{i['event']}] {i['title'][:70]}" for i in top)))
    print(f"scored {len(items)} articles ({len(fb)} with FinBERT), {len(by_sym)} symbols tracked; top signals (ens):")
    for r in sorted([m for m in made if m[2] == "ens" and m[3] is not None and m[5] >= 3], key=lambda m: -abs(m[4] or 0))[:8]:
        print(f"  {r[1]:6s} idx {r[3]:+.2f} z={'n/a' if r[4] is None else f'{r[4]:+.1f}':>5} stories24h={r[5]:<3d} attention_z={r[8]:+.1f}")
    for sym, z, detail in alerts:
        print("ALERT:", detail)
        if not args.dry_run and not args.no_alert:
            con.execute("insert into alerts values(?,?,?,?)", (now.isoformat(), sym, "sentiment", detail))
            ntfy(f"Signal: {sym}", detail + " (news signal, not advice)", priority=4, tags=["chart_with_upwards_trend" if z > 0 else "chart_with_downwards_trend"])
    con.commit()
    if alerts and os.environ.get("MI_AUTO_DIGEST") == "1" and not args.dry_run:
        st = ROOT / "logs" / "digest-status.txt"
        if not (st.exists() and (datetime.now().timestamp() - st.stat().st_mtime) < 4 * 3600):
            subprocess.Popen(["/bin/bash", str(ROOT / "run_digest.sh")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            log.info("auto-digest started by signal")


if __name__ == "__main__":
    main()
