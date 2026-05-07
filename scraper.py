"""
scraper.py - جلب الأخبار من RSS بشكل parallel
"""

import re
import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import logger, clean_title, clean_url, clean_summary
from config import RSS_FEEDS

session = requests.Session()
session.headers.update({"User-Agent": "CryptoNewsBot/2.0"})


def _extract_image(entry) -> str:
    """استخراج صورة من RSS entry"""
    # media_content
    if hasattr(entry, 'media_content') and entry.media_content:
        for m in entry.media_content:
            url = m.get('url', '')
            if url and ('jpg' in url or 'png' in url or 'webp' in url):
                return url
    # media_thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        for m in entry.media_thumbnail:
            url = m.get('url', '')
            if url:
                return url
    # enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.href
    # img في summary
    if hasattr(entry, 'summary'):
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.summary)
        if match:
            return match.group(1)
    # img في content
    if hasattr(entry, 'content') and entry.content:
        for c in entry.content:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c.value)
            if match:
                return match.group(1)
    return ""


def _extract_summary(entry) -> str:
    """استخراج ملخص من RSS entry"""
    if hasattr(entry, 'summary'):
        return clean_summary(entry.summary)
    if hasattr(entry, 'description'):
        return clean_summary(entry.description)
    if hasattr(entry, 'content') and entry.content:
        return clean_summary(entry.content[0].value)
    return ""


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
                "id":        news_id,
                "title":     title,
                "url":       url,
                "source":    feed["name"],
                "image_url": _extract_image(entry),
                "summary":   _extract_summary(entry),
            })
        logger.info(f"📡 {feed['name']}: {len(news)} خبر")
        return news
    except Exception as e:
        logger.warning(f"⚠️ فشل {feed['name']}: {e}")
        return []


def fetch_all_news() -> list:
    """جلب الأخبار من جميع المصادر بشكل parallel"""
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
