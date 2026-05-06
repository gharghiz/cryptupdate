"""
main.py - نقطة البداية
- posted_news: الموقع — ما يتحذفش أبداً
- telegram_log: تيليغرام — يتحذف كل 6 ساعات
"""

import sys
import time
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor
from utils import logger, now_utc
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    INTERVAL_MINUTES, MAX_POSTS_PER_CYCLE,
    PRICE_ALERT_COINS, PRICE_ALERT_THRESHOLD, PRICE_CHECK_INTERVAL,
    CLEANUP_HOURS,
)
from database import (
    init_db,
    is_telegram_posted, mark_telegram_posted,
    save_news,
    get_recent_titles,
    cleanup_telegram_log,
)
from scraper import fetch_all_news
from processor import is_important, is_breaking, is_high_impact, is_duplicate, format_message, prioritize
from bot import send_message, send_price_alert

# ============================================================
# Startup check
# ============================================================

def check_env():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("❌ TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID ناقصين!")
        sys.exit(1)
    logger.info("✅ جميع المفاتيح موجودة")

# ============================================================
# Price Alerts
# ============================================================

_last_price_check = 0
_session = requests.Session()

def check_price_alerts():
    global _last_price_check
    now = time.time()
    if now - _last_price_check < PRICE_CHECK_INTERVAL * 60:
        return
    _last_price_check = now

    try:
        resp = _session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(PRICE_ALERT_COINS.keys()), "vs_currencies": "usd", "include_1hr_change": "true"},
            timeout=5
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"⚠️ فشل الأسعار: {e}")
        return

    for coin_id, symbol in PRICE_ALERT_COINS.items():
        raw = data.get(coin_id, {})
        if not raw:
            continue
        price    = raw.get("usd", 0)
        change1h = round(raw.get("usd_1h_change", 0), 2)
        if abs(change1h) >= PRICE_ALERT_THRESHOLD:
            sign      = "+" if change1h > 0 else ""
            direction = "🚀 Surge" if change1h > 0 else "🔴 Drop"
            price_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
            send_price_alert(
                f"⚡️ <b>Price Alert — {symbol}</b>\n\n"
                f"{direction} in the last hour!\n\n"
                f"💰 {price_str}\n"
                f"📈 1h: {sign}{change1h}%\n\n"
                f"#Crypto #{symbol} #PriceAlert"
            )

# ============================================================
# Enrich
# ============================================================

def enrich(news_list: list) -> list:
    enriched = []
    for item in news_list:
        title = item["title"]
        if not is_important(title):
            continue
        item["breaking"]    = is_breaking(title)
        item["high_impact"] = is_high_impact(title)
        enriched.append(item)
    return enriched

# ============================================================
# Process single item
# ============================================================

def process_item(args):
    item, recent_titles = args
    news_id = item["id"]
    title   = item["title"]

    # تحقق من telegram_log — مو posted_news
    if is_telegram_posted(news_id):
        return None

    if is_duplicate(title, recent_titles):
        logger.info(f"🔁 مشابه: {title[:60]}")
        return None

    msg, ai = format_message(item)
    message_id = send_message(msg)

    if message_id:
        # حفظ في الجدولين
        mark_telegram_posted(news_id)
        save_news(
            news_id, title, item["source"],
            summary=ai.get("summary", ""),
            sentiment=ai.get("sentiment", ""),
            reason=ai.get("reason", "")
        )
        return title

    return None

# ============================================================
# Main cycle
# ============================================================

def run_cycle():
    logger.info(f"🔄 UTC {now_utc().strftime('%H:%M:%S')}")

    # تنظيف telegram_log فقط — الموقع ما يتأثرش
    cleanup_telegram_log(hours=CLEANUP_HOURS)

    check_price_alerts()

    all_news      = fetch_all_news()
    enriched      = enrich(all_news)
    prioritized   = prioritize(enriched)
    recent_titles = get_recent_titles(200)
    posted_count  = 0

    items = prioritized[:MAX_POSTS_PER_CYCLE]

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(process_item, [(item, recent_titles) for item in items]))

    for r in results:
        if r:
            recent_titles.append(r)
            posted_count += 1

    logger.info(f"✅ نشرنا {posted_count} خبر")

# ============================================================
# Entry point
# ============================================================

def main():
    check_env()
    init_db()
    logger.info(f"🤖 البوت بدا | كل {INTERVAL_MINUTES} دقيقة | cleanup كل {CLEANUP_HOURS}h")

    send_message(
        f"🚀 <b>Bot Started!</b>\n\n"
        f"⏱ Every {INTERVAL_MINUTES} minutes\n"
        f"🗑 Telegram log cleanup every {CLEANUP_HOURS}h\n"
        f"📰 Website keeps ALL news forever\n"
        f"🕐 {now_utc().strftime('%H:%M UTC')}"
    )

    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"❌ {e}")
            traceback.print_exc()
        logger.info(f"😴 {INTERVAL_MINUTES} دقيقة...")
        time.sleep(INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"❌ {e}")
        traceback.print_exc()
        sys.exit(1)
