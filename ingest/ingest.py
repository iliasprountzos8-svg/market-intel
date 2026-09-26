"""Large-scale ingester: polls the `sources` registry (S&P 500 per-company feeds, SEC filings,
outlets, central banks, regulators) with parallel, polite, conditional requests and stores new
articles in the local database.

Politeness: per-host concurrency caps, conditional GET (ETag / If-Modified-Since), tiered poll
intervals (poll_minutes), a circuit breaker per host (repeated 403/429 stops that host for the
run), and auto-disable of dead feeds (revived after 7 days).

Run: python ingest.py [--limit N] [--dry-run] [--max-minutes 12]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))
from logger import get_logger  # noqa: E402
from scrape import is_spam, parse_published  # noqa: E402  (reuse the project's own filters)
from sec_filings import SEC_HEADERS, ITEM_DESCRIPTIONS  # noqa: E402

log = get_logger("ingest")
UA = {"User-Agent": "Mozilla/5.0 (compatible; MarketIntelPersonal/1.0)"}
LABEL = {"yahoo_ticker": "Yahoo Finance", "nasdaq_ticker": "Nasdaq.com", "sa_ticker": "Seeking Alpha", "sec": "SEC EDGAR"}
HOST_LIMIT = {"seekingalpha.com": 3, "www.nasdaq.com": 4, "feeds.finance.yahoo.com": 4, "www.sec.gov": 2}
HOST_DELAY = {"www.sec.gov": 0.35, "seekingalpha.com": 0.25}
BREAKER_LIMIT = 8

SP500 = json.load(open(ROOT / "data" / "sp500.json"))
CIK2SYM = {x["cik"]: x["symbol"] for x in SP500}
CIK2SYM.update({"0000937966": "ASML", "0001046179": "TSM"})
SYMBOLS = {x["symbol"] for x in SP500} | {"ASML", "TSM", "ARM"}
DOLLAR = re.compile(r"\$([A-Z]{1,5})\b")
EXCH = re.compile(r"\((?:NASDAQ|NYSE|NYSEARCA|NYSEAMERICAN|AMEX)[:\s]+([A-Z][A-Z.\-]{0,5})\)")
TAG = re.compile(r"<[^>]+>")

_locks = defaultdict(threading.Lock)
_sems, _blocked, _hits = {}, set(), defaultdict(int)
_glock = threading.Lock()


def host_sem(host):
    with _glock:
        if host not in _sems:
            _sems[host] = threading.Semaphore(HOST_LIMIT.get(host, 6))
        return _sems[host]


def tickers_in(text):
    found = set(DOLLAR.findall(text)) | set(m.replace(".", "-") for m in EXCH.findall(text))
    return sorted(t for t in found if t in SYMBOLS)


def fetch_one(src):
    """Returns (src, rows, status, etag, last_modified, error)."""
    url, host = src["url"], urlparse(src["url"]).netloc
    if host in _blocked:
        return (src, [], "skipped", None, None, "host backoff")
    headers = dict(SEC_HEADERS if host.endswith("sec.gov") else UA)
    if src.get("etag"):
        headers["If-None-Match"] = src["etag"]
    if src.get("last_modified"):
        headers["If-Modified-Since"] = src["last_modified"]
    with host_sem(host):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except Exception as e:  # noqa: BLE001
            return (src, [], 0, None, None, str(e)[:80])
        if host in HOST_DELAY:
            time.sleep(HOST_DELAY[host])
    if resp.status_code in (403, 429):
        with _glock:
            _hits[host] += 1
            if _hits[host] >= BREAKER_LIMIT:
                _blocked.add(host)
        return (src, [], resp.status_code, None, None, f"http {resp.status_code}")
    if resp.status_code == 304:
        return (src, [], 304, src.get("etag"), src.get("last_modified"), None)
    if resp.status_code != 200:
        return (src, [], resp.status_code, None, None, f"http {resp.status_code}")
    feed = feedparser.parse(resp.content)
    rows = []
    for e in feed.entries:
        title, link = (e.get("title") or "").strip(), (e.get("link") or "").strip()
        if not title or not link:
            continue
        summary = TAG.sub(" ", e.get("summary", "") or e.get("description", "") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        if is_spam(title, summary):
            continue
        tick = []
        source = LABEL.get(src["kind"], src["name"])
        if src["kind"] == "sec":
            m = re.match(r"^([\w\-/]+) - (.+?) \((\d{10})\) \((?:Filer|Subject|Reporting)\)", title)
            if not m or m.group(3) not in CIK2SYM:
                continue
            form, company, cik = m.group(1), m.group(2), m.group(3)
            sym = CIK2SYM[cik]
            items = [f"{i} {ITEM_DESCRIPTIONS.get(i, '')}".strip() for i in re.findall(r"Item (\d\.\d\d)", summary)]
            title = f"{sym} ({company}) filed {form}" + (f": {'; '.join(items)}" if items else "")
            tick = [sym]
        elif src.get("ticker"):
            tick = [src["ticker"]]
        else:
            tick = tickers_in(f"{title} {summary}")
        try:
            t = e.get("published_parsed") or e.get("updated_parsed")  # feedparser normalises to UTC
            published = (datetime(*t[:6], tzinfo=timezone.utc).isoformat() if t else parse_published(e))
        except Exception:  # noqa: BLE001
            continue
        rows.append({"source": source, "url": link, "title": title[:500], "summary_raw": (summary[:2000] or None),
                     "author": e.get("author"), "published_at": published, "category": src["category"], "tickers_raw": tick})
    return (src, rows, 200, resp.headers.get("ETag"), resp.headers.get("Last-Modified"), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=12)
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()
    t0 = time.time()
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    now = datetime.now(timezone.utc)

    # revive long-dead feeds once a week
    client.table("sources").update({"enabled": True, "fail_count": 0}).eq("enabled", False).lt("last_fetch", (now - timedelta(days=7)).isoformat()).execute()

    srcs, off = [], 0
    while True:
        r = client.table("sources").select("*").eq("enabled", True).range(off, off + 999).execute().data
        srcs += r
        if len(r) < 1000:
            break
        off += 1000
    due = []
    for s in srcs:
        lf = s.get("last_fetch")
        if not lf or (now - datetime.fromisoformat(lf.replace("Z", "+00:00"))).total_seconds() >= s["poll_minutes"] * 60 * 0.9:
            due.append(s)
    due.sort(key=lambda s: (s.get("last_fetch") or ""))  # stalest first
    if args.limit:
        due = due[:args.limit]
    log.info(f"{len(srcs)} enabled sources, {len(due)} due")

    by_url, results = {}, []
    deadline = t0 + args.max_minutes * 60
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch_one, s) for s in due]
        for f in cf.as_completed(futs):
            if time.time() > deadline:
                log.warning("time budget reached; remaining sources wait for the next run")
                for g in futs:
                    g.cancel()
                break
            src, rows, status, etag, lmod, err = f.result()
            results.append((src, len(rows), status, etag, lmod, err))
            for r in rows:
                cur = by_url.get(r["url"])
                if cur:
                    cur["tickers_raw"] = sorted(set(cur["tickers_raw"]) | set(r["tickers_raw"]))
                else:
                    by_url[r["url"]] = r

    rows = list(by_url.values())
    year = now.year
    rows = [r for r in rows if r["published_at"] and 2000 < int(str(r["published_at"])[:4]) <= year + 1]
    new_count = 0
    if not args.dry_run:
        for i in range(0, len(rows), 200):
            try:
                res = client.table("articles").upsert(rows[i:i + 200], on_conflict="url", ignore_duplicates=True).execute()
                new_count += len(res.data or [])
            except Exception as e:  # noqa: BLE001
                log.error(f"insert batch failed: {str(e)[:160]}")
        upd = []
        for src, n, status, etag, lmod, err in results:
            if status == "skipped":
                continue
            ok = status in (200, 304)
            fc = 0 if ok else (src.get("fail_count") or 0) + 1
            upd.append({"name": src["name"], "url": src["url"], "etag": etag if ok else src.get("etag"), "last_modified": lmod if ok else src.get("last_modified"),
                        "last_status": status if isinstance(status, int) else 0, "last_fetch": now.isoformat(),
                        "last_success": now.isoformat() if ok else src.get("last_success"), "fail_count": fc,
                        "items_total": (src.get("items_total") or 0) + n, "enabled": fc < 12})
        for i in range(0, len(upd), 200):
            client.table("sources").upsert(upd[i:i + 200], on_conflict="url").execute()
        errs = sum(1 for r in results if r[2] not in (200, 304, "skipped"))
        client.table("source_health").insert({"source": "INGEST_ALL", "entries_fetched": new_count, "errors": errs, "skipped_domains": len(_blocked)}).execute()
    codes = defaultdict(int)
    for r in results:
        codes[r[2]] += 1
    log.info(f"fetched {len(results)} feeds in {time.time() - t0:.0f}s | unique items {len(rows)} | NEW {new_count} | status {dict(codes)} | blocked hosts {sorted(_blocked)}")
    print(f"ingest: feeds={len(results)} unique_items={len(rows)} new={new_count} blocked={sorted(_blocked)} status={dict(codes)} secs={time.time() - t0:.0f}")


if __name__ == "__main__":
    main()
