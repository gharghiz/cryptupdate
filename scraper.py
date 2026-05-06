# -*- coding: utf-8 -*-
"""
CryptositNews - RSS Scraper
Parallel fetching with date filtering, failed source tracking, and cooldown.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import feedparser
import requests

import config
from utils import setup_logger, now_utc, parse_rfc2822_date, is_valid_title, clean_title
from database import save_news

logger = setup_logger("scraper")

# Track failed sources for cooldown
_failed_sources = {}
_failed_lock = threading.Lock()


def _check_source_cooldown(url):
    """Check if source is in cooldown period."""
    with _failed_lock:
        if url in _failed_sources:
            fail_info = _failed_sources[url]
            if fail_info["count"] >= config.FAILED_SOURCE_MAX:
                cooldown_end = fail_info["last_fail"] + config.SOURCE_COOLDOWN
                if time.time() < cooldown_end:
                    remaining = int(cooldown_end - time.time())
                    logger.debug(f"Source in cooldown: {url[:50]}... ({remaining}s remaining)")
                    return True
                else:
                    # Reset after cooldown
                    fail_info["count"] = 0
        return False


def _record_source_failure(url):
    """Record a source failure."""
    with _failed_lock:
        if url not in _failed_sources:
            _failed_sources[url] = {"count": 0, "last_fail": 0}
        _failed_sources[url]["count"] += 1
        _failed_sources[url]["last_fail"] = time.time()
        logger.warning(
            f"Source failed ({_failed_sources[url]['count']}/{config.FAILED_SOURCE_MAX}): "
            f"{url[:60]}..."
        )


def _record_source_success(url):
    """Record a source success, reset failure count."""
    with _failed_lock:
        if url in _failed_sources:
            _failed_sources[url]["count"] = 0


def fetch_feed(feed_config):
    """Fetch and parse a single RSS feed."""
    url = feed_config["url"]
    source_lang = feed_config.get("lang", "en")

    if _check_source_cooldown(url):
        return {"url": url, "entries": [], "error": "cooldown", "source": ""}

    try:
        headers = {
            "User-Agent": "CryptositNews/2.0 (RSS Reader)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()

        feed = feedparser.parse(response.content)
        entries = []
        max_age = now_utc() - timedelta(hours=config.NEWS_MAX_AGE_HOURS)
        source_name = ""

        if feed.feed:
            source_name = feed.feed.get("title", url)
            source_name = source_name[:100]

        for entry in feed.entries[:config.MAX_ENTRIES_PER_FEED]:
            title = entry.get("title", "")
            title = clean_title(title)

            if not is_valid_title(title):
                continue

            # Date filtering
            published = None
            if entry.get("published"):
                published = parse_rfc2822_date(entry["published"])
            elif entry.get("updated"):
                published = parse_rfc2822_date(entry["updated"])

            if published and published < max_age:
                continue

            summary = entry.get("summary", "")
            link = entry.get("link", "")

            # Clean HTML from summary
            import re
            summary = re.sub(r'<[^>]+>', '', summary)
            summary = summary.strip()[:500]

            entries.append({
                "title": title,
                "summary": summary,
                "url": link,
                "source": source_name,
                "published": published,
                "lang": source_lang,
            })

        _record_source_success(url)
        logger.info(f"Fetched {url[:40]}...: {len(entries)} entries")
        return {"url": url, "entries": entries, "error": None, "source": source_name}

    except requests.exceptions.Timeout:
        _record_source_failure(url)
        return {"url": url, "entries": [], "error": "timeout", "source": ""}
    except requests.exceptions.ConnectionError:
        _record_source_failure(url)
        return {"url": url, "entries": [], "error": "connection", "source": ""}
    except Exception as e:
        _record_source_failure(url)
        logger.error(f"Error fetching {url[:40]}...: {e}")
        return {"url": url, "entries": [], "error": str(e)[:100], "source": ""}


def scrape_all():
    """Scrape all RSS feeds in parallel."""
    logger.info(f"Starting scrape of {len(config.RSS_FEEDS)} feeds...")
    start_time = time.time()

    results = []
    total_entries = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=config.SCRAPER_THREADS) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in config.RSS_FEEDS}

        for future in as_completed(futures, timeout=120):
            try:
                result = future.result()
                results.append(result)
                if result["error"]:
                    errors += 1
                else:
                    total_entries += len(result["entries"])
            except Exception as e:
                errors += 1
                logger.error(f"Scrape task failed: {e}")

    # Save all entries
    saved = 0
    for result in results:
        for entry in result["entries"]:
            try:
                news_id = save_news(
                    title=entry["title"],
                    summary=entry["summary"],
                    url=entry["url"],
                    source=entry["source"],
                    published=entry["published"],
                )
                if news_id:
                    saved += 1
            except Exception as e:
                logger.error(f"Error saving entry: {e}")

    elapsed = time.time() - start_time
    logger.info(
        f"Scrape complete: {total_entries} entries found, {saved} saved, "
        f"{errors} errors, {elapsed:.1f}s"
    )

    return {
        "feeds": len(config.RSS_FEEDS),
        "entries": total_entries,
        "saved": saved,
        "errors": errors,
        "elapsed": round(elapsed, 2),
    }
