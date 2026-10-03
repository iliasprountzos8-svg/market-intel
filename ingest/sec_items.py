"""Pure parsing of SEC EDGAR 'submissions' JSON into article rows for 8-K / 6-K filings.

The submissions API (data.sec.gov/submissions/CIK##########.json) lists each company's recent filings with the
8-K item numbers already decoded, so we get primary-source events (earnings, executive changes, agreements,
restatements ...) minutes after filing without downloading the documents. No I/O here; see sec8k.py.
"""
import re

FORMS = {"8-K", "8-K/A", "6-K"}
MATERIAL = {"1.01", "1.02", "1.03", "1.05", "2.01", "2.02", "2.03", "2.04", "2.05", "2.06", "3.01", "3.02", "4.01", "4.02",
            "5.01", "5.02", "5.03", "5.07", "7.01", "8.01"}

ITEM_DESCRIPTIONS = {
    "1.01": "Material Agreement", "1.02": "Termination of Material Agreement", "1.03": "Bankruptcy",
    "1.05": "Material Cybersecurity Incident", "2.01": "Completion of Acquisition/Disposition",
    "2.02": "Results of Operations (Earnings)", "2.03": "Creation of Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate an Obligation", "2.05": "Exit or Disposal Costs",
    "2.06": "Material Impairments", "3.01": "Delisting/Listing Rule Notice", "3.02": "Unregistered Sales of Equity",
    "4.01": "Change in Auditor", "4.02": "Non-Reliance on Prior Financials (Restatement)", "5.01": "Change in Control",
    "5.02": "Officer/Director Departure or Appointment", "5.03": "Amendment to Articles/Bylaws",
    "5.07": "Shareholder Vote Results", "5.08": "Shareholder Director Nominations", "7.01": "Regulation FD Disclosure", "8.01": "Other Material Events",
    "9.01": "Financial Statements and Exhibits",
}


def split_items(items):
    return [i for i in re.findall(r"\d\.\d\d", items or "")]


def describe_items(items):
    return "; ".join(dict.fromkeys(ITEM_DESCRIPTIONS.get(i, i) for i in split_items(items)))


def index_url(cik, accession):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def parse_submissions(js, symbol, name, cik, since_iso=None):
    """Turn one company's submissions JSON into article dicts (newest first). since_iso filters by acceptance time."""
    rec = ((js or {}).get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        acc = rec["accessionNumber"][i]
        accepted = (rec.get("acceptanceDateTime") or [None] * len(forms))[i] or f"{rec['filingDate'][i]}T00:00:00.000Z"
        if since_iso and accepted < since_iso:
            continue
        items = (rec.get("items") or [""] * len(forms))[i] or ""
        nums = split_items(items)
        desc = describe_items(items) or "Foreign private issuer report" if form == "6-K" and not nums else describe_items(items)
        if nums and not (set(nums) & MATERIAL):
            continue  # only exhibit-style items: nothing to read
        doc = (rec.get("primaryDocDescription") or [""] * len(forms))[i] or ""
        title = f"{symbol} files {form}: {desc or 'no items listed'}"
        summary = (f"{name} ({symbol}) filed a Form {form} on {rec['filingDate'][i]}. "
                   f"Items: {', '.join(nums) if nums else 'n/a'}. {doc}".strip())
        out.append({
            "url": index_url(cik, acc), "title": title, "summary_raw": summary, "source": "SEC EDGAR 8-K" if form != "6-K" else "SEC EDGAR 6-K",
            "author": None, "published_at": accepted.replace("Z", "+00:00"), "category": "filing", "tickers_raw": [symbol],
        })
    return out
