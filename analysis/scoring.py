"""Shared, honest scoring helpers for market-intel calls.

Why this exists: the original check_outcomes measured "price move from the call until
NOW", so the same call scored differently depending on when it was checked, had no fixed
horizon, no benchmark, and entered at a price that could already include the news.

Rules used here:
  * A call is scored over a FIXED horizon (horizon_days, default 5 calendar days).
  * It is only scored once that window has fully elapsed.
  * Entry = the last close BEFORE the call could have been acted on (see entry_close),
    exit = the last close on/before entry_date + horizon.
  * Single stocks are also compared with a benchmark (VT, global equities) so a rising
    tide does not make every bullish call look smart.
"""

import math
from datetime import datetime, timedelta, timezone

import yfinance as yf

DEFAULT_HORIZON_DAYS = 5
DEFAULT_THRESHOLD_PCT = 0.5
BENCHMARK = "VT"  # global equities, USD -- closest liquid proxy for VWCE
STOCKS = {"NVDA", "MSFT", "GOOGL", "ASML"}

# Ordered: more specific keys first (first substring match wins).
TICKER_MAP = [
    ("10y treasury", "^TNX"), ("10-year", "^TNX"), ("treasury yield", "^TNX"), ("us 10y", "^TNX"), ("yield", "^TNX"),
    ("vwce", "VWCE.DE"), ("broad market", "VWCE.DE"), ("global equit", "VWCE.DE"), ("world equit", "VWCE.DE"),
    ("s&p", "^GSPC"), ("sp500", "^GSPC"), ("equities", "^GSPC"),
    ("brent", "BZ=F"), ("wti", "CL=F"), ("crude", "CL=F"), ("oil", "CL=F"),
    ("nvda", "NVDA"), ("nvidia", "NVDA"),
    ("msft", "MSFT"), ("microsoft", "MSFT"),
    ("googl", "GOOGL"), ("google", "GOOGL"), ("alphabet", "GOOGL"),
    ("asml", "ASML"),
    ("gold", "GC=F"),
    ("dollar", "DX-Y.NYB"), ("usd", "DX-Y.NYB"),
]


def resolve_symbol(theme, explicit=None):
    if explicit:
        return explicit.strip()
    t = (theme or "").lower()
    for key, symbol in TICKER_MAP:
        if key in t:
            return symbol
    return None


def _close_hour_utc(symbol):
    # Approximate cash-market close in UTC. Used only to decide whether a call made at a
    # given hour could already have known that day's closing move.
    return 16 if symbol.endswith(".DE") else 21


def _history(symbol, start, end):
    h = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    if h is None or h.empty:
        return None
    s = h["Close"].dropna()
    s.index = s.index.tz_localize(None) if getattr(s.index, "tz", None) is not None else s.index
    return s


def entry_date(symbol, created):
    """Trading-day whose close is used as the entry price."""
    d = created.astimezone(timezone.utc)
    same_day_ok = d.hour >= _close_hour_utc(symbol)
    day = d.date() if same_day_ok else d.date() - timedelta(days=1)
    return datetime(day.year, day.month, day.day)


def window_return(symbol, created, horizon_days=DEFAULT_HORIZON_DAYS, entry_price=None):
    """Return dict(entry_price, exit_price, return_pct) or None if window not elapsed / no data."""
    now = datetime.now(timezone.utc)
    start_day = entry_date(symbol, created)
    end_day = start_day + timedelta(days=horizon_days)
    if end_day.date() >= now.date():
        return None  # window not fully elapsed yet
    s = _history(symbol, start_day - timedelta(days=6), end_day + timedelta(days=4))
    if s is None:
        return None
    before = s[s.index <= start_day]
    upto = s[s.index <= end_day]
    if before.empty or upto.empty:
        return None
    p0 = float(entry_price) if entry_price else float(before.iloc[-1])
    p1 = float(upto.iloc[-1])
    if p0 == 0:
        return None
    return {"entry_price": p0, "exit_price": p1, "return_pct": (p1 - p0) / p0 * 100.0,
            "exit_date": upto.index[-1].date().isoformat()}


def latest_close(symbol):
    s = _history(symbol, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc) + timedelta(days=1))
    return float(s.iloc[-1]) if s is not None and len(s) else None


def verdict(call, ret_pct, threshold=DEFAULT_THRESHOLD_PCT):
    if call == "neutral":
        return "unclear"
    if abs(ret_pct) < threshold:
        return "unclear"
    if (ret_pct > 0 and call == "bullish") or (ret_pct < 0 and call == "bearish"):
        return "correct"
    return "incorrect"


def wilson(hits, n, z=1.96):
    """95% Wilson confidence interval for a hit rate."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)
