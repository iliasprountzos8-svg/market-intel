"""Rule-based transaction categorization: keyword match on the normalized merchant string.
No LLM call -- merchant text is short and repetitive, a keyword table covers most of it for
free, and anything unmatched stays visible as `uncategorized` rather than being guessed at.
"""
import re

# Ordered: first matching category wins, so put more specific patterns before generic ones.
RULES = [
    ("groceries", r"\b(ab |ab basilopoulos|sklavenitis|lidl|mymarket|market in|kritikos|masoutis)\b"),
    ("dining", r"\b(wolt|efood|box|foody|mcdonald|starbucks|coffee|barista|cafe|restaurant|taverna|bistro|mangiare)\b"),
    ("transport", r"\b(oasa|oseth|ktel|aegean|ryanair|olympic|taxi|uber|beat|lime|fuel|shell|eko|avin|bp )\b"),
    ("subscriptions", r"\b(netflix|spotify|youtube|prime video|disney|apple\.com/bill|icloud|google (one|storage))\b"),
    ("utilities", r"\b(deh|dei|adei|eydap|cosmote|vodafone|nova|wind|nrg|elpedison|protergia)\b"),
    ("rent_housing", r"\b(rent|enoikio|misthoma|aristokatoikein|katoikein)\b"),
    ("health_fitness", r"\b(pharmacy|farmakeio|gym|clinic|iatr|dentist|odontiatr)\b"),
    ("education", r"\b(acca|kaplan|becker|university|ihu|tuition|book|skroutz.*book)\b"),
    ("shopping", r"\b(skroutz|public|kotsovolos|zara|h&m|amazon|aliexpress|ikea|yes store)\b"),
    ("investing", r"\b(trading\s?212|degiro|revolut.*invest|etoro|interactive brokers)\b"),
    ("cash_withdrawal", r"\b(atm|withdrawal|analipsi)\b"),
    ("fees", r"\b(fee|commission|proithia|xreosi)\b"),
    ("income", r"\b(salary|misthos|misthodosia|μισθοδοσια|payroll|deposit from|transfer in)\b"),
]
_COMPILED = [(cat, re.compile(pat, re.I)) for cat, pat in RULES]


def normalize_merchant(description: str) -> str:
    """Collapse a raw statement line to a stable merchant key: lowercase, strip trailing
    reference numbers/dates/card tails banks tack onto every line, collapse whitespace.
    Keeps Greek letters (many Greek bank statement lines are Greek-only, e.g. ΜΙΣΘΟΔΟΣΙΑ)."""
    s = (description or "").lower()
    s = re.sub(r"\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?", "", s)  # embedded dates (before digit-run strip below)
    s = re.sub(r"\d{4,}", "", s)          # long numeric refs/card numbers
    s = re.sub(r"[^a-z0-9 &./Ͱ-Ͽἀ-῿]", " ", s)  # punctuation noise, keep Greek
    s = re.sub(r"\s+", " ", s).strip()
    return s or "unknown"


def categorize(description: str) -> str:
    merchant = normalize_merchant(description)
    for cat, pattern in _COMPILED:
        if pattern.search(merchant):
            return cat
    return "uncategorized"
