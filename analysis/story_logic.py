"""Pure logic for reading the news better: freshness gate, story clustering, event tags v2 and priority.

No I/O and no third-party imports, so it can be unit tested anywhere (see tests/test_story_logic.py).
The database glue lives in stories.py.
"""
import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BACKFILL_HOURS = 24
CLUSTER_WINDOW_H = 72
JACCARD_MIN = 0.6
MIN_OVERLAP = 3

PER_TICKER_SOURCES = {"yahoo finance", "seeking alpha", "nasdaq.com"}
PRIMARY_HINTS = ("sec edgar", "sec 8-k", "sec ", "federal reserve", "ecb", "european central bank", "bank of england",
                 "bank of greece", "treasury", "bls", "ftc", "doj", "cftc", "esma")

STOP = set("""the and for with from that this what why how here more than over into your their about after before
stock stocks share shares market markets company companies inc corp says said will could would should just
new now today update news report reports reported amid out are was were has have had its it's not but can
may one two all get gets top best buy sell hold rating price target""".split())

KNOWN_SOURCES = ("reuters", "yahoo finance", "seeking alpha", "marketwatch", "investing.com", "bloomberg", "cnbc", "barron's",
                 "barrons", "the motley fool", "motley fool", "benzinga", "nasdaq", "zacks", "tipranks", "business insider",
                 "the wall street journal", "wsj", "financial times", "ft.com", "forbes", "investopedia", "thestreet", "fool.com")
_TICKER_TAG = re.compile(r"\(\s*(?:NASDAQ|NYSE|NYSEARCA|NYSEAMERICAN|AMEX|OTC|LON|EPA|ETR)?\s*:?\s*[A-Z]{1,5}(?:[.-][A-Z])?\s*\)")
_LEAD = re.compile(r"^\s*(?:breaking|update\s*\d*|exclusive|watch|video|analysis|opinion)\s*[:\-]+\s*", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")
_TAIL = re.compile(r"^(.*?)\s+[-–—|]\s+([^-–—|]{2,32})$")
_TITLECASE_TAIL = re.compile(r"^[A-Z][A-Za-z0-9.&']*(?: [A-Z][A-Za-z0-9.&']*){0,2}$")


def strip_source_tail(title):
    """'Nvidia jumps - Reuters' -> 'Nvidia jumps'. Only strips a short tail that looks like a publisher."""
    m = _TAIL.match(title.strip())
    if not m:
        return title.strip()
    head, tail = m.group(1), m.group(2).strip()
    if tail.lower() in KNOWN_SOURCES or any(tail.lower().startswith(k) for k in KNOWN_SOURCES):
        return head
    return head if _TITLECASE_TAIL.match(tail) and len(tail.split()) <= 2 and tail.lower() not in ("here", "why") else title.strip()


def normalize_title(title):
    t = strip_source_tail(title or "")
    t = _LEAD.sub("", t)
    t = _TICKER_TAG.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def title_tokens(title):
    toks = [w for w in _TOKEN.findall(normalize_title(title)) if w not in STOP and (len(w) >= 3 or (len(w) >= 2 and any(c.isdigit() for c in w)))]
    return frozenset(toks)


def title_hash(title):
    toks = sorted(title_tokens(title))
    return hashlib.md5(" ".join(toks).encode()).hexdigest() if toks else None


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_backfill(published_at, scraped_at, hours=BACKFILL_HOURS):
    """True when we saw the item long after it was published: it is old news to us, not a fresh event."""
    if published_at is None or scraped_at is None:
        return False
    return (scraped_at - published_at) > timedelta(hours=hours)


AMBIG_NAMES = {"Target", "Ford", "Gap", "Match", "Ball", "Hess", "Amgen", "Dow", "Visa", "Southern", "General", "United", "American",
               "First", "National", "Lowe"}
_NAME_SUFFIX = re.compile(r"[,\.]?\s+(Inc\.?|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|plc|Holdings?|Group|Incorporated|Class [ABC]|& Co\.?|\(The\)|The)\s*$", re.I)


def load_names(sp500_json_path):
    """S&P 500 company names (legal suffixes stripped) -> symbol, for headline entity matching. Skips ambiguous or very short names."""
    import json
    names = {}
    with open(sp500_json_path, encoding="utf-8") as f:
        sp500 = json.load(f)
    for x in sp500:
        name = x["name"]
        for _ in range(3):
            name = _NAME_SUFFIX.sub("", name).strip()
        if name in AMBIG_NAMES or len(name) < 4 or (" " not in name and len(name) < 5):
            continue
        names[name] = x["symbol"]
    return names


class EntityMatcher:
    """Which companies does a headline name? Exact-case company names (from names -> symbol) plus '(TICKER)' tags.
    ignore_case=True also matches 'Nvidia' for 'NVIDIA' (used for merging, where missing a company is the riskier error)."""

    _PAREN = re.compile(r"\(\s*(?:[A-Za-z]+:)?([A-Z]{1,5}(?:[.-][A-Z])?)\s*\)")

    def __init__(self, names=None, ignore_case=False):
        self.ignore_case = ignore_case
        self.names = {(k.lower() if ignore_case else k): v for k, v in (names or {}).items()}
        flags = re.I if ignore_case else 0
        self.rx = (re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in sorted(self.names, key=len, reverse=True)) + r")(?![A-Za-z0-9])", flags)
                   if self.names else None)

    def __call__(self, title):
        found = {m.group(1) for m in self._PAREN.finditer(title or "")}
        if self.rx:
            found |= {self.names[m.group(1).lower() if self.ignore_case else m.group(1)] for m in self.rx.finditer(title or "")}
        return found


class Story:
    __slots__ = ("id", "title", "tokens", "hash", "first_pub", "first_seen", "last_pub", "last_seen", "tickers",
                 "sources", "n_articles", "first_source", "first_article_id", "dirty", "new", "ents")

    def __init__(self, sid, art, ents=None):
        self.ents = set(ents or ())
        self.id = sid
        self.title = art["title"]
        self.tokens = title_tokens(art["title"])
        self.hash = title_hash(art["title"])
        self.first_pub = self.last_pub = art["published_at"]
        self.first_seen = self.last_seen = art["scraped_at"]
        self.tickers = set(art.get("tickers") or [])
        self.sources = {art["source"]}
        self.n_articles = 1
        self.first_source = art["source"]
        self.first_article_id = art["id"]
        self.dirty = True
        self.new = True

    def add(self, art, ents=None):
        self.ents |= set(ents or ())
        self.n_articles += 1
        self.sources.add(art["source"])
        self.tickers |= set(art.get("tickers") or [])
        if art["published_at"] < self.first_pub:
            self.first_pub = art["published_at"]
            self.first_source = art["source"]
            self.first_article_id = art["id"]
        self.last_pub = max(self.last_pub, art["published_at"])
        self.last_seen = max(self.last_seen, art["scraped_at"])
        self.dirty = True


def build_df(titles):
    """Document frequency of title tokens over a corpus of headlines: (Counter, n_docs). Template words score high."""
    df, n = Counter(), 0
    for t in titles:
        toks = title_tokens(t)
        if toks:
            n += 1
            df.update(toks)
    return df, n


class StoryIndex:
    """Incremental clustering of articles into stories.

    Match rule: identical title hash within CLUSTER_WINDOW_H hours, or rarity-weighted token Jaccard >= JACCARD_MIN with
    at least MIN_OVERLAP shared tokens, within the window, and compatible feed tickers (disjoint ticker sets never merge). Candidates come from an inverted index,
    so the cost per article is small even with tens of thousands of stories.
    """

    def __init__(self, next_id=1, df=None, n_docs=0, entity_fn=None):
        self.df, self.n_docs = df, n_docs
        self.entity_fn = entity_fn
        self.stories = {}
        self.by_hash = defaultdict(list)
        self.inv = defaultdict(set)
        self.next_id = next_id

    def add_existing(self, story):
        self.stories[story.id] = story
        if story.hash:
            self.by_hash[story.hash].append(story.id)
        for t in story.tokens:
            self.inv[t].add(story.id)
        self.next_id = max(self.next_id, story.id + 1)

    def _w(self, t):
        """Rarity weight: template words appear in many stories (weight near 1); names and numbers are rare (much higher)."""
        if self.df is not None and self.n_docs:
            return 1.0 + math.log((self.n_docs + 1) / (self.df.get(t, 0) + 1))
        return 1.0 + math.log((len(self.stories) + 1) / (len(self.inv.get(t, ())) + 1))

    def wjaccard(self, a, b):
        if not a or not b:
            return 0.0
        inter = sum(self._w(t) for t in a & b)
        union = sum(self._w(t) for t in a | b)
        return inter / union if union else 0.0

    def _index(self, s):
        if s.hash:
            self.by_hash[s.hash].append(s.id)
        for t in s.tokens:
            self.inv[t].add(s.id)

    def assign(self, art):
        """Returns (story_id, is_new). art needs id, title, published_at, scraped_at, source, tickers."""
        toks = title_tokens(art["title"])
        h = title_hash(art["title"])
        pub = art["published_at"]
        ents = self.entity_fn(art["title"]) if self.entity_fn else set()
        if h:
            for sid in reversed(self.by_hash.get(h, [])):
                s = self.stories[sid]
                if abs((pub - s.first_pub).total_seconds()) <= CLUSTER_WINDOW_H * 3600:
                    s.add(art, ents)
                    return sid, False
        best, best_j = None, 0.0
        atk = set(art.get("tickers") or [])
        if toks:
            counts = Counter(sid for t in toks for sid in self.inv.get(t, ()))
            for sid, shared in counts.items():
                if shared < MIN_OVERLAP:
                    continue
                s = self.stories[sid]
                if abs((pub - s.first_pub).total_seconds()) > CLUSTER_WINDOW_H * 3600:
                    continue
                if atk and s.tickers and not (atk & s.tickers):
                    continue  # feeds for different companies: same template, different story
                if ents and s.ents and not (ents & s.ents):
                    continue  # headlines name different companies: same template, different story
                j = self.wjaccard(toks, s.tokens)
                if j > best_j:
                    best, best_j = sid, j
        if best is not None and best_j >= JACCARD_MIN:
            self.stories[best].add(art, ents)
            return best, False
        s = Story(self.next_id, art, ents)
        self.next_id += 1
        self.stories[s.id] = s
        self._index(s)
        return s.id, True


# ---------------------------------------------------------------- events v2
# Order matters: first match wins, most specific first.
from event_rules import EVENT_RULES  # noqa: E402  (title-only rules; see event_rules.py)

# The guard stops a rule firing mid-word ("sue" in "issue", "opec" in "alopecia"); stems still match at a word start.
_EVENT_RX = [(n, re.compile(r"(?<![A-Za-z])(?:" + p + ")", re.I)) for n, p in EVENT_RULES]

SEC_ITEM_EVENTS = {
    "1.01": "agreement", "1.02": "agreement", "1.03": "bankruptcy", "1.05": "cyber", "2.01": "m&a", "2.02": "earnings",
    "2.03": "capital", "2.04": "credit", "2.05": "layoffs", "2.06": "impairment", "3.01": "listing", "3.02": "capital",
    "4.01": "auditor", "4.02": "restatement", "5.01": "control", "5.02": "leadership", "5.03": "governance",
    "5.07": "governance", "7.01": "disclosure", "8.01": "other_filing", "9.01": "exhibits",
}
SEC_ITEM_WEIGHT = {"restatement": 30, "bankruptcy": 30, "cyber": 25, "earnings": 25, "leadership": 22, "m&a": 25, "agreement": 15,
                   "impairment": 22, "auditor": 18, "control": 25, "layoffs": 18, "capital": 12, "credit": 15, "listing": 20,
                   "governance": 6, "disclosure": 5, "other_filing": 6, "exhibits": 0}


def event_v2(title, summary=None, sec_items=None):
    """Returns an event tag, never None. SEC item numbers (e.g. ['2.02','9.01']) take priority."""
    if sec_items:
        tags = [SEC_ITEM_EVENTS[i] for i in sec_items if i in SEC_ITEM_EVENTS and SEC_ITEM_EVENTS[i] != "exhibits"]
        if tags:
            return max(tags, key=lambda t: SEC_ITEM_WEIGHT.get(t, 0))
    # Title only: RSS summaries are boilerplate-heavy and caused clickbait ("Should you buy X?") to be tagged as earnings.
    text = title or ""
    for name, rx in _EVENT_RX:
        if rx.search(text):
            return name
    return "other"


# ---------------------------------------------------------------- priority
EVENT_WEIGHT = {"restatement": 30, "bankruptcy": 30, "cyber": 22, "leadership": 20, "m&a": 22, "guidance": 24, "earnings": 24,
                "capital": 10, "layoffs": 14, "legal_reg": 16, "analyst": 6, "supply": 12, "product": 8, "macro": 10,
                "index_change": 10, "opinion": 0, "insider": 8, "geopolitics": 8, "agreement": 15, "impairment": 22, "auditor": 18, "control": 24, "credit": 15,
                "listing": 20, "governance": 6, "disclosure": 5, "other_filing": 6, "other": 0}


def independent_sources(sources):
    """Distinct publishers, ignoring the per-ticker distributor feeds that just re-serve other people's articles."""
    originals = {x for x in sources if (x or "").lower() not in PER_TICKER_SOURCES}
    return max(1, len(originals))


def source_class(source):
    s = (source or "").lower()
    if s in PER_TICKER_SOURCES:
        return "ticker_feed"
    if any(h in s for h in PRIMARY_HINTS):
        return "primary"
    return "outlet"


def priority(held, event, source_cls, n_sources, backfill, age_hours, in_sp500=True, n_tickers=1):
    """0..100. How much this story deserves scarce resources (full-text fetch, Claude reading, alerts)."""
    p = 0.0
    if held:
        p += 40
    elif in_sp500:
        p += 8
    p += EVENT_WEIGHT.get(event or "other", 0)
    p += {"primary": 15, "outlet": 8, "ticker_feed": 0}.get(source_cls, 0)
    p += min(max(n_sources - 1, 0), 5) * 3
    p += 10 * math.exp(-max(age_hours, 0) / 24.0)
    if n_tickers >= 6:  # a market-wide roundup, not about one company
        p *= 0.6
    if event == "opinion":
        p *= 0.4
    if backfill:
        p *= 0.1
    return round(min(p, 100.0), 1)


def parse_ts(s):
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
