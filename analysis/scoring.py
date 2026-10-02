"""Shared, honest scoring helpers for market-intel calls.

Why this exists: the original check_outcomes measured "price move from the call until
NOW", so the same call scored differently depending on when it was checked, had no fixed
horizon, no benchmark, and entered at a price that could already include the news.

Rules used here:
  * A call is scored over a FIXED horizon (horizon_days, default 5 calendar days).
  * It is only scored once that window has fully elapsed.
  * Entry = the last close BEFORE the call could have been acted on (see entry_close),
    exit = the last close on/before entry_date + horizon (entry_date = a real trading day).
  * Single stocks are also compared with a benchmark (VT, global equities) so a rising
    tide does not make every bullish call look smart.
"""

import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger  # noqa: E402

log = get_logger("analysis.scoring")

DEFAULT_HORIZON_DAYS = 5
DEFAULT_THRESHOLD_PCT = 0.5
BENCHMARK = "VT"  # global equities, USD -- closest liquid proxy for VWCE
STOCKS = {"NVDA", "MSFT", "GOOGL", "ASML"}
YIELD_SYMBOLS = {"^TNX"}
YIELD_THRESHOLD_BP = 5
SCORING_VERSION = 2

# Ordered: more specific keys first (first substring match wins).
TICKER_MAP = [
    ("10y treasury", "^TNX"), ("10-year", "^TNX"), ("treasury yield", "^TNX"), ("us 10y", "^TNX"), ("yield", "^TNX"),
    ("vwce", "VWCE.DE"), ("broad market", "VWCE.DE"), ("global equit", "VWCE.DE"), ("world equit", "VWCE.DE"),
    ("s&p", "^GSPC"), ("sp500", "^GSPC"), ("equities", "^GSPC"),
    ("brent", "BZ=F"), ("wti", "CL=F"), ("crude", "CL=F"), ("oil", "CL=F"),
    ("nvda", "NVDA"), ("nvidia", "NVDA"),
    ("msft", "MSFT"), ("microsoft", "MSFT"),
    ("googl", "GOOGL"), ("google", "GOOGL"), ("alphabet", "GOOGL"),
    ("asml", "ASML"), ("lng", "LNG"),
    ("gold", "GC=F"),
    ("dollar", "DX-Y.NYB"), ("usd", "DX-Y.NYB"),
]


def resolve_symbol(theme, explicit=None):
    if explicit:
        return explicit.strip()
    raw = (theme or "").strip()
    if raw.startswith("^"):
        return raw.upper()
    t = raw.lower()
    for key, symbol in TICKER_MAP:
        if key in t:
            return symbol
    return None


def _close_hour_utc(symbol):
    # Approximate cash-market close in UTC. Used only to decide whether a call made at a
    # given hour could already have known that day's closing move.
    return 16 if symbol.endswith(".DE") else 21


_SYMBOL_CACHE = {}  # symbol -> (cached_start, cached_end, price Series)


def _cached_history(symbol, start, end, attempts=3, backoff=2):
    """Like _history, but reuses a per-symbol cache within this process so a run that
    checks many calls for the same symbol (or the same benchmark, over and over) makes
    ONE yfinance request per symbol instead of one per call. This is the actual fix for
    Yahoo's burst rate-limit: check_outcomes used to fire a separate request per logged
    call, which could be dozens in quick succession and trip a block that made every
    single one fail together -- retries alone don't help once you're inside that block."""
    cached = _SYMBOL_CACHE.get(symbol)
    if cached and cached[0] <= start and cached[1] >= end:
        return cached[2]
    fetch_start = min(start, cached[0]) if cached else start
    fetch_end = max(end, cached[1]) if cached else end
    s = _history(symbol, fetch_start, fetch_end, attempts=attempts, backoff=backoff)
    if s is None:
        return cached[2] if cached else None
    _SYMBOL_CACHE[symbol] = (fetch_start, fetch_end, s)
    return s


def _history(symbol, start, end, attempts=3, backoff=2):
    """Fetch price history with retry + exponential backoff. Returns None (and logs loudly)
    only after all attempts are exhausted or the market genuinely has no data for the window."""
    last_err = None
    for attempt in range(attempts):
        try:
            h = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < attempts - 1:
                wait = backoff * (2 ** attempt)
                log.warning(f"scoring._history: yfinance error for {symbol} (attempt {attempt + 1}/{attempts}): {e}; retrying in {wait}s")
                time.sleep(wait)
                continue
            log.warning(f"scoring._history: yfinance FAILED for {symbol} after {attempts} attempts: {e}")
            return None
        if h is None or h.empty:
            if attempt < attempts - 1:
                wait = backoff * (2 ** attempt)
                time.sleep(wait)
                continue
            log.warning(f"scoring._history: no data returned for {symbol} ({start.date()} to {end.date()}) after {attempts} attempts")
            return None
        s = h["Close"].dropna()
        s.index = s.index.tz_localize(None) if getattr(s.index, "tz", None) is not None else s.index
        return s
    return None


def window_return(symbol, created, horizon_days=DEFAULT_HORIZON_DAYS, entry_price=None):
    """Return dict(entry_date, entry_price, exit_price, return_pct, ...) or None if the window
    has not fully elapsed / no data.

    The window is anchored on a real trading day: entry = last close before the call could be
    acted on (the call day's own close only if it was made after the close on a weekday), exit =
    last close on/before entry + horizon calendar days. Every call made in the same market state
    therefore shares one window, weekends included. `entry_price` is accepted for backwards
    compatibility but ignored: the logged value is a live quote, not a close."""
    now = datetime.now(timezone.utc)
    d = created.astimezone(timezone.utc)
    cutoff = datetime(d.year, d.month, d.day) - timedelta(days=0 if d.hour >= _close_hour_utc(symbol) else 1)
    s = _cached_history(symbol, cutoff - timedelta(days=10), cutoff + timedelta(days=horizon_days + 5))
    if s is None:
        log.warning(f"scoring.window_return: window elapsed for {symbol} but no price data available -- outcome cannot be checked this cycle")
        return None
    before = s[s.index <= cutoff]
    if before.empty:
        log.warning(f"scoring.window_return: {symbol} has no close on/before {cutoff.date()} in the fetched window")
        return None
    e_day = before.index[-1]
    end_day = e_day + timedelta(days=horizon_days)
    if end_day.date() >= now.date():
        return None  # window not fully elapsed yet
    upto = s[s.index <= end_day]
    p0, p1 = float(before.iloc[-1]), float(upto.iloc[-1])
    if p0 == 0:
        return None
    return {"entry_date": e_day.date().isoformat(), "entry_price": p0, "exit_price": p1,
            "return_pct": (p1 - p0) / p0 * 100.0, "change_bp": (p1 - p0) * 100.0,
            "exit_date": upto.index[-1].date().isoformat()}


def latest_close(symbol):
    s = _cached_history(symbol, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc) + timedelta(days=1))
    return float(s.iloc[-1]) if s is not None and len(s) else None


def verdict(call, ret_pct, threshold=DEFAULT_THRESHOLD_PCT):
    if call == "neutral":
        return "unclear"
    if abs(ret_pct) < threshold:
        return "unclear"
    if (ret_pct > 0 and call == "bullish") or (ret_pct < 0 and call == "bearish"):
        return "correct"
    return "incorrect"


def judge(call, symbol, w, bench=None, threshold=DEFAULT_THRESHOLD_PCT):
    """Fair verdict for one window. Yields are judged by their change in bp (a % move of a ~5%
    level is noise), single stocks by excess return vs the benchmark (a rising tide is not skill),
    everything else by absolute return. Returns (verdict, basis)."""
    if symbol in YIELD_SYMBOLS:
        return verdict(call, w["change_bp"], YIELD_THRESHOLD_BP), "bp"
    if symbol in STOCKS and bench is not None:
        return verdict(call, w["return_pct"] - bench["return_pct"], threshold), "excess"
    return verdict(call, w["return_pct"], threshold), "abs"


def view_key(symbol, call, w):
    """Calls with the same key are the same view re-logged, not independent evidence."""
    return (symbol, call, w["entry_date"], w["exit_date"])


def distinct_views(rows):
    """Collapse stored rows that are the same view re-logged: same symbol, direction and scored
    window (identical entry and exit price). Unscored rows are kept as they are."""
    seen, out = set(), []
    for r in rows:
        sym = r.get("symbol") or r.get("ticker_or_theme")
        if r.get("outcome") and r.get("entry_price") is not None and r.get("exit_price") is not None:
            key = (sym, r.get("call"), round(float(r["entry_price"]), 4), round(float(r["exit_price"]), 4))
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


def wilson(hits, n, z=1.96):
    """95% Wilson confidence interval for a hit rate."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)
