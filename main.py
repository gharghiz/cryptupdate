# -*- coding: utf-8 -*-
"""
CryptositNews - Main Worker
Periodic news scraping, AI analysis, price monitoring, and Telegram posting.
"""

import time
import threading

import config
from utils import setup_logger, now_utc
from database import (
    get_news, get_active_alerts, mark_alert_triggered,
    is_telegram_posted, mark_telegram_posted,
    cleanup_telegram_log, cache,
)
from scraper import scrape_all
from processor import is_important, prioritize, format_message, extract_coins
from ai import analyze_news
from bot import send_message, send_important_news, send_alert_message

logger = setup_logger("main")

# Track last seen titles for duplicate detection
_seen_titles = []
_seen_lock = threading.Lock()

_start_time = time.time()


def _get_coin_price(coin_id):
    """Get current price for a coin from CoinGecko."""
    import requests
    from database import _coingecko_wait

    _coingecko_wait()
    try:
        url = f"{config.COINGECKO_BASE}/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if coin_id in data and "usd" in data[coin_id]:
            return data[coin_id]["usd"]
    except Exception as e:
        logger.error(f"Error getting price for {coin_id}: {e}")
    return None


def process_news():
    """Fetch, analyze, and process news articles."""
    logger.info("Starting news processing cycle...")

    # Scrape RSS feeds
    scrape_result = scrape_all()

    # Get recent unprocessed news
    news_list = get_news(limit=50, important_only=False)
    if not news_list:
        logger.info("No news to process")
        return

    # Update seen titles
    with _seen_lock:
        _seen_titles.extend([n["title"] for n in news_list])
        # Keep only last 500 titles
        _seen_titles = _seen_titles[-500:]

    # Process each news item
    posted = 0
    for news in news_list:
        try:
            title = news["title"]
            news_id = news["id"]

            # Skip already posted
            if news.get("telegram_posted") or is_telegram_posted(title):
                continue

            # Check if important
            important = is_important(title, news.get("summary", ""))
            priority = prioritize(title, news.get("summary", ""))

            # Analyze with AI if important
            ai_summary = news.get("ai_summary", "")
            ai_sentiment = news.get("ai_sentiment", "")
            ai_reason = news.get("ai_reason", "")

            if important or priority >= 50:
                if not ai_summary:
                    ai_summary, ai_sentiment, ai_reason = analyze_news(
                        title, news.get("summary", "")
                    )
                    if ai_summary:
                        # Update news with AI analysis
                        from database import get_db
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE news SET
                                    ai_summary = ?, ai_sentiment = ?,
                                    ai_reason = ?, is_important = 1
                                WHERE id = ?
                            """, (ai_summary, ai_sentiment, ai_reason, news_id))
                            conn.commit()

                # Send to Telegram
                success, error = send_important_news(
                    title=title,
                    summary=news.get("summary", ""),
                    url=news.get("url", ""),
                    sentiment=ai_sentiment,
                    ai_summary=ai_summary,
                )

                mark_telegram_posted(news_id, title, success, error or "")
                if success:
                    posted += 1

            # Rate limiting between messages
            if posted > 0:
                time.sleep(2)

        except Exception as e:
            logger.error(f"Error processing news: {e}")
            continue

    logger.info(f"News cycle complete: {posted} posted, {scrape_result['saved']} new articles")


def check_price_alerts():
    """Check active price alerts against current prices."""
    alerts = get_active_alerts()
    if not alerts:
        return

    for alert in alerts:
        try:
            coin_id = config.COIN_MAP.get(alert["symbol"])
            if not coin_id:
                continue

            price = _get_coin_price(coin_id)
            if price is None:
                continue

            target = float(alert["target_price"])
            condition = alert["condition"]
            triggered = False

            if condition == "above" and price >= target:
                triggered = True
            elif condition == "below" and price <= target:
                triggered = True

            if triggered:
                symbol = alert["symbol"]
                coin = alert["coin"]
                msg = (
                    f"📈 <b>Price Alert Triggered!</b>\n\n"
                    f"🪙 {coin} ({symbol})\n"
                    f"💰 Target: ${target:,.4f} ({condition})\n"
                    f"📊 Current: ${price:,.4f}\n"
                )
                send_alert_message(msg)
                mark_alert_triggered(alert["id"])
                logger.info(f"Alert triggered: {symbol} {condition} {target}")

            time.sleep(3)  # Rate limiting

        except Exception as e:
            logger.error(f"Error checking alert: {e}")
            continue


def cleanup():
    """Periodic cleanup tasks."""
    try:
        cleanup_telegram_log(days=config.TELEGRAM_LOG_DAYS)
        logger.info("Cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


def worker_loop():
    """Main worker loop."""
    logger.info("CryptositNews Worker starting...")

    # Initial scrape on startup
    try:
        process_news()
    except Exception as e:
        logger.error(f"Initial scrape error: {e}")

    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            logger.info(f"Worker cycle #{cycle_count}")

            # News processing
            process_news()

            # Price alerts (every 5 cycles ~25 minutes)
            if cycle_count % 5 == 0:
                check_price_alerts()

            # Cleanup (every 12 cycles ~1 hour)
            if cycle_count % 12 == 0:
                cleanup()

            # Wait
            time.sleep(config.SCRAPER_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Worker stopped by user")
            break
        except Exception as e:
            logger.error(f"Worker error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    worker_loop()
