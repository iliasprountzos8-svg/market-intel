"""Detect recurring charges: group transactions by merchant, flag a group as recurring only if
it repeats at a roughly stable interval and amount -- so two unrelated trips to the same
supermarket don't get mistaken for a subscription, but a monthly gym fee does.

Pure detection logic lives in detect_recurring() (unit-tested); main() wires it to the DB.
"""
import statistics
from datetime import date, datetime

MIN_OCCURRENCES = 3
MIN_INTERVAL_DAYS = 6          # excludes near-daily repeats (groceries, coffee) from "recurring"
MAX_INTERVAL_CV = 0.3          # coefficient of variation on the gaps between occurrences
MAX_AMOUNT_CV = 0.15           # coefficient of variation on the charged amount
LAPSED_AFTER_MULTIPLE = 1.75   # if last charge is this many typical-intervals overdue, call it lapsed


def _cv(values):
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0
    return statistics.pstdev(values) / abs(mean)


def detect_recurring(transactions, as_of: date | None = None):
    """transactions: iterable of dicts with keys txn_date (date), merchant_norm (str), amount (float).
    Returns a list of dicts ready to upsert into money_recurring."""
    as_of = as_of or date.today()
    by_merchant: dict[str, list] = {}
    for t in transactions:
        by_merchant.setdefault(t["merchant_norm"], []).append(t)

    out = []
    for merchant, txns in by_merchant.items():
        if merchant == "unknown" or len(txns) < MIN_OCCURRENCES:
            continue
        txns = sorted(txns, key=lambda t: t["txn_date"])
        dates = [t["txn_date"] for t in txns]
        amounts = [t["amount"] for t in txns]
        intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
        if not intervals or min(intervals) < MIN_INTERVAL_DAYS:
            continue
        if _cv(intervals) > MAX_INTERVAL_CV or _cv(amounts) > MAX_AMOUNT_CV:
            continue

        typical_interval = statistics.fmean(intervals)
        last_seen = dates[-1]
        days_since_last = (as_of - last_seen).days
        status = "lapsed" if days_since_last > typical_interval * LAPSED_AFTER_MULTIPLE else "active"

        out.append({
            "merchant_norm": merchant,
            "typical_amount": round(statistics.fmean(amounts), 2),
            "typical_interval_days": round(typical_interval, 1),
            "last_seen": last_seen,
            "n_occurrences": len(txns),
            "status": status,
        })
    return out


def main():
    import dbconn as db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select txn_date, merchant_norm, amount from money_transactions")
        rows = [{"txn_date": d, "merchant_norm": m, "amount": float(a)} for d, m, a in cur.fetchall()]

        cur.execute("select merchant_norm from money_recurring")
        known_before = {r[0] for r in cur.fetchall()}

        recurring = detect_recurring(rows)
        for r in recurring:
            cur.execute(
                "insert into money_recurring (merchant_norm, typical_amount, typical_interval_days, "
                "last_seen, n_occurrences, status, updated_at) values (%s,%s,%s,%s,%s,%s, now()) "
                "on conflict (merchant_norm) do update set typical_amount = excluded.typical_amount, "
                "typical_interval_days = excluded.typical_interval_days, last_seen = excluded.last_seen, "
                "n_occurrences = excluded.n_occurrences, status = excluded.status, updated_at = now()",
                (r["merchant_norm"], r["typical_amount"], r["typical_interval_days"],
                 r["last_seen"], r["n_occurrences"], r["status"]),
            )
        conn.commit()

    new = {r["merchant_norm"] for r in recurring} - known_before
    print(f"{len(recurring)} recurring charge(s) tracked, {len(new)} newly detected: {sorted(new)}")
    return recurring, new


if __name__ == "__main__":
    main()
