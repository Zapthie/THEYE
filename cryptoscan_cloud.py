"""
NewsScan - Cloud Edition v8
===========================
Faster. Smarter. Tougher. Never crashes.

- Parallel source fetching with timeout isolation
- Correlation engine: detects related event clusters
- Exponential backoff on failed sources
- Full error isolation: one dead source never kills others
- Auto-recovery: sources retry on next cycle
- Cloud (Railway) + Local (PC) single file

Config via environment variables (Railway dashboard):
  NEWSAPI_KEY, GROQ_API_KEY, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID, NEWSSCAN_TOPICS, NEWSSCAN_INTERVAL, PORT
"""

import feedparser
import webbrowser
import requests
import time
import threading
import os
import sys
import re
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

# CONFIG
import os as _os
NEWSAPI_KEY        = _os.environ.get("NEWSAPI_KEY",        "")
GROQ_API_KEY       = _os.environ.get("GROQ_API_KEY",       "")
TELEGRAM_BOT_TOKEN = _os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = _os.environ.get("TELEGRAM_CHAT_ID",   "")
CHECK_INTERVAL     = 10
DASHBOARD_PORT     = int(_os.environ.get("PORT", 5000))

TRADINGVIEW_LINKS = [
    {"label": "BTC/USD",  "url": "BTCUSD"},
    {"label": "Gold",     "url": "XAUUSD"},
    {"label": "Oil/WTI",  "url": "USOIL"},
    {"label": "S&P 500",  "url": "SPX"},
    {"label": "EUR/USD",  "url": "EURUSD"},
    {"label": "ETH/USD",  "url": "ETHUSD"},
    {"label": "DXY",      "url": "DXY"},
]

RSS_SOURCES = [
    ("CoinTelegraph", [
        "https://cointelegraph.com/rss",
        "https://cointelegraph.com/rss/tag/bitcoin",
    ]),
    ("CoinDesk", [
        "https://www.coindesk.com/feed/",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]),
    ("Reuters", [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/financialNews",
        "https://feeds.reuters.com/reuters/topNews",
    ]),
    ("MarketWatch", [
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    ]),
    ("CNBC", [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    ]),
    ("Investing.com", [
        "https://www.investing.com/rss/news.rss",
        "https://www.investing.com/rss/news_25.rss",
    ]),
    ("HindustanTimes", [
        "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
        "https://www.hindustantimes.com/feeds/rss/business/rssfeed.xml",
    ]),
]

REDDIT_FEEDS = [
    ("r/CryptoCurrency", "https://www.reddit.com/r/CryptoCurrency/new/.rss?sort=new"),
    ("r/wallstreetbets",  "https://www.reddit.com/r/wallstreetbets/new/.rss?sort=new"),
    ("r/Bitcoin",         "https://www.reddit.com/r/Bitcoin/new/.rss?sort=new"),
    ("r/Economics",       "https://www.reddit.com/r/Economics/new/.rss?sort=new"),
]

TWITTER_ACCOUNTS = [
    ("DeItaone",       "Walter Bloomberg", True),
    ("federalreserve", "Federal Reserve",  True),
    ("POTUS",          "US President",     True),
    ("cz_binance",     "CZ Binance",       True),
    ("saylor",         "Michael Saylor",   True),
    ("whale_alert",    "Whale Alert",      False),
    ("CoinDesk",       "CoinDesk",         False),
    ("Reuters_Biz",    "Reuters Business", False),
]

WHALE_ALERT_RSS = "https://whale-alert.io/rss"

PRICE_WATCH = {
    "BTC":  ("bitcoin",  1.5),
    "ETH":  ("ethereum", 1.5),
}

XRSS_BRIDGES = [
    "https://rsshub.app/twitter/user/{handle}",
    "https://rsshub.rssforever.com/twitter/user/{handle}",
    "https://hub.slarker.me/twitter/user/{handle}",
    "https://nitter.privacydev.net/{handle}/rss",
    "https://nitter.poast.org/{handle}/rss",
]

# MARKET INTELLIGENCE DICTS
# ══════════════════════════════════════════════════════════
# SCORING PHILOSOPHY: ONE RULE
# Article must prove it MOVES crypto or stock markets.
# Needs MARKET ASSET word + MARKET IMPACT word to pass.
# No asset = blocked. Asset with no market impact = blocked.
# ══════════════════════════════════════════════════════════

MIN_RELEVANCE_SCORE = 8   # higher bar = cleaner feed

# These are financial assets we track - article MUST mention at least one
MARKET_ASSETS = {
    # Crypto
    "bitcoin":8, "btc":8, "ethereum":8, "eth":8, "crypto":7,
    "cryptocurrency":7, "altcoin":6, "defi":6, "nft":4,
    "blockchain":5, "solana":6, "xrp":6, "ripple":6, "bnb":6,
    "stablecoin":6, "usdt":5, "tether":5, "coinbase":7,
    "binance":7, "kraken":6, "bybit":6, "bitmex":6,
    # Stocks / indices
    "stock market":8, "s&p 500":8, "s&p":7, "nasdaq":8, "dow jones":7,
    "equities":7, "stocks":6, "shares":5, "wall street":7,
    "nyse":7, "stock exchange":7, "market cap":6,
    # Commodities
    "gold price":8, "gold futures":8, "xau":8, "gold spot":8,
    "gold etf":8, "precious metal":7, "bullion":7, "comex gold":8,
    "oil price":8, "crude oil":8, "brent crude":8, "wti crude":8,
    "oil futures":8, "opec":8, "oil supply":8,
    "silver price":7, "copper price":6,
    # Macro / rates
    "interest rate":8, "rate hike":9, "rate cut":9, "fomc":9,
    "federal reserve":9, "fed reserve":9, "fed rate":9,
    "inflation":7, "cpi":8, "ppi":7, "gdp":7,
    "bond yield":8, "yield curve":8, "treasury yield":8,
    "monetary policy":8, "quantitative":7,
    "dollar index":7, "dxy":7, "forex":6,
    # Crypto-specific events
    "btc etf":9, "spot etf":9, "sec":8, "cftc":7,
    "crypto regulation":9, "crypto ban":9, "hack":8, "exploit":8,
    "rug pull":9, "exchange hack":9, "crypto fraud":8,
    "etf approval":9, "etf rejection":9,
    # Market-specific entities
    "blackrock":7, "jpmorgan":7, "goldman sachs":7, "morgan stanley":7,
    "vanguard":6, "fidelity":7, "citadel":6,
    "powell":7, "yellen":7, "lagarde":7,
    # Market events
    "vix":7, "market crash":9, "market rally":8, "market selloff":9,
    "recession":8, "debt ceiling":8, "default":7, "bailout":7,
    "tariff":7, "trade war":8, "sanctions":6,
}

# Market IMPACT words - article must contain at least one of these
# (proves it's about market movement, not just mentioning an asset name)
MARKET_IMPACT = [
    # Security events (crypto-specific market movers)
    "hacked","stolen","hack","exploit","rug pull","breach","seized","arrested",
    # Price movement
    "price","prices","surges","surge","drops","drop","falls","fall",
    "rises","rise","rallies","rally","tumbles","plunges","plunge",
    "crashes","crash","soars","soar","gains","jumps","climbs",
    "record high","record low","all-time high","ath","new high",
    "up %","down %","gains %","drops %","% gain","% loss",
    # Market context
    "market","markets","trading","traders","investors","investment",
    "bullish","bearish","sentiment","outlook","forecast","analysis",
    "buy","sell","short","long","hedge","position",
    # Financial instruments
    "etf","futures","options","derivatives","fund","portfolio",
    "yield","rate","basis points","bps",
    # Economic impact
    "inflation","deflation","cpi","gdp","unemployment","jobs","payroll",
    "earnings","revenue","profit","loss","quarterly","annual report",
    # Policy events
    "rate hike","rate cut","fomc","decision","meeting","statement",
    "regulation","ban","approve","reject","lawsuit","investigation",
    "sec filing","cftc","enforcement",
    # Geopolitical market triggers
    "oil supply","supply cut","production cut","sanctions impact",
    "war premium","risk","uncertainty","safe haven",
]

# Geopolitical events that INDIRECTLY move markets - only if economic context present
GEO_MARKET_TRIGGERS = {
    "strait of hormuz":9, "oil supply disruption":9, "opec cut":9,
    "iran nuclear":7, "iran oil":8, "russia gas":7, "russia oil":8,
    "middle east oil":8, "china trade":7, "us china":6,
    "executive order":5, "truth social":4,
}

# Absolute hard blocks - nothing redeems these
NEVER_SHOW = [
    # People who never move markets
    "taylor swift","beyonce","kardashian","zendaya","bieber","drake",
    "rihanna","cardi b","ariana grande","lady gaga","katy perry",
    "jennifer lopez","brad pitt","angelina jolie","tom cruise",
    "cristiano","messi","lebron","tiger woods",
    # Event types that never move markets
    "gold medal","gold medalist","olympic","grammy","oscar","emmy",
    "box office","album review","movie review","film review",
    "recipe","fashion week","runway","couture",
    "cricket match","football match","nba game","nfl game","ipl match",
    "soccer match","tennis match","golf tournament",
    # Specific spam patterns
    "non-brokered","private placement","flow-through units",
    "exploration announces","stock titan","100.7 wmms",
    "everyone's saying","opens up about","speaks to","talks to",
    "tips for","how to lose","how to get","best restaurants",
    "travel guide","lifestyle","wellness","self-care",
]

BULLISH_WORDS = {
    "surge":3,"surges":3,"rally":3,"rallies":3,"soar":3,
    "jump":2,"gain":2,"gains":2,"rise":2,"rises":2,"risen":2,
    "breakout":3,"bull":2,"bullish":3,"boom":3,"record high":4,
    "all-time high":4,"ath":4,"approval":3,"approved":3,
    "adoption":3,"accumulate":2,"recovery":2,"rebound":2,
    "upgrade":2,"beat":2,"outperform":2,"growth":2,"profit":2,
}

BEARISH_WORDS = {
    "crash":3,"crashes":3,"dump":3,"drop":2,"drops":2,
    "fall":2,"falls":2,"decline":2,"declines":2,"plunge":3,
    "selloff":3,"sell-off":3,"bear":2,"bearish":3,
    "ban":3,"banned":3,"hack":3,"hacked":3,"stolen":3,
    "lawsuit":2,"investigation":2,"fraud":3,"scam":3,
    "collapse":3,"bankrupt":3,"default":3,"recession":3,
    "rate hike":2,"delisted":3,"suspended":3,"warning":2,
    "loss":2,"losses":2,"downgrade":2,
}

HIGH_WORDS = [
    "sec","ban","banned","hack","hacked","crash","crashed","etf",
    "lawsuit","seized","arrested","breach","exploit","rug pull","scam",
    "collapse","bankrupt","bankruptcy","emergency","suspended","delisted",
    "rate hike","rate cut","fomc","cpi","nonfarm","opec","nuclear","missile",
]
MEDIUM_WORDS = [
    "elon","fed","federal reserve","regulation","tariff","inflation",
    "recession","interest rate","treasury","sanction","binance",
    "coinbase","blackrock","rally","dump","war","iran","israel",
]

CORRELATION_CLUSTERS = [
    {"name": "OIL_CRISIS",   "keywords": ["iran","oil","opec","crude","strait"]},
    {"name": "FED_MACRO",    "keywords": ["fed","fomc","rate","inflation","cpi","powell"]},
    {"name": "CRYPTO_REG",   "keywords": ["sec","etf","regulation","ban","cftc","coinbase"]},
    {"name": "RISK_OFF",     "keywords": ["crash","recession","yield","treasury","default","debt"]},
    {"name": "GEOPOLITICAL", "keywords": ["war","nuclear","missile","attack","sanction","russia","ukraine"]},
    {"name": "WHALE_MOVE",   "keywords": ["whale","billion","btc transfer","eth transfer","exchange"]},
]

# STATE
state = {
    "topics":              [],
    "interval":            CHECK_INTERVAL,
    "checks":              0,
    "new_articles":        [],
    "article_buffer":      [],   # rolling last 200 articles for page refresh
    "sound_on":            True,
    "newsapi_calls_today": 0,
    "sources": {
        "newsapi":  "wait",
        "google":   "wait",
        "coindesk": "wait",
    },
    "fear_greed":    {"value": None, "label": "", "updated": ""},
    "prices":        {},
    "econ_calendar": [],
    "whale_alerts":  [],
    "twitter_feed":  [],
    "reddit_hot":    [],
    "correlations":  [],
    "system_score":  {"speed": 0, "accuracy": 0, "overall": 0,
                      "sources_live": 0, "sources_total": 0, "breakdown": []},
}

seen_ids          = set()
seen_titles       = set()
seen_price_alerts = set()
state_lock        = threading.Lock()

_source_health      = {}
_source_health_lock = threading.Lock()


def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def now_iso():
    return datetime.now(timezone.utc).isoformat()


# SOURCE HEALTH / BACKOFF

def source_ok_to_fetch(name):
    with _source_health_lock:
        h = _source_health.get(name, {})
        return time.time() >= h.get("backoff_until", 0)

def source_record_fail(name):
    with _source_health_lock:
        h = _source_health.setdefault(name, {"fails": 0, "backoff_until": 0})
        h["fails"] += 1
        delay = min(240, 30 * (2 ** (h["fails"] - 1)))
        h["backoff_until"] = time.time() + delay

def source_record_success(name):
    with _source_health_lock:
        _source_health[name] = {"fails": 0, "backoff_until": 0}


# DATE PARSING

def parse_published(pub_str):
    if not pub_str:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"]:
        try:
            return datetime.strptime(pub_str[:25], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        return parsedate_to_datetime(pub_str).astimezone(timezone.utc)
    except Exception:
        pass
    return None

def is_recent_enough(pub_str, max_hours=2):
    dt = parse_published(pub_str)
    if not dt:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=max_hours)

def published_iso(pub_str):
    dt = parse_published(pub_str)
    return dt.isoformat() if dt else now_iso()


# SCORING

def market_relevance_score(title):
    """
    Core rule: article must prove it moves crypto or stock markets.
    Pass = has at least one MARKET_ASSET + at least one MARKET_IMPACT word.
    Anything that only mentions an asset name with no market impact = blocked.
    """
    tl = title.lower()

    # ── Step 1: Hard never-show list ──────────────────────────────
    for word in NEVER_SHOW:
        if word in tl:
            return -100, ["BLOCKED:" + word]

    # ── Step 2: Check for market asset mention ────────────────────
    asset_score = 0
    asset_reasons = []
    for asset, pts in MARKET_ASSETS.items():
        if asset in tl:
            asset_score = max(asset_score, pts)  # take highest single asset score
            asset_reasons.append(asset)
            if len(asset_reasons) >= 3:
                break

    # ── Step 3: Check for market impact word ─────────────────────
    has_impact = any(word in tl for word in MARKET_IMPACT)

    # ── Step 4: Check geo triggers (secondary path) ───────────────
    geo_score = 0
    for trigger, pts in GEO_MARKET_TRIGGERS.items():
        if trigger in tl:
            geo_score += pts

    # ── Decision logic ────────────────────────────────────────────
    # Path A: has asset + has market impact word → PASS
    if asset_score > 0 and has_impact:
        final = asset_score
        # Bonus for multiple asset signals or strong impact words
        if "%" in tl or "record" in tl or "all-time" in tl:
            final += 5
        if any(w in tl for w in ["etf","fomc","cpi","rate cut","rate hike","hack","ban","sec"]):
            final += 8
        return final, asset_reasons[:5]

    # Path B: geo trigger with clear market/oil/price context → PASS
    if geo_score >= 7 and has_impact:
        return geo_score, ["geo:" + list(GEO_MARKET_TRIGGERS.keys())[0]]

    # Path C: pure macro event that doesn't need an asset name
    # e.g. "Fed raises rates", "CPI data hotter than expected"
    macro_standalone = [
        "federal reserve","fed reserve","fomc","rate hike","rate cut",
        "cpi data","cpi report","inflation data","gdp data","jobs report",
        "nonfarm payroll","unemployment rate","bond yields","yield curve",
        "us treasury","debt ceiling","us default",
    ]
    for m in macro_standalone:
        if m in tl and has_impact:
            return 12, [m]

    # Everything else is rejected
    return 0, []

def is_market_relevant(title):
    return market_relevance_score(title)[0] >= MIN_RELEVANCE_SCORE

def classify_sentiment(title):
    tl = title.lower()
    b  = sum(v for k, v in BULLISH_WORDS.items() if k in tl)
    br = sum(v for k, v in BEARISH_WORDS.items() if k in tl)
    if b == 0 and br == 0: return "neutral"
    return "bullish" if b > br else ("bearish" if br > b else "neutral")

def classify_impact(title):
    t = title.lower()
    if any(w in t for w in HIGH_WORDS):   return "high"
    if any(w in t for w in MEDIUM_WORDS): return "medium"
    return "low"

def normalize_title(title):
    return title.strip().lower().split(" - ")[0].strip()

def make_article(title, link, source, topic, pub):
    score, reasons = market_relevance_score(title)
    return {
        "title":         title,
        "link":          link,
        "source":        source,
        "topic":         topic,
        "published":     pub,
        "published_iso": published_iso(pub),
        "found_at":      now_iso(),
        "relevance":     score,
        "reasons":       reasons,
        "sentiment":     classify_sentiment(title),
        "impact":        classify_impact(title),
    }


# TOPIC MATCHING

def matches_topic(title, topic):
    tl = title.lower()
    tp = topic.lower().strip()
    if tp in tl:
        return True
    words = [w for w in tp.split() if len(w) > 2]
    if not words:
        return True
    return all(re.search(r'\b' + re.escape(w) + r'\b', tl) for w in words)

def first_matching_topic(title, topics):
    for t in topics:
        if matches_topic(title, t):
            return t
    return None


# CORRELATION ENGINE

def detect_correlations(recent_titles):
    signals = []
    title_blob = " ".join(t.lower() for t in recent_titles)
    for cluster in CORRELATION_CLUSTERS:
        hits = [kw for kw in cluster["keywords"] if kw in title_blob]
        headline_hits = sum(
            1 for t in recent_titles
            if any(kw in t.lower() for kw in cluster["keywords"])
        )
        if len(hits) >= 2 and headline_hits >= 2:
            signals.append({
                "cluster":   cluster["name"],
                "keywords":  hits,
                "headlines": headline_hits,
                "detected":  now_iso(),
            })
    return signals


# TELEGRAM

_tg_last_sent = {}

def send_telegram(title, impact, sentiment, source):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    now_t = time.time()
    key   = title[:60]
    if now_t - _tg_last_sent.get(key, 0) < 600:
        return
    _tg_last_sent[key] = now_t
    impact_icon = {"high": "[!!]", "medium": "[!]", "low": "[i]"}.get(impact, "[i]")
    sent_icon   = {"bullish": "[UP]", "bearish": "[DN]", "neutral": "[--]"}.get(sentiment, "[--]")
    msg = (
        f"{impact_icon} *{impact.upper()}*  {sent_icon} {sentiment.upper()}\n"
        f"[SRC] {source}\n\n{title}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=8
        )
    except Exception as e:
        log(f"Telegram error: {str(e)[:60]}")


# SOURCE: NewsAPI

def fetch_newsapi(topics):
    articles = {}
    if not NEWSAPI_KEY:
        state["sources"]["newsapi"] = "wait"
        return articles
    if state["newsapi_calls_today"] >= 80:
        state["sources"]["newsapi"] = "limit"
        return articles
    if not source_ok_to_fetch("newsapi"):
        return articles

    def in_chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    try:
        for batch in in_chunks(topics, 2):
            query = " OR ".join(f'"{t}"' for t in batch)
            for attempt in range(2):
                try:
                    r = requests.get(
                        "https://newsapi.org/v2/everything"
                        f"?q={requests.utils.quote(query)}"
                        "&sortBy=publishedAt&language=en&pageSize=20"
                        f"&apiKey={NEWSAPI_KEY}",
                        timeout=10
                    )
                    state["newsapi_calls_today"] += 1
                    if r.status_code == 429:
                        state["sources"]["newsapi"] = "limit"
                        source_record_fail("newsapi")
                        return articles
                    data = r.json()
                    if data.get("status") != "ok":
                        state["sources"]["newsapi"] = "err"
                        source_record_fail("newsapi")
                        break
                    for item in data.get("articles", []):
                        title = item.get("title") or ""
                        if not title or title == "[Removed]": continue
                        if not is_recent_enough(item.get("publishedAt","")): continue
                        if not is_market_relevant(title): continue
                        uid = "na_" + (item.get("url") or title)
                        if uid not in articles:
                            matched = first_matching_topic(title, topics) or batch[0]
                            articles[uid] = make_article(
                                title, item.get("url","#"),
                                "NewsAPI", matched, item.get("publishedAt","")
                            )
                    state["sources"]["newsapi"] = "ok"
                    source_record_success("newsapi")
                    break
                except requests.exceptions.ConnectionError:
                    if attempt < 1:
                        time.sleep(2)
                    else:
                        state["sources"]["newsapi"] = "err"
                        source_record_fail("newsapi")
                except Exception as e:
                    log(f"NewsAPI: {str(e)[:60]}")
                    state["sources"]["newsapi"] = "err"
                    source_record_fail("newsapi")
                    break
    except Exception as e:
        log(f"NewsAPI outer: {str(e)[:60]}")
    return articles


# SOURCE: Google News

def fetch_google_news(topics):
    articles = {}
    lock     = threading.Lock()
    any_ok   = [False]

    def fetch_one(topic):
        if not source_ok_to_fetch(f"google_{topic}"):
            return
        for attempt in range(2):
            try:
                url  = (
                    "https://news.google.com/rss/search"
                    f"?q={requests.utils.quote(topic)}&hl=en-US&gl=US&ceid=US:en"
                )
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    title = entry.get("title","")
                    pub   = entry.get("published","")
                    link  = entry.get("link","#")
                    if not matches_topic(title, topic): continue
                    if not is_recent_enough(pub):       continue
                    if not is_market_relevant(title):   continue
                    uid = "gn_" + (link or title)
                    with lock:
                        if uid not in articles:
                            articles[uid] = make_article(title, link, "Google News", topic, pub)
                if feed.entries:
                    any_ok[0] = True
                    source_record_success(f"google_{topic}")
                return
            except Exception:
                if attempt == 0:
                    time.sleep(1)
                else:
                    source_record_fail(f"google_{topic}")

    with ThreadPoolExecutor(max_workers=min(len(topics), 6)) as ex:
        futs = [ex.submit(fetch_one, t) for t in topics]
        for f in as_completed(futs, timeout=15):
            try: f.result()
            except Exception: pass

    state["sources"]["google"] = "ok" if any_ok[0] else "err"
    return articles


# SOURCE: Multi RSS

def fetch_rss_sources(topics):
    articles    = {}
    src_results = {}
    lock        = threading.Lock()

    def fetch_source(name, urls):
        if not source_ok_to_fetch(f"rss_{name}"):
            src_results[name] = "skip"
            return
        feed = None
        for url in urls:
            try:
                f = feedparser.parse(url)
                if f.entries:
                    feed = f
                    break
            except Exception:
                continue
        if not feed or not feed.entries:
            src_results[name] = "err"
            source_record_fail(f"rss_{name}")
            return
        for entry in feed.entries:
            try:
                title   = entry.get("title","")
                pub     = entry.get("published","") or entry.get("updated","")
                link    = entry.get("link","#")
                if not title: continue
                matched = first_matching_topic(title, topics)
                if not matched:               continue
                if not is_recent_enough(pub): continue
                if not is_market_relevant(title): continue
                uid = f"rss_{name}_{link or title}"
                with lock:
                    if uid not in articles:
                        articles[uid] = make_article(title, link, name, matched, pub)
            except Exception:
                continue
        src_results[name] = "ok"
        source_record_success(f"rss_{name}")

    with ThreadPoolExecutor(max_workers=len(RSS_SOURCES)) as ex:
        futs = {ex.submit(fetch_source, name, urls): name for name, urls in RSS_SOURCES}
        for f in as_completed(futs, timeout=20):
            try: f.result()
            except Exception as e:
                name = futs[f]
                src_results[name] = "err"

    coin_ok = any(src_results.get(n) == "ok" for n in ("CoinDesk","CoinTelegraph"))
    state["sources"]["coindesk"] = "ok" if coin_ok else "err"

    working = [k for k,v in src_results.items() if v == "ok"]
    failed  = [k for k,v in src_results.items() if v == "err"]
    if working: log(f"  RSS OK : {', '.join(working)}")
    if failed:  log(f"  RSS ERR: {', '.join(failed)}")
    return articles


# INTELLIGENCE: Fear & Greed

def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data  = r.json()
        entry = data["data"][0]
        state["fear_greed"] = {
            "value":   int(entry["value"]),
            "label":   entry["value_classification"],
            "updated": entry["timestamp"],
        }
        log(f"  Fear & Greed: {entry['value']} ({entry['value_classification']})")
    except Exception:
        pass


# INTELLIGENCE: Prices

def fetch_prices():
    def _parse_and_store(prices_raw):
        alerts = []
        for sym, info in prices_raw.items():
            threshold = PRICE_WATCH.get(sym, ("", 1.5))[1]
            change    = info["change_pct"]
            price     = info["price"]
            alert     = abs(change) >= threshold
            state["prices"][sym] = {
                "price":      price,
                "change_pct": round(change, 2),
                "alert":      alert,
                "direction":  "up" if change >= 0 else "down",
            }
            if alert:
                direction = "surged" if change > 0 else "dropped"
                dedup_key = f"{sym}_{direction}_{round(abs(change)*2)/2:.1f}"
                if dedup_key not in seen_price_alerts:
                    seen_price_alerts.add(dedup_key)
                    alerts.append((sym, direction, change, price))
                    log(f"  PRICE ALERT: {sym} {direction} {change:+.1f}%")
        if len(seen_price_alerts) > 100:
            seen_price_alerts.clear()
        for sym, direction, change, price in alerts:
            title = f"[PRICE SPIKE] {sym} {direction} {abs(change):.1f}% -> ${price:,.0f}"
            art = {
                "title": title, "link": "https://www.coingecko.com",
                "source": "Price Spike", "topic": "price alert",
                "published": "", "published_iso": now_iso(),
                "found_at": now_iso(), "relevance": 10,
                "reasons": ["price spike"],
                "sentiment": "bullish" if direction == "surged" else "bearish",
                "impact": "high",
            }
            with state_lock:
                state["new_articles"].append(art)
            with state_lock:
                state["article_buffer"].append(art)
                state["article_buffer"] = state["article_buffer"][-200:]
            threading.Thread(
                target=send_telegram,
                args=(title, "high", art["sentiment"], "Price Alert"),
                daemon=True
            ).start()

    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=10, headers={"Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json()
            raw = {}
            for cg_id, sym in [("bitcoin","BTC"), ("ethereum","ETH")]:
                if cg_id in data:
                    raw[sym] = {
                        "price":      data[cg_id].get("usd", 0),
                        "change_pct": data[cg_id].get("usd_24h_change", 0) or 0,
                    }
            if raw:
                _parse_and_store(raw)
                log(f"  Prices (CoinGecko): BTC=${raw.get('BTC',{}).get('price',0):,.0f}")
                return
    except Exception as e:
        log(f"  CoinGecko error: {str(e)[:40]}")

    try:
        raw = {}
        for asset_id, sym in [("bitcoin","BTC"), ("ethereum","ETH")]:
            r2 = requests.get(f"https://api.coincap.io/v2/assets/{asset_id}", timeout=8)
            if r2.status_code == 200:
                d = r2.json().get("data", {})
                raw[sym] = {
                    "price":      float(d.get("priceUsd", 0)),
                    "change_pct": float(d.get("changePercent24Hr", 0) or 0),
                }
        if raw:
            _parse_and_store(raw)
            log(f"  Prices (CoinCap): BTC=${raw.get('BTC',{}).get('price',0):,.0f}")
            return
    except Exception as e:
        log(f"  CoinCap error: {str(e)[:40]}")

    log("  Prices: all sources unreachable")


# INTELLIGENCE: Economic Calendar

def fetch_econ_calendar():
    now = datetime.now(timezone.utc)
    events = [
        {"date": "2026-03-12", "event": "US CPI Inflation Report", "impact": "high"},
        {"date": "2026-03-19", "event": "FOMC Meeting",            "impact": "high"},
        {"date": "2026-03-20", "event": "Fed Rate Decision",       "impact": "high"},
        {"date": "2026-03-28", "event": "US GDP Q4 Final",         "impact": "medium"},
        {"date": "2026-04-02", "event": "OPEC Meeting",            "impact": "high"},
        {"date": "2026-04-03", "event": "US Non-Farm Payrolls",    "impact": "high"},
        {"date": "2026-04-10", "event": "US CPI Inflation",        "impact": "high"},
        {"date": "2026-04-14", "event": "US Retail Sales",         "impact": "medium"},
        {"date": "2026-04-29", "event": "FOMC Meeting",            "impact": "high"},
        {"date": "2026-04-30", "event": "Fed Rate Decision",       "impact": "high"},
    ]
    upcoming = []
    for ev in events:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            diff    = (ev_date - now).days
            if -1 <= diff <= 30:
                ev["days_away"] = diff
                ev["today"]     = diff == 0
                upcoming.append(ev)
        except Exception:
            pass
    upcoming.sort(key=lambda x: x["days_away"])
    state["econ_calendar"] = upcoming[:8]


# INTELLIGENCE: Whale Alerts

def fetch_whale_alerts():
    try:
        feed   = feedparser.parse(WHALE_ALERT_RSS)
        whales = []
        for entry in feed.entries[:6]:
            title = entry.get("title","")
            link  = entry.get("link","#")
            pub   = entry.get("published","")
            if not is_recent_enough(pub, max_hours=2): continue
            whales.append({"title": title, "link": link, "pub": pub})
            if any(x in title.lower() for x in ["million","billion","btc","eth","usdt"]):
                art = {
                    "title": f"[WHALE] {title}", "link": link,
                    "source": "Whale Alert", "topic": "whale move",
                    "published": pub, "published_iso": published_iso(pub),
                    "found_at": now_iso(), "relevance": 8,
                    "reasons": ["whale transaction"],
                    "sentiment": "neutral", "impact": "medium",
                }
                nt = normalize_title(art["title"])
                with state_lock:
                    if nt not in seen_titles:
                        seen_titles.add(nt)
                        state["new_articles"].append(art)
        state["whale_alerts"] = whales
        if whales: log(f"  Whale alerts: {len(whales)} recent txns")
    except Exception as e:
        log(f"  Whale alert error: {str(e)[:50]}")


# INTELLIGENCE: Twitter/X

def fetch_twitter():
    tweets = []

    def inject_article(handle, title, link, pub):
        art = {
            "title":         f"[X] @{handle}: {title[:120]}",
            "link":          link, "source": f"X/@{handle}",
            "topic":         "twitter", "published": pub,
            "published_iso": published_iso(pub), "found_at": now_iso(),
            "relevance": 8, "reasons": ["twitter", handle],
            "sentiment": classify_sentiment(title),
            "impact":    classify_impact(title),
        }
        nt = normalize_title(art["title"])
        with state_lock:
            if nt not in seen_titles:
                seen_titles.add(nt)
                state["new_articles"].append(art)

    def try_account(handle, display_name, is_high_impact):
        for bridge_tpl in XRSS_BRIDGES:
            try:
                url  = bridge_tpl.format(handle=handle)
                feed = feedparser.parse(url)
                if not feed.entries: continue
                got = 0
                for entry in feed.entries[:5]:
                    title = entry.get("title","").strip()
                    link  = entry.get("link","#")
                    pub   = entry.get("published","")
                    if not title or title.lower().startswith("rt "): continue
                    if not is_recent_enough(pub, max_hours=3):       continue
                    tweets.append({
                        "title":       title[:200], "link": link,
                        "source":      f"@{handle}", "display": display_name,
                        "pub":         pub, "high_impact": is_high_impact,
                    })
                    if is_high_impact and is_market_relevant(title):
                        inject_article(handle, title, link, pub)
                    got += 1
                if got: return
            except Exception:
                continue

    def fetch_truth_social():
        try:
            feed = feedparser.parse("https://truthsocial.com/@realDonaldTrump.rss")
            for entry in feed.entries[:5]:
                title = entry.get("title","").strip()
                link  = entry.get("link","#")
                pub   = entry.get("published","")
                if not title or not is_recent_enough(pub, max_hours=3): continue
                tweets.append({
                    "title": title[:200], "link": link,
                    "source": "@realDonaldTrump", "display": "Trump (Truth Social)",
                    "pub": pub, "high_impact": True,
                })
                if is_market_relevant(title):
                    inject_article("realDonaldTrump", title, link, pub)
        except Exception as e:
            log(f"  TruthSocial error: {str(e)[:50]}")

    threads = [threading.Thread(target=try_account, args=(h,d,hi), daemon=True)
               for h,d,hi in TWITTER_ACCOUNTS]
    threads.append(threading.Thread(target=fetch_truth_social, daemon=True))
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)

    tweets.sort(key=lambda x: x.get("pub",""), reverse=True)
    state["twitter_feed"] = tweets[:20]
    log(f"  X/Social: {len(tweets)} posts" if tweets else "  X/Social: none fetched")


# INTELLIGENCE: Reddit

def fetch_reddit():
    posts = []
    for name, url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title","")
                link  = entry.get("link","#")
                pub   = entry.get("published","")
                if not is_recent_enough(pub, max_hours=4): continue
                if not is_market_relevant(title): continue
                posts.append({"title": title, "link": link, "source": name, "pub": pub})
        except Exception as e:
            log(f"  Reddit {name} error: {str(e)[:40]}")
    state["reddit_hot"] = posts[:10]
    if posts: log(f"  Reddit: {len(posts)} relevant posts")


# SYSTEM SCORE

def calculate_system_score():
    src       = state["sources"]
    speed_pts = 0
    acc_pts   = 0
    breakdown = []

    if src.get("coindesk") == "ok":
        speed_pts += 3; breakdown.append(("CoinDesk/CT", "+3 speed", "2-5 min"))
    if src.get("google") == "ok":
        speed_pts += 2; breakdown.append(("Google News", "+2 speed", "5-15 min"))
    if src.get("newsapi") == "ok":
        speed_pts += 1; breakdown.append(("NewsAPI", "+1 speed", "15+ min"))
    if state["prices"]:
        speed_pts += 2; breakdown.append(("Prices", "+2 speed", "real-time"))
    if state["whale_alerts"]:
        speed_pts += 1; breakdown.append(("Whale Alert", "+1 speed", "on-chain"))
    if state["reddit_hot"]:
        speed_pts += 1; breakdown.append(("Reddit", "+1 speed", "social"))
    if state["twitter_feed"]:
        speed_pts += 2; breakdown.append(("X/Twitter", "+2 speed", "real-time"))

    live_sources = sum(1 for v in src.values() if v == "ok")
    acc_pts += min(4, live_sources * 1.5)
    if state["fear_greed"].get("value") is not None: acc_pts += 1.5
    if state["econ_calendar"]:   acc_pts += 1.5
    if state["reddit_hot"]:      acc_pts += 1.0
    if state["twitter_feed"]:    acc_pts += 1.0
    if src.get("newsapi") == "ok": acc_pts += 1.5

    state["system_score"] = {
        "speed":         min(10, round(speed_pts, 1)),
        "accuracy":      min(10, round(acc_pts, 1)),
        "overall":       min(10, round((min(10,speed_pts)*0.5 + min(10,acc_pts)*0.5), 1)),
        "sources_live":  live_sources,
        "sources_total": len(src),
        "breakdown":     breakdown,
    }


# FETCH ALL

def fetch_all(topics):
    combined = {}
    lock     = threading.Lock()

    def run_and_merge(fn, *args):
        try:
            result = fn(*args)
            with lock:
                combined.update(result)
        except Exception as e:
            log(f"fetch_all {fn.__name__}: {str(e)[:60]}")

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [
            ex.submit(run_and_merge, fetch_newsapi,     topics),
            ex.submit(run_and_merge, fetch_google_news, topics),
            ex.submit(run_and_merge, fetch_rss_sources, topics),
        ]
        for f in as_completed(futs, timeout=25):
            try: f.result()
            except Exception: pass

    return combined

def run_intelligence():
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [
            ex.submit(fetch_fear_greed),
            ex.submit(fetch_prices),
            ex.submit(fetch_econ_calendar),
            ex.submit(fetch_whale_alerts),
            ex.submit(fetch_reddit),
            ex.submit(fetch_twitter),
        ]
        for f in as_completed(futs, timeout=20):
            try: f.result()
            except Exception as e:
                log(f"Intel error: {str(e)[:60]}")
    calculate_system_score()


# TRACKER LOOP

def tracker_loop():
    global seen_ids, seen_titles
    last_topics = []
    last_day    = datetime.now().day
    log("Tracker ready. Waiting for topics...")

    while True:
        try:
            with state_lock:
                topics = list(state["topics"])

            if not topics:
                time.sleep(2)
                continue

            today = datetime.now().day
            if today != last_day:
                state["newsapi_calls_today"] = 0
                last_day = today

            if topics != last_topics:
                log(f"Topics changed to: {topics}")
                log("Seeding baseline...")
                seed = fetch_all(topics)
                with state_lock:
                    seen_ids    = set(seed.keys())
                    seen_titles = {normalize_title(v["title"]) for v in seed.values()}
                last_topics = list(topics)
                log(f"Baseline seeded: {len(seen_ids)} articles. Watching for NEW...\n")
                threading.Thread(target=run_intelligence, daemon=True).start()
                time.sleep(state["interval"])
                continue

            time.sleep(state["interval"])
            state["checks"] += 1
            log(f"Check #{state['checks']} | {', '.join(topics)}")

            if state["checks"] % 4 == 1:
                threading.Thread(target=run_intelligence, daemon=True).start()

            current  = fetch_all(topics)
            new_ones = {}
            with state_lock:
                for k, v in current.items():
                    nt = normalize_title(v["title"])
                    if k not in seen_ids and nt not in seen_titles:
                        new_ones[k] = v

            if new_ones:
                log(f"  [NEW] {len(new_ones)} article(s)!")
                with state_lock:
                    for uid, art in new_ones.items():
                        seen_ids.add(uid)
                        seen_titles.add(normalize_title(art["title"]))
                        state["new_articles"].append(art)
                        state["article_buffer"] = (state["article_buffer"] + [art])[-200:]
                        log(f"  [{art['impact'].upper()}][{art['sentiment']}]"
                            f" {art['source']} | {art['title'][:55]}")
                        if art.get("impact") in ("high","medium"):
                            threading.Thread(
                                target=send_telegram,
                                args=(art["title"], art["impact"],
                                      art.get("sentiment","neutral"), art.get("source","")),
                                daemon=True
                            ).start()

                recent = [a["title"] for a in state["new_articles"][-30:]]
                sigs   = detect_correlations(recent)
                if sigs:
                    state["correlations"] = sigs
                    for s in sigs:
                        log(f"  [CORRELATION] {s['cluster']}: {', '.join(s['keywords'])}")
            else:
                log("  No new articles this cycle.")

        except Exception as e:
            log(f"Tracker loop error: {str(e)[:80]}")
            log(traceback.format_exc()[-300:])
            time.sleep(5)


# FLASK

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False, max_age=3600)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

@app.route("/feed")
def feed():
    init = request.args.get("init") == "1"
    with state_lock:
        new  = list(state["new_articles"])
        state["new_articles"].clear()
        if init and state["article_buffer"]:
            # On page load/refresh, return the rolling buffer so feed isn't blank
            buf_titles = {a["title"] for a in new}
            for a in reversed(state["article_buffer"]):
                if a["title"] not in buf_titles:
                    new.append(a)
                    buf_titles.add(a["title"])
    return jsonify({
        "topics":              state["topics"],
        "interval":            state["interval"],
        "checks":              state["checks"],
        "sources":             state["sources"],
        "sound_on":            state["sound_on"],
        "newsapi_calls_today": state["newsapi_calls_today"],
        "new_articles":        new,
    })

@app.route("/set_topics", methods=["POST"])
def set_topics():
    data = request.get_json(silent=True) or {}
    new_topics = [t.strip().lower() for t in data.get("topics",[]) if t.strip()]
    with state_lock:
        state["topics"] = new_topics
    log(f"Topics updated: {new_topics}")
    return jsonify({"ok": True, "topics": new_topics})

@app.route("/set_sound", methods=["POST"])
def set_sound():
    data = request.get_json(silent=True) or {}
    with state_lock:
        state["sound_on"] = bool(data.get("sound_on", True))
    return jsonify({"ok": True})

@app.route("/set_interval", methods=["POST"])
def set_interval():
    data = request.get_json(silent=True) or {}
    try:
        ivl = max(5, int(data.get("interval", CHECK_INTERVAL)))
        with state_lock:
            state["interval"] = ivl
        return jsonify({"ok": True, "interval": ivl})
    except Exception:
        return jsonify({"ok": False}), 400

@app.route("/intelligence")
def intelligence():
    return jsonify({
        "fear_greed":    state["fear_greed"],
        "prices":        state["prices"],
        "econ_calendar": state["econ_calendar"],
        "whale_alerts":  state["whale_alerts"],
        "twitter_feed":  state["twitter_feed"],
        "reddit_hot":    state["reddit_hot"],
        "system_score":  state["system_score"],
        "correlations":  state.get("correlations", []),
    })

@app.route("/prices_live")
def prices_live():
    return jsonify(state.get("prices", {}))

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "time": now_iso(),
                    "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)})

@app.route("/telegram_test", methods=["POST"])
def telegram_test():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({"ok": False, "error": "Telegram not configured"}), 400
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID,
                  "text": "[!!] NewsScan test alert - connection working!"},
            timeout=8
        )
        return jsonify({"ok": r.status_code == 200, "status": r.status_code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/telegram_status")
def telegram_status():
    return jsonify({
        "configured":    bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "bot_token_set": bool(TELEGRAM_BOT_TOKEN),
        "chat_id_set":   bool(TELEGRAM_CHAT_ID),
    })

@app.route("/")
@app.route("/dashboard")
def index():
    headers = {
        "Content-Type":  "text/html; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    }
    for folder in [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]:
        path = os.path.join(folder, "crypto_dashboard.html")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read(), 200, headers
    return "<h2>Dashboard not found</h2><p>Put crypto_dashboard.html in same folder.</p>", 404


def run_server():
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)


def main():
    import os
    os.makedirs("logs", exist_ok=True)

    IS_CLOUD = bool(
        os.environ.get("RAILWAY_ENVIRONMENT") or
        os.environ.get("RENDER") or
        os.environ.get("DYNO") or
        os.environ.get("NEWSSCAN_CLOUD")
    )

    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 58)
    print("  NewsScan v8  -  Live Market Intelligence")
    print("=" * 58)

    if IS_CLOUD:
        raw    = os.environ.get("NEWSSCAN_TOPICS", "bitcoin,gold,oil,iran,israel,trump,federal reserve")
        topics = [t.strip().lower() for t in raw.split(",") if t.strip()]
        ivl    = int(os.environ.get("NEWSSCAN_INTERVAL", str(CHECK_INTERVAL)))
        print(f"  Mode     : Cloud")
    else:
        print()
        print("  Enter topics (comma-separated).")
        print()
        raw    = input("  Topics   : ").strip()
        topics = [t.strip().lower() for t in raw.split(",") if t.strip()] if raw else []
        try:
            iv  = input(f"  Interval in seconds (default {CHECK_INTERVAL}): ").strip()
            ivl = max(5, int(iv)) if iv else CHECK_INTERVAL
        except ValueError:
            ivl = CHECK_INTERVAL
        print(f"  Mode     : Local")

    with state_lock:
        state["topics"]   = topics
        state["interval"] = ivl

    try:
        import socket
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "YOUR_PC_IP"

    print(f"  Topics   : {', '.join(topics) if topics else '(add on dashboard)'}")
    print(f"  Interval : {ivl}s")
    print(f"  Port     : {DASHBOARD_PORT}")
    print()
    print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    if not IS_CLOUD:
        print(f"  WiFi     : http://{local_ip}:{DASHBOARD_PORT}")
    print()

    threading.Thread(target=run_server,   daemon=True).start()
    time.sleep(1)
    threading.Thread(target=tracker_loop, daemon=True).start()

    if not IS_CLOUD:
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")

    print("  Server running. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nStopping...")


if __name__ == "__main__":
    main()