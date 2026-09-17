"""Shared ticker-extraction logic, used by both scrape.py (title/RSS-summary
pass) and fetch_fulltext.py (re-run once the full article body is available).

News prose almost never spells out a bare ticker symbol -- it says "Nvidia"
or "Google's search business", not "NVDA" or "GOOGL". A naive all-caps-word
regex misses essentially every mention of our own portfolio names, which is
exactly the gap that showed up empty-handed during a manual digest pass on
2026-09-17: articles clearly about Nvidia (Huang's 2027 guidance, Burry's
NVDA puts) and Google (an Evercore note on search) came back with zero
matching tickers_raw. COMPANY_ALIASES closes that gap.
"""

import re

TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")

# Common false positives to filter out of naive ticker extraction
STOPWORDS = {
    "THE", "AND", "FOR", "ARE", "NEW", "CEO", "CFO", "USA", "USD", "EUR",
    "GDP", "CPI", "FED", "ECB", "IPO", "ETF", "SEC", "AI", "US", "UK",
    "Q1", "Q2", "Q3", "Q4", "YOY", "NYSE", "NASDAQ",
    # seen in the wild polluting real results (ad copy, generic caps words)
    "GO", "HOURS", "LAST", "ONLY", "TO", "YOUR", "CNBC", "WSJ", "PM", "TV",
    "UN", "EU", "GO", "IPO", "IV", "RED", "RBC",
}

# Company-name -> ticker aliases. Case-insensitive whole-word match against
# the raw prose. Covers the portfolio sleeve (NVDA/MSFT/GOOGL/ASML) plus a
# handful of names that show up constantly in the same news flow and are
# useful context even when not held directly.
COMPANY_ALIASES = {
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "asml": "ASML",
    "apple": "AAPL",
    "amazon": "AMZN",
    "meta": "META",
    "tesla": "TSLA",
    "openai": "OPENAI",
    "taiwan semiconductor": "TSM",
    "tsmc": "TSM",
    "amd": "AMD",
    "intel": "INTC",
    "palantir": "PLTR",
}
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in COMPANY_ALIASES) + r")\b",
    re.IGNORECASE,
)


def extract_tickers(text: str) -> list[str]:
    if not text:
        return []
    candidates = set(TICKER_RE.findall(text)) - STOPWORDS
    for match in _ALIAS_RE.findall(text):
        candidates.add(COMPANY_ALIASES[match.lower()])
    return sorted(candidates)
