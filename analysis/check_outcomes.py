"""Check past directional calls (ai_calls_log) against what actually happened,
using free market data (yfinance), and write the verdict back.

This is what makes the system self-correcting instead of just self-reporting:
every call logged via `cli.py log-call` gets revisited N days later and marked
correct/incorrect/unclear, building a real, checkable hit-rate over time
instead of trusting the AI's own confidence score.

A call's ticker_or_theme is matched to a yfinance symbol via TICKER_MAP below.
Add an entry there for any new theme you start logging calls about.

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run: python check_outcomes.py [--min-age-days 3] [--move-threshold 0.5]
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import yfinance as yf  # noqa: F401 (kept for TICKER_MAP users)
from dotenv import load_dotenv
from supabase import create_client

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger
sys.path.append(str(Path(__file__).parent))
import scoring

log = get_logger("analysis.check_outcomes")

load_dotenv()

# Maps a logged theme/ticker string (case-insensitive substring match) to a
# yfinance symbol and the direction that counts as "up" for that symbol.
TICKER_MAP = {
    "10y treasury": "^TNX",
    "10-year": "^TNX",
    "treasury yield": "^TNX",
    "brent": "BZ=F",
    "wti": "CL=F",
    "oil": "CL=F",
    "nvda": "NVDA",
    "nvidia": "NVDA",
    "msft": "MSFT",
    "microsoft": "MSFT",
    "googl": "GOOGL",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "gold": "GC=F",
    "dollar": "DX-Y.NYB",
    "usd": "DX-Y.NYB",
}


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def resolve_symbol(theme: str, explicit=None):
    return scoring.resolve_symbol(theme, explicit)


def price_move_pct(symbol: str, since: datetime):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=since.strftime("%Y-%m-%d"))
    if hist.empty or len(hist) < 2:
        return None
    start_price = hist["Close"].iloc[0]
    end_price = hist["Close"].iloc[-1]
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price * 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-age-days", type=int, default=3,
                         help="only check calls at least this many days old (give the market time to move)")
    parser.add_argument("--move-threshold", type=float, default=0.5,
                         help="minimum %% price move to count as a directional outcome; smaller moves are 'unclear'")
    args = parser.parse_args()

    client = get_client()
    res = client.table("ai_calls_log").select("*").is_("outcome", "null").execute()
    calls = res.data
    if not calls:
        log.info("No unscored calls.")
        return

    checked, skipped = 0, 0
    for c in calls:
        symbol = resolve_symbol(c["ticker_or_theme"], c.get("symbol"))
        if not symbol:
            log.warning(f"no symbol mapping for '{c['ticker_or_theme']}' -- add one to scoring.TICKER_MAP")
            skipped += 1
            continue

        created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
        horizon = int(c.get("horizon_days") or scoring.DEFAULT_HORIZON_DAYS)
        w = scoring.window_return(symbol, created, horizon)
        if w is None:
            skipped += 1  # window still open, or no price data yet: try again next cycle
            continue

        bench = scoring.window_return(scoring.BENCHMARK, created, horizon) if symbol in scoring.STOCKS else None
        outcome, basis = scoring.judge(c["call"], symbol, w, bench, args.move_threshold)
        now_iso = datetime.now(timezone.utc).isoformat()
        update = {"outcome": outcome, "outcome_checked_at": now_iso, "resolved_at": now_iso,
                  "entry_price": round(w["entry_price"], 4), "exit_price": round(w["exit_price"], 4),
                  "asset_return_pct": round(w["return_pct"], 3), "scoring_version": scoring.SCORING_VERSION}
        if bench:
            update["benchmark_return_pct"] = round(bench["return_pct"], 3)
            update["excess_return_pct"] = round(w["return_pct"] - bench["return_pct"], 3)
        for attempt in range(8):  # drop columns that migration 004 hasn't added yet
            try:
                client.table("ai_calls_log").update(update).eq("id", c["id"]).execute()
                break
            except Exception as e:  # noqa: BLE001
                bad = next((k for k in list(update) if k not in ("outcome", "outcome_checked_at") and (f"'{k}'" in str(e) or f'"{k}"' in str(e) or f"column {k}" in str(e))), None)
                if not bad:
                    raise
                update.pop(bad)

        log.info(f"{c['ticker_or_theme']:30s} called {c['call']:8s} -> {symbol:10s} {horizon}d {w['return_pct']:+.2f}% -> {outcome} ({basis})")
        checked += 1

    total = checked + skipped
    skip_frac = (skipped / total) if total else 0.0
    log.info(f"Checked {checked}, skipped {skipped} (no symbol mapping, window not elapsed, or no data).")
    if total >= 5 and skip_frac > 0.20:
        log.warning(f"check_outcomes: {skipped}/{total} ({skip_frac:.0%}) of unscored calls were skipped this cycle "
                    f"-- likely a yfinance/data problem (see scoring.window_return warnings above), not just open windows. "
                    f"Investigate before trusting the hit-rate below.")

    # print running hit-rate (one count per distinct view, not per logged row)
    res = client.table("ai_calls_log").select("*").not_.is_("outcome", "null").execute()
    views = scoring.distinct_views(res.data)
    outcomes = [r["outcome"] for r in views]
    if outcomes:
        correct = outcomes.count("correct")
        incorrect = outcomes.count("incorrect")
        unclear = outcomes.count("unclear")
        decided = correct + incorrect
        rate = correct / decided * 100 if decided else 0
        log.info(f"Running hit-rate (distinct views, excl. unclear): {correct}/{decided} = {rate:.1f}%  (+{unclear} unclear, {len(res.data) - len(views)} duplicate rows ignored)")


if __name__ == "__main__":
    main()
