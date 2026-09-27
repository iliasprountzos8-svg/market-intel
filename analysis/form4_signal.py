"""Turn ingested Form 4 transactions (form4_transactions, local Postgres) into a daily
per-symbol insider-buying index, written into signals.py's snapshots2 table as model="form4"
so analysis/signal_eval.py can test it the same way it tests news sentiment.

Only open-market purchases (P) and sales (S) count -- grants, awards, gifts and other
non-discretionary transaction codes carry no buy/sell signal and are excluded.

    python form4_signal.py            # compute today's index from the last N days of transactions
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect  # noqa: E402
import signals  # noqa: E402

BUY, SELL = "P", "S"


def compute_daily_index(transactions):
    """transactions: iterable of dicts with symbol, transaction_code, shares, price_per_share.
    Returns {symbol: (idx, n)} where idx in [-1, 1] is (buy_usd - sell_usd) / (buy_usd + sell_usd)
    over P/S transactions only, and n is the count of P/S transactions contributing.
    Symbols with no usable P/S dollar volume are omitted."""
    by_sym = {}
    for t in transactions:
        code = t.get("transaction_code")
        if code not in (BUY, SELL):
            continue
        shares, price = t.get("shares"), t.get("price_per_share")
        if not shares or not price:
            continue
        usd = float(shares) * float(price)
        buy_usd, sell_usd, n = by_sym.setdefault(t["symbol"], [0.0, 0.0, 0])
        if code == BUY:
            buy_usd += usd
        else:
            sell_usd += usd
        by_sym[t["symbol"]] = [buy_usd, sell_usd, n + 1]

    out = {}
    for sym, (buy_usd, sell_usd, n) in by_sym.items():
        denom = buy_usd + sell_usd
        if denom <= 0:
            continue
        out[sym] = ((buy_usd - sell_usd) / denom, n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="lookback window of transactions to summarize into today's index")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select symbol, transaction_code, shares, price_per_share from form4_transactions "
            "where filed_at >= %s",
            (since,),
        )
        rows = [{"symbol": s, "transaction_code": c, "shares": sh, "price_per_share": p} for s, c, sh, p in cur.fetchall()]

    idx_by_sym = compute_daily_index(rows)
    if not idx_by_sym:
        print("form4_signal: no P/S transactions in window, nothing to write")
        return 0

    now = datetime.now(timezone.utc)
    con = signals.db()
    written = 0
    for sym, (idx, n) in idx_by_sym.items():
        z = signals.zscore(con, sym, "form4", idx, now)
        con.execute(
            "insert or replace into snapshots2 (ts, symbol, model, idx, z, n_lead) values (?, ?, ?, ?, ?, ?)",
            (now.isoformat(), sym, "form4", idx, z, n),
        )
        written += 1
    con.commit()
    print(f"form4_signal: wrote {written} symbol snapshots (model=form4) from {len(rows)} transactions over {args.days}d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
