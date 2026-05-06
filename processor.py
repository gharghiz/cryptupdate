import time
import difflib
import hashlib
import requests
from utils import logger, safe_html
from config import (
    IMPORTANT_KEYWORDS, BREAKING_KEYWORDS, HIGH_IMPACT_KEYWORDS,
    POSITIVE_WORDS, NEGATIVE_WORDS, COIN_MAP,
    COINGECKO_CACHE_SECONDS, SIMILARITY_THRESHOLD,
)
from ai import generate_ai_insight

_price_cache = {}
_session = requests.Session()

# ============================
# Market Data
# ============================

def get_market_data(title: str):
    title_lower = title.lower()
    coin_id = None
    for keyword, cid in COIN_MAP.items():
        if keyword in title_lower:
            coin_id = cid
            break

    if not coin_id:
        return None

    now = time.time()
    if coin_id in _price_cache:
        data, ts = _price_cache[coin_id]
        if now - ts < COINGECKO_CACHE_SECONDS:
            return data

    try:
        resp = _session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_1hr_change": "true",
            },
            timeout=5
        )
        raw = resp.json().get(coin_id, {})
        if not raw:
            return None

        price = raw.get("usd", 0)
        change_1h = round(raw.get("usd_1h_change", 0), 2)
        change_24h = round(raw.get("usd_24h_change", 0), 2)

        def fmt(c):
            return f"{'▲' if c >= 0 else '▼'} {'+' if c >= 0 else ''}{c}%"

        result = {
            "price": f"${price:,.2f}",
            "change_1h": fmt(change_1h),
            "change_24h": fmt(change_24h),
        }

        _price_cache[coin_id] = (result, now)
        return result

    except Exception as e:
        logger.warning(f"⚠️ Market error: {e}")
        return None

# ============================
# Sentiment
# ============================

def analyze_sentiment(title: str):
    t = title.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in t)
    neg = sum(1 for w in NEGATIVE_WORDS if w in t)

    if pos > neg:
        return "🟢"
    elif neg > pos:
        return "🔴"
    return "🟡"

# ============================
# IMPORTANT FIX
# ============================

def is_important(title: str) -> bool:
    # ⚡ خففنا الفلترة بزاف
    return True

def is_breaking(title: str):
    return any(k in title.lower() for k in BREAKING_KEYWORDS)

def is_high_impact(title: str):
    return any(k in title.lower() for k in HIGH_IMPACT_KEYWORDS)

# ============================
# Duplicate Fix
# ============================

def is_duplicate(title: str, recent_titles: list):
    title = title.lower()

    for prev in recent_titles[-50:]:  # ⚡ غير آخر 50
        prev = prev.lower()

        if title == prev:
            return True

        if title in prev or prev in title:
            return True

        if difflib.SequenceMatcher(None, title, prev).ratio() >= 0.6:
            return True

    return False

# ============================
# Format Message + AI
# ============================

def format_message(item: dict):
    title = item["title"]
    source = item["source"]

    breaking = item.get("breaking", False)
    high = item.get("high_impact", False)

    sentiment = analyze_sentiment(title)
    market = get_market_data(title)

    # AI فقط للأخبار المهمة
    ai = {"summary": "", "sentiment": "", "reason": ""}
    if breaking or high:
        ai = generate_ai_insight(title)

    headline = f"{sentiment} {safe_html(title)}"
    if breaking:
        headline = f"🚨 <b>BREAKING:</b> {safe_html(title)}"

    msg = f"{headline}\n\n"

    if market:
        msg += f"💰 {market['price']}\n"
        msg += f"⏱ 1h: {market['change_1h']} | 24h: {market['change_24h']}\n\n"

    if ai["summary"]:
        msg += (
            f"🧠 <b>AI Insight</b>\n"
            f"├ {safe_html(ai['summary'])}\n"
            f"├ {safe_html(ai['sentiment'])}\n"
            f"└ {safe_html(ai['reason'])}\n\n"
        )

    msg += f"📌 {safe_html(source)}\n#Crypto"

    return msg, ai

# ============================
# Prioritize
# ============================

def prioritize(news_list):
    return news_list
