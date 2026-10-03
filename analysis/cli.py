"""CLI for Claude (or you) to read/write the market-intel DB during an on-demand
analysis pass. No separate AI API key needed -- Claude reads the fetched articles
itself in-conversation, reasons about them, and writes conclusions back via
mark-processed / write-digest.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Examples:
  python cli.py fetch-unprocessed --limit 50
  python cli.py fetch-recent --hours 24
  python cli.py mark-processed <article_id> \
      --sentiment bullish --relevance 80 \
      --tickers NVDA,MSFT --summary "..." \
      --action "watch" --risk none --confidence 70
  python cli.py write-digest --hours 24 --summary "..." \
      --themes "Fed rate path,AI capex" --guidance "..." \
      --watchlist '{"NVDA": "earnings beat, no action needed"}'
  python cli.py log-call --ticker NVDA --call bullish --rationale "..." --digest-id <id>
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client

try:
    from classify import score_article
except ImportError:
    score_article = None

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

log = get_logger("analysis.cli")

load_dotenv()


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def cmd_fetch_unprocessed(client, args):
    res = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,url")
        .eq("ai_processed", False)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    # Prefer full_text when available; fall back to the RSS summary otherwise
    # (some sources block scraping -- see fetch_fulltext.py). Keeps the payload
    # Claude reads free of a redundant near-duplicate field.
    rows = []
    for r in res.data:
        r["text"] = r.pop("full_text") or r.pop("summary_raw")
        r.pop("summary_raw", None)
        rows.append(r)
    print(json.dumps(rows, indent=2, default=str))


def cmd_fetch_recent(client, args):
    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    query = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,"
                "ai_sentiment,ai_relevance_score,ai_summary,url")
        .gte("published_at", since)
    )
    if args.min_relevance:
        query = query.gte("ai_relevance_score", args.min_relevance)
    res = query.order("published_at", desc=True).limit(args.limit).execute()
    rows = []
    for r in res.data:
        r["text"] = r.pop("full_text") or r.pop("summary_raw")
        r.pop("summary_raw", None)
        if args.max_chars and r["text"]:
            r["text"] = r["text"][:args.max_chars]
        rows.append(r)
    print(json.dumps(rows, indent=2, default=str))


def cmd_mark_processed(client, args):
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else []
    payload = {
        "ai_processed": True,
        "ai_processed_at": datetime.now(timezone.utc).isoformat(),
        "ai_sentiment": args.sentiment,
        "ai_relevance_score": args.relevance,
        "ai_affected_tickers": tickers,
        "ai_summary": args.summary,
        "ai_suggested_action": args.action,
        "ai_risk_flag": args.risk,
        "ai_confidence": args.confidence,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    res = client.table("articles").update(payload).eq("id", args.article_id).execute()
    print(json.dumps(res.data, indent=2, default=str))


def cmd_bulk_classify_rule_based(client, args):
    if not score_article:
        log.error("vaderSentiment is not installed. Please pip install -r requirements.txt")
        sys.exit(1)

    res = (
        client.table("articles")
        .select("id,source,title,summary_raw,full_text,published_at,category,tickers_raw,url")
        .eq("ai_processed", False)
        .order("published_at", desc=True)
        .limit(args.limit)
        .execute()
    )
    
    rows = res.data
    if not rows:
        log.info("No unprocessed articles found.")
        return

    updated_count = 0
    for row in rows:
        payload = score_article(row)
        payload["ai_processed_at"] = datetime.now(timezone.utc).isoformat()
        
        try:
            client.table("articles").update(payload).eq("id", row["id"]).execute()
            updated_count += 1
        except Exception as e:
            log.error(f"Failed to update article {row['id']}: {e}")
            
    log.info(f"Bulk classified {updated_count} articles using rule-based heuristics.")


def cmd_write_digest(client, args):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    themes = [t.strip() for t in args.themes.split(",")] if args.themes else []
    watchlist = json.loads(args.watchlist) if args.watchlist else None

    # count articles covered in this window
    count_res = (
        client.table("articles")
        .select("id", count="exact")
        .gte("published_at", since.isoformat())
        .execute()
    )
    covered = count_res.count or 0

    payload = {
        "period_start": since.isoformat(),
        "period_end": now.isoformat(),
        "articles_covered": covered,
        "summary": args.summary,
        "key_themes": themes,
        "guidance": args.guidance,
        "watchlist_notes": watchlist,
    }
    res = client.table("digests").insert(payload).execute()
    print(json.dumps(res.data, indent=2, default=str))


def find_recent_duplicate(client, symbol, ticker, call, horizon_days):
    """An identical view (same symbol, same direction) logged inside the last `horizon_days` days
    is the same bet, not new evidence: re-logging it would be scored again and inflate the hit rate."""
    since = (datetime.now(timezone.utc) - timedelta(days=horizon_days or 5)).isoformat()
    rows = client.table("ai_calls_log").select("id,created_at,symbol,ticker_or_theme").eq("call", call)         .gte("created_at", since).execute().data or []
    want = {str(symbol).lower(), str(ticker).lower()}
    for r in rows:
        if {str(r.get("symbol") or "").lower(), str(r.get("ticker_or_theme") or "").lower()} & want:
            return r
    return None


def cmd_log_call(client, args):
    """Log a directional call in a form that can be scored fairly later: symbol, horizon,
    confidence, invalidation, and the entry price captured at logging time. Columns that
    don't exist in the database yet (migration 004) are dropped with a warning, not fatal."""
    import scoring

    symbol = scoring.resolve_symbol(args.ticker, args.symbol)
    entry_price = scoring.latest_close(symbol) if symbol else None
    payload = {
        "digest_id": args.digest_id,
        "ticker_or_theme": args.ticker,
        "call": args.call,
        "rationale": args.rationale,
        "confidence": args.confidence,
        "horizon_days": args.horizon_days,
        "symbol": symbol,
        "invalidation": args.invalidation,
        "entry_price": entry_price,
        "entry_at": datetime.now(timezone.utc).isoformat() if entry_price else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if not getattr(args, "force", False):
        dup = find_recent_duplicate(client, symbol or args.ticker, args.ticker, args.call, args.horizon_days)
        if dup:
            print(f"skipped: same view already logged {dup['created_at'][:16]} (id {str(dup['id'])[:8]}); it is still "
                  f"inside its {args.horizon_days}-day window. Pass --force to log it anyway.", file=sys.stderr)
            return
    for attempt in range(16):  # one column may be dropped per retry
        try:
            res = client.table("ai_calls_log").insert(payload).execute()
            print(json.dumps(res.data, indent=2, default=str))
            return
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            missing = next((k for k in list(payload) if f"'{k}'" in msg or f'"{k}"' in msg or f"column {k}" in msg), None)
            if missing and missing not in ("ticker_or_theme", "call"):
                print(f"warning: column '{missing}' not in database yet (run migration 004); logging without it", file=sys.stderr)
                payload.pop(missing)
                continue
            raise
    raise SystemExit("log-call failed: could not insert after dropping unknown columns")


def cmd_market_snapshot(client, args):
    """Compact price context so analysis can tell 'news' from 'already priced in'."""
    import yfinance as yf
    import pandas as pd

    rows = [("NVDA", "NVDA"), ("MSFT", "MSFT"), ("GOOGL", "GOOGL"), ("ASML", "ASML"), ("VWCE.DE", "VWCE (EUR)"),
            ("VT", "Global equities (VT)"), ("^GSPC", "S&P 500"), ("^TNX", "US 10y yield"), ("CL=F", "WTI oil"),
            ("BZ=F", "Brent"), ("GC=F", "Gold"), ("DX-Y.NYB", "Dollar index")]
    STOCKS = {"NVDA", "MSFT", "GOOGL", "ASML"}
    out = []
    for sym, name in rows:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="3mo")
            s = hist["Close"].dropna()
            if len(s) < 6:
                continue

            def chg(n):
                return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if len(s) > n else None

            entry = {"symbol": sym, "name": name, "last": round(float(s.iloc[-1]), 2),
                     "1d_pct": round(chg(1), 2), "5d_pct": round(chg(5), 2),
                     "20d_pct": round(chg(20), 2) if chg(20) is not None else None,
                     "asof": str(s.index[-1].date())}

            vol = hist["Volume"].dropna()
            if len(vol) >= 21 and vol.iloc[-2::-1][:20].mean() > 0:
                avg20 = vol.iloc[-21:-1].mean()
                entry["volume_vs_20d_avg"] = round(float(vol.iloc[-1] / avg20), 2)

            if sym in STOCKS:
                try:
                    cal = t.get_earnings_dates(limit=4)
                    if cal is not None and not cal.empty:
                        now = pd.Timestamp.now(tz=cal.index.tz)
                        upcoming = cal.index[cal.index >= now]
                        if len(upcoming):
                            days_out = (upcoming[0] - now).days
                            if days_out <= 20:
                                entry["earnings_in_days"] = days_out
                except Exception:  # noqa: BLE001
                    pass

            out.append(entry)
        except Exception as e:  # noqa: BLE001
            out.append({"symbol": sym, "error": str(e)[:60]})
    print(json.dumps(out, indent=1))


def cmd_signals(client, args):
    """Latest per-ticker news-sentiment index (signals.py, ensemble model) plus recent spike alerts. Read-only."""
    import sqlite3
    db = Path(__file__).parent.parent / "data" / "signals.db"
    if not db.exists():
        print("[]")
        return
    con = sqlite3.connect(db, timeout=60)
    last = con.execute("select max(ts) from snapshots2 where model='ens'").fetchone()[0]
    rows = con.execute("select symbol, round(idx,2), round(z,1), n_lead, round(att_z,1) from snapshots2 where ts=? and model='ens' and n_lead>=4 "
                       "order by abs(coalesce(z,0)) desc limit 12", (last,)).fetchall()
    alerts = con.execute("select ts, symbol, detail from alerts order by ts desc limit 5").fetchall()
    print(json.dumps({"as_of": last, "note": "index in [-1,1], relative to each source's normal tone; z vs the ticker's own 14d history; small samples, not advice",
                      "tickers": [dict(zip(("symbol", "index", "z", "stories_24h", "attention_z"), r)) for r in rows],
                      "recent_alerts": [dict(zip(("ts", "symbol", "detail"), r)) for r in alerts]}, indent=1))


def cmd_lab(client, args):
    """Latest prediction-lab ranked ideas and paper-ledger results (research only). Read-only."""
    f = Path(__file__).parent.parent / "logs" / "lab-daily.json"
    print(f.read_text() if f.exists() else "{}")


def cmd_calibration_summary(client, args):
    """Your own track record: does stated confidence match the actual hit rate? Read this before logging new calls."""
    res = (
        client.table("ai_calls_log")
        .select("confidence,outcome,call")
        .not_.is_("outcome", "null")
        .neq("outcome", "unclear")
        .execute()
    )
    rows = [r for r in res.data if r.get("confidence") is not None]
    if len(rows) < 5:
        print(f"Only {len(rows)} decided calls with a confidence score so far -- too few to calibrate on. "
              "Do not adjust confidence based on this yet.")
        return

    pairs = [(r["confidence"] / 100.0, 1 if r["outcome"] == "correct" else 0) for r in rows]
    brier = sum((p - o) ** 2 for p, o in pairs) / len(pairs)
    lines = [f"Resolved calls with confidence: {len(pairs)} | Brier score {brier:.3f} "
             "(0.250 = always-50% baseline, lower is better)"]

    edges = (0.0, 0.4, 0.6, 0.8, 1.01)
    for lo, hi in zip(edges, edges[1:]):
        sel = [(p, o) for p, o in pairs if lo <= p < hi]
        if sel:
            stated = sum(p for p, _ in sel) / len(sel)
            actual = sum(o for _, o in sel) / len(sel)
            lines.append(f"  stated {lo:.0%}-{min(hi, 1.0):.0%}: n={len(sel)}, "
                         f"stated avg {stated:.0%} vs actual hit rate {actual:.0%}")

    by_dir = {}
    for r in rows:
        d = by_dir.setdefault(r.get("call"), {"n": 0, "hits": 0})
        d["n"] += 1
        if r["outcome"] == "correct":
            d["hits"] += 1
    for d, s in by_dir.items():
        lines.append(f"  direction {d}: {s['hits']}/{s['n']} correct")

    if len(pairs) < 30:
        lines.append(f"Only {len(pairs)} decided calls: treat as a weak signal, not evidence (need 30+ for real confidence).")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch-unprocessed")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_fetch_unprocessed)

    p = sub.add_parser("fetch-recent")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--min-relevance", type=int, default=0,
                   help="Only articles whose rule-based ai_relevance_score is >= this")
    p.add_argument("--max-chars", type=int, default=0,
                   help="Truncate each article's text to this many characters (0 = full)")
    p.set_defaults(func=cmd_fetch_recent)

    p = sub.add_parser("mark-processed")
    p.add_argument("article_id")
    p.add_argument("--sentiment", choices=["bullish", "bearish", "neutral", "mixed"])
    p.add_argument("--relevance", type=int)
    p.add_argument("--tickers", help="comma-separated tickers/sectors")
    p.add_argument("--summary")
    p.add_argument("--action")
    p.add_argument("--risk")
    p.add_argument("--confidence", type=int)
    p.set_defaults(func=cmd_mark_processed)

    p = sub.add_parser("write-digest")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--summary", required=True)
    p.add_argument("--themes", help="comma-separated")
    p.add_argument("--guidance")
    p.add_argument("--watchlist", help="JSON object string")
    p.set_defaults(func=cmd_write_digest)

    p = sub.add_parser("log-call")
    p.add_argument("--ticker", required=True)
    p.add_argument("--call", required=True, choices=["bullish", "bearish", "neutral"])
    p.add_argument("--rationale")
    p.add_argument("--digest-id")
    p.add_argument("--confidence", type=int, required=True, help="0-100 confidence score (required: a call without one can't be calibration-scored)")
    p.add_argument("--horizon-days", type=int, default=5, help="calendar days until the call is scored (5=short, 20=medium)")
    p.add_argument("--symbol", help="explicit price symbol (e.g. NVDA, ^TNX, VWCE.DE); resolved from --ticker if omitted")
    p.add_argument("--invalidation", help="what would prove this call wrong")
    p.add_argument("--force", action="store_true", help="log even if the same view is already open")
    p.set_defaults(func=cmd_log_call)

    p = sub.add_parser("signals")
    p.set_defaults(func=cmd_signals)

    p = sub.add_parser("lab")
    p.set_defaults(func=cmd_lab)

    p = sub.add_parser("calibration-summary")
    p.set_defaults(func=cmd_calibration_summary)

    p = sub.add_parser("market-snapshot")
    p.set_defaults(func=cmd_market_snapshot)
    
    p = sub.add_parser("bulk-classify")
    p.add_argument("--rule-based", action="store_true", help="Use local rule-based heuristics instead of LLM")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_bulk_classify_rule_based)

    args = parser.parse_args()
    client = get_client()
    args.func(client, args)


if __name__ == "__main__":
    main()
