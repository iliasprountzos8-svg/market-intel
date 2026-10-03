"""Honest track-record report for ai_calls_log.

Re-scores EVERY logged call the same fair way (fixed horizon, entry before the news could
be acted on, benchmark for stocks) instead of trusting the stored `outcome`, then reports
the hit rate with a 95% confidence interval, the naive "always follow the market drift"
baseline, and a plain-English verdict about whether the sample is even big enough to mean
anything.

Read-only: writes nothing to Supabase. Output: printed report + logs/track-record.json.

Run: python track_record.py [--threshold 0.5] [--min-n 30] [--notify]
"""

import argparse
import collections
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402
import scoring  # noqa: E402
import calibration  # noqa: E402

log = get_logger("analysis.track_record")
load_dotenv()


def get_client():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def rate(hits, n):
    return (hits / n) if n else None


def fmt_rate(hits, n):
    if not n:
        return "n/a"
    lo, hi = scoring.wilson(hits, n)
    return f"{hits}/{n} = {hits / n:.0%} (95% CI {lo:.0%}-{hi:.0%})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=scoring.DEFAULT_THRESHOLD_PCT)
    ap.add_argument("--min-n", type=int, default=30, help="decided calls needed before the number means anything")
    ap.add_argument("--notify", action="store_true", help="push a short summary to ntfy (NTFY_SERVER/NTFY_TOPIC)")
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "logs" / "track-record.json"))
    args = ap.parse_args()

    rows = get_client().table("ai_calls_log").select("*").order("created_at").execute().data or []
    have = set(rows[0].keys()) if rows else set()

    # de-duplicate: the same view logged twice on the same day is one call, not two
    seen, calls = set(), []
    for r in rows:
        if r.get("call") not in ("bullish", "bearish"):
            continue
        created = parse_ts(r["created_at"])
        sym = scoring.resolve_symbol(r.get("ticker_or_theme"), r.get("symbol"))
        key = (sym or r.get("ticker_or_theme"), r["call"], created.date())
        if key in seen:
            continue
        seen.add(key)
        calls.append((r, created, sym))

    recs, unscorable, pending, views_seen = [], [], [], set()
    for r, created, sym in calls:
        horizon = int(r.get("horizon_days") or scoring.DEFAULT_HORIZON_DAYS)
        if not sym:
            unscorable.append(r.get("ticker_or_theme"))
            continue
        w = scoring.window_return(sym, created, horizon)
        if w is None:
            pending.append(f"{sym}({created.date()})")
            continue
        bench = scoring.window_return(scoring.BENCHMARK, created, horizon) if sym in scoring.STOCKS else None
        v, basis = scoring.judge(r["call"], sym, w, bench, args.threshold)
        vk = scoring.view_key(sym, r["call"], w)
        if vk in views_seen:
            continue  # same view re-logged against the same window
        views_seen.add(vk)
        rec = {"symbol": sym, "call": r["call"], "date": created.date().isoformat(), "horizon": horizon,
               "ret": w["return_pct"], "verdict": v, "basis": basis, "confidence": r.get("confidence")}
        if bench:
            rec["excess"] = w["return_pct"] - bench["return_pct"]
        recs.append(rec)

    decided = [x for x in recs if x["verdict"] in ("correct", "incorrect")]
    hits = sum(x["verdict"] == "correct" for x in decided)
    bull = [x for x in recs if x["call"] == "bullish"]
    up_rate_hits = sum(x["ret"] >= args.threshold for x in bull)

    by_dir = collections.defaultdict(lambda: [0, 0])
    by_sym = collections.defaultdict(lambda: [0, 0])
    by_conf = collections.defaultdict(lambda: [0, 0])
    for x in decided:
        for bucket, key in ((by_dir, x["call"]), (by_sym, x["symbol"])):
            bucket[key][1] += 1
            bucket[key][0] += x["verdict"] == "correct"
        if x["confidence"] is not None:
            cb = "high 70+" if x["confidence"] >= 70 else "medium 40-69" if x["confidence"] >= 40 else "low <40"
            by_conf[cb][1] += 1
            by_conf[cb][0] += x["verdict"] == "correct"

    stock_bull = [x for x in bull if "excess" in x]
    beat = sum(x["excess"] > 0 for x in stock_bull)
    avg_excess = (sum(x["excess"] for x in stock_bull) / len(stock_bull)) if stock_bull else None

    n = len(decided)
    if n < args.min_n:
        verdict_txt = (f"Too few decided calls ({n}, need {args.min_n}+). Treat the hit rate as anecdote, "
                       f"not evidence of skill.")
    else:
        lo, _ = scoring.wilson(hits, n)
        base = rate(up_rate_hits, len(bull)) or 0.5
        verdict_txt = ("Hit rate's lower bound is above the always-bullish baseline: there is some evidence of edge."
                       if lo > max(base, 0.5) else
                       "Cannot distinguish the hit rate from luck or from simply following market drift.")

    lines = [
        f"Track record (fixed-horizon, benchmark-aware) as of {datetime.now(timezone.utc):%Y-%m-%d}",
        f"calls logged: {len(rows)} | distinct directional: {len(calls)} | scored: {len(recs)} | "
        f"decided (moved >= {args.threshold}%): {n} | window still open: {len(pending)} | no symbol: {len(unscorable)}",
        f"HIT RATE: {fmt_rate(hits, n)}",
        f"baseline (share of bullish windows where the asset rose): {fmt_rate(up_rate_hits, len(bull))}",
        "by direction: " + ", ".join(f"{k} {fmt_rate(*v)}" for k, v in by_dir.items()),
        "by symbol: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by_sym.items(), key=lambda kv: -kv[1][1])[:8]),
    ]
    if by_conf:
        lines.append("by confidence: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in by_conf.items()))
    elif "confidence" not in have:
        lines.append("confidence not stored in the database yet (run migration 004) -> calibration impossible")
    pairs = [(x["confidence"] / 100.0, 1 if x["verdict"] == "correct" else 0) for x in decided if x["confidence"] is not None]
    if pairs:
        b = calibration.brier(pairs)
        br = sum(o for _, o in pairs) / len(pairs)
        bb = calibration.brier([(br, o) for _, o in pairs])
        lines.append(f"Brier {b:.3f} vs constant-baseline {bb:.3f} (skill {(1 - b / bb):+.0%}; n={len(pairs)}, lower is better)" if bb else f"Brier {b:.3f}")
    if stock_bull:
        lines.append(f"bullish stock calls beating VT: {beat}/{len(stock_bull)}, average excess {avg_excess:+.2f}%")
    if unscorable:
        lines.append("no price symbol for: " + ", ".join(sorted(set(str(u) for u in unscorable))[:6]))
    lines.append("VERDICT: " + verdict_txt)
    report = "\n".join(lines)
    print(report)

    out = {"generated": datetime.now(timezone.utc).isoformat(), "n_decided": n, "hits": hits,
           "hit_rate": rate(hits, n), "ci95": scoring.wilson(hits, n), "baseline_up_rate": rate(up_rate_hits, len(bull)),
           "min_n": args.min_n, "verdict": verdict_txt, "by_direction": {k: v for k, v in by_dir.items()},
           "pending": len(pending), "unscorable": len(unscorable), "report": report}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, default=str))

    if args.notify and os.environ.get("NTFY_SERVER") and os.environ.get("NTFY_TOPIC"):
        body = (f"Hit rate {fmt_rate(hits, n)}. Baseline {fmt_rate(up_rate_hits, len(bull))}. {verdict_txt}")
        req = urllib.request.Request(
            os.environ["NTFY_SERVER"].rstrip("/"),
            data=json.dumps({"topic": os.environ["NTFY_TOPIC"], "title": "Market Intel: weekly track record",
                             "message": body, "priority": 1, "tags": ["bar_chart"],
                             "click": os.environ.get("NTFY_CONTROL_URL", "")}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # noqa: BLE001
            log.warning(f"ntfy failed: {e}")


if __name__ == "__main__":
    main()
