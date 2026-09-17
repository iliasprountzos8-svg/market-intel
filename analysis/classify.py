import re
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Initialize VADER analyzer
analyzer = SentimentIntensityAnalyzer()

# Financial tuning: Update VADER lexicon with financial terms
# (Loughran-McDonald inspired)
financial_lexicon = {
    "beat": 2.0,
    "missed": -2.0,
    "upgrade": 2.0,
    "downgrade": -2.0,
    "outperform": 1.5,
    "underperform": -1.5,
    "bullish": 2.0,
    "bearish": -2.0,
    "slashed": -2.0,
    "cut": -1.5,
    "raised": 1.5,
    "surge": 2.0,
    "plunge": -2.0,
    "rally": 1.5,
    "crash": -2.5,
    "default": -3.0,
    "bankruptcy": -3.0,
    "growth": 1.0,
    "decline": -1.0,
    "record": 1.0,
    "dividend": 1.0,
    "buyback": 1.5
}
analyzer.lexicon.update(financial_lexicon)

NUMERIC_RE = re.compile(r"([+-]?\$\d+(?:\.\d+)?[MBB]?|[+-]?\d+(?:\.\d+)?%)")

# The actual holdings this pipeline exists to track (see README /
# ANTIGRAVITY_PROMPT). An article mentioning one of these directly is
# structurally more relevant than the same article about an unrelated ticker,
# regardless of how VADER scores its prose.
PORTFOLIO_TICKERS = {"NVDA", "MSFT", "GOOGL", "ASML"}

def extract_numeric_impact(text: str) -> float:
    """Find magnitude figures like +34%, -$500M, and derive a sentiment modifier."""
    if not text:
        return 0.0
    impact = 0.0
    matches = NUMERIC_RE.findall(text)
    for m in matches:
        if m.startswith('+'):
            impact += 0.5
        elif m.startswith('-'):
            impact -= 0.5
    return impact

def score_article(article: dict) -> dict:
    """
    Given an article dict from the DB, return a dict of AI columns to update.
    Returns: ai_sentiment, ai_relevance_score, ai_confidence, ai_processed
    """
    title = article.get("title", "") or ""
    body = article.get("full_text") or article.get("summary_raw") or ""
    category = article.get("category", "")
    tickers = article.get("tickers_raw", [])
    
    full_text = f"{title} {body}"
    
    # 1. Base Sentiment (VADER)
    scores = analyzer.polarity_scores(full_text)
    compound = scores['compound']
    
    # 2. Numeric Magnitude Modifier
    numeric_impact = extract_numeric_impact(full_text)
    compound += (numeric_impact * 0.2)
    
    # 3. Ticker Proximity (Amplify if sentiment words are near our tickers)
    # A simple proxy: if the article mentions our tickers AND has high sentiment, amplify.
    if tickers and abs(compound) > 0.3:
        compound *= 1.2
    
    # Clamp compound between -1 and 1
    compound = max(-1.0, min(1.0, compound))
    
    # Map to discrete sentiment
    if compound >= 0.25:
        ai_sentiment = "bullish"
    elif compound <= -0.25:
        ai_sentiment = "bearish"
    elif -0.25 < compound < 0.25 and (scores['pos'] > 0.1 and scores['neg'] > 0.1):
        ai_sentiment = "mixed"
    else:
        ai_sentiment = "neutral"
        
    # 4. Relevance Score
    # Base relevance on category and ticker presence
    relevance = 30 # default baseline
    if tickers:
        relevance += 20
    if set(tickers) & PORTFOLIO_TICKERS:
        relevance += 25

    if category == "sec-filing":
        relevance = 85 # SEC filings are always high relevance
    elif category in ["macro", "energy"]:
        relevance += 10
        
    # Title impact: if it's breaking or has magnitude
    if numeric_impact != 0:
        relevance += 15
        
    if "update" in title.lower() or "alert" in title.lower():
        relevance += 10
        
    relevance = max(0, min(100, relevance))
    
    # 5. Confidence
    # Rule-based is inherently lower confidence (~40) to leave room for human/LLM override (~60+)
    ai_confidence = 40
    if category == "sec-filing":
        ai_confidence = 70 # Highly confident about SEC filing relevance
        
    return {
        "ai_processed": True,
        "ai_sentiment": ai_sentiment,
        "ai_relevance_score": relevance,
        "ai_confidence": ai_confidence,
    }
