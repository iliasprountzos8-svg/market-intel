"""Poll SEC EDGAR for new Form 4 (insider buy/sell) filings of every S&P 500 company (+ ASML)
and store the parsed transactions in the local Postgres DB for analysis/form4_signal.py.

Two fetches per filing found: the company's submissions JSON (same endpoint ingest/sec8k.py
polls, listing every recent filing including Form 4 metadata) and, for each Form 4 filing in
the window, its ownership XML document. Same politeness pattern as sec8k.py: SEC-required
identifying User-Agent, 4 workers, small delay, stops on repeated 429/403.

    python sec_form4.py                  # last 2 days of filings for all companies
    python sec_form4.py --only NVDA,MSFT --days 30 --dry-run
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(Path(__file__).parent))
from logger import get_logger  # noqa: E402
from sec_filings import SEC_HEADERS  # noqa: E402
from sec8k import universe  # noqa: E402
import form4_items  # noqa: E402

log = get_logger("ingest.sec_form4")
load_dotenv(ROOT / ".env")

_blocked = {"n": 0}

INSERT_SQL = """
insert into form4_transactions
    (symbol, cik, accession, insider_name, is_officer, is_director, is_ten_pct_owner,
     officer_title, transaction_code, transaction_date, shares, price_per_share,
     shares_owned_after, filed_at)
values (%(symbol)s, %(cik)s, %(accession)s, %(insider_name)s, %(is_officer)s, %(is_director)s,
        %(is_ten_pct_owner)s, %(officer_title)s, %(transaction_code)s, %(transaction_date)s,
        %(shares)s, %(price_per_share)s, %(shares_owned_after)s, %(filed_at)s)
on conflict (accession, insider_name, transaction_code, transaction_date, shares) do nothing
"""


def _get(url):
    if _blocked["n"] >= 5:
        return None, "skipped: too many blocks"
    time.sleep(0.12)
    try:
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        if r.status_code in (403, 429):
            _blocked["n"] += 1
            return None, f"http {r.status_code}"
        r.raise_for_status()
        return r, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:100]


def fetch_one(item, since_iso):
    symbol, name, cik = item
    r, err = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if err:
        return item, [], err
    filings = form4_items.list_form4_filings(r.json(), since_iso)
    accession_nodash = None
    txns = []
    errs = []
    for f in filings:
        accession_nodash = f["accession"].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{f['primary_document']}"
        xr, xerr = _get(url)
        if xerr:
            errs.append(f"{f['accession']}: {xerr}")
            continue
        try:
            txns += form4_items.parse_form4_xml(xr.text, symbol, cik, f["accession"], f["filed_at"])
        except Exception as e:  # noqa: BLE001
            errs.append(f"{f['accession']}: parse error {e}")
    return item, txns, "; ".join(errs) if errs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=2)
    ap.add_argument("--only", default=None, help="comma separated symbols")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    items = universe(a.only)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda it: fetch_one(it, since), items))
    all_txns, errs = [], []
    for item, txns, err in results:
        if err:
            errs.append((item[0], err))
        all_txns += txns
    print(f"sec_form4: polled {len(items)} companies in {time.time() - t0:.0f}s, {len(all_txns)} transactions in window, {len(errs)} errors")
    for sym, e in errs[:5]:
        print(f"  error {sym}: {e}")
    if a.dry_run:
        for t in sorted(all_txns, key=lambda t: t["filed_at"], reverse=True)[:12]:
            print(f"  {t['filed_at'][:10]}  {t['symbol']:6s} {t['insider_name']:<25s} {t['transaction_code']} {t['shares']}@{t['price_per_share']}")
        return 0

    from db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(INSERT_SQL, all_txns)
        conn.commit()
    print(f"sec_form4: inserted (or deduped) {len(all_txns)} transactions into form4_transactions")
    return 0 if len(errs) < len(items) * 0.2 else 1


if __name__ == "__main__":
    sys.exit(main())
