"""Notify on newly high-relevance articles and new digests, via Web Push and
email. Runs as a step in run_cycle.py, after the classify step so freshly
rule-classified articles have an ai_relevance_score to check.

Idempotent: each article/digest is notified once (tracked via notified_at),
so re-running the cycle never re-sends the same alert.

Env vars required:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
Optional (each channel silently no-ops if its vars are missing):
  VAPID_PRIVATE_KEY, VAPID_SUBJECT   -- web push
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD  -- email (via Gmail SMTP, see README)

Run: python notify.py
"""

import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

load_dotenv()
log = get_logger("analysis.notify")

PORTFOLIO_TICKERS = {"NVDA", "MSFT", "GOOGL", "ASML"}


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    return create_client(url, key)


def send_push(subscriptions: list, title: str, body: str, url: str, client):
    from pywebpush import webpush, WebPushException

    private_key = os.environ.get("VAPID_PRIVATE_KEY")
    subject = os.environ.get("VAPID_SUBJECT")
    if not private_key or not subject:
        return  # push not configured, silently skip

    import json

    payload = json.dumps({"title": title, "body": body, "url": url})
    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # subscription expired/revoked -- clean it up
                client.table("push_subscriptions").delete().eq("endpoint", sub["endpoint"]).execute()
                log.info(f"Removed expired push subscription {sub['endpoint'][:40]}...")
            else:
                log.warning(f"Push failed: {e}")


def send_email(to_address: str, subject: str, body: str):
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_address or not gmail_app_password or not to_address:
        return  # email not configured, silently skip

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = to_address

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)
    except Exception as e:
        log.warning(f"Email send failed: {e}")


def main():
    client = get_client()

    settings_res = client.table("app_settings").select("*").eq("id", 1).maybe_single().execute()
    settings = settings_res.data or {}
    push_enabled = settings.get("push_enabled", False)
    email_enabled = settings.get("email_enabled", False)
    notify_email = settings.get("notify_email")
    threshold = settings.get("relevance_threshold", 70)

    if not push_enabled and not email_enabled:
        log.info("Notifications disabled in app_settings, nothing to do.")
        return

    subscriptions = []
    if push_enabled:
        subs_res = client.table("push_subscriptions").select("*").execute()
        subscriptions = subs_res.data or []

    dashboard_url = os.environ.get("DASHBOARD_URL", "https://market-intel-chi-wheat.vercel.app")
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. High-relevance portfolio articles not yet notified
    articles_res = (
        client.table("articles")
        .select("id,title,ai_relevance_score,ai_affected_tickers,tickers_raw,ai_sentiment")
        .is_("notified_at", "null")
        .eq("ai_processed", True)
        .gte("ai_relevance_score", threshold)
        .order("published_at", desc=True)
        .limit(50)
        .execute()
    )
    notified_count = 0
    for a in articles_res.data or []:
        tickers = set(a.get("ai_affected_tickers") or a.get("tickers_raw") or [])
        if not (tickers & PORTFOLIO_TICKERS):
            continue

        title = f"{a.get('ai_sentiment', 'update').upper()}: {a['title'][:80]}"
        body = f"Relevance {a['ai_relevance_score']} · {', '.join(tickers & PORTFOLIO_TICKERS)}"
        url = f"{dashboard_url}/news/{a['id']}"

        if push_enabled:
            send_push(subscriptions, title, body, url, client)
        if email_enabled:
            send_email(notify_email, title, f"{body}\n\n{url}")

        client.table("articles").update({"notified_at": now_iso}).eq("id", a["id"]).execute()
        notified_count += 1

    # 2. New digests not yet notified
    digests_res = (
        client.table("digests")
        .select("id,summary,created_at")
        .is_("notified_at", "null")
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    for d in digests_res.data or []:
        title = "New Market Intel digest"
        body = (d.get("summary") or "")[:200]
        url = dashboard_url

        if push_enabled:
            send_push(subscriptions, title, body, url, client)
        if email_enabled:
            send_email(notify_email, title, f"{d.get('summary', '')}\n\n{url}")

        client.table("digests").update({"notified_at": now_iso}).eq("id", d["id"]).execute()
        notified_count += 1

    log.info(f"Sent {notified_count} notification(s).")


if __name__ == "__main__":
    main()
