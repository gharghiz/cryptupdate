# -*- coding: utf-8 -*-
"""
CryptositNews - Worker Process
Periodic news scraping, AI analysis, price monitoring, Telegram posting.
Runs as a separate process (Railway worker dyno or Docker container).
"""

import time
import threading

from app import create_app
from app.config import Config
from app.utils.helpers import setup_logger, now_utc
from app.models.db import (
    get_news, get_active_alerts, mark_alert_triggered,
    is_telegram_posted, mark_telegram_posted,
    cleanup_telegram_log,
)
from app.services.scraper import scrape_all
from app.services.processor import is_important, prioritize, format_message
from app.services.ai_service import analyze_news
from app.services.telegram import send_important_news, send_alert_message

logger = setup_logger("worker")

_start_time = time.time()


def _get_coin_price(coin_id):
    """Get current price from CoinGecko."""
    import requests
    from app.models.db import coingecko_wait

    coingecko_wait()
    try:
        resp = requests.get(
            f"{Config.COINGECKO_BASE}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=10,
        )
        data = resp.json()
        return data.get(coin_id, {}).get("usd")
    except Exception as e:
        logger.error(f"Price fetch error for {coin_id}: {e}")
        return None


def process_news():
    """Scrape → Analyze → Post to Telegram."""
    logger.info("Starting news processing cycle...")
    scrape_result = scrape_all()

    if scrape_result["saved"] == 0 and scrape_result["errors"] > 5:
        logger.warning(f"High error rate: {scrape_result['errors']}/{scrape_result['feeds']} feeds failed")

    news_list = get_news(limit=50, important_only=False)
    if not news_list:
        logger.info("No news found in database")
        return

    logger.info(f"Processing {len(news_list)} articles...")

    posted = 0
    for news in news_list:
        try:
            if news.get("telegram_posted") or is_telegram_posted(news["title"]):
                continue

            title = news["title"]
            summary = news.get("summary", "")
            important = is_important(title, summary)
            priority = prioritize(title, summary)

            # AI analysis for important news
            ai_summary = news.get("ai_summary", "")
            ai_sentiment = news.get("ai_sentiment", "")

            if (important or priority >= 50) and not ai_summary:
                result = analyze_news(title, summary)
                if result:
                    ai_summary, ai_sentiment, _ = result
                    try:
                        from app.models.db import is_pg, get_db
                        with get_db() as conn:
                            cursor = conn.cursor()
                            if is_pg():
                                cursor.execute(
                                    "UPDATE news SET ai_summary=%s, ai_sentiment=%s, is_important=TRUE WHERE id=%s",
                                    (ai_summary, ai_sentiment, news["id"]),
                                )
                            else:
                                cursor.execute(
                                    "UPDATE news SET ai_summary=?, ai_sentiment=?, is_important=1 WHERE id=?",
                                    (ai_summary, ai_sentiment, news["id"]),
                                )
                            conn.commit()
                    except Exception as e:
                        logger.error(f"AI update error: {e}")

            # Post ALL news to Telegram
            success, error = send_important_news(
                title=title, summary=summary,
                url=news.get("url", ""),
                sentiment=ai_sentiment, ai_summary=ai_summary,
            )
            mark_telegram_posted(news["id"], title, success, error or "")

            if success:
                posted += 1

            time.sleep(2)  # Telegram rate limit

        except Exception as e:
            logger.error(f"Process error (ID:{news.get('id')}): {e}")

    logger.info(f"Cycle done: {posted} posted, {scrape_result['saved']} new scraped")


def check_price_alerts():
    """Check active price alerts."""
    alerts = get_active_alerts()
    if not alerts:
        return

    for alert in alerts:
        try:
            coin_id = Config.COIN_MAP.get(alert["symbol"])
            if not coin_id:
                continue

            price = _get_coin_price(coin_id)
            if price is None:
                continue

            target = float(alert["target_price"])
            condition = alert["condition"]
            triggered = (condition == "above" and price >= target) or (condition == "below" and price <= target)

            if triggered:
                msg = (
                    f"📈 <b>Price Alert!</b>\n\n"
                    f"🪙 {alert['coin']} ({alert['symbol']})\n"
                    f"💰 Target: ${target:,.4f} ({condition})\n"
                    f"📊 Current: ${price:,.4f}\n"
                )
                send_alert_message(msg)
                mark_alert_triggered(alert["id"])
                logger.info(f"Alert triggered: {alert['symbol']} {condition} {target}")

            time.sleep(3)

        except Exception as e:
            logger.error(f"Alert check error: {e}")


def worker_loop():
    """Main worker loop."""
    logger.info("=" * 60)
    logger.info("CryptositNews v2.0 Worker")
    logger.info(f"  RSS Feeds  : {len(Config.RSS_FEEDS)}")
    logger.info(f"  Interval   : {Config.SCRAPER_INTERVAL}s")
    logger.info(f"  Telegram   : {'✓' if Config.BOT_TOKEN else '✗'}")
    logger.info(f"  OpenAI     : {'✓' if Config.OPENAI_API_KEY else '✗'}")
    logger.info(f"  Database   : {'PostgreSQL' if Config.DATABASE_URL else 'SQLite'}")
    logger.info("=" * 60)

    # Initial scrape
    try:
        process_news()
    except Exception as e:
        logger.error(f"Initial scrape: {e}")

    cycle = 0
    while True:
        try:
            cycle += 1
            logger.info(f"--- Cycle #{cycle} ---")
            process_news()

            if cycle % 5 == 0:
                check_price_alerts()
            if cycle % 12 == 0:
                cleanup_telegram_log(days=Config.TELEGRAM_LOG_DAYS)

            time.sleep(Config.SCRAPER_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Worker stopped")
            break
        except Exception as e:
            logger.error(f"Worker error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    worker_loop()
