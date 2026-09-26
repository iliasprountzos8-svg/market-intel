"""Build/refresh the `sources` registry: S&P 500 per-company feeds from three providers,
SEC current-filing feeds, and a curated list of outlets/central banks/regulators.
Every candidate outlet feed is validated (HTTP ok + parseable + >=1 entry) before it is kept.
Run: python build_sources.py [--skip-validate]
"""
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
UA = {"User-Agent": "Mozilla/5.0 (compatible; MarketIntelPersonal/1.0)"}
sys.path.insert(0, str(ROOT / "scraper"))
from sec_filings import SEC_HEADERS  # noqa: E402  (the project's own declared SEC identity)

HOLDINGS = ["NVDA", "MSFT", "GOOGL", "ASML"]
MEGA = set("""NVDA MSFT GOOGL GOOG AMZN META AAPL TSLA AVGO BRK-B JPM V UNH XOM LLY MA COST HD PG JNJ ORCL NFLX BAC WMT ABBV
CVX KO MRK AMD ADBE CRM PEP TMO CSCO ACN LIN MCD ABT WFC DHR IBM GE INTU QCOM TXN CAT NOW AMAT DIS PM ISRG GS UBER MU LRCX
KLAC ANET INTC PANW ASML TSM ARM""".split())
EXTRAS = [("ASML", "ASML Holding"), ("TSM", "Taiwan Semiconductor"), ("ARM", "Arm Holdings")]

# (name, url, category, poll_minutes)
OUTLETS = [
 ("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "macro", 30),
 ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "equities", 30),
 ("CNBC Business", "https://www.cnbc.com/id/10001147/device/rss/rss.html", "equities", 30),
 ("CNBC Technology", "https://www.cnbc.com/id/19854910/device/rss/rss.html", "equities", 30),
 ("CNBC Earnings", "https://www.cnbc.com/id/15839135/device/rss/rss.html", "equities", 30),
 ("CNBC Investing", "https://www.cnbc.com/id/15839069/device/rss/rss.html", "equities", 30),
 ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "macro", 30),
 ("MarketWatch Top", "https://feeds.marketwatch.com/marketwatch/topstories/", "equities", 30),
 ("MarketWatch Pulse", "https://feeds.marketwatch.com/marketwatch/marketpulse/", "equities", 30),
 ("MarketWatch Realtime", "https://feeds.marketwatch.com/marketwatch/realtimeheadlines/", "equities", 30),
 ("MarketWatch Bulletins", "https://feeds.marketwatch.com/marketwatch/bulletins", "equities", 30),
 ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "equities", 30),
 ("WSJ World", "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "macro", 60),
 ("WSJ Technology", "https://feeds.a.dj.com/rss/RSSWSJD.xml", "equities", 60),
 ("WSJ US Business", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml", "equities", 60),
 ("Financial Times Markets", "https://www.ft.com/markets?format=rss", "equities", 30),
 ("Financial Times Companies", "https://www.ft.com/companies?format=rss", "equities", 60),
 ("Financial Times Technology", "https://www.ft.com/technology?format=rss", "equities", 60),
 ("NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "macro", 60),
 ("NYT Economy", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "macro", 60),
 ("NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "equities", 60),
 ("NYT DealBook", "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml", "equities", 60),
 ("Guardian Business", "https://www.theguardian.com/business/rss", "macro", 60),
 ("Guardian Economics", "https://www.theguardian.com/business/economics/rss", "macro", 60),
 ("Guardian Technology", "https://www.theguardian.com/technology/rss", "equities", 120),
 ("BBC Business", "http://feeds.bbci.co.uk/news/business/rss.xml", "macro", 60),
 ("BBC Technology", "http://feeds.bbci.co.uk/news/technology/rss.xml", "equities", 120),
 ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml", "macro", 120),
 ("Economist Finance", "https://www.economist.com/finance-and-economics/rss.xml", "macro", 120),
 ("Economist Business", "https://www.economist.com/business/rss.xml", "equities", 120),
 ("Fortune", "https://fortune.com/feed/", "equities", 60),
 ("Forbes Business", "https://www.forbes.com/business/feed/", "equities", 60),
 ("Forbes Investing", "https://www.forbes.com/investing/feed/", "equities", 60),
 ("Business Insider", "https://www.businessinsider.com/rss", "macro", 30),
 ("Seeking Alpha Market Currents", "https://seekingalpha.com/market_currents.xml", "equities", 30),
 ("Seeking Alpha Feed", "https://seekingalpha.com/feed.xml", "equities", 60),
 ("Motley Fool", "https://www.fool.com/feeds/index.aspx", "equities", 60),
 ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline", "equities", 120),
 ("Benzinga", "https://www.benzinga.com/feed", "equities", 30),
 ("Barrons", "https://www.barrons.com/xml/rss/3_7510.xml", "equities", 60),
 ("TheStreet", "https://www.thestreet.com/.rss/full/", "equities", 60),
 ("Kiplinger", "https://www.kiplinger.com/feed/all", "macro", 120),
 ("Yahoo Finance Top", "https://finance.yahoo.com/news/rssindex", "equities", 30),
 ("Investing.com News", "https://www.investing.com/rss/news.rss", "macro", 30),
 ("Investing.com Stocks", "https://www.investing.com/rss/news_25.rss", "equities", 30),
 ("Investing.com Economy", "https://www.investing.com/rss/news_95.rss", "macro", 60),
 ("Investing.com Commodities", "https://www.investing.com/rss/news_11.rss", "energy", 60),
 ("Investing.com Forex", "https://www.investing.com/rss/news_1.rss", "macro", 60),
 ("Investing.com Economic Indicators", "https://www.investing.com/rss/news_14.rss", "macro", 60),
 ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss", "equities", 30),
 ("Bloomberg Technology", "https://feeds.bloomberg.com/technology/news.rss", "equities", 60),
 ("Bloomberg Politics", "https://feeds.bloomberg.com/politics/news.rss", "macro", 60),
 ("Bloomberg Wealth", "https://feeds.bloomberg.com/wealth/news.rss", "equities", 120),
 ("CNN Money", "http://rss.cnn.com/rss/money_latest.rss", "macro", 60),
 ("Nasdaq Markets", "https://www.nasdaq.com/feed/rssoutbound?category=Markets", "equities", 60),
 ("Nasdaq Stocks", "https://www.nasdaq.com/feed/rssoutbound?category=Stocks", "equities", 60),
 ("Nasdaq Earnings", "https://www.nasdaq.com/feed/rssoutbound?category=Earnings", "equities", 60),
 ("Nasdaq Technology", "https://www.nasdaq.com/feed/rssoutbound?category=Technology", "equities", 60),
 ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "macro", 120),
 ("Fed Press Releases", "https://www.federalreserve.gov/feeds/press_all.xml", "macro", 15),
 ("Fed Monetary Policy", "https://www.federalreserve.gov/feeds/press_monetary.xml", "macro", 15),
 ("Fed Speeches", "https://www.federalreserve.gov/feeds/speeches.xml", "macro", 30),
 ("ECB Press", "https://www.ecb.europa.eu/rss/press.html", "macro", 30),
 ("Bank of England News", "https://www.bankofengland.co.uk/rss/news", "macro", 60),
 ("Bank of Japan", "https://www.boj.or.jp/en/rss/whatsnew.xml", "macro", 120),
 ("IMF News", "https://www.imf.org/en/News/rss?language=eng", "macro", 120),
 ("BIS", "https://www.bis.org/doclist/all_rss.rss", "macro", 240),
 ("BLS Latest", "https://www.bls.gov/feed/bls_latest.rss", "macro", 30),
 ("BLS CPI", "https://www.bls.gov/feed/cpi.rss", "macro", 60),
 ("BLS Employment", "https://www.bls.gov/feed/empsit.rss", "macro", 60),
 ("BEA", "https://apps.bea.gov/rss/rss.xml", "macro", 60),
 ("US Treasury Press", "https://home.treasury.gov/system/files/136/treasury-press-releases.xml", "macro", 60),
 ("EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml", "energy", 120),
 ("EIA Press", "https://www.eia.gov/rss/press_rss.xml", "energy", 120),
 ("SEC Press Releases", "https://www.sec.gov/news/pressreleases.rss", "macro", 60),
 ("FTC Press Releases", "https://www.ftc.gov/feeds/press-release.xml", "macro", 120),
 ("DOJ Press Releases", "https://www.justice.gov/news/rss?type=press_release&m=1", "macro", 120),
 ("FDA Press", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", "equities", 120),
 ("OilPrice", "https://oilprice.com/rss/main", "energy", 30),
 ("Rigzone", "https://www.rigzone.com/news/rss/rigzone_latest.aspx", "energy", 120),
 ("Mining.com", "https://www.mining.com/feed/", "energy", 120),
 ("Kitco News", "https://www.kitco.com/rss/kitconews.xml", "energy", 120),
 ("TechCrunch", "https://techcrunch.com/feed/", "equities", 60),
 ("The Verge", "https://www.theverge.com/rss/index.xml", "equities", 120),
 ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "equities", 120),
 ("Wired", "https://www.wired.com/feed/rss", "equities", 240),
 ("Engadget", "https://www.engadget.com/rss.xml", "equities", 240),
 ("The Register", "https://www.theregister.com/headlines.atom", "equities", 120),
 ("Hacker News Front Page", "https://hnrss.org/frontpage", "equities", 60),
 ("Tom's Hardware", "https://www.tomshardware.com/feeds/all", "equities", 120),
 ("EE Times", "https://www.eetimes.com/feed/", "equities", 120),
 ("Semiconductor Engineering", "https://semiengineering.com/feed/", "equities", 120),
 ("Data Center Dynamics", "https://www.datacenterdynamics.com/en/rss/", "equities", 120),
 ("VentureBeat", "https://venturebeat.com/feed/", "equities", 120),
 ("MIT Technology Review", "https://www.technologyreview.com/feed/", "equities", 240),
 ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss", "equities", 240),
 ("Techmeme", "https://www.techmeme.com/feed.xml", "equities", 30),
 ("SemiWiki", "https://semiwiki.com/feed/", "equities", 240),
 ("Calculated Risk", "https://feeds.feedburner.com/CalculatedRisk", "macro", 120),
 ("Marginal Revolution", "https://feeds.feedburner.com/marginalrevolution/feed", "macro", 240),
 ("Wolf Street", "https://wolfstreet.com/feed/", "macro", 240),
 ("Abnormal Returns", "https://abnormalreturns.com/feed/", "equities", 240),
 ("A Wealth of Common Sense", "https://awealthofcommonsense.com/feed/", "equities", 240),
 ("Naked Capitalism", "https://www.nakedcapitalism.com/feed", "macro", 240),
 ("Noahpinion", "https://www.noahpinion.blog/feed", "macro", 240),
 ("Damodaran Blog", "https://aswathdamodaran.blogspot.com/feeds/posts/default", "equities", 480),
 ("Naftemporiki", "https://www.naftemporiki.gr/feed/", "macro", 60),
 ("Capital.gr", "https://www.capital.gr/rss", "macro", 60),
 ("Kathimerini Economy", "https://www.kathimerini.gr/economy/feed/", "macro", 120),
 ("Euronews Business", "https://www.euronews.com/rss?format=mrss&level=theme&name=business", "macro", 120),
 ("DW Business", "https://rss.dw.com/rdf/rss-en-bus", "macro", 120),
 ("France24 Business", "https://www.france24.com/en/business/rss", "macro", 120),
 ("Politico Europe", "https://www.politico.eu/feed/", "macro", 120),
 ("Euractiv", "https://www.euractiv.com/feed/", "macro", 240),
 ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", "macro", 120),
 ("SCMP Business", "https://www.scmp.com/rss/92/feed", "macro", 120),
 ("Straits Times Business", "https://www.straitstimes.com/news/business/rss.xml", "macro", 120),
 ("CNA Business", "https://www.channelnewsasia.com/rssfeeds/8395884", "macro", 120),
 ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "macro", 60),
 ("Cointelegraph", "https://cointelegraph.com/rss", "macro", 120),
 ("NVIDIA Blog", "https://blogs.nvidia.com/feed/", "equities", 120),
 ("Microsoft News", "https://news.microsoft.com/feed/", "equities", 120),
 ("Google Blog", "https://blog.google/rss/", "equities", 120),
 ("Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss", "equities", 240),
 ("Amazon News", "https://press.aboutamazon.com/rss/news-releases.xml", "equities", 240),
 ("Meta Newsroom", "https://about.fb.com/news/feed/", "equities", 240),
 ("PR Newswire", "https://www.prnewswire.com/rss/news-releases-list.rss", "equities", 30),
 ("GlobeNewswire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies", "equities", 30),
]


def validate(row):
    name, url, cat, poll = row
    try:
        r = requests.get(url, headers=UA, timeout=20)
        f = feedparser.parse(r.content)
        return (row, r.status_code == 200 and len(f.entries) >= 1, len(f.entries), r.status_code)
    except Exception as e:  # noqa: BLE001
        return (row, False, 0, str(e)[:40])


def main():
    skip = "--skip-validate" in sys.argv
    sp = json.load(open(ROOT / "data" / "sp500.json"))
    tickers = [(x["symbol"], x["name"]) for x in sp] + [e for e in EXTRAS if e[0] not in {x["symbol"] for x in sp}]
    rows = []
    for sym, nm in tickers:
        tier_a = sym in MEGA or sym in HOLDINGS
        rows.append({"name": f"Yahoo Finance ({sym})", "kind": "yahoo_ticker", "category": "equities", "ticker": sym, "weight": 0.8,
                     "poll_minutes": 30 if tier_a else 180, "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"})
        rows.append({"name": f"Nasdaq ({sym})", "kind": "nasdaq_ticker", "category": "equities", "ticker": sym, "weight": 0.9,
                     "poll_minutes": 60 if tier_a else 240, "url": f"https://www.nasdaq.com/feed/rssoutbound?symbol={sym}"})
        rows.append({"name": f"Seeking Alpha ({sym})", "kind": "sa_ticker", "category": "equities", "ticker": sym, "weight": 0.7,
                     "poll_minutes": 60 if tier_a else 360, "url": f"https://seekingalpha.com/api/sa/combined/{sym}.xml"})
    for form, poll in (("8-K", 15), ("10-K", 120), ("10-Q", 120), ("6-K", 60)):
        rows.append({"name": f"SEC EDGAR current {form}", "kind": "sec", "category": "sec-filing", "ticker": None, "weight": 1.3, "poll_minutes": poll,
                     "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&count=100&output=atom"})
    kept = 0
    if skip:
        good = OUTLETS
    else:
        good = []
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            for row, ok, n, status in ex.map(validate, OUTLETS):
                (good.append(row) if ok else print(f"  dropped {row[0]:34s} status={status} entries={n}"))
                time.sleep(0.02)
    for name, url, cat, poll in good:
        rows.append({"name": name, "kind": "rss", "category": cat, "ticker": None, "weight": 0.9, "poll_minutes": poll, "url": url})
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    for i in range(0, len(rows), 200):
        client.table("sources").upsert(rows[i:i + 200], on_conflict="url", ignore_duplicates=False).execute()
    total = client.table("sources").select("id", count="exact").limit(1).execute().count
    print(f"outlets kept {len(good)}/{len(OUTLETS)} | per-company feeds {len(tickers) * 3} | SEC 4 | registry total {total}")


if __name__ == "__main__":
    main()
