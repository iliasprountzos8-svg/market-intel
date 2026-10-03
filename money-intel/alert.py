"""ntfy alerts for money-intel: newly-detected recurring charges, bills likely due soon, and a
forecast trending over budget. Same NTFY_SERVER/NTFY_TOPIC env vars as analysis/notify.py;
no-ops silently if they aren't set (so this is safe to run before ntfy is configured for this).
"""
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()  # picks up NTFY_SERVER/NTFY_TOPIC from market-intel/.env (searches upward from cwd)


def send_ntfy(title: str, body: str, priority: int = 3, tags: list | None = None):
    import json
    import urllib.request

    server = os.environ.get("NTFY_SERVER")
    topic = os.environ.get("NTFY_TOPIC")
    if not server or not topic:
        print(f"[ntfy not configured, would send] {title}: {body}")
        return
    payload = {"topic": topic, "title": title, "message": body, "priority": priority}
    if tags:
        payload["tags"] = tags
    try:
        req = urllib.request.Request(server.rstrip("/"), data=json.dumps(payload).encode("utf-8"),
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"ntfy send failed: {e}")


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="Send one test ping and exit, to verify the ntfy channel.")
    args = ap.parse_args()
    if args.test:
        send_ntfy("money-intel test", f"ntfy channel OK ({date.today().isoformat()}).", tags=["white_check_mark"])
        return

    import dbconn as db
    import forecast
    import recurring

    _, newly_detected = recurring.main()
    for merchant in sorted(newly_detected):
        send_ntfy("New recurring charge detected", f"'{merchant}' looks like a recurring charge now.",
                   tags=["repeat"])

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select merchant_norm, typical_amount, typical_interval_days, last_seen "
                     "from money_recurring where status = 'active'")
        today = date.today()
        for merchant, amount, interval, last_seen in cur.fetchall():
            days_until_next = (last_seen + timedelta(days=round(interval))) - today
            if 0 <= days_until_next.days <= 2:
                send_ntfy(f"Bill likely due soon: {merchant}", f"EUR {amount:.2f}, expected around now "
                          f"based on a {interval:.0f}-day pattern.", tags=["moneybag"])

    result = forecast.main()
    if result.get("over_budget"):
        lo, hi = result["projected_ci95"]
        send_ntfy("Spending trending over budget", f"Projected EUR {result['projected_total']:.2f} "
                  f"(CI {lo:.2f}-{hi:.2f}) vs budget EUR {result['budget']:.2f}.",
                  priority=4, tags=["warning"])


if __name__ == "__main__":
    main()
