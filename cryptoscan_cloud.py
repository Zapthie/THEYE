"""
NewsScan - Cloud Edition
========================
Deploy on Railway.app (free) for 24/7 access from anywhere.
Config via environment variables (set in Railway dashboard).

Requirements: see requirements.txt
Deploy: push to GitHub, connect to Railway
"""

import feedparser
import requests
import time
import threading
import os
import sys
import re
import logging
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

HAS_WINSOUND = False  # Cloud: no Windows sound

# --- CONFIG ------------------------------------------------------------------
import os as _os
NEWSAPI_KEY    = _os.environ.get("NEWSAPI_KEY",   "")  # optional - set in Railway
GROQ_API_KEY   = _os.environ.get("GROQ_API_KEY",  "")  # free at console.groq.com
TELEGRAM_BOT_TOKEN = _os.environ.get("TELEGRAM_BOT_TOKEN", "")  # from @BotFather
TELEGRAM_CHAT_ID   = _os.environ.get("TELEGRAM_CHAT_ID",   "")  # your chat ID
CHECK_INTERVAL = 15
DASHBOARD_PORT = int(_os.environ.get("PORT", 5000))

TRADINGVIEW_LINKS = [
    {"label": "BTC/USD",  "url": "BTCUSD"},
    {"label": "Gold",     "url": "XAUUSD"},
    {"label": "Oil/WTI",  "url": "USOIL"},
    {"label": "S&P 500",  "url": "SPX"},
    {"label": "EUR/USD",  "url": "EURUSD"},
]

RSS_SOURCES = [
    ("CoinTelegraph", [
        "https://cointelegraph.com/rss",
    ]),
    ("CoinDesk", [
        "https://www.coindesk.com/feed/",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]),
    ("Reuters", [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/financialNews",
    ]),
    ("MarketWatch", [
        "https://feeds.marketwatch.com/marketwatch/topstories/",
    ]),
    ("CNBC", [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
    ]),
    ("Investing.com", [
        "https://www.investing.com/rss/news.rss",
    ]),
    ("HindustanTimes", [
        "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
        "https://www.hindustantimes.com/feeds/rss/business/rssfeed.xml",
    ]),
]

# --- INTELLIGENCE SOURCES ---------------------------------------------------

WHALE_ALERT_RSS = "https://whale-alert.io/rss"

REDDIT_FEEDS = [
    ("r/CryptoCurrency", "https://www.reddit.com/r/CryptoCurrency/new/.rss?sort=new"),
    ("r/wallstreetbets",  "https://www.reddit.com/r/wallstreetbets/new/.rss?sort=new"),
    ("r/Bitcoin",         "https://www.reddit.com/r/Bitcoin/new/.rss?sort=new"),
    ("r/Economics",       "https://www.reddit.com/r/Economics/new/.rss?sort=new"),
]

# X/Twitter tracked via RSSHub bridges (defined in fetch_twitter)

# (account_handle, display_name, is_high_impact)
TWITTER_ACCOUNTS = [
    ("DeItaone",      "Walter Bloomberg",  True),   # fastest geopolitical/macro news
    ("federalreserve","Federal Reserve",   True),   # rate decisions
    ("POTUS",         "US President",      True),   # policy/tariffs/war
    ("cz_binance",    "CZ Binance",        True),   # crypto exchange moves
    ("saylor",        "Michael Saylor",    True),   # BTC whale signals
    ("whale_alert",   "Whale Alert",       False),  # on-chain moves backup
    ("CoinDesk",      "CoinDesk",          False),  # fast crypto news
    ("Reuters_Biz",   "Reuters Business",  False),  # macro news
]

# Price watch: symbol -> (CoinGecko id or Yahoo ticker, threshold % change)
PRICE_WATCH = {
    "BTC":  ("bitcoin",   1.5),
    "ETH":  ("ethereum",  1.5),
    "Gold": ("gold",      0.8),
    "Oil":  ("oil",       1.0),
}

# --- MARKET RELEVANCE --------------------------------------------------------


MIN_RELEVANCE_SCORE = 2

MARKET_MOVERS = {
    "bitcoin":5, "btc":5, "ethereum":5, "eth":5, "crypto":4, "cryptocurrency":4,
    "gold":5, "xau":5, "silver":4, "oil":5, "crude":5, "brent":5, "wti":5,
    "s&p":5, "nasdaq":5, "dow jones":5, "stock market":5, "equities":4,
    "forex":4, "dollar":4, "usd":4, "eur":3, "yuan":3, "yen":3,
    "commodities":4, "futures":4, "options":4, "derivatives":3,
    "federal reserve":5, "fed reserve":5, "fomc":5, "interest rate":5,
    "rate hike":5, "rate cut":5, "inflation":5, "cpi":5, "ppi":4,
    "monetary policy":5, "quantitative":4, "powell":4, "yellen":4,
    "ecb":4, "bank of england":4, "rbi":3, "pboc":4,
    "sec":5, "cftc":4, "etf":5, "spot etf":5, "approval":3,
    "regulation":4, "ban":4, "banned":4, "lawsuit":4, "settlement":3,
    "investigation":3, "fraud":4, "hack":5, "hacked":5,
    "exploit":5, "breach":4, "stolen":4, "rug pull":5,
    "recession":5, "gdp":4, "unemployment":4, "jobs report":5,
    "nonfarm":5, "trade war":4, "tariff":4, "sanctions":4,
    "opec":5, "supply cut":5, "production cut":5, "energy":3,
    "debt ceiling":4, "treasury":4, "bond yield":5, "yield curve":5,
    "bankruptcy":4, "collapse":4, "bailout":4, "default":4,
    "elon musk":4, "trump":4, "xi jinping":3, "putin":3,
    "warren buffett":3, "blackrock":4, "jpmorgan":4, "goldman":4,
    "binance":5, "coinbase":5, "kraken":4, "bybit":4,
    "nyse":4, "cboe":4,
    "rally":3, "surge":3, "dump":3, "crash":4, "pump":3,
    "bull":2, "bear":2, "correction":3, "breakout":3,
    "market cap":3, "liquidity":3, "volatility":4, "vix":5,
}

NOISE_REDUCERS = {
    # Pure noise - not market moving
    "sports":-4, "celebrity":-4, "entertainment":-4,
    "obituary":-4, "cricket":-3, "football":-3, "bollywood":-4,
    "climate":-2, "weather":-2, "earthquake":-2,
    "hospital":-1,
    # NOTE: war/geopolitical news kept neutral or positive
    # because Iran/Israel/US conflict directly moves oil, gold, BTC
}

# Geopolitical movers - war news IS market news (adds to score)
GEO_MOVERS = {
    "iran":4, "israel":4, "war":4, "strike":3, "attack":4,
    "missile":4, "nuclear":5, "airstrike":4, "khamenei":5,
    "middle east":4, "oil supply":5, "strait of hormuz":5,
    "hamas":3, "hezbollah":3, "ukraine":3, "russia":3,
    "trump":3, "sanctions":4, "ceasefire":3, "escalation":4,
    "retaliation":4, "killed":2, "military":2, "troops":2,
    "conflict":3, "tension":3, "crisis":4, "emergency":3,
    "truth social":3, "executive order":4, "tariff":4,
}

def market_relevance_score(title):
    tl = title.lower()
    score, reasons = 0, []
    for kw, pts in MARKET_MOVERS.items():
        if kw in tl:
            score += pts
            reasons.append(kw)
    for kw, pts in GEO_MOVERS.items():
        if kw in tl:
            score += pts
            if kw not in reasons:
                reasons.append(kw)
    for kw, pts in NOISE_REDUCERS.items():
        if kw in tl:
            score += pts
    return score, reasons[:5]

def is_market_relevant(title):
    return market_relevance_score(title)[0] >= MIN_RELEVANCE_SCORE

# --- SENTIMENT ---------------------------------------------------------------

BULLISH_WORDS = {
    "surge":3, "surges":3, "rally":3, "rallies":3, "soar":3,
    "jump":2, "gain":2, "gains":2, "rise":2, "rises":2, "risen":2,
    "breakout":3, "bull":2, "bullish":3, "boom":3, "record high":4,
    "all-time high":4, "ath":4, "approval":3, "approved":3,
    "adoption":3, "accumulate":2, "recovery":2, "rebound":2,
    "upgrade":2, "beat":2, "outperform":2, "growth":2, "profit":2,
}

BEARISH_WORDS = {
    "crash":3, "crashes":3, "dump":3, "drop":2, "drops":2,
    "fall":2, "falls":2, "decline":2, "declines":2, "plunge":3,
    "selloff":3, "sell-off":3, "bear":2, "bearish":3,
    "ban":3, "banned":3, "hack":3, "hacked":3, "stolen":3,
    "lawsuit":2, "investigation":2, "fraud":3, "scam":3,
    "collapse":3, "bankrupt":3, "default":3, "recession":3,
    "rate hike":2, "delisted":3, "suspended":3, "warning":2,
    "loss":2, "losses":2, "downgrade":2,
}

def classify_sentiment(title):
    tl = title.lower()
    b  = sum(v for k, v in BULLISH_WORDS.items() if k in tl)
    br = sum(v for k, v in BEARISH_WORDS.items() if k in tl)
    if b == 0 and br == 0: return "neutral"
    if b > br:  return "bullish"
    if br > b:  return "bearish"
    return "neutral"

# --- IMPACT ------------------------------------------------------------------

HIGH_WORDS = [
    "sec", "ban", "banned", "hack", "hacked", "crash", "crashed", "etf",
    "lawsuit", "seized", "arrested", "breach", "exploit", "rug pull", "scam",
    "collapse", "bankrupt", "bankruptcy", "emergency", "suspended", "delisted",
    "rate hike", "rate cut", "fomc", "cpi", "nonfarm", "opec",
]
MEDIUM_WORDS = [
    "elon", "fed", "federal reserve", "regulation", "tariff", "inflation",
    "recession", "interest rate", "treasury", "sanction", "binance",
    "coinbase", "blackrock", "rally", "dump",
]

def classify_impact(title):
    t = title.lower()
    if any(w in t for w in HIGH_WORDS):   return "high"
    if any(w in t for w in MEDIUM_WORDS): return "medium"
    return "low"

# --- DATE PARSING ------------------------------------------------------------

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

def is_recent_enough(pub_str, max_hours=6):
    dt = parse_published(pub_str)
    if not dt:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=max_hours)

def published_iso(pub_str):
    dt = parse_published(pub_str)
    return dt.isoformat() if dt else datetime.now(timezone.utc).isoformat()

# --- STATE -------------------------------------------------------------------

state = {
    "topics":              [],
    "interval":            CHECK_INTERVAL,
    "checks":              0,
    "new_articles":        [],
    "sound_on":            True,
    "newsapi_calls_today": 0,
    "sources": {
        "newsapi":  "wait",
        "google":   "wait",
        "coindesk": "wait",
    },
    # Intelligence panels
    "fear_greed": {
        "value": None, "label": "", "updated": ""
    },
    "prices": {},          # symbol -> {price, change_pct, alert}
    "econ_calendar": [],   # upcoming events
    "whale_alerts": [],    # recent whale txns
    "twitter_feed": [],    # tweets from key accounts
    "_cf_proc":     None,          # cloudflare tunnel process handle
    "reddit_hot": [],      # hot reddit posts
    # System score
    "system_score": {
        "speed": 0, "accuracy": 0, "overall": 0,
        "sources_live": 0, "sources_total": 0,
        "breakdown": []
    },
}

seen_ids          = set()
seen_titles       = set()
seen_price_alerts = set()   # dedup: "BTC_down_2.0" so same spike doesn't fire every cycle
state_lock        = threading.Lock()

# --- HELPERS -----------------------------------------------------------------

def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

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

# -- Telegram alert --
_tg_last_sent = {}   # title -> timestamp, to avoid duplicate alerts

def send_telegram(title, impact, sentiment, source):
    """Send alert to Telegram. Called in background thread."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    # Deduplicate: don't send same headline twice within 10 minutes
    now = time.time()
    key = title[:60]
    if now - _tg_last_sent.get(key, 0) < 600:
        return
    _tg_last_sent[key] = now

    # Build message
    impact_icon = {"high": "[!!]", "medium": "[!]", "low": "[i]"}.get(impact, "[i]")
    sent_icon   = {"bullish": "[UP]", "bearish": "[DN]", "neutral": "[--]"}.get(sentiment, "[--]")
    msg = (
        f"{impact_icon} *{impact.upper()} IMPACT*  {sent_icon} {sentiment.upper()}\n"
        f"[SRC] {source}\n\n"
        f"{title}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=8
        )
    except Exception as e:
        log(f"  Telegram error: {str(e)[:60]}")

def do_beep(impact):
    pass  # Browser-side audio only (see dashboard JS)

# --- TOPIC MATCHING ----------------------------------------------------------

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

# --- SOURCE: NewsAPI ---------------------------------------------------------

def fetch_newsapi(topics):
    articles = {}

    if not NEWSAPI_KEY:
        state["sources"]["newsapi"] = "wait"
        return articles

    if state["newsapi_calls_today"] >= 80:
        state["sources"]["newsapi"] = "limit"
        log("NewsAPI: daily quota reached (80). Resets at midnight.")
        return articles

    def in_chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    for batch in in_chunks(topics, 2):
        query = " OR ".join(f'"{t}"' for t in batch)
        for attempt in range(3):
            try:
                url = (
                    "https://newsapi.org/v2/everything"
                    f"?q={requests.utils.quote(query)}"
                    "&sortBy=publishedAt&language=en&pageSize=20"
                    f"&apiKey={NEWSAPI_KEY}"
                )
                r = requests.get(url, timeout=10)
                state["newsapi_calls_today"] += 1

                if r.status_code == 429:
                    log("NewsAPI: 429 - daily rate limit reached (100 req/day on free tier)")
                    state["sources"]["newsapi"] = "limit"
                    return articles

                data = r.json()
                if data.get("status") != "ok":
                    log(f"NewsAPI: {data.get('code','')} - {data.get('message','')}")
                    state["sources"]["newsapi"] = "err"
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
                break

            except requests.exceptions.ConnectionError:
                log(f"NewsAPI: connection error (attempt {attempt+1}/3)")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    state["sources"]["newsapi"] = "err"
            except Exception as e:
                log(f"NewsAPI: {e}")
                state["sources"]["newsapi"] = "err"
                break

    return articles

# --- SOURCE: Google News RSS --------------------------------------------------

def fetch_google_news(topics):
    articles = {}
    lock     = threading.Lock()
    any_ok   = [False]

    def fetch_one(topic):
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
                            articles[uid] = make_article(
                                title, link, "Google News", topic, pub
                            )
                if feed.entries:
                    any_ok[0] = True
                return
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                else:
                    log(f"Google News ({topic}): {str(e)[:50]}")

    threads = [
        threading.Thread(target=fetch_one, args=(t,), daemon=True)
        for t in topics
    ]
    for th in threads: th.start()
    for th in threads: th.join(timeout=15)

    state["sources"]["google"] = "ok" if any_ok[0] else "err"
    return articles

# --- SOURCE: Multi RSS -------------------------------------------------------

def fetch_rss_sources(topics):
    articles    = {}
    src_results = {}
    lock        = threading.Lock()

    def fetch_source(name, urls):
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
            return

        found = 0
        for entry in feed.entries:
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
                    found += 1

        src_results[name] = "ok"

    threads = [
        threading.Thread(target=fetch_source, args=(name, urls), daemon=True)
        for name, urls in RSS_SOURCES
    ]
    for th in threads: th.start()
    for th in threads: th.join(timeout=15)

    # CoinDesk UI badge = OK if CoinDesk or CoinTelegraph worked
    coin_ok = any(src_results.get(n) == "ok" for n in ("CoinDesk","CoinTelegraph"))
    state["sources"]["coindesk"] = "ok" if coin_ok else "err"

    working = [k for k,v in src_results.items() if v == "ok"]
    failed  = [k for k,v in src_results.items() if v == "err"]
    if working: log(f"  RSS OK : {', '.join(working)}")
    if failed:  log(f"  RSS ERR: {', '.join(failed)}")

    return articles

# --- INTELLIGENCE: Fear & Greed Index ---------------------------------------

def fetch_fear_greed():
    """Fear & Greed index - tries multiple sources."""
    sources = [
        "https://api.alternative.me/fng/?limit=1",
        "https://api.alternative.me/fng/",   # alternate path
    ]
    for url in sources:
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            entry = data["data"][0]
            state["fear_greed"] = {
                "value":   int(entry["value"]),
                "label":   entry["value_classification"],
                "updated": entry["timestamp"],
            }
            log(f"  Fear & Greed: {entry['value']} ({entry['value_classification']})")
            return
        except Exception:
            continue
    # If all sources fail, keep whatever we had (or leave as-is)
    log("  Fear & Greed: all sources unreachable (network block?)")

# --- INTELLIGENCE: Price Spike Detector --------------------------------------

def fetch_prices():
    """Fetch BTC/ETH prices - tries CoinGecko, then CoinCap fallback."""

    def _parse_and_store(prices_raw):
        """prices_raw = {sym: {price, change_pct}}"""
        alerts = []
        for sym, info in prices_raw.items():
            threshold = 1.5
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
                "title":         title,
                "link":          "https://www.coingecko.com",
                "source":        "Price Spike",
                "topic":         "price alert",
                "published":     "",
                "published_iso": now_iso(),
                "found_at":      now_iso(),
                "relevance":     10,
                "reasons":       ["price spike"],
                "sentiment":     "bullish" if direction == "surged" else "bearish",
                "impact":        "high",
            }
            with state_lock:
                state["new_articles"].append(art)
            threading.Thread(
                target=send_telegram,
                args=(title, "high",
                      "bullish" if direction == "surged" else "bearish",
                      "Binance Price Alert"),
                daemon=True
            ).start()

    # --- Source 1: CoinGecko ---
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

    # --- Source 2: CoinCap (no rate limits, no key) ---
    try:
        raw = {}
        for asset_id, sym in [("bitcoin","BTC"), ("ethereum","ETH")]:
            r2 = requests.get(
                f"https://api.coincap.io/v2/assets/{asset_id}",
                timeout=10
            )
            if r2.status_code == 200:
                d = r2.json().get("data", {})
                price  = float(d.get("priceUsd", 0))
                change = float(d.get("changePercent24Hr", 0) or 0)
                raw[sym] = {"price": price, "change_pct": change}
        if raw:
            _parse_and_store(raw)
            log(f"  Prices (CoinCap): BTC=${raw.get('BTC',{}).get('price',0):,.0f}")
            return
    except Exception as e:
        log(f"  CoinCap error: {str(e)[:40]}")

    log("  Prices: all sources unreachable (network blocked?)")

# --- INTELLIGENCE: Economic Calendar -----------------------------------------

def fetch_econ_calendar():
    """
    Hardcoded high-impact scheduled events for the next 30 days.
    Real-time calendar would need a paid API - this covers the key dates.
    We use a free investing.com RSS as supplement.
    """
    now = datetime.now(timezone.utc)
    # Static high-impact events - update monthly
    events = [
        {"date": "2026-03-07", "event": "US Non-Farm Payrolls",    "impact": "high"},
        {"date": "2026-03-12", "event": "US CPI Inflation Report", "impact": "high"},
        {"date": "2026-03-19", "event": "FOMC Meeting",            "impact": "high"},
        {"date": "2026-03-20", "event": "Fed Rate Decision",       "impact": "high"},
        {"date": "2026-03-28", "event": "US GDP Q4 Final",         "impact": "medium"},
        {"date": "2026-04-02", "event": "OPEC Meeting",            "impact": "high"},
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

# --- INTELLIGENCE: Whale Alerts RSS ------------------------------------------

def fetch_whale_alerts():
    """Whale-alert.io free RSS - large crypto transactions."""
    try:
        feed = feedparser.parse(WHALE_ALERT_RSS)
        whales = []
        for entry in feed.entries[:6]:
            title = entry.get("title", "")
            link  = entry.get("link", "#")
            pub   = entry.get("published", "")
            if not is_recent_enough(pub, max_hours=2): continue
            whales.append({"title": title, "link": link, "pub": pub})
            # Also inject into main feed if big enough
            if any(x in title.lower() for x in ["million", "billion", "btc", "eth", "usdt"]):
                art = {
                    "title":         f"[WHALE] {title}",
                    "link":          link,
                    "source":        "Whale Alert",
                    "topic":         "whale move",
                    "published":     pub,
                    "published_iso": published_iso(pub),
                    "found_at":      now_iso(),
                    "relevance":     8,
                    "reasons":       ["whale transaction"],
                    "sentiment":     "neutral",
                    "impact":        "medium",
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

# --- INTELLIGENCE: Reddit Hot Posts ------------------------------------------

def fetch_twitter():
    """
    Fetch from X/Twitter accounts using multiple RSS bridges.
    Primary: RSSHub public instances (reliable, maintained)
    Fallback: rsshub.app, nitter mirrors
    Also fetches Trump Truth Social RSS (no X account needed).
    """
    tweets = []

    # RSS bridges for X/Twitter - tried in order until one works
    XRSS_BRIDGES = [
        "https://rsshub.app/twitter/user/{handle}",
        "https://rsshub.rssforever.com/twitter/user/{handle}",
        "https://hub.slarker.me/twitter/user/{handle}",
        # Nitter fallbacks
        "https://nitter.privacydev.net/{handle}/rss",
        "https://nitter.poast.org/{handle}/rss",
        "https://nitter.net/{handle}/rss",
    ]

    def inject_article(handle, display_name, title, link, pub):
        """Inject high-impact tweet directly into main news feed."""
        art = {
            "title":         f"[X] @{handle}: {title[:120]}",
            "link":          link,
            "source":        f"X/@{handle}",
            "topic":         "twitter",
            "published":     pub,
            "published_iso": published_iso(pub),
            "found_at":      now_iso(),
            "relevance":     8,
            "reasons":       ["twitter", handle],
            "sentiment":     classify_sentiment(title),
            "impact":        classify_impact(title),
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
                if not feed.entries:
                    continue
                got = 0
                for entry in feed.entries[:5]:
                    title = entry.get("title", "").strip()
                    link  = entry.get("link", "#")
                    pub   = entry.get("published", "")
                    if not title or title.lower().startswith("rt "):
                        continue
                    if not is_recent_enough(pub, max_hours=3):
                        continue
                    tweets.append({
                        "title":       title[:200],
                        "link":        link,
                        "source":      f"@{handle}",
                        "display":     display_name,
                        "pub":         pub,
                        "high_impact": is_high_impact,
                    })
                    if is_high_impact and is_market_relevant(title):
                        inject_article(handle, display_name, title, link, pub)
                    got += 1
                if got:
                    return  # success
            except Exception:
                continue
        # All bridges failed for this handle - silent

    def fetch_truth_social():
        """Trump Truth Social RSS - he posts there more than X now."""
        try:
            url  = "https://truthsocial.com/@realDonaldTrump.rss"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "#")
                pub   = entry.get("published", "")
                if not title or not is_recent_enough(pub, max_hours=3):
                    continue
                tweets.append({
                    "title":       title[:200],
                    "link":        link,
                    "source":      "@realDonaldTrump",
                    "display":     "Trump (Truth Social)",
                    "pub":         pub,
                    "high_impact": True,
                })
                if is_market_relevant(title):
                    inject_article("realDonaldTrump", "Trump", title, link, pub)
        except Exception as e:
            log(f"  TruthSocial error: {str(e)[:50]}")

    # Run all account fetches + Truth Social in parallel
    threads = [
        threading.Thread(target=try_account, args=(h, d, hi), daemon=True)
        for h, d, hi in TWITTER_ACCOUNTS
    ]
    threads.append(threading.Thread(target=fetch_truth_social, daemon=True))
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)

    tweets.sort(key=lambda x: x.get("pub", ""), reverse=True)
    state["twitter_feed"] = tweets[:20]
    if tweets:
        log(f"  X/Social: {len(tweets)} posts from tracked accounts")
    else:
        log("  X/Social: no posts fetched (RSS bridges may be blocked)")

def fetch_reddit():
    """Reddit RSS - no auth needed, free."""
    posts = []
    headers = {"User-Agent": "NewsScan/6.0 (market news aggregator)"}
    for name, url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "")
                link  = entry.get("link", "#")
                pub   = entry.get("published", "")
                if not is_recent_enough(pub, max_hours=4): continue
                if not is_market_relevant(title): continue
                posts.append({
                    "title":   title,
                    "link":    link,
                    "source":  name,
                    "pub":     pub,
                })
        except Exception as e:
            log(f"  Reddit {name} error: {str(e)[:40]}")
    state["reddit_hot"] = posts[:10]
    if posts: log(f"  Reddit: {len(posts)} relevant posts")

# --- SYSTEM SCORE CALCULATOR -------------------------------------------------

def calculate_system_score():
    """
    Score out of 10 for Speed and Accuracy based on:
    - How many sources are live
    - Which high-quality sources are working
    - NewsAPI status
    - Whether intel feeds are responding
    """
    src = state["sources"]

    # Speed scoring (0-10)
    # Reuters/CoinTelegraph = fastest (2-5min), Google = medium (5-15min), NewsAPI free = slow
    speed_pts = 0
    breakdown = []

    # RSS sources - check via coindesk proxy (CoinTelegraph counts here)
    if src.get("coindesk") == "ok":
        speed_pts += 3
        breakdown.append(("CoinDesk/CoinTelegraph RSS", "+3 speed", "fast 2-5 min"))
    if src.get("google") == "ok":
        speed_pts += 2
        breakdown.append(("Google News RSS", "+2 speed", "5-15 min delay"))
    if src.get("newsapi") == "ok":
        speed_pts += 1
        breakdown.append(("NewsAPI", "+1 speed", "15+ min delay on free"))
    elif src.get("newsapi") == "limit":
        breakdown.append(("NewsAPI", "0 speed", "daily limit reached"))
    # Price spike detector
    if state["prices"]:
        speed_pts += 2
        breakdown.append(("Price Spike Detector", "+2 speed", "real-time CoinGecko"))
    # Whale alerts
    if state["whale_alerts"]:
        speed_pts += 1
        breakdown.append(("Whale Alert", "+1 speed", "on-chain real-time"))
    # Reddit
    if state["reddit_hot"]:
        speed_pts += 1
        breakdown.append(("Reddit", "+1 speed", "social early signal"))

    speed_score = min(10, speed_pts)

    # Accuracy scoring (0-10)
    acc_pts = 0
    live_sources = sum(1 for v in src.values() if v == "ok")

    acc_pts += min(4, live_sources * 1.5)  # up to 4 pts for source diversity
    if state["fear_greed"].get("value") is not None:
        acc_pts += 1.5
        breakdown.append(("Fear & Greed Index", "+1.5 accuracy", "market sentiment context"))
    if state["econ_calendar"]:
        acc_pts += 1.5
        breakdown.append(("Economic Calendar", "+1.5 accuracy", "scheduled event awareness"))
    if state["reddit_hot"]:
        acc_pts += 1
        breakdown.append(("Reddit Sentiment", "+1 accuracy", "retail crowd signal"))
    if state.get("twitter_feed"):
        speed_pts += 2
        acc_pts += 1
        breakdown.append(("Twitter/@accounts", "+2 speed +1 acc", "real-time social signal"))
    if src.get("newsapi") == "ok":
        acc_pts += 1.5
        breakdown.append(("NewsAPI", "+1.5 accuracy", "verified news source"))

    acc_score    = min(10, round(acc_pts, 1))
    speed_score  = min(10, round(speed_score, 1))
    overall      = round((speed_score * 0.5 + acc_score * 0.5), 1)

    state["system_score"] = {
        "speed":         speed_score,
        "accuracy":      acc_score,
        "overall":       overall,
        "sources_live":  live_sources,
        "sources_total": len(src),
        "breakdown":     breakdown,
    }

# --- FETCH ALL ---------------------------------------------------------------

def fetch_all(topics):
    combined = {}
    combined.update(fetch_newsapi(topics))
    combined.update(fetch_google_news(topics))
    combined.update(fetch_rss_sources(topics))
    return combined

def run_intelligence():
    """Run all intelligence feeds in parallel - separate from main news loop."""
    threads = [
        threading.Thread(target=fetch_fear_greed,   daemon=True),
        threading.Thread(target=fetch_prices,        daemon=True),
        threading.Thread(target=fetch_econ_calendar, daemon=True),
        threading.Thread(target=fetch_whale_alerts,  daemon=True),
        threading.Thread(target=fetch_reddit,        daemon=True),
        threading.Thread(target=fetch_twitter,       daemon=True),
    ]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)
    calculate_system_score()

# --- TRACKER LOOP ------------------------------------------------------------

def tracker_loop():
    global seen_ids, seen_titles
    last_topics = []
    last_day    = datetime.now().day

    log("Tracker ready. Waiting for topics...")

    while True:
        with state_lock:
            topics = list(state["topics"])

        if not topics:
            time.sleep(2)
            continue

        # Midnight reset for NewsAPI counter
        today = datetime.now().day
        if today != last_day:
            state["newsapi_calls_today"] = 0
            last_day = today
            log("NewsAPI daily counter reset.")

        # Re-seed when topics change
        if topics != last_topics:
            log(f"Topics changed to: {topics}")
            log("Seeding baseline (existing articles will be ignored)...")
            seed = fetch_all(topics)
            with state_lock:
                seen_ids    = set(seed.keys())
                seen_titles = {normalize_title(v["title"]) for v in seed.values()}
            last_topics = list(topics)
            log(f"Baseline seeded: {len(seen_ids)} articles. Now watching for NEW ones...\n")
            # Run intel feeds immediately on first seed
            threading.Thread(target=run_intelligence, daemon=True).start()
            time.sleep(state["interval"])
            continue

        time.sleep(state["interval"])
        state["checks"] += 1
        log(f"Check #{state['checks']} | {', '.join(topics)}")

        # Run intelligence feeds every 5 checks (~75s at 15s interval)
        if state["checks"] % 5 == 1:
            threading.Thread(target=run_intelligence, daemon=True).start()

        current = fetch_all(topics)

        with state_lock:
            new_ones = {}
            for k, v in current.items():
                nt = normalize_title(v["title"])
                if k not in seen_ids and nt not in seen_titles:
                    new_ones[k] = v

        if new_ones:
            log(f"  [NEW] {len(new_ones)} NEW article(s)!")
            with state_lock:
                for uid, art in new_ones.items():
                    seen_ids.add(uid)
                    seen_titles.add(normalize_title(art["title"]))
                    state["new_articles"].append(art)
                    log(f"  [{art['impact'].upper()}][{art['sentiment']}]"
                        f"[{art['source']}] {art['title'][:55]}")
                    # Telegram alert for high/medium impact articles
                    if art.get("impact") in ("high", "medium"):
                        threading.Thread(
                            target=send_telegram,
                            args=(art["title"], art["impact"],
                                  art.get("sentiment","neutral"), art.get("source","")),
                            daemon=True
                        ).start()
        else:
            log("  No new articles this cycle.")

# --- FLASK -------------------------------------------------------------------

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False, max_age=3600)

# Auto-start background tracker when deployed to cloud (gunicorn/Railway)
def _cloud_autostart():
    """Starts news tracker automatically - no interactive input needed in cloud."""
    import time as _t2
    _t2.sleep(3)
    raw = _os.environ.get("NEWSSCAN_TOPICS",
          "bitcoin,gold,oil,iran,israel,trump,federal reserve,stock market,bitcoin etf")
    topics = [t.strip().lower() for t in raw.split(",") if t.strip()]
    ivl    = int(_os.environ.get("NEWSSCAN_INTERVAL", str(CHECK_INTERVAL)))
    with state_lock:
        state["topics"]   = topics
        state["interval"] = ivl
    log(f"Cloud autostart: {len(topics)} topics, interval={ivl}s")
    threading.Thread(target=tracker_loop,   daemon=True).start()
    threading.Thread(target=run_intelligence, daemon=True).start()

threading.Thread(target=_cloud_autostart, daemon=True).start()
logging.getLogger("werkzeug").setLevel(logging.ERROR)

@app.route("/feed")
def feed():
    with state_lock:
        new = list(state["new_articles"])
        state["new_articles"].clear()
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
    log(f"Topics updated via dashboard: {new_topics}")
    return jsonify({"ok": True, "topics": new_topics})

@app.route("/set_sound", methods=["POST"])
def set_sound():
    data = request.get_json(silent=True) or {}
    state["sound_on"] = bool(data.get("sound_on", True))
    log(f"Sound {'ON' if state['sound_on'] else 'OFF'}")
    return jsonify({"ok": True, "sound_on": state["sound_on"]})

@app.route("/tradingview_links")
def tradingview_links():
    return jsonify(TRADINGVIEW_LINKS)

@app.route("/intelligence")
def intelligence():
    """All intelligence data in one call."""
    return jsonify({
        "fear_greed":    state["fear_greed"],
        "prices":        state["prices"],
        "econ_calendar": state["econ_calendar"],
        "whale_alerts":  state["whale_alerts"],
        "reddit_hot":    state["reddit_hot"],
        "twitter_feed":  state["twitter_feed"],
        "system_score":  state["system_score"],
    })

def _builtin_summarize(articles, fg_value, fg_label, btc_price):
    """
    Built-in extractive summarizer - no API, no internet, always works.
    Scores sentences by keyword frequency and market relevance.
    """
    if not articles:
        return "No articles to analyze yet."

    # Count keyword frequency across all headlines
    from collections import Counter
    all_words = []
    for a in articles:
        words = re.findall(r"\b[a-z]{4,}\b", a["title"].lower())
        all_words.extend(words)
    freq = Counter(all_words)

    # Score each article by sum of word frequencies (tf-idf lite)
    def score(a):
        words = re.findall(r"\b[a-z]{4,}\b", a["title"].lower())
        return sum(freq[w] for w in words) + (5 if a.get("impact") == "high" else 0)

    ranked   = sorted(articles, key=score, reverse=True)
    bullish  = [a for a in articles if a.get("sentiment") == "bullish"]
    bearish  = [a for a in articles if a.get("sentiment") == "bearish"]
    high_imp = [a for a in articles if a.get("impact")    == "high"]

    lines = []
    lines.append(f"=== MARKET BRIEFING ({datetime.now().strftime('%H:%M')}) ===")
    lines.append(f"Fear & Greed: {fg_value} ({fg_label})  |  BTC: {btc_price}")
    lines.append(f"Analyzed {len(articles)} headlines  |  "
                 f"Bullish: {len(bullish)}  Bearish: {len(bearish)}  High-impact: {len(high_imp)}")
    lines.append("")

    lines.append("** OVERALL MOOD **")
    if len(bullish) > len(bearish) * 1.3:
        lines.append("Leaning BULLISH - majority of signals positive.")
    elif len(bearish) > len(bullish) * 1.3:
        lines.append("Leaning BEARISH - majority of signals negative.")
    else:
        lines.append("MIXED - bulls and bears roughly balanced.")
    lines.append("")

    if high_imp:
        lines.append("** HIGH IMPACT EVENTS **")
        for a in high_imp[:3]:
            lines.append(f"- {a['title']}")
        lines.append("")

    if bearish:
        lines.append("** KEY RISKS **")
        for a in bearish[:3]:
            lines.append(f"- {a['title']}")
        lines.append("")

    if bullish:
        lines.append("** OPPORTUNITIES **")
        for a in bullish[:3]:
            lines.append(f"- {a['title']}")
        lines.append("")

    lines.append("** TOP STORIES BY RELEVANCE **")
    for a in ranked[:5]:
        lines.append(f"- [{a.get('source','')}] {a['title']}")

    return "\n".join(lines)


@app.route("/ai_summary", methods=["POST"])
def ai_summary():
    """
    AI market summary - tries multiple free options in order:
    1. Groq API (FREE - 14400 tokens/day, no credit card - groq.com)
    2. Ollama local (FREE - if installed locally - ollama.ai)
    3. Built-in Python summarizer (FREE - always works, no internet needed)
    """
    try:
        body        = request.get_json()
        prompt_text = body.get("prompt", "")
        fg_value    = body.get("fg_value",  "-")
        fg_label    = body.get("fg_label",  "Unknown")
        btc_price   = body.get("btc_price", "Unknown")

        if not prompt_text:
            return jsonify({"error": "No prompt"}), 400

        # -- Option 1: Groq API (completely free, 14400 tokens/day, no card) --
        groq_key = GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model":      "llama-3.1-8b-instant",  # free, fast
                        "max_tokens": 800,
                        "messages": [
                            {"role": "system", "content":
                                "You are a sharp financial markets analyst. "
                                "Give concise, actionable market briefings. No fluff."},
                            {"role": "user", "content": prompt_text},
                        ],
                    },
                    timeout=20,
                )
                data = r.json()
                if "choices" in data and data["choices"]:
                    text = data["choices"][0]["message"]["content"]
                    return jsonify({"text": text, "engine": "Groq/Llama-3.1"})
                log(f"  Groq error: {str(data)[:150]}")
            except Exception as e:
                log(f"  Groq failed: {str(e)[:60]}")

        # -- Option 2: Ollama local (free, private, if installed) --
        try:
            r = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3.2", "prompt": prompt_text, "stream": False},
                timeout=30,
            )
            if r.status_code == 200:
                text = r.json().get("response", "")
                if text:
                    return jsonify({"text": text, "engine": "Ollama/local"})
        except Exception:
            pass  # Ollama not installed - silent fallback

        # -- Option 3: Built-in Python summarizer (always works) --
        arts = state.get("new_articles", [])[-40:]
        text = _builtin_summarize(arts, fg_value, fg_label, btc_price)
        note = (
            "\n\n---\n"
            "Using built-in summarizer (no AI API needed).\n"
            "For real AI analysis: get a FREE Groq key at groq.com\n"
            "Then set GROQ_API_KEY in cryptoscan.py"
        )
        return jsonify({"text": text + note, "engine": "built-in"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/prices_live")
def prices_live():
    """Fast price endpoint - returns current cached prices."""
    return jsonify(state.get("prices", {}))

@app.route("/")
@app.route("/dashboard")
def index():
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Frame-Options": "SAMEORIGIN",
    }
    for folder in [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]:
        path = os.path.join(folder, "crypto_dashboard.html")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read(), 200, headers
    return (
        "<h2>Dashboard not found</h2>"
        "<p>Put <b>crypto_dashboard.html</b> in the same folder as <b>cryptoscan.py</b></p>"
    ), 404

@app.route("/ping")
def ping():
    """Health check."""
    return jsonify({"status": "ok", "time": now_iso(),
                    "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)})

@app.route("/telegram_test", methods=["POST"])
def telegram_test():
    """Send a test Telegram message to verify setup."""
    if not TELEGRAM_BOT_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN not set in environment variables"}), 400
    if not TELEGRAM_CHAT_ID:
        return jsonify({"error": "TELEGRAM_CHAT_ID not set in environment variables"}), 400
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID,
                  "text": "*NewsScan alerts are working!*\n\nYou will receive high-impact news alerts here.",
                  "parse_mode": "Markdown"},
            timeout=8
        )
        if r.status_code == 200:
            return jsonify({"ok": True, "message": "Test message sent!"})
        return jsonify({"error": f"Telegram API error: {r.text[:100]}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500

@app.route("/telegram_status")
def telegram_status():
    """Check if Telegram is configured."""
    return jsonify({
        "configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "has_token":   bool(TELEGRAM_BOT_TOKEN),
        "has_chat_id": bool(TELEGRAM_CHAT_ID),
    })

def run_server():
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)

# (No tunnel needed - Railway provides public URL)


def main():
    """Cloud entry point - reads topics from env var, no interactive prompts."""
    import os
    os.makedirs("logs", exist_ok=True)

    print("=" * 55)
    print("  NewsScan - Cloud Edition")
    print("=" * 55)

    # Topics from env var (comma separated) or defaults
    raw_topics = os.environ.get("NEWSSCAN_TOPICS", "bitcoin,gold,oil,iran,israel,trump,federal reserve")
    topics = [t.strip().lower() for t in raw_topics.split(",") if t.strip()]

    interval = int(os.environ.get("NEWSSCAN_INTERVAL", CHECK_INTERVAL))

    with state_lock:
        state["topics"]   = topics
        state["interval"] = interval

    print(f"  Topics   : {', '.join(topics)}")
    print(f"  Interval : {interval}s")
    print(f"  Port     : {DASHBOARD_PORT}")
    print(f"  Sound    : disabled (cloud)")
    print()

    threading.Thread(target=run_server,    daemon=True).start()
    threading.Thread(target=tracker_loop,  daemon=True).start()

    print("  Server running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nStopping...")


if __name__ == "__main__":
    main()
