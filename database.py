# -*- coding: utf-8 -*-
"""
CryptositNews - Database Layer
PostgreSQL + SQLite dual support with connection pooling,
Redis cache with in-memory fallback, rate limiting, search, newsletter.
"""

import os
import time
import sqlite3
import threading
import json
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

import config
from utils import setup_logger, hash_text, hours_ago, now_utc, time_ago

logger = setup_logger("database")

# ============================================================
# DATABASE DIALECT DETECTION
# ============================================================
_is_postgres = False

def _is_pg():
    """Check if we're using PostgreSQL."""
    global _is_postgres
    return _is_postgres

def _param(n):
    """Return correct parameter placeholder: %s for PostgreSQL, ? for SQLite."""
    return "%s" if _is_pg() else "?"

def _params(n):
    """Return n parameter placeholders separated by commas."""
    p = "%s" if _is_pg() else "?"
    return ", ".join([p] * n)


# ============================================================
# CONNECTION POOL (SQLite / PostgreSQL)
# ============================================================
_local = threading.local()


class ConnectionPool:
    """Simple thread-safe connection pool for SQLite."""

    def __init__(self, db_path, pool_size=8):
        self.db_path = db_path
        self.pool_size = pool_size
        self._pool = []
        self._lock = threading.Lock()
        self._created = 0

    def get_connection(self):
        with self._lock:
            if self._pool:
                return self._pool.pop()
            if self._created < self.pool_size:
                self._created += 1
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                return conn
            pass
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def return_connection(self, conn):
        try:
            with self._lock:
                if len(self._pool) < self.pool_size:
                    self._pool.append(conn)
                    return
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def close_all(self):
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()


# Global pool
_pool = None
_db_path = "cryptositnews.db"


def get_pool():
    global _pool, _db_path, _is_postgres
    if _pool is None:
        database_url = config.DATABASE_URL
        if database_url and database_url.startswith("postgres"):
            _db_path = database_url
            _is_postgres = True
            _pool = None  # Use psycopg2 directly
        else:
            _db_path = database_url or "cryptositnews.db"
            _is_postgres = False
            _pool = ConnectionPool(_db_path, config.DB_POOL_SIZE)
    return _pool


# ============================================================
# REDIS CACHE with In-Memory Fallback
# ============================================================
_redis_client = None
_memory_cache = {}
_memory_cache_lock = threading.Lock()


class CacheStats:

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.evictions = 0
        self._lock = threading.Lock()

    def record_hit(self):
        with self._lock:
            self.hits += 1

    def record_miss(self):
        with self._lock:
            self.misses += 1

    def record_set(self):
        with self._lock:
            self.sets += 1

    def record_eviction(self):
        with self._lock:
            self.evictions += 1

    def to_dict(self):
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "evictions": self.evictions,
            "hit_rate": round(hit_rate, 2),
        }


cache_stats = CacheStats()


def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not config.REDIS_URL:
        return None
    try:
        import redis
        _redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info("Redis connected successfully")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory cache: {e}")
        _redis_client = False
        return None


class UnifiedCache:

    def get(self, key):
        r = get_redis()
        if r:
            try:
                val = r.get(f"cn:{key}")
                if val is not None:
                    cache_stats.record_hit()
                    return json.loads(val)
            except Exception:
                pass
        with _memory_cache_lock:
            item = _memory_cache.get(key)
            if item and item["exp"] > time.time():
                cache_stats.record_hit()
                return item["val"]
            elif item:
                del _memory_cache[key]
                cache_stats.record_eviction()
        cache_stats.record_miss()
        return None

    def set(self, key, value, ttl=None):
        ttl = ttl or config.CACHE_DEFAULT_TTL
        cache_stats.record_set()
        r = get_redis()
        if r:
            try:
                r.setex(f"cn:{key}", ttl, json.dumps(value, default=str))
            except Exception:
                pass
        with _memory_cache_lock:
            _memory_cache[key] = {"val": value, "exp": time.time() + ttl}
            if len(_memory_cache) > 500:
                self._evict_old()

    def delete(self, key):
        r = get_redis()
        if r:
            try:
                r.delete(f"cn:{key}")
            except Exception:
                pass
        with _memory_cache_lock:
            _memory_cache.pop(key, None)

    def clear(self):
        r = get_redis()
        if r:
            try:
                for key in r.scan_iter("cn:*"):
                    r.delete(key)
            except Exception:
                pass
        with _memory_cache_lock:
            _memory_cache.clear()
        logger.info("Cache cleared")

    def _evict_old(self):
        now = time.time()
        expired = [k for k, v in _memory_cache.items() if v["exp"] <= now]
        for k in expired:
            del _memory_cache[k]
        if len(_memory_cache) > 500:
            sorted_keys = sorted(_memory_cache.keys(),
                                 key=lambda k: _memory_cache[k]["exp"])
            for k in sorted_keys[:100]:
                del _memory_cache[k]
                cache_stats.record_eviction()

    def get_stats(self):
        stats = cache_stats.to_dict()
        stats["backend"] = "redis" if get_redis() else "memory"
        stats["memory_entries"] = len(_memory_cache)
        return stats


cache = UnifiedCache()


# ============================================================
# COINGECKO RATE LIMITER
# ============================================================
_cg_lock = threading.Lock()
_cg_timestamps = []


def _coingecko_wait():
    global _cg_timestamps
    with _cg_lock:
        now = time.time()
        _cg_timestamps = [t for t in _cg_timestamps if now - t < config.COINGECKO_RATE_WINDOW]
        if len(_cg_timestamps) >= config.COINGECKO_RATE_LIMIT:
            sleep_time = config.COINGECKO_RATE_WINDOW - (now - _cg_timestamps[0]) + 1
            if sleep_time > 0:
                logger.info(f"CoinGecko rate limit reached, waiting {sleep_time:.1f}s")
                time.sleep(sleep_time)
        _cg_timestamps.append(time.time())


# ============================================================
# CATEGORIZATION
# ============================================================
def categorize_title_with_confidence(title):
    if not title:
        return "market", 0.0

    title_lower = title.lower()
    scores = {}

    for category, rules in config.CATEGORY_RULES.items():
        score = 0
        matched = 0
        for kw in rules["keywords"]:
            kw_lower = kw.lower()
            if kw_lower in title_lower:
                score += rules["weight"] * 2
                matched += 1
        if matched > 0:
            scores[category] = score

    if not scores:
        return "market", 0.3

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]

    max_possible = max(r["weight"] * 2 * 5 for r in config.CATEGORY_RULES.values())
    confidence = min(best_score / (max_possible * 0.3), 1.0)

    return best_category, round(confidence, 2)


# ============================================================
# DATABASE CONTEXT MANAGER
# ============================================================
@contextmanager
def get_db():
    """Get database connection with proper cleanup."""
    pool = get_pool()
    conn = None
    try:
        if _is_pg():
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(_db_path)
            conn.autocommit = True
            yield conn
            conn.close()
        elif pool:
            conn = pool.get_connection()
            yield conn
            pool.return_connection(conn)
        else:
            conn = sqlite3.connect(_db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.close()
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        raise


def init_db():
    """Initialize database tables."""
    with get_db() as conn:
        cursor = conn.cursor()
        is_pg = _is_pg()

        # News table
        if is_pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    url TEXT UNIQUE,
                    source TEXT DEFAULT '',
                    published TIMESTAMP,
                    category TEXT DEFAULT 'market',
                    category_confidence REAL DEFAULT 0.0,
                    ai_sentiment TEXT DEFAULT '',
                    ai_reason TEXT DEFAULT '',
                    ai_summary TEXT DEFAULT '',
                    is_important BOOLEAN DEFAULT FALSE,
                    telegram_posted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_url TEXT DEFAULT ''
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    url TEXT UNIQUE,
                    source TEXT DEFAULT '',
                    published TIMESTAMP,
                    category TEXT DEFAULT 'market',
                    category_confidence REAL DEFAULT 0.0,
                    ai_sentiment TEXT DEFAULT '',
                    ai_reason TEXT DEFAULT '',
                    ai_summary TEXT DEFAULT '',
                    is_important BOOLEAN DEFAULT FALSE,
                    telegram_posted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_url TEXT DEFAULT ''
                )
            """)

        # Telegram log
        if is_pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id SERIAL PRIMARY KEY,
                    news_id INTEGER,
                    title TEXT,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT TRUE,
                    error TEXT DEFAULT ''
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_id INTEGER,
                    title TEXT,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT TRUE,
                    error TEXT DEFAULT ''
                )
            """)

        # Price alerts
        if is_pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id SERIAL PRIMARY KEY,
                    coin TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    condition TEXT DEFAULT 'above',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    triggered_at TIMESTAMP,
                    triggered BOOLEAN DEFAULT FALSE
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    condition TEXT DEFAULT 'above',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    triggered_at TIMESTAMP,
                    triggered BOOLEAN DEFAULT FALSE
                )
            """)

        # Newsletter subscribers
        if is_pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS newsletter (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS newsletter (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # Scrape log (for diagnostics)
        if is_pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrape_log (
                    id SERIAL PRIMARY KEY,
                    feed_url TEXT,
                    source TEXT DEFAULT '',
                    entries_found INTEGER DEFAULT 0,
                    entries_saved INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrape_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feed_url TEXT,
                    source TEXT DEFAULT '',
                    entries_found INTEGER DEFAULT 0,
                    entries_saved INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # Create indexes
        try:
            if is_pg:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published DESC NULLS LAST)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at DESC)")
            else:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(coalesce(published, created_at) DESC)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_important ON news(is_important)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_telegram ON news(telegram_posted)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_title ON news(title)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_telegram_log_posted ON telegram_log(posted_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_active ON price_alerts(active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_newsletter_email ON newsletter(email)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scrape_log_date ON scrape_log(scraped_at DESC)")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

        conn.commit()
        logger.info(f"Database initialized successfully (PostgreSQL: {is_pg})")


# ============================================================
# NEWS OPERATIONS
# ============================================================
def save_news(title, summary="", url="", source="", published=None,
              ai_sentiment="", ai_reason="", ai_summary="",
              is_important=False, image_url=""):
    """Save a news article, skip if duplicate URL."""
    if not title or not is_valid_title(title):
        return None

    category, confidence = categorize_title_with_confidence(title)
    is_pg = _is_pg()
    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if is_pg:
                cursor.execute("""
                    INSERT INTO news (title, summary, url, source, published,
                        category, category_confidence, ai_sentiment, ai_reason,
                        ai_summary, is_important, image_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (title, summary, url, source, published,
                      category, confidence, ai_sentiment, ai_reason,
                      ai_summary, is_important, image_url))
                news_id = cursor.fetchone()[0]
            else:
                cursor.execute("""
                    INSERT INTO news (title, summary, url, source, published,
                        category, category_confidence, ai_sentiment, ai_reason,
                        ai_summary, is_important, image_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (title, summary, url, source, published,
                      category, confidence, ai_sentiment, ai_reason,
                      ai_summary, is_important, image_url))
                news_id = cursor.lastrowid

            logger.info(f"Saved news [{category}]: {title[:60]}... (ID: {news_id})")

            cache.delete("news:latest")
            cache.delete("news:all")

            return news_id
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                logger.debug(f"Duplicate news skipped: {title[:40]}...")
                return None
            logger.error(f"Error saving news: {e}")
            return None


def get_news(limit=50, offset=0, category=None, important_only=False):
    """Get news with caching and filtering."""
    cache_key = f"news:latest:{limit}:{offset}:{category}:{important_only}"
    result = cache.get(cache_key)
    if result:
        return result

    is_pg = _is_pg()
    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        conditions = []
        params = []

        if category and category != "all":
            conditions.append(f"category = {p}")
            params.append(category)
        if important_only:
            if is_pg:
                conditions.append("is_important = TRUE")
            else:
                conditions.append("is_important = 1")

        query = "SELECT * FROM news"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # SQLite doesn't support NULLS LAST
        if is_pg:
            query += " ORDER BY published DESC NULLS LAST, created_at DESC"
        else:
            query += " ORDER BY coalesce(published, created_at) DESC"

        query += f" LIMIT {p} OFFSET {p}"
        params.extend([limit, offset])

        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            news_list = []
            for row in rows:
                item = dict(row)
                for key in ("published", "created_at"):
                    if item.get(key) and hasattr(item[key], "isoformat"):
                        item[key] = item[key].isoformat()
                news_list.append(item)

            cache.set(cache_key, news_list, config.CACHE_NEWS_TTL)
            return news_list
        except Exception as e:
            logger.error(f"Error getting news: {e}")
            return []


def get_news_by_id(news_id):
    """Get single news article by ID."""
    cache_key = f"news:id:{news_id}"
    result = cache.get(cache_key)
    if result:
        return result

    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT * FROM news WHERE id = {p}", (news_id,))
            row = cursor.fetchone()
            if row:
                item = dict(row)
                for key in ("published", "created_at"):
                    if item.get(key) and hasattr(item[key], "isoformat"):
                        item[key] = item[key].isoformat()
                cache.set(cache_key, item, config.CACHE_NEWS_TTL)
                return item
        except Exception as e:
            logger.error(f"Error getting news by ID: {e}")
    return None


def search_news(query, limit=20):
    """Search news articles by title, summary, or source."""
    if not query or len(query.strip()) < 2:
        return []

    from utils import sanitize_search
    query = sanitize_search(query)
    search_term = f"%{query}%"
    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute("""
                    SELECT * FROM news
                    WHERE title ILIKE %s OR summary ILIKE %s OR source ILIKE %s
                    ORDER BY coalesce(published, created_at) DESC
                    LIMIT %s
                """, (search_term, search_term, search_term, limit))
            else:
                cursor.execute("""
                    SELECT * FROM news
                    WHERE title LIKE ? OR summary LIKE ? OR source LIKE ?
                    ORDER BY coalesce(published, created_at) DESC
                    LIMIT ?
                """, (search_term, search_term, search_term, limit))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                for key in ("published", "created_at"):
                    if item.get(key) and hasattr(item[key], "isoformat"):
                        item[key] = item[key].isoformat()
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"Error searching news: {e}")
            return []


def get_related_news(news_id, limit=5):
    """Get related news based on same category, excluding the given article."""
    article = get_news_by_id(news_id)
    if not article:
        return []

    category = article.get("category", "market")
    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"""
                    SELECT * FROM news
                    WHERE category = {p} AND id != {p}
                    ORDER BY published DESC NULLS LAST, created_at DESC
                    LIMIT {p}
                """, (category, news_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM news
                    WHERE category = ? AND id != ?
                    ORDER BY coalesce(published, created_at) DESC
                    LIMIT ?
                """, (category, news_id, limit))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                for key in ("published", "created_at"):
                    if item.get(key) and hasattr(item[key], "isoformat"):
                        item[key] = item[key].isoformat()
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"Error getting related news: {e}")
            return []


# ============================================================
# STATISTICS
# ============================================================
def get_stats():
    """Get news statistics with caching."""
    cache_key = "stats:all"
    result = cache.get(cache_key)
    if result:
        return result

    is_pg = _is_pg()
    p = _param(0)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) as total FROM news")
            row = cursor.fetchone()
            total = row["total"] if hasattr(row, "keys") else row[0]

            if is_pg:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM news
                    WHERE published >= %s OR (published IS NULL AND created_at >= %s)
                """, (hours_ago(24), hours_ago(24)))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM news
                    WHERE published >= ? OR (published IS NULL AND created_at >= ?)
                """, (hours_ago(24), hours_ago(24)))
            row = cursor.fetchone()
            today = row["cnt"] if hasattr(row, "keys") else row[0]

            if is_pg:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE is_important = TRUE")
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE is_important = 1")
            row = cursor.fetchone()
            important = row["cnt"] if hasattr(row, "keys") else row[0]

            if is_pg:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE telegram_posted = TRUE")
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE telegram_posted = 1")
            row = cursor.fetchone()
            posted = row["cnt"] if hasattr(row, "keys") else row[0]

            cursor.execute("""
                SELECT category, COUNT(*) as cnt FROM news
                GROUP BY category ORDER BY cnt DESC LIMIT 14
            """)
            categories = {}
            for row in cursor.fetchall():
                cat = row["category"] if hasattr(row, "keys") else row[0]
                cnt = row["cnt"] if hasattr(row, "keys") else row[1]
                categories[cat] = cnt

            if is_pg:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE ai_sentiment != ''")
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM news WHERE ai_sentiment != ''")
            row = cursor.fetchone()
            analyzed = row["cnt"] if hasattr(row, "keys") else row[0]

            if is_pg:
                cursor.execute("SELECT COUNT(DISTINCT source) as cnt FROM news WHERE source != ''")
            else:
                cursor.execute("SELECT COUNT(DISTINCT source) as cnt FROM news WHERE source != ''")
            row = cursor.fetchone()
            sources = row["cnt"] if hasattr(row, "keys") else row[0]

            if is_pg:
                cursor.execute("SELECT COUNT(*) as cnt FROM telegram_log WHERE posted_at >= %s", (hours_ago(24),))
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM telegram_log WHERE posted_at >= ?", (hours_ago(24),))
            row = cursor.fetchone()
            telegram_today = row["cnt"] if hasattr(row, "keys") else row[0]

            stats = {
                "total_news": total,
                "today_news": today,
                "important_news": important,
                "telegram_posted": posted,
                "categories": categories,
                "ai_analyzed": analyzed,
                "active_sources": sources,
                "telegram_today": telegram_today,
            }

            cache.set(cache_key, stats, config.CACHE_STATS_TTL)
            return stats
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {
                "total_news": 0, "today_news": 0, "important_news": 0,
                "telegram_posted": 0, "categories": {}, "ai_analyzed": 0,
                "active_sources": 0, "telegram_today": 0,
            }


# ============================================================
# SCRAPE LOG (diagnostics)
# ============================================================
def log_scrape_result(feed_url, source, entries_found, entries_saved, error="", duration_ms=0):
    """Log a scrape result for diagnostics."""
    p = _param(0)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if _is_pg():
                cursor.execute(f"""
                    INSERT INTO scrape_log (feed_url, source, entries_found, entries_saved, error, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (feed_url, source, entries_found, entries_saved, error, duration_ms))
            else:
                cursor.execute("""
                    INSERT INTO scrape_log (feed_url, source, entries_found, entries_saved, error, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (feed_url, source, entries_found, entries_saved, error, duration_ms))
            conn.commit()
    except Exception as e:
        logger.error(f"Error logging scrape: {e}")


def get_scrape_logs(limit=50):
    """Get recent scrape logs for diagnostics."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute("""
                    SELECT * FROM scrape_log
                    ORDER BY scraped_at DESC NULLS LAST
                    LIMIT %s
                """, (limit,))
            else:
                cursor.execute("""
                    SELECT * FROM scrape_log
                    ORDER BY scraped_at DESC
                    LIMIT ?
                """, (limit,))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                for key in ("scraped_at",):
                    if item.get(key) and hasattr(item[key], "isoformat"):
                        item[key] = item[key].isoformat()
                results.append(item)
            return results
        except Exception as e:
            logger.error(f"Error getting scrape logs: {e}")
            return []


# ============================================================
# TELEGRAM LOG
# ============================================================
def is_telegram_posted(title):
    """Check if news was already posted to Telegram."""
    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"""
                    SELECT 1 FROM telegram_log WHERE title = {p} AND success = TRUE
                """, (title,))
            else:
                cursor.execute("""
                    SELECT 1 FROM telegram_log WHERE title = ? AND success = 1
                """, (title,))
            return cursor.fetchone() is not None
        except Exception:
            return False


def mark_telegram_posted(news_id, title, success=True, error=""):
    """Mark news as posted to Telegram."""
    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"""
                    INSERT INTO telegram_log (news_id, title, success, error)
                    VALUES (%s, %s, %s, %s)
                """, (news_id, title, success, error))
                if success and news_id:
                    cursor.execute(f"""
                        UPDATE news SET telegram_posted = TRUE WHERE id = %s
                    """, (news_id,))
            else:
                cursor.execute("""
                    INSERT INTO telegram_log (news_id, title, success, error)
                    VALUES (?, ?, ?, ?)
                """, (news_id, title, success, error))
                if success and news_id:
                    cursor.execute("""
                        UPDATE news SET telegram_posted = 1 WHERE id = ?
                    """, (news_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking telegram post: {e}")


def cleanup_telegram_log(days=3):
    """Clean up old telegram log entries."""
    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg:
                cursor.execute(f"DELETE FROM telegram_log WHERE posted_at < {p}", (hours_ago(days * 24),))
            else:
                cursor.execute("DELETE FROM telegram_log WHERE posted_at < ?", (hours_ago(days * 24),))
            conn.commit()
            logger.info("Telegram log cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning telegram log: {e}")


# ============================================================
# NEWSLETTER
# ============================================================
def newsletter_subscribe(email):
    if not email:
        return False, "Email is required"
    from utils import is_valid_email
    if not is_valid_email(email):
        return False, "Invalid email address"

    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"INSERT INTO newsletter (email) VALUES ({p})", (email,))
            else:
                cursor.execute("INSERT INTO newsletter (email) VALUES (?)", (email,))
            conn.commit()
            return True, "Subscribed successfully!"
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                if _is_pg():
                    cursor.execute(f"UPDATE newsletter SET active = TRUE WHERE email = {p}", (email,))
                else:
                    cursor.execute("UPDATE newsletter SET active = 1 WHERE email = ?", (email,))
                conn.commit()
                return True, "Re-subscribed successfully!"
            return False, f"Subscription failed: {str(e)}"


def newsletter_unsubscribe(email):
    if not email:
        return False, "Email is required"

    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"UPDATE newsletter SET active = FALSE WHERE email = {p}", (email,))
            else:
                cursor.execute("UPDATE newsletter SET active = 0 WHERE email = ?", (email,))
            conn.commit()
            return True, "Unsubscribed successfully!"
        except Exception as e:
            return False, f"Unsubscribe failed: {str(e)}"


def get_newsletter_subscribers():
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute("SELECT email, created_at FROM newsletter WHERE active = TRUE")
            else:
                cursor.execute("SELECT email, created_at FROM newsletter WHERE active = 1")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting subscribers: {e}")
            return []


def get_newsletter_stats():
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute("SELECT COUNT(*) as cnt FROM newsletter WHERE active = TRUE")
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM newsletter WHERE active = 1")
            row = cursor.fetchone()
            active = row["cnt"] if hasattr(row, "keys") else row[0]

            cursor.execute("SELECT COUNT(*) as cnt FROM newsletter")
            row = cursor.fetchone()
            total = row["cnt"] if hasattr(row, "keys") else row[0]

            return {"active": active, "total": total}
        except Exception:
            return {"active": 0, "total": 0}


# ============================================================
# PRICE ALERTS
# ============================================================
def get_active_alerts():
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute("SELECT * FROM price_alerts WHERE active = TRUE ORDER BY created_at DESC")
            else:
                cursor.execute("SELECT * FROM price_alerts WHERE active = 1 ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting alerts: {e}")
            return []


def add_price_alert(coin, symbol, target_price, condition="above"):
    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"""
                    INSERT INTO price_alerts (coin, symbol, target_price, condition)
                    VALUES ({p}, {p}, {p}, {p})
                """, (coin, symbol, float(target_price), condition))
            else:
                cursor.execute("""
                    INSERT INTO price_alerts (coin, symbol, target_price, condition)
                    VALUES (?, ?, ?, ?)
                """, (coin, symbol, float(target_price), condition))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding price alert: {e}")
            return False


def mark_alert_triggered(alert_id):
    p = _param(0)
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if _is_pg():
                cursor.execute(f"""
                    UPDATE price_alerts
                    SET active = FALSE, triggered = TRUE, triggered_at = CURRENT_TIMESTAMP
                    WHERE id = {p}
                """, (alert_id,))
            else:
                cursor.execute("""
                    UPDATE price_alerts
                    SET active = 0, triggered = 1, triggered_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (alert_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking alert triggered: {e}")


# ============================================================
# HEALTH CHECK
# ============================================================
def get_health_info():
    """Get comprehensive health information."""
    info = {
        "status": "healthy",
        "database": "connected",
        "database_type": "postgresql" if _is_pg() else "sqlite",
        "cache_backend": "redis" if get_redis() else "memory",
        "uptime": time.time(),
    }

    try:
        import psutil
        process = psutil.Process(os.getpid())
        info["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        info["memory_mb"] = 0

    # Check database connectivity
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
    except Exception as e:
        info["status"] = "unhealthy"
        info["database"] = f"error: {str(e)[:100]}"

    return info
