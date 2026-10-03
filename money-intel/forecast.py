"""End-of-month spend forecast with an honest confidence band, not a fake-precise number.
Same bootstrap-CI method as analysis/benchmark_snapshot.py's boot_ci (small enough to duplicate
rather than import a module that pulls in the news pipeline's dependencies).

Convention: amount < 0 is money out, amount > 0 is money in (as most bank CSV exports encode
it). Income and transfers-to-investing are excluded from "spend" so the forecast tracks actual
living-cost burn, not your whole cash flow.

Usage: python forecast.py [--budget 400]
   --budget overrides the monthly living-cost budget checked against; falls back to
   MONEY_INTEL_MONTHLY_BUDGET env var, then to no budget check if neither is set.
"""
import argparse
import calendar
import os
import random
import statistics
from datetime import date

NON_SPEND_CATEGORIES = {"income", "investing"}


def boot_ci(vals, n=2000, seed=7):
    if len(vals) < 2:
        return None
    rnd = random.Random(seed)
    k = len(vals)
    means = sorted(statistics.fmean(rnd.choices(vals, k=k)) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def forecast_month(daily_spend_by_date: dict, today: date | None = None):
    """daily_spend_by_date: {date: spend_amount (positive number)} for the current month so far.
    Returns a dict with month-to-date total, projected total, and a 95% CI, or an explanation
    of why there isn't enough data yet."""
    today = today or date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    days_remaining = days_in_month - days_elapsed

    mtd_total = sum(daily_spend_by_date.values())
    n_logged = len(daily_spend_by_date)
    if days_elapsed < 5:
        return {"mtd_total": round(mtd_total, 2), "n_days_logged": n_logged,
                "note": "fewer than 5 days into the month; forecast needs more history"}

    # Zero-fill every elapsed day, not just the ones with a logged charge -- otherwise the daily
    # average is computed only over spending days and overshoots (a quiet day is real data too).
    from datetime import timedelta
    month_start = today.replace(day=1)
    vals = [daily_spend_by_date.get(month_start + timedelta(days=i), 0.0) for i in range(days_elapsed)]

    ci = boot_ci(vals)
    mean_daily = statistics.fmean(vals)
    projected = mtd_total + mean_daily * days_remaining
    lo = mtd_total + ci[0] * days_remaining
    hi = mtd_total + ci[1] * days_remaining
    return {
        "mtd_total": round(mtd_total, 2), "n_days_logged": n_logged,
        "days_elapsed": days_elapsed, "days_remaining": days_remaining,
        "projected_total": round(projected, 2),
        "projected_ci95": (round(lo, 2), round(hi, 2)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=None)
    args = ap.parse_args()
    budget = args.budget or (float(os.environ["MONEY_INTEL_MONTHLY_BUDGET"])
                              if os.environ.get("MONEY_INTEL_MONTHLY_BUDGET") else None)

    import dbconn as db
    today = date.today()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select txn_date, amount from money_transactions "
            "where amount < 0 and (category is null or not (category = any(%s))) "
            "and date_trunc('month', txn_date) = date_trunc('month', %s::date)",
            (list(NON_SPEND_CATEGORIES), today),
        )
        rows = cur.fetchall()

    daily = {}
    for d, amt in rows:
        daily[d] = daily.get(d, 0.0) + float(-amt)

    result = forecast_month(daily, today)
    print(f"Month-to-date spend: EUR {result['mtd_total']:.2f} ({result['n_days_logged']} day(s) with spend logged)")
    if "note" in result:
        print(result["note"])
        return result
    lo, hi = result["projected_ci95"]
    print(f"Projected month total: EUR {result['projected_total']:.2f} (95% CI: EUR {lo:.2f} - {hi:.2f})")
    if budget:
        over = result["projected_total"] > budget
        print(f"Budget EUR {budget:.2f}: {'OVER TREND' if over else 'on track'} "
              f"(projected {'+' if over else ''}{result['projected_total'] - budget:.2f} vs budget)")
        result["budget"] = budget
        result["over_budget"] = over
    return result


if __name__ == "__main__":
    main()
