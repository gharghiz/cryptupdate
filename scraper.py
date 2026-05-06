"""
scraper.py - جلب الأخبار من RSS بشكل parallel
"""

import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import logger, clean_title, clean_url
from config import RSS_FEEDS

session = requests.Session()
session.headers.update({"User-Agent": "CryptoNewsBot/2.0"})

def fetch_feed(feed: dict) -> list:
    try:
        parsed = feedparser.parse(feed["url"])
        news = []
        for entry in parsed.entries[:25]:
            news_id = entry.get("id") or entry.get("link", "")
            title   = clean_title(entry.get("title", ""))
            url     = clean_url(entry.get("link", ""))
            if not title or not news_id:
                continue
            news.append({
                "id":     news_id,
                "title":  title,
                "url":    url,
                "source": feed["name"],
            })
        logger.info(f"📡 {feed['name']}: {len(news)} خبر")
        return news
    except Exception as e:
        logger.warning(f"⚠️ فشل {feed['name']}: {e}")
        return []

def fetch_all_news() -> list:
    """جلب الأخبار من جميع المصادر بشكل parallel — أسرع ×3"""
    all_news = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in RSS_FEEDS}
        for future in as_completed(futures):
            try:
                all_news.extend(future.result())
            except Exception as e:
                logger.warning(f"⚠️ خطأ في scraper: {e}")
    logger.info(f"📊 إجمالي: {len(all_news)} خبر")
    return all_news
