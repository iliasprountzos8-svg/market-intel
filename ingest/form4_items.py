"""Pure parsing for SEC Form 4 (insider ownership) filings: which filings to fetch from a
company's submissions JSON, and the buy/sell transactions inside each filing's XML.

No I/O here; see sec_form4.py. Mirrors ingest/sec_items.py's split between parsing and fetching.

V1 scope: nonDerivativeTable only (open-market common-stock buys/sells, transaction codes P/S
and any other non-derivative code) -- the derivativeTable (options, RSUs settling into
derivatives) is a later extension, not parsed here.
"""
import xml.etree.ElementTree as ET

FORMS = {"4", "4/A"}


def list_form4_filings(submissions_json, since_iso=None):
    """Extract (accession, primary_document, filed_at) for Form 4 filings from one company's
    submissions JSON (same payload ingest/sec8k.py already fetches). since_iso filters by
    acceptance time. primary_document is returned as a bare filename: EDGAR's own
    `primaryDocument` field sometimes carries a viewer-path prefix (e.g.
    "xslF345X06/wk-form4_....xml") for the human-rendered HTML view; the raw XML lives at the
    same accession root under just the basename, which is what callers should fetch."""
    rec = ((submissions_json or {}).get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        accepted = (rec.get("acceptanceDateTime") or [None] * len(forms))[i] or f"{rec['filingDate'][i]}T00:00:00.000Z"
        if since_iso and accepted < since_iso:
            continue
        doc = (rec.get("primaryDocument") or [""] * len(forms))[i] or ""
        out.append({
            "accession": rec["accessionNumber"][i],
            "primary_document": doc.rsplit("/", 1)[-1],
            "filed_at": accepted.replace("Z", "+00:00"),
        })
    return out


def _text(el, path, default=None):
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else default


def _num(el, path):
    v = _text(el, path)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse_form4_xml(xml_text, symbol, cik, accession, filed_at):
    """One ownershipDocument XML -> a list of non-derivative transaction dicts. Returns [] for
    filings with no non-derivative transactions (pure derivative/option filings, amendments
    with only footnote changes, etc.) rather than raising."""
    root = ET.fromstring(xml_text)

    rel = root.find("reportingOwner/reportingOwnerRelationship")
    insider_name = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    is_officer = _text(rel, "isOfficer") == "1" if rel is not None else False
    is_director = _text(rel, "isDirector") == "1" if rel is not None else False
    is_ten_pct_owner = _text(rel, "isTenPercentOwner") == "1" if rel is not None else False
    officer_title = _text(rel, "officerTitle") if rel is not None else None
    issuer_symbol = _text(root, "issuer/issuerTradingSymbol") or symbol

    out = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "transactionCoding/transactionCode")
        if not code:
            continue
        out.append({
            "symbol": issuer_symbol,
            "cik": cik,
            "accession": accession,
            "insider_name": insider_name,
            "is_officer": is_officer,
            "is_director": is_director,
            "is_ten_pct_owner": is_ten_pct_owner,
            "officer_title": officer_title,
            "transaction_code": code,
            "transaction_date": _text(txn, "transactionDate/value"),
            "shares": _num(txn, "transactionAmounts/transactionShares/value"),
            "price_per_share": _num(txn, "transactionAmounts/transactionPricePerShare/value"),
            "shares_owned_after": _num(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
            "filed_at": filed_at,
        })
    return out
