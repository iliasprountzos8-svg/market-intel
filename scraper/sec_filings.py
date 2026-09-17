"""SEC EDGAR filings for the watchlist companies -- official, zero-noise,
directly tied to the actual portfolio. NVDA/MSFT/GOOGL file 8-K (US domestic
issuer "material event" reports); ASML is a foreign private issuer and files
6-K instead. No API key needed, but the SEC requires a descriptive
User-Agent identifying the requester (not optional -- undeclared requests
get blocked).
"""

import re

import requests
import feedparser
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

log = get_logger("scraper.sec_filings")

SEC_HEADERS = {
    # SEC fair-access policy requires a real identifying UA; update the email
    # if this project changes hands.
    "User-Agent": "MarketIntel personal research project (contact: iliasprountzos8@gmail.com)"
}

# (name, CIK, form type) for the actual watchlist tickers
SEC_WATCHLIST = [
    ("NVIDIA (NVDA)", "0001045810", "8-K"),
    ("Microsoft (MSFT)", "0000789019", "8-K"),
    ("Alphabet (GOOGL)", "0001652044", "8-K"),
    ("ASML (foreign issuer)", "0000937966", "6-K"),
]

# Plain-English decode for 8-K item numbers (the ones that actually show up
# in practice) -- the raw SEC feed just gives "item 5.02", which means
# nothing without this. https://www.sec.gov/about/forms/form8-k.pdf
ITEM_DESCRIPTIONS = {
    "1.01": "Material Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy",
    "2.01": "Completion of Acquisition/Disposition",
    "2.02": "Results of Operations (Earnings)",
    "2.03": "Creation of Direct Financial Obligation",
    "2.05": "Costs Associated with Exit/Disposal",
    "2.06": "Material Impairments",
    "3.01": "Delisting/Failure to Satisfy Listing Rule",
    "3.02": "Unregistered Sales of Securities",
    "4.01": "Change in Auditor",
    "5.01": "Change in Control",
    "5.02": "Officer/Director Departure or Appointment",
    "5.03": "Amendment to Articles/Bylaws",
    "5.07": "Shareholder Vote Results",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Material Events",
    "9.01": "Financial Statements/Exhibits",
}


def decode_items(item_desc: str) -> str:
    """'items 7.01 and 9.01' -> 'Regulation FD Disclosure, Financial Statements/Exhibits'"""
    if not item_desc:
        return ""
    found = re.findall(r"\d\.\d\d", item_desc)
    decoded = [ITEM_DESCRIPTIONS.get(n, n) for n in found]
    return ", ".join(dict.fromkeys(decoded)) if decoded else item_desc

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException)
)
def robust_get(url, **kwargs):
    kwargs.setdefault("timeout", 15)
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp

def scrape_sec_filings():
    rows = []
    health_metrics = []
    for name, cik, form_type in SEC_WATCHLIST:
        errors = 0
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type={form_type}&dateb=&owner=include&count=10&output=atom"
        )
        try:
            resp = robust_get(url, headers=SEC_HEADERS)
            feed = feedparser.parse(resp.content)
        except Exception as e:
            log.warning(f"SEC EDGAR fetch failed for {name}: {e}")
            errors += 1
            health_metrics.append({"source": f"SEC EDGAR {name}", "entries_fetched": 0, "errors": errors})
            continue

        entries_added = 0
        for entry in feed.entries:
            item_desc = entry.get("items-desc", "").strip()
            decoded = decode_items(item_desc)
            form_name = entry.get("form-name", form_type).strip()
            title = f"{name}: {form_name}" + (f" -- {decoded}" if decoded else "")
            link = entry.get("link", "")
            filing_date = entry.get("updated", "") or entry.get("filing-date", "")
            if not link:
                continue

            rows.append({
                "source": "SEC EDGAR",
                "url": link,
                "title": title,
                "summary_raw": item_desc or form_name,
                "author": None,
                "published_at": filing_date,
                "category": "sec-filing",
                "tickers_raw": [name.split("(")[-1].rstrip(")")] if "(" in name else [],
            })
            entries_added += 1
        log.info(f"SEC EDGAR {name}: {entries_added} filings")
        health_metrics.append({"source": f"SEC EDGAR {name}", "entries_fetched": entries_added, "errors": errors})
    return rows, health_metrics
