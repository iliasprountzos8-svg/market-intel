"""Re-score EVERY row of ai_calls_log with the current rules (scoring.SCORING_VERSION) and,
with --apply, write the corrected outcomes back. Dry-run by default: prints stored vs new.

Use after changing scoring.py so old verdicts (made with older, looser rules) stop standing in
the track record. Back the table up first (pg_dump -t ai_calls_log).

Run: python rescore_calls.py [--apply]
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent.parent))
import scoring  # noqa: E402

load_dotenv()


def rescore(rows, threshold=scoring.DEFAULT_THRESHOLD_PCT):
    """Yield (row, update_dict_or_None, new_outcome). update None = window not scorable yet."""
    for c in rows:
        created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
        sym = scoring.resolve_symbol(c["ticker_or_theme"], c.get("symbol"))
        if c["call"] == "neutral":
            yield c, {"outcome": "unclear", "scoring_version": scoring.SCORING_VERSION}, "unclear"
            continue
        horizon = int(c.get("horizon_days") or scoring.DEFAULT_HORIZON_DAYS)
        w = scoring.window_return(sym, created, horizon) if sym else None
        if w is None:
            yield c, None, None
            continue
        bench = scoring.window_return(scoring.BENCHMARK, created, horizon) if sym in scoring.STOCKS else None
        outcome, _ = scoring.judge(c["call"], sym, w, bench, threshold)
        now = datetime.now(timezone.utc).isoformat()
        upd = {"outcome": outcome, "symbol": sym, "outcome_checked_at": now, "resolved_at": now,
               "entry_price": round(w["entry_price"], 4), "exit_price": round(w["exit_price"], 4),
               "asset_return_pct": round(w["return_pct"], 3), "scoring_version": scoring.SCORING_VERSION,
               "benchmark_return_pct": round(bench["return_pct"], 3) if bench else None,
               "excess_return_pct": round(w["return_pct"] - bench["return_pct"], 3) if bench else None}
        yield c, upd, outcome


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    rows = client.table("ai_calls_log").select("*").order("created_at").execute().data
    changed = 0
    for c, upd, new in rescore(rows):
        old = c.get("outcome")
        if upd is None:
            # previously scored under old rules but not scorable now -> back to pending
            tag = "-> pending" if old else "pending"
            print(f"{str(c['id'])[:8]} {c['ticker_or_theme'][:22]:22} {c['call']:8} {old or '-':10} {tag}")
            if old and args.apply:
                client.table("ai_calls_log").update({"outcome": None, "scoring_version": None}).eq("id", c["id"]).execute()
                changed += 1
            continue
        flag = "" if old == new else "  <-- changed"
        print(f"{str(c['id'])[:8]} {c['ticker_or_theme'][:22]:22} {c['call']:8} {old or '-':10} {new:10}{flag}")
        if old != new:
            changed += 1
        if args.apply:
            client.table("ai_calls_log").update(upd).eq("id", c["id"]).execute()
    print(f"\n{changed} outcome(s) changed; {'APPLIED' if args.apply else 'dry-run, nothing written (use --apply)'}")


if __name__ == "__main__":
    main()
