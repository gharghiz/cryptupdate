# -*- coding: utf-8 -*-
"""
CryptositNews - RSS Scraper Service
Parallel fetching with date filtering, failed source tracking, and cooldown.
"""

import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import feedparser
import requests

from app.config import Config
from app.utils.helpers import setup_logger, now_utc, parse_rfc2822_date, is_valid_title, clean_title
from app.models.db import save_news, log_scrape_result

logger = setup_logger("scraper")

# Track failed sources for cooldown
_failed_sources = {}
_failed_lock = threading.Lock()


def _check_source_cooldown(url):
    """Check if source is in cooldown period."""
    with _failed_lock:
        if url in _failed_sources:
            fail_info = _failed_sources[url]
            if fail_info["count"] >= Config.FAILED_SOURCE_MAX:
                cooldown_end = fail_info["last_fail"] + Config.SOURCE_COOLDOWN
                if time.time() < cooldown_end:
                    remaining = int(cooldown_end - time.time())
                    logger.debug(f"Source in cooldown: {url[:50]}... ({remaining}s remaining)")
                    return True
                else:
                    # Cooldown expired, reset counter
                    fail_info["count"] = 0
        return False


def _record_source_failure(url, error="unknown"):
    """Record a source failure for cooldown tracking."""
    with _failed_lock:
        if url not in _failed_sources:
            _failed_sources[url] = {"count": 0, "last_fail": 0}
        _failed_sources[url]["count"] += 1
        _failed_sources[url]["last_fail"] = time.time()
        logger.warning(
            f"Source failed ({_failed_sources[url]['count']}/{Config.FAILED_SOURCE_MAX}): "
            f"{url[:60]}... - {error}"
        )


def _record_source_success(url):
    """Record a source success, reset failure count."""
    with _failed_lock:
        if url in _failed_sources:
            _failed_sources[url]["count"] = 0


def _parse_entry_date(entry):
    """Extract published date from a feed entry using multiple strategies."""
    published = None

    if entry.get("published"):
        published = parse_rfc2822_date(entry["published"])
    elif entry.get("updated"):
        published = parse_rfc2822_date(entry["updated"])
    elif entry.get("published_parsed"):
        try:
            published = datetime.fromtimestamp(
                time.mktime(entry["published_parsed"]), tz=timezone.utc
            )
        except Exception:
            pass
    elif entry.get("updated_parsed"):
        try:
            published = datetime.fromtimestamp(
                time.mktime(entry["updated_parsed"]), tz=timezone.utc
            )
        except Exception:
            pass

    return published


def _clean_summary(raw_summary):
    """Strip HTML tags and truncate summary."""
    if not raw_summary:
        return ""
    summary = re.sub(r'<[^>]+>', '', raw_summary)
    summary = summary.strip()[:500]
    return summary


def fetch_feed(feed_config):
    """Fetch and parse a single RSS feed.

    Args:
        feed_config: Dict with 'url', 'lang', and optional 'category'.

    Returns:
        Dict with 'url', 'entries' (list), 'error' (str|None), 'source' (str).
    """
    url = feed_config["url"]
    source_lang = feed_config.get("lang", "en")
    start_time = time.time()

    # Skip if source is in cooldown
    if _check_source_cooldown(url):
        return {"url": url, "entries": [], "error": "cooldown", "source": ""}

    try:
        headers = {
            "User-Agent": "CryptositNews/2.0 (RSS Reader; +https://cryptositnews.up.railway.app)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()

        # Reject responses that returned HTML instead of RSS
        content_type = response.headers.get("Content-Type", "")
        if "html" in content_type.lower() and "xml" not in content_type.lower():
            _record_source_failure(url, "returned HTML instead of RSS")
            log_scrape_result(url, "", 0, 0, "not_rss", int((time.time() - start_time) * 1000))
            return {"url": url, "entries": [], "error": "not_rss", "source": ""}

        feed = feedparser.parse(response.content)
        entries = []
        max_age = now_utc() - timedelta(hours=Config.NEWS_MAX_AGE_HOURS)
        source_name = ""

        # Validate feed parsed correctly
        if feed.feed:
            source_name = feed.feed.get("title", url)[:100]
        else:
            if feed.bozo:
                _record_source_failure(url, f"parse error: {str(feed.bozo_exception)[:80]}")
                log_scrape_result(
                    url, "", 0, 0, f"parse_error: {str(feed.bozo_exception)[:80]}",
                    int((time.time() - start_time) * 1000)
                )
                return {"url": url, "entries": [], "error": "parse_error", "source": ""}

        for entry in feed.entries[:Config.MAX_ENTRIES_PER_FEED]:
            title = clean_title(entry.get("title", ""))

            if not is_valid_title(title):
                continue

            # Date filtering: skip entries older than max age
            published = _parse_entry_date(entry)
            if published and published < max_age:
                continue

            link = entry.get("link", "")
            if not link:
                continue

            summary = _clean_summary(entry.get("summary", ""))

            entries.append({
                "title": title,
                "summary": summary,
                "url": link,
                "source": source_name,
                "published": published,
                "lang": source_lang,
            })

        elapsed_ms = int((time.time() - start_time) * 1000)
        _record_source_success(url)
        logger.info(f"[scraper] OK {source_name or url[:30]}: {len(entries)} entries ({elapsed_ms}ms)")

        # Log scrape result for diagnostics
        log_scrape_result(url, source_name, len(entries), -1, "", elapsed_ms)

        return {"url": url, "entries": entries, "error": None, "source": source_name}

    except requests.exceptions.Timeout:
        _record_source_failure(url, "timeout")
        log_scrape_result(url, "", 0, 0, "timeout", int((time.time() - start_time) * 1000))
        return {"url": url, "entries": [], "error": "timeout", "source": ""}

    except requests.exceptions.ConnectionError:
        _record_source_failure(url, "connection refused")
        log_scrape_result(url, "", 0, 0, "connection_error", int((time.time() - start_time) * 1000))
        return {"url": url, "entries": [], "error": "connection", "source": ""}

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        _record_source_failure(url, f"HTTP {status}")
        log_scrape_result(url, "", 0, 0, f"HTTP {status}", int((time.time() - start_time) * 1000))
        return {"url": url, "entries": [], "error": f"HTTP {status}", "source": ""}

    except Exception as e:
        _record_source_failure(url, str(e)[:80])
        log_scrape_result(url, "", 0, 0, str(e)[:100], int((time.time() - start_time) * 1000))
        logger.error(f"[scraper] Error fetching {url[:40]}...: {e}")
        return {"url": url, "entries": [], "error": str(e)[:100], "source": ""}


def scrape_all():
    """Scrape all configured RSS feeds in parallel and save to database.

    Returns:
        Dict with 'feeds', 'entries', 'saved', 'errors', 'elapsed' keys.
    """
    feed_count = len(Config.RSS_FEEDS)
    logger.info(f"[scraper] Starting scrape of {feed_count} feeds with {Config.SCRAPER_THREADS} threads...")
    start_time = time.time()

    results = []
    total_entries = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=Config.SCRAPER_THREADS) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in Config.RSS_FEEDS}

        for future in as_completed(futures, timeout=120):
            try:
                result = future.result()
                results.append(result)
                if result["error"]:
                    errors += 1
                    if result["error"] not in ("cooldown",):
                        logger.debug(f"[scraper] FAIL {result['url'][:50]}...: {result['error']}")
                else:
                    total_entries += len(result["entries"])
            except Exception as e:
                errors += 1
                logger.error(f"[scraper] Scrape task failed: {e}")

    # Save all entries to database
    saved = 0
    duplicates = 0
    for result in results:
        feed_saved = 0
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
                    feed_saved += 1
                else:
                    duplicates += 1
            except Exception as e:
                logger.error(f"[scraper] Error saving entry: {e}")

        # Update scrape log with actual saved count
        if result["source"] and result["error"] is None and feed_saved >= 0:
            try:
                from app.models.db import get_db, is_pg
                with get_db() as conn:
                    cursor = conn.cursor()
                    if is_pg():
                        cursor.execute("""
                            UPDATE scrape_log SET entries_saved = %s
                            WHERE feed_url = %s AND scraped_at >= NOW() - INTERVAL '5 minutes'
                            ORDER BY scraped_at DESC LIMIT 1
                        """, (feed_saved, result["url"]))
                    else:
                        cursor.execute("""
                            UPDATE scrape_log SET entries_saved = ?
                            WHERE feed_url = ? AND scraped_at >= datetime('now', '-5 minutes')
                            ORDER BY scraped_at DESC LIMIT 1
                        """, (feed_saved, result["url"]))
                    conn.commit()
            except Exception:
                pass

    elapsed = time.time() - start_time
    logger.info(
        f"[scraper] Complete in {elapsed:.1f}s: {total_entries} entries, "
        f"{saved} saved, {duplicates} duplicates, {errors} errors from {feed_count} feeds"
    )

    return {
        "feeds": feed_count,
        "entries": total_entries,
        "saved": saved,
        "errors": errors,
        "elapsed": round(elapsed, 2),
    }
