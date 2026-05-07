# -*- coding: utf-8 -*-
"""
CryptositNews v3 - Worker Process
PARALLEL news processing: scraping, AI analysis, Telegram posting.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import create_app
from app.config import Config
from app.utils.helpers import setup_logger, now_utc
from app.models.db import (
    get_news, get_active_alerts, mark_alert_triggered,
    is_telegram_posted, mark_telegram_posted,
    cleanup_telegram_log, save_ai_cache, batch_save_ai_cache,
)
from app.services.scraper import scrape_all
from app.services.processor import is_important, prioritize, format_message
from app.services.ai_service import analyze_news, batch_analyze_news
from app.services.telegram import send_important_news, send_alert_message

logger = setup_logger("worker")

_start_time = time.time()


def _get_coin_price(coin_id):
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


def _process_single_news(news):
    """Process a single news item: AI analysis + Telegram posting."""
    try:
        if news.get("telegram_posted") or is_telegram_posted(news["title"]):
            return {"id": news["id"], "status": "skipped"}

        title = news["title"]
        summary = news.get("summary", "")
        important = is_important(title, summary)
        priority = prioritize(title, summary)

        ai_summary = news.get("ai_summary", "")
        ai_sentiment = news.get("ai_sentiment", "")

        # AI analysis for important news
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

        # Post to Telegram
        success, error = send_important_news(
            title=title, summary=summary,
            url=news.get("url", ""),
            sentiment=ai_sentiment, ai_summary=ai_summary,
        )
        mark_telegram_posted(news["id"], title, success, error or "")

        return {"id": news["id"], "status": "posted" if success else "failed"}

    except Exception as e:
        logger.error(f"Process error (ID:{news.get('id')}): {e}")
        return {"id": news.get("id"), "status": "error", "error": str(e)}


def process_news():
    """Scrape -> Analyze -> Post to Telegram (PARALLEL)."""
    logger.info("Starting news processing cycle...")
    scrape_result = scrape_all()

    if scrape_result["saved"] == 0 and scrape_result["errors"] > 5:
        logger.warning(f"High error rate: {scrape_result['errors']}/{scrape_result['feeds']} feeds failed")

    news_list = get_news(limit=50, important_only=False)
    if not news_list:
        logger.info("No news found in database")
        return

    logger.info(f"Processing {len(news_list)} articles with {Config.WORKER_PARALLEL_POSTS} parallel workers...")

    posted = 0
    with ThreadPoolExecutor(max_workers=Config.WORKER_PARALLEL_POSTS) as executor:
        futures = {executor.submit(_process_single_news, news): news for news in news_list}
        for future in as_completed(futures, timeout=300):
            try:
                result = future.result()
                if result.get("status") == "posted":
                    posted += 1
            except Exception as e:
                logger.error(f"Future error: {e}")

    logger.info(f"Cycle done: {posted} posted, {scrape_result['saved']} new scraped")


def check_price_alerts():
    alerts = get_active_alerts()
    if not alerts:
        return

    # Batch price fetches in parallel
    def check_alert(alert):
        try:
            coin_id = Config.COIN_MAP.get(alert["symbol"])
            if not coin_id:
                return None
            price = _get_coin_price(coin_id)
            if price is None:
                return None
            target = float(alert["target_price"])
            condition = alert["condition"]
            triggered = (condition == "above" and price >= target) or (condition == "below" and price <= target)
            if triggered:
                msg = (
                    f"Price Alert!\n\n"
                    f"{alert['coin']} ({alert['symbol']})\n"
                    f"Target: ${target:,.4f} ({condition})\n"
                    f"Current: ${price:,.4f}\n"
                )
                send_alert_message(msg)
                mark_alert_triggered(alert["id"])
                logger.info(f"Alert triggered: {alert['symbol']} {condition} {target}")
            return alert["symbol"]
        except Exception as e:
            logger.error(f"Alert check error: {e}")
            return None

    with ThreadPoolExecutor(max_workers=min(len(alerts), 5)) as executor:
        list(executor.map(check_alert, alerts))


def worker_loop():
    logger.info("=" * 60)
    logger.info("CryptositNews v3.0 Worker (Parallel Edition)")
    logger.info(f"  RSS Feeds       : {len(Config.RSS_FEEDS)}")
    logger.info(f"  Interval        : {Config.SCRAPER_INTERVAL}s")
    logger.info(f"  Telegram        : {'Configured' if Config.BOT_TOKEN else 'NOT CONFIGURED'}")
    logger.info(f"  OpenAI          : {'Configured' if Config.OPENAI_API_KEY else 'NOT CONFIGURED'}")
    logger.info(f"  Database        : {'PostgreSQL (Pooled)' if Config.DATABASE_URL else 'SQLite'}")
    logger.info(f"  Parallel Posts  : {Config.WORKER_PARALLEL_POSTS}")
    logger.info(f"  Redis           : {'Configured' if Config.REDIS_URL else 'In-memory'}")
    logger.info("=" * 60)

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
