# -*- coding: utf-8 -*-
"""
CryptositNews v3 - Database Layer
PostgreSQL (pooled) with SQLite fallback.
Redis caching layer for hot data.
"""

import os
import sqlite3
import time
import threading
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from queue import Queue, Empty

from app.config import Config
from app.utils.helpers import setup_logger

logger = setup_logger("db")

# ============================================================
# Redis singleton
# ============================================================
_redis_client = None
_redis_lock = threading.Lock()


def _get_redis():
    """Get Redis client singleton. Returns None if Redis is not configured."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not Config.REDIS_URL:
        return None
    with _redis_lock:
        if _redis_client is None and Config.REDIS_URL:
            try:
                import redis
                _redis_client = redis.from_url(
                    Config.REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                _redis_client.ping()
                logger.info("Redis connected successfully")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
                _redis_client = None
    return _redis_client


# ============================================================
# In-memory cache (fallback when Redis is unavailable)
# ============================================================
_mem_cache = {}
_mem_cache_lock = threading.Lock()


def _mem_cache_get(key):
    with _mem_cache_lock:
        entry = _mem_cache.get(key)
        if entry and entry["expires"] > time.time():
            return entry["value"]
        return None


def _mem_cache_set(key, value, ttl=300):
    with _mem_cache_lock:
        _mem_cache[key] = {"value": value, "expires": time.time() + ttl}
        # Prune old entries if cache grows too large
        if len(_mem_cache) > 10000:
            now = time.time()
            expired = [k for k, v in _mem_cache.items() if v["expires"] <= now]
            for k in expired[:2000]:
                del _mem_cache[k]


def _mem_cache_delete(key):
    with _mem_cache_lock:
        _mem_cache.pop(key, None)


def cache_get(key, ttl=None):
    """Get from Redis or in-memory cache."""
    redis_client = _get_redis()
    if redis_client:
        try:
            val = redis_client.get(key)
            if val is not None:
                return val
        except Exception:
            pass
    return _mem_cache_get(key)


def cache_set(key, value, ttl=300):
    """Set in Redis and in-memory cache."""
    redis_client = _get_redis()
    if redis_client:
        try:
            redis_client.setex(key, ttl, value)
        except Exception:
            pass
    _mem_cache_set(key, value, ttl)


def cache_delete(key):
    """Delete from Redis and in-memory cache."""
    redis_client = _get_redis()
    if redis_client:
        try:
            redis_client.delete(key)
        except Exception:
            pass
    _mem_cache_delete(key)


def cache_get_json(key):
    """Get JSON from cache."""
    import json
    raw = cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def cache_set_json(key, data, ttl=300):
    """Set JSON to cache."""
    import json
    cache_set(key, json.dumps(data, default=str), ttl)


# ============================================================
# Database detection and path
# ============================================================
_pg_pool = None
_sqlite_pool = None
_db_path = os.path.join(
    os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data", "cryptositnews.db",
)


def is_pg():
    """Check if using PostgreSQL."""
    return bool(Config.DATABASE_URL and Config.DATABASE_URL.startswith("postgresql"))


# ============================================================
# PostgreSQL Connection Pool
# ============================================================
def _get_pg_pool():
    """Get or create the PostgreSQL ThreadedConnectionPool."""
    global _pg_pool
    if _pg_pool is None:
        import psycopg2.pool
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=Config.DB_POOL_SIZE + Config.DB_MAX_OVERFLOW,
            dsn=Config.DATABASE_URL,
        )
        logger.info(
            f"PostgreSQL connection pool created "
            f"(size={Config.DB_POOL_SIZE}, overflow={Config.DB_MAX_OVERFLOW})"
        )
    return _pg_pool


# ============================================================
# SQLite Connection Pool (Queue-based thread-safe)
# ============================================================
def _get_sqlite_pool():
    global _sqlite_pool
    if _sqlite_pool is None:
        _sqlite_pool = Queue(maxsize=Config.DB_POOL_SIZE + Config.DB_MAX_OVERFLOW)
        os.makedirs(os.path.dirname(_db_path), exist_ok=True)
        for _ in range(Config.DB_POOL_SIZE):
            conn = sqlite3.connect(_db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            _sqlite_pool.put(conn)
        logger.info(f"SQLite connection pool created (size={Config.DB_POOL_SIZE})")
    return _sqlite_pool


# ============================================================
# get_db context manager
# ============================================================
@contextmanager
def get_db():
    """Get a database connection from the pool (PostgreSQL or SQLite)."""
    conn = None
    try:
        if is_pg():
            pool = _get_pg_pool()
            conn = pool.getconn()
            conn.autocommit = True
            yield conn
            pool.putconn(conn)
        elif _sqlite_pool or _get_sqlite_pool():
            conn = _sqlite_pool.get()
            yield conn
            _sqlite_pool.put(conn)
        else:
            conn = sqlite3.connect(_db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            yield conn
            conn.close()
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            try:
                if is_pg():
                    _get_pg_pool().putconn(conn, close=True)
                else:
                    conn.close()
            except Exception:
                pass
        raise


def _pg_placeholder(n):
    """Generate PostgreSQL placeholders: %s, %s, ..."""
    return ", ".join(["%s"] * n)


def _qs_placeholder(n):
    """Generate SQLite placeholders: ?, ?, ..."""
    return ", ".join(["?"] * n)


# ============================================================
# CoinGecko rate limiter
# ============================================================
_cg_lock = threading.Lock()
_cg_last_call = 0


def coingecko_wait():
    """Rate-limit CoinGecko API calls."""
    global _cg_last_call
    with _cg_lock:
        elapsed = time.time() - _cg_last_call
        if elapsed < Config.COINGECKO_DELAY:
            time.sleep(Config.COINGECKO_DELAY - elapsed)
        _cg_last_call = time.time()


# ============================================================
# INIT DB - Schema creation
# ============================================================
def init_db():
    """Create all tables if they don't exist."""
    if is_pg():
        _init_pg()
    else:
        _init_sqlite()
    logger.info("Database initialized successfully")


def _init_pg():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                url TEXT UNIQUE,
                source TEXT DEFAULT '',
                category TEXT DEFAULT 'crypto',
                sentiment TEXT DEFAULT 'neutral',
                ai_summary TEXT DEFAULT '',
                ai_sentiment TEXT DEFAULT '',
                ai_reason TEXT DEFAULT '',
                is_important BOOLEAN DEFAULT FALSE,
                priority_score INTEGER DEFAULT 0,
                language TEXT DEFAULT 'en',
                image_url TEXT DEFAULT '',
                published_at TIMESTAMP,
                scraped_at TIMESTAMP DEFAULT NOW(),
                telegram_posted BOOLEAN DEFAULT FALSE,
                telegram_posted_at TIMESTAMP,
                telegram_error TEXT DEFAULT '',
                views INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT DEFAULT '',
                role TEXT DEFAULT 'free',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                last_login TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL,
                device_info TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP,
                revoked BOOLEAN DEFAULT FALSE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, news_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_log (
                id SERIAL PRIMARY KEY,
                news_id INTEGER,
                title TEXT,
                posted BOOLEAN DEFAULT FALSE,
                error TEXT DEFAULT '',
                posted_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id SERIAL PRIMARY KEY,
                source_url TEXT,
                feed_category TEXT DEFAULT '',
                status TEXT DEFAULT '',
                articles_found INTEGER DEFAULT 0,
                articles_saved INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                scraped_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                coin TEXT NOT NULL,
                symbol TEXT NOT NULL,
                target_price NUMERIC(20, 8) NOT NULL,
                condition TEXT CHECK (condition IN ('above', 'below')),
                is_active BOOLEAN DEFAULT TRUE,
                triggered BOOLEAN DEFAULT FALSE,
                triggered_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                id SERIAL PRIMARY KEY,
                content_hash TEXT UNIQUE NOT NULL,
                ai_summary TEXT DEFAULT '',
                ai_sentiment TEXT DEFAULT 'neutral',
                ai_reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS newsletters (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                subscribed_at TIMESTAMP DEFAULT NOW(),
                unsubscribed_at TIMESTAMP
            )
        """)
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_important ON news(is_important) WHERE is_important = TRUE")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news(ai_sentiment)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_telegram ON news(telegram_posted) WHERE telegram_posted = FALSE")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_source ON news(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_refresh_hash ON refresh_tokens(token_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_telegram_title ON telegram_log(title)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active) WHERE is_active = TRUE")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_cache_hash ON ai_cache(content_hash)")


def _init_sqlite():
    os.makedirs(os.path.dirname(_db_path), exist_ok=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                url TEXT UNIQUE,
                source TEXT DEFAULT '',
                category TEXT DEFAULT 'crypto',
                sentiment TEXT DEFAULT 'neutral',
                ai_summary TEXT DEFAULT '',
                ai_sentiment TEXT DEFAULT '',
                ai_reason TEXT DEFAULT '',
                is_important INTEGER DEFAULT 0,
                priority_score INTEGER DEFAULT 0,
                language TEXT DEFAULT 'en',
                image_url TEXT DEFAULT '',
                published_at TIMESTAMP,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                telegram_posted INTEGER DEFAULT 0,
                telegram_posted_at TIMESTAMP,
                telegram_error TEXT DEFAULT '',
                views INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT DEFAULT '',
                role TEXT DEFAULT 'free',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL,
                device_info TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                revoked INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                news_id INTEGER REFERENCES news(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, news_id)
            );

            CREATE TABLE IF NOT EXISTS telegram_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER,
                title TEXT,
                posted INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT,
                feed_category TEXT DEFAULT '',
                status TEXT DEFAULT '',
                articles_found INTEGER DEFAULT 0,
                articles_saved INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                coin TEXT NOT NULL,
                symbol TEXT NOT NULL,
                target_price REAL NOT NULL,
                condition TEXT CHECK (condition IN ('above', 'below')),
                is_active INTEGER DEFAULT 1,
                triggered INTEGER DEFAULT 0,
                triggered_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ai_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_hash TEXT UNIQUE NOT NULL,
                ai_summary TEXT DEFAULT '',
                ai_sentiment TEXT DEFAULT 'neutral',
                ai_reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS newsletters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                is_active INTEGER DEFAULT 1,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                unsubscribed_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
            CREATE INDEX IF NOT EXISTS idx_news_important ON news(is_important);
            CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news(ai_sentiment);
            CREATE INDEX IF NOT EXISTS idx_news_telegram ON news(telegram_posted);
            CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_hash ON refresh_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id);
            CREATE INDEX IF NOT EXISTS idx_telegram_title ON telegram_log(title);
            CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);
            CREATE INDEX IF NOT EXISTS idx_ai_cache_hash ON ai_cache(content_hash);
        """)
        conn.commit()


# ============================================================
# NEWS OPERATIONS
# ============================================================
def save_news(title, summary, url, source, category="crypto",
              published_at=None, language="en", image_url=""):
    """Save a single news article. Returns (news_id, is_new)."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        if is_pg():
            cursor.execute(
                "SELECT id FROM news WHERE url = %s LIMIT 1", (url,)
            )
        else:
            cursor.execute(
                "SELECT id FROM news WHERE url = ? LIMIT 1", (url,)
            )

        existing = cursor.fetchone()

        if existing:
            # Update existing record
            if is_pg():
                cursor.execute("""
                    UPDATE news SET title=%s, summary=%s, source=%s, category=%s,
                        language=%s, image_url=%s, scraped_at=%s
                    WHERE id=%s
                """, (title, summary, source, category, language, image_url, now, existing[0]))
            else:
                cursor.execute("""
                    UPDATE news SET title=?, summary=?, source=?, category=?,
                        language=?, image_url=?, scraped_at=?
                    WHERE id=?
                """, (title, summary, source, category, language, image_url, now, existing[0]))
            conn.commit()
            return existing[0], False

        # Insert new
        if is_pg():
            cursor.execute("""
                INSERT INTO news (title, summary, url, source, category, language, image_url, published_at, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (title, summary, url, source, category, language, image_url, published_at or now, now))
            news_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO news (title, summary, url, source, category, language, image_url, published_at, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, summary, url, source, category, language, image_url, published_at or now, now))
            news_id = cursor.lastrowid

        conn.commit()

        # Invalidate news caches
        cache_delete("news:homepage")
        cache_delete("news:latest")
        cache_delete("news:trending")

        return news_id, True


def save_news_batch(news_items):
    """Save multiple news articles efficiently.

    Args:
        news_items: List of dicts with keys: title, summary, url, source, category,
                    published_at, language, image_url.

    Returns:
        dict: {saved: int, duplicates: int, errors: int}
    """
    saved = 0
    duplicates = 0
    errors = 0

    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        for item in news_items:
            try:
                title = item.get("title", "").strip()
                url = item.get("url", "").strip()
                if not title or not url:
                    continue

                # Check for duplicate
                if is_pg():
                    cursor.execute("SELECT id FROM news WHERE url = %s LIMIT 1", (url,))
                else:
                    cursor.execute("SELECT id FROM news WHERE url = ? LIMIT 1", (url,))

                if cursor.fetchone():
                    duplicates += 1
                    continue

                # Insert
                if is_pg():
                    cursor.execute("""
                        INSERT INTO news (title, summary, url, source, category, language, image_url, published_at, scraped_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        title,
                        item.get("summary", "")[:1000],
                        url,
                        item.get("source", "")[:100],
                        item.get("category", "crypto"),
                        item.get("language", "en"),
                        item.get("image_url", "")[:500],
                        item.get("published_at") or now,
                        now,
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO news (title, summary, url, source, category, language, image_url, published_at, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        title,
                        item.get("summary", "")[:1000],
                        url,
                        item.get("source", "")[:100],
                        item.get("category", "crypto"),
                        item.get("language", "en"),
                        item.get("image_url", "")[:500],
                        item.get("published_at") or now,
                        now,
                    ))
                saved += 1
            except Exception as e:
                errors += 1
                logger.debug(f"Save news error: {e}")

        conn.commit()

    # Invalidate caches
    if saved > 0:
        cache_delete("news:homepage")
        cache_delete("news:latest")
        cache_delete("news:trending")

    return {"saved": saved, "duplicates": duplicates, "errors": errors}


def get_news(limit=20, offset=0, category=None, important_only=False,
             language=None, search=None):
    """Get news articles with filters and caching."""
    cache_key = f"news:list:{limit}:{offset}:{category}:{important_only}:{language}:{search}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached

    with get_db() as conn:
        cursor = conn.cursor()

        conditions = []
        params = []

        if category and category != "all":
            conditions.append("category = %s" if is_pg() else "category = ?")
            params.append(category)

        if important_only:
            conditions.append("is_important = TRUE" if is_pg() else "is_important = 1")

        if language:
            conditions.append("language = %s" if is_pg() else "language = ?")
            params.append(language)

        if search:
            search_like = f"%{search}%"
            conditions.append("(title LIKE %s OR summary LIKE %s)" if is_pg() else "(title LIKE ? OR summary LIKE ?)")
            params.extend([search_like, search_like])

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
            SELECT id, title, summary, url, source, category, sentiment,
                   ai_summary, ai_sentiment, ai_reason, is_important,
                   priority_score, language, image_url, published_at,
                   scraped_at, telegram_posted, views
            FROM news {where}
            ORDER BY published_at DESC
            LIMIT %s OFFSET %s
        """ if is_pg() else f"""
            SELECT id, title, summary, url, source, category, sentiment,
                   ai_summary, ai_sentiment, ai_reason, is_important,
                   priority_score, language, image_url, published_at,
                   scraped_at, telegram_posted, views
            FROM news {where}
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

    news_list = []
    for row in rows:
        news_list.append({
            "id": row[0],
            "title": row[1],
            "summary": row[2] or "",
            "url": row[3] or "",
            "source": row[4] or "",
            "category": row[5] or "crypto",
            "sentiment": row[6] or "neutral",
            "ai_summary": row[7] or "",
            "ai_sentiment": row[8] or "neutral",
            "ai_reason": row[9] or "",
            "is_important": bool(row[10]),
            "priority_score": row[11] or 0,
            "language": row[12] or "en",
            "image_url": row[13] or "",
            "published_at": str(row[14]) if row[14] else "",
            "scraped_at": str(row[15]) if row[15] else "",
            "telegram_posted": bool(row[16]),
            "views": row[17] or 0,
        })

    cache_set_json(cache_key, news_list, ttl=Config.CACHE_NEWS_TTL)
    return news_list


def get_news_by_id(news_id):
    """Get a single news article by ID."""
    cache_key = f"news:{news_id}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached

    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT id, title, summary, url, source, category, sentiment, "
                "ai_summary, ai_sentiment, ai_reason, is_important, priority_score, "
                "language, image_url, published_at, scraped_at, telegram_posted, views "
                "FROM news WHERE id = %s", (news_id,)
            )
        else:
            cursor.execute(
                "SELECT id, title, summary, url, source, category, sentiment, "
                "ai_summary, ai_sentiment, ai_reason, is_important, priority_score, "
                "language, image_url, published_at, scraped_at, telegram_posted, views "
                "FROM news WHERE id = ?", (news_id,)
            )
        row = cursor.fetchone()

    if not row:
        return None

    result = {
        "id": row[0],
        "title": row[1],
        "summary": row[2] or "",
        "url": row[3] or "",
        "source": row[4] or "",
        "category": row[5] or "crypto",
        "sentiment": row[6] or "neutral",
        "ai_summary": row[7] or "",
        "ai_sentiment": row[8] or "neutral",
        "ai_reason": row[9] or "",
        "is_important": bool(row[10]),
        "priority_score": row[11] or 0,
        "language": row[12] or "en",
        "image_url": row[13] or "",
        "published_at": str(row[14]) if row[14] else "",
        "scraped_at": str(row[15]) if row[15] else "",
        "telegram_posted": bool(row[16]),
        "views": row[17] or 0,
    }

    cache_set_json(cache_key, result, ttl=Config.CACHE_DEFAULT_TTL)
    return result


def search_news(query, limit=20, offset=0):
    """Full-text search news by title and summary."""
    with get_db() as conn:
        cursor = conn.cursor()
        search_like = f"%{query}%"

        if is_pg():
            cursor.execute("""
                SELECT id, title, summary, url, source, category,
                       ai_summary, ai_sentiment, is_important, published_at
                FROM news
                WHERE title ILIKE %s OR summary ILIKE %s
                ORDER BY published_at DESC
                LIMIT %s OFFSET %s
            """, (search_like, search_like, limit, offset))
        else:
            cursor.execute("""
                SELECT id, title, summary, url, source, category,
                       ai_summary, ai_sentiment, is_important, published_at
                FROM news
                WHERE title LIKE ? OR summary LIKE ?
                ORDER BY published_at DESC
                LIMIT ? OFFSET ?
            """, (search_like, search_like, limit, offset))

        rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row[0], "title": row[1], "summary": row[2] or "",
            "url": row[3] or "", "source": row[4] or "",
            "category": row[5] or "crypto",
            "ai_summary": row[6] or "", "ai_sentiment": row[7] or "neutral",
            "is_important": bool(row[8]),
            "published_at": str(row[9]) if row[9] else "",
        })
    return results


def get_related_news(news_id, limit=5):
    """Get related news based on category."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                SELECT category FROM news WHERE id = %s LIMIT 1
            """, (news_id,))
        else:
            cursor.execute("""
                SELECT category FROM news WHERE id = ? LIMIT 1
            """, (news_id,))

        row = cursor.fetchone()
        if not row:
            return []

        category = row[0]

        if is_pg():
            cursor.execute("""
                SELECT id, title, summary, url, source, category,
                       ai_sentiment, published_at
                FROM news
                WHERE category = %s AND id != %s
                ORDER BY published_at DESC
                LIMIT %s
            """, (category, news_id, limit))
        else:
            cursor.execute("""
                SELECT id, title, summary, url, source, category,
                       ai_sentiment, published_at
                FROM news
                WHERE category = ? AND id != ?
                ORDER BY published_at DESC
                LIMIT ?
            """, (category, news_id, limit))

        rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row[0], "title": row[1], "summary": row[2] or "",
            "url": row[3] or "", "source": row[4] or "",
            "category": row[5] or "crypto",
            "ai_sentiment": row[6] or "neutral",
            "published_at": str(row[7]) if row[7] else "",
        })
    return results


# ============================================================
# AI CACHE
# ============================================================
def get_ai_cached(content_hash):
    """Get cached AI analysis by content hash."""
    cache_key = f"ai_db:{content_hash}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached

    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT ai_summary, ai_sentiment, ai_reason FROM ai_cache WHERE content_hash = %s",
                (content_hash,),
            )
        else:
            cursor.execute(
                "SELECT ai_summary, ai_sentiment, ai_reason FROM ai_cache WHERE content_hash = ?",
                (content_hash,),
            )
        row = cursor.fetchone()

    if not row:
        return None

    result = {
        "ai_summary": row[0] or "",
        "ai_sentiment": row[1] or "neutral",
        "ai_reason": row[2] or "",
    }
    cache_set_json(cache_key, result, ttl=Config.CACHE_AI_TTL)
    return result


def save_ai_cache(content_hash, ai_summary, ai_sentiment, ai_reason):
    """Save AI analysis result to cache."""
    cache_key = f"ai_db:{content_hash}"
    data = {
        "ai_summary": ai_summary,
        "ai_sentiment": ai_sentiment,
        "ai_reason": ai_reason,
    }
    cache_set_json(cache_key, data, ttl=Config.CACHE_AI_TTL)

    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                INSERT INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (content_hash) DO UPDATE SET
                    ai_summary = EXCLUDED.ai_summary,
                    ai_sentiment = EXCLUDED.ai_sentiment,
                    ai_reason = EXCLUDED.ai_reason
            """, (content_hash, ai_summary, ai_sentiment, ai_reason))
        else:
            cursor.execute("""
                INSERT INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    ai_summary = excluded.ai_summary,
                    ai_sentiment = excluded.ai_sentiment,
                    ai_reason = excluded.ai_reason
            """, (content_hash, ai_summary, ai_sentiment, ai_reason))
        conn.commit()


def batch_save_ai_cache(items):
    """Save multiple AI cache entries efficiently.

    Args:
        items: List of dicts with keys: content_hash, ai_summary, ai_sentiment, ai_reason.
    """
    if not items:
        return

    with get_db() as conn:
        cursor = conn.cursor()
        for item in items:
            content_hash = item.get("content_hash", "")
            if not content_hash:
                continue
            ai_summary = item.get("ai_summary", "")
            ai_sentiment = item.get("ai_sentiment", "neutral")
            ai_reason = item.get("ai_reason", "")

            # Update memory cache
            cache_key = f"ai_db:{content_hash}"
            cache_set_json(cache_key, {
                "ai_summary": ai_summary,
                "ai_sentiment": ai_sentiment,
                "ai_reason": ai_reason,
            }, ttl=Config.CACHE_AI_TTL)

            if is_pg():
                cursor.execute("""
                    INSERT INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (content_hash) DO UPDATE SET
                        ai_summary = EXCLUDED.ai_summary,
                        ai_sentiment = EXCLUDED.ai_sentiment,
                        ai_reason = EXCLUDED.ai_reason
                """, (content_hash, ai_summary, ai_sentiment, ai_reason))
            else:
                cursor.execute("""
                    INSERT INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(content_hash) DO UPDATE SET
                        ai_summary = excluded.ai_summary,
                        ai_sentiment = excluded.ai_sentiment,
                        ai_reason = excluded.ai_reason
                """, (content_hash, ai_summary, ai_sentiment, ai_reason))

        conn.commit()
    logger.debug(f"[db] batch_save_ai_cache: {len(items)} entries saved")


def batch_get_ai_cached(content_hashes):
    """Batch retrieve AI cached entries.

    Args:
        content_hashes: List of content hash strings.

    Returns:
        dict: {content_hash: {ai_summary, ai_sentiment, ai_reason}} for cached entries.
    """
    if not content_hashes:
        return {}

    results = {}
    uncached = []

    # Check memory cache first
    for ch in content_hashes:
        cache_key = f"ai_db:{ch}"
        cached = cache_get_json(cache_key)
        if cached:
            results[ch] = cached
        else:
            uncached.append(ch)

    if not uncached:
        return results

    # Check DB
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            ph = _pg_placeholder(len(uncached))
            cursor.execute(
                f"SELECT content_hash, ai_summary, ai_sentiment, ai_reason FROM ai_cache WHERE content_hash IN ({ph})",
                tuple(uncached),
            )
        else:
            ph = _qs_placeholder(len(uncached))
            cursor.execute(
                f"SELECT content_hash, ai_summary, ai_sentiment, ai_reason FROM ai_cache WHERE content_hash IN ({ph})",
                tuple(uncached),
            )
        rows = cursor.fetchall()

    for row in rows:
        ch = row[0]
        data = {
            "ai_summary": row[1] or "",
            "ai_sentiment": row[2] or "neutral",
            "ai_reason": row[3] or "",
        }
        results[ch] = data
        cache_key = f"ai_db:{ch}"
        cache_set_json(cache_key, data, ttl=Config.CACHE_AI_TTL)

    return results


# ============================================================
# USER OPERATIONS
# ============================================================
def create_user(email, password_hash=None, role="free"):
    """Create a new user. Returns user_id or raises on duplicate."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        if is_pg():
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, is_active, created_at)
                VALUES (%s, %s, %s, TRUE, %s)
                RETURNING id
            """, (email, password_hash or "", role, now))
            user_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, 1, ?)
            """, (email, password_hash or "", role, now))
            user_id = cursor.lastrowid

        conn.commit()
    return user_id


def get_user_by_email(email):
    """Get user by email address."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT id, email, role, is_active, created_at, last_login FROM users WHERE email = %s",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT id, email, role, is_active, created_at, last_login FROM users WHERE email = ?",
                (email,),
            )
        row = cursor.fetchone()

    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "role": row[2] or "free",
        "is_active": bool(row[3]),
        "created_at": str(row[4]) if row[4] else "",
        "last_login": str(row[5]) if row[5] else None,
    }


def get_user_with_password(email):
    """Get user with password hash for authentication."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT id, email, password_hash, role, is_active FROM users WHERE email = %s",
                (email,),
            )
        else:
            cursor.execute(
                "SELECT id, email, password_hash, role, is_active FROM users WHERE email = ?",
                (email,),
            )
        row = cursor.fetchone()

    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "password_hash": row[2] or "",
        "role": row[3] or "free", "is_active": bool(row[4]),
    }


def update_last_login(user_id):
    """Update user's last login timestamp."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        if is_pg():
            cursor.execute("UPDATE users SET last_login = %s WHERE id = %s", (now, user_id))
        else:
            cursor.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
        conn.commit()


def get_user_by_id(user_id):
    """Get user by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT id, email, role, is_active, created_at, last_login FROM users WHERE id = %s",
                (user_id,),
            )
        else:
            cursor.execute(
                "SELECT id, email, role, is_active, created_at, last_login FROM users WHERE id = ?",
                (user_id,),
            )
        row = cursor.fetchone()

    if not row:
        return None
    return {
        "id": row[0], "email": row[1], "role": row[2] or "free",
        "is_active": bool(row[3]),
        "created_at": str(row[4]) if row[4] else "",
        "last_login": str(row[5]) if row[5] else None,
    }


# ============================================================
# REFRESH TOKENS
# ============================================================
def save_refresh_token(user_id, token_hash, device_info="", ip_address="",
                       expires_days=30):
    """Save a refresh token to the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=expires_days)

        if is_pg():
            cursor.execute("""
                INSERT INTO refresh_tokens (user_id, token_hash, device_info, ip_address, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, token_hash, device_info, ip_address, now, expires_at))
        else:
            cursor.execute("""
                INSERT INTO refresh_tokens (user_id, token_hash, device_info, ip_address, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, token_hash, device_info, ip_address, now, expires_at))
        conn.commit()


def verify_refresh_token_db(token_hash):
    """Verify a refresh token is valid and not revoked.

    Returns:
        dict with user_id if valid, None otherwise.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        if is_pg():
            cursor.execute("""
                SELECT id, user_id, expires_at, revoked
                FROM refresh_tokens
                WHERE token_hash = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (token_hash,))
        else:
            cursor.execute("""
                SELECT id, user_id, expires_at, revoked
                FROM refresh_tokens
                WHERE token_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (token_hash,))
        row = cursor.fetchone()

    if not row:
        return None

    # Check if revoked
    if row[3]:
        return None

    # Check if expired
    expires_at = row[2]
    if expires_at:
        try:
            if hasattr(expires_at, "timestamp"):
                exp_ts = expires_at.timestamp()
            else:
                from dateutil.parser import parse
                exp_ts = parse(str(expires_at)).timestamp()
            if now.timestamp() > exp_ts:
                return None
        except Exception:
            pass

    return {"id": row[0], "user_id": row[1]}


def delete_refresh_token(token_hash):
    """Delete (revoke) a specific refresh token."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE token_hash = %s",
                (token_hash,),
            )
        else:
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
                (token_hash,),
            )
        conn.commit()


def revoke_all_user_tokens(user_id):
    """Revoke all refresh tokens for a user."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = %s",
                (user_id,),
            )
        else:
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?",
                (user_id,),
            )
        conn.commit()


# ============================================================
# BOOKMARKS
# ============================================================
def add_bookmark(user_id, news_id):
    """Add a bookmark. Returns True if new, False if exists."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            now = datetime.now(timezone.utc)
            if is_pg():
                cursor.execute("""
                    INSERT INTO bookmarks (user_id, news_id, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, news_id) DO NOTHING
                """, (user_id, news_id, now))
            else:
                cursor.execute("""
                    INSERT OR IGNORE INTO bookmarks (user_id, news_id, created_at)
                    VALUES (?, ?, ?)
                """, (user_id, news_id, now))
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            return False


def remove_bookmark(user_id, news_id):
    """Remove a bookmark."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "DELETE FROM bookmarks WHERE user_id = %s AND news_id = %s",
                (user_id, news_id),
            )
        else:
            cursor.execute(
                "DELETE FROM bookmarks WHERE user_id = ? AND news_id = ?",
                (user_id, news_id),
            )
        conn.commit()
        return cursor.rowcount > 0


def get_user_bookmarks(user_id, limit=20, offset=0):
    """Get user's bookmarked news articles."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                SELECT n.id, n.title, n.summary, n.url, n.source, n.category,
                       n.ai_sentiment, n.is_important, n.published_at, b.created_at AS bookmarked_at
                FROM bookmarks b
                JOIN news n ON b.news_id = n.id
                WHERE b.user_id = %s
                ORDER BY b.created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))
        else:
            cursor.execute("""
                SELECT n.id, n.title, n.summary, n.url, n.source, n.category,
                       n.ai_sentiment, n.is_important, n.published_at, b.created_at AS bookmarked_at
                FROM bookmarks b
                JOIN news n ON b.news_id = n.id
                WHERE b.user_id = ?
                ORDER BY b.created_at DESC
                LIMIT ? OFFSET ?
            """, (user_id, limit, offset))

        rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row[0], "title": row[1], "summary": row[2] or "",
            "url": row[3] or "", "source": row[4] or "",
            "category": row[5] or "crypto",
            "ai_sentiment": row[6] or "neutral",
            "is_important": bool(row[7]),
            "published_at": str(row[8]) if row[8] else "",
            "bookmarked_at": str(row[9]) if row[9] else "",
        })
    return results


def is_bookmarked(user_id, news_id):
    """Check if a news article is bookmarked by user."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT 1 FROM bookmarks WHERE user_id = %s AND news_id = %s LIMIT 1",
                (user_id, news_id),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM bookmarks WHERE user_id = ? AND news_id = ? LIMIT 1",
                (user_id, news_id),
            )
        return cursor.fetchone() is not None


# ============================================================
# STATS
# ============================================================
def get_stats():
    """Get platform statistics with caching."""
    cached = cache_get_json("stats:global")
    if cached:
        return cached

    with get_db() as conn:
        cursor = conn.cursor()

        # Total news
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM news")
        else:
            cursor.execute("SELECT COUNT(*) FROM news")
        total_news = cursor.fetchone()[0]

        # News today
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM news WHERE scraped_at >= NOW() - INTERVAL '1 day'")
        else:
            cursor.execute("SELECT COUNT(*) FROM news WHERE scraped_at >= datetime('now', '-1 day')")
        news_today = cursor.fetchone()[0]

        # Important news
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM news WHERE is_important = TRUE")
        else:
            cursor.execute("SELECT COUNT(*) FROM news WHERE is_important = 1")
        important_count = cursor.fetchone()[0]

        # Telegram posted
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM news WHERE telegram_posted = TRUE")
        else:
            cursor.execute("SELECT COUNT(*) FROM news WHERE telegram_posted = 1")
        telegram_count = cursor.fetchone()[0]

        # Total users
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM users")
        else:
            cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        # Active users (last 7 days)
        if is_pg():
            cursor.execute("SELECT COUNT(*) FROM users WHERE last_login >= NOW() - INTERVAL '7 days'")
        else:
            cursor.execute("SELECT COUNT(*) FROM users WHERE last_login >= datetime('now', '-7 days')")
        active_users = cursor.fetchone()[0]

        # Sentiment breakdown
        if is_pg():
            cursor.execute("""
                SELECT ai_sentiment, COUNT(*) FROM news
                WHERE ai_sentiment IS NOT NULL AND ai_sentiment != ''
                GROUP BY ai_sentiment
            """)
        else:
            cursor.execute("""
                SELECT ai_sentiment, COUNT(*) FROM news
                WHERE ai_sentiment IS NOT NULL AND ai_sentiment != ''
                GROUP BY ai_sentiment
            """)
        sentiment_rows = cursor.fetchall()

    sentiments = {}
    for row in sentiment_rows:
        sentiments[row[0] or "neutral"] = row[1]

    result = {
        "total_news": total_news,
        "news_today": news_today,
        "important_count": important_count,
        "telegram_count": telegram_count,
        "total_users": total_users,
        "active_users": active_users,
        "sentiments": sentiments,
        "cached": False,
    }
    cache_set_json("stats:global", result, ttl=Config.CACHE_STATS_TTL)
    return result


# ============================================================
# TELEGRAM LOG
# ============================================================
def is_telegram_posted(title):
    """Check if a news title was already posted to Telegram."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "SELECT 1 FROM telegram_log WHERE title = %s AND posted = TRUE LIMIT 1",
                (title[:500],),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM telegram_log WHERE title = ? AND posted = 1 LIMIT 1",
                (title[:500],),
            )
        return cursor.fetchone() is not None


def mark_telegram_posted(news_id=None, title=None, success=True, error=""):
    """Log Telegram posting attempt."""
    if not title and not news_id:
        return
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        if is_pg():
            cursor.execute("""
                INSERT INTO telegram_log (news_id, title, posted, error, posted_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (news_id, (title or "")[:500], success, error, now))
        else:
            cursor.execute("""
                INSERT INTO telegram_log (news_id, title, posted, error, posted_at)
                VALUES (?, ?, ?, ?, ?)
            """, (news_id, (title or "")[:500], success, error, now))
        conn.commit()


def cleanup_telegram_log(days=3):
    """Remove old Telegram log entries."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "DELETE FROM telegram_log WHERE posted_at < NOW() - INTERVAL %s",
                (f"{days} days",),
            )
        else:
            cursor.execute(
                "DELETE FROM telegram_log WHERE posted_at < datetime('now', ?)",
                (f"-{days} days",),
            )
        deleted = cursor.rowcount
        conn.commit()
    if deleted > 0:
        logger.info(f"[db] Cleaned up {deleted} old Telegram log entries")
    return deleted


# ============================================================
# SCRAPE LOG
# ============================================================
def log_scrape_result(source_url, feed_category, status, articles_found,
                      articles_saved, error=""):
    """Log a scrape result for monitoring."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                INSERT INTO scrape_log (source_url, feed_category, status, articles_found, articles_saved, error, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (source_url[:500], feed_category, status, articles_found, articles_saved, error[:1000]))
        else:
            cursor.execute("""
                INSERT INTO scrape_log (source_url, feed_category, status, articles_found, articles_saved, error, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (source_url[:500], feed_category, status, articles_found, articles_saved, error[:1000]))
        conn.commit()


def get_scrape_stats():
    """Get recent scrape statistics."""
    cached = cache_get_json("stats:scrape")
    if cached:
        return cached

    with get_db() as conn:
        cursor = conn.cursor()

        # Last scrape results
        if is_pg():
            cursor.execute("""
                SELECT source_url, feed_category, status, articles_found, articles_saved, error, scraped_at
                FROM scrape_log
                ORDER BY scraped_at DESC
                LIMIT 50
            """)
        else:
            cursor.execute("""
                SELECT source_url, feed_category, status, articles_found, articles_saved, error, scraped_at
                FROM scrape_log
                ORDER BY scraped_at DESC
                LIMIT 50
            """)
        rows = cursor.fetchall()

        # Summary stats
        if is_pg():
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'success') as success,
                    COUNT(*) FILTER (WHERE status = 'error') as errors,
                    SUM(articles_saved) as total_saved
                FROM scrape_log
                WHERE scraped_at >= NOW() - INTERVAL '24 hours'
            """)
        else:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
                    COALESCE(SUM(articles_saved), 0) as total_saved
                FROM scrape_log
                WHERE scraped_at >= datetime('now', '-24 hours')
            """)
        summary = cursor.fetchone()

    recent = []
    for row in rows:
        recent.append({
            "source_url": row[0] or "",
            "feed_category": row[1] or "",
            "status": row[2] or "",
            "articles_found": row[3] or 0,
            "articles_saved": row[4] or 0,
            "error": row[5] or "",
            "scraped_at": str(row[6]) if row[6] else "",
        })

    result = {
        "recent": recent,
        "summary": {
            "total_feeds": summary[0] or 0,
            "successful": summary[1] or 0,
            "failed": summary[2] or 0,
            "total_saved": summary[3] or 0,
        },
    }
    cache_set_json("stats:scrape", result, ttl=Config.CACHE_STATS_TTL)
    return result


# ============================================================
# ALERTS
# ============================================================
def get_active_alerts():
    """Get all active (untriggered) price alerts."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                SELECT id, user_id, coin, symbol, target_price, condition, created_at
                FROM alerts
                WHERE is_active = TRUE AND triggered = FALSE
            """)
        else:
            cursor.execute("""
                SELECT id, user_id, coin, symbol, target_price, condition, created_at
                FROM alerts
                WHERE is_active = 1 AND triggered = 0
            """)
        rows = cursor.fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row[0], "user_id": row[1], "coin": row[2] or "",
            "symbol": row[3] or "", "target_price": float(row[4]),
            "condition": row[5] or "above",
            "created_at": str(row[6]) if row[6] else "",
        })
    return results


def mark_alert_triggered(alert_id):
    """Mark an alert as triggered."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        if is_pg():
            cursor.execute("""
                UPDATE alerts SET triggered = TRUE, triggered_at = %s WHERE id = %s
            """, (now, alert_id))
        else:
            cursor.execute("""
                UPDATE alerts SET triggered = 1, triggered_at = ? WHERE id = ?
            """, (now, alert_id))
        conn.commit()


# ============================================================
# CLEANUP
# ============================================================
def cleanup_old_news(days=7):
    """Remove news older than N days."""
    with get_db() as conn:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute(
                "DELETE FROM news WHERE published_at < NOW() - INTERVAL %s",
                (f"{days} days",),
            )
        else:
            cursor.execute(
                "DELETE FROM news WHERE published_at < datetime('now', ?)",
                (f"-{days} days",),
            )
        deleted = cursor.rowcount
        conn.commit()
    if deleted > 0:
        logger.info(f"[db] Cleaned up {deleted} old news articles (older than {days} days)")
        # Invalidate all news caches
        cache_delete("news:homepage")
        cache_delete("news:latest")
        cache_delete("news:trending")
        cache_delete("stats:global")
    return deleted


# ============================================================
# HEALTH CHECK
# ============================================================
def get_health():
    """Get system health status."""
    health = {
        "status": "healthy",
        "database": "unknown",
        "redis": "unknown",
        "postgres_pool": "unknown",
    }

    # Check database
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if is_pg():
                cursor.execute("SELECT 1")
            else:
                cursor.execute("SELECT 1")
            cursor.fetchone()
        health["database"] = "connected"
    except Exception as e:
        health["database"] = f"error: {e}"
        health["status"] = "degraded"

    # Check Redis
    redis_client = _get_redis()
    if redis_client:
        try:
            redis_client.ping()
            health["redis"] = "connected"
        except Exception as e:
            health["redis"] = f"error: {e}"
            health["status"] = "degraded"
    else:
        health["redis"] = "not_configured"

    # Check PostgreSQL pool stats
    if is_pg():
        try:
            pool = _get_pg_pool()
            health["postgres_pool"] = {
                "minconn": pool.minconn,
                "maxconn": pool.maxconn,
            }
        except Exception:
            pass
    else:
        health["postgres_pool"] = "not_used"

    return health


# ============================================================
# NEWSLETTER
# ============================================================
def subscribe_newsletter(email):
    """Subscribe an email to the newsletter. Returns True if new subscriber."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        try:
            if is_pg():
                cursor.execute("""
                    INSERT INTO newsletters (email, is_active, subscribed_at, unsubscribed_at)
                    VALUES (%s, TRUE, %s, NULL)
                    ON CONFLICT (email) DO UPDATE SET
                        is_active = TRUE,
                        unsubscribed_at = NULL
                """, (email, now))
            else:
                cursor.execute("""
                    INSERT INTO newsletters (email, is_active, subscribed_at, unsubscribed_at)
                    VALUES (?, 1, ?, NULL)
                    ON CONFLICT(email) DO UPDATE SET
                        is_active = 1,
                        unsubscribed_at = NULL
                """, (email, now))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Newsletter subscribe error: {e}")
            return False


def unsubscribe_newsletter(email):
    """Unsubscribe an email from the newsletter."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)
        if is_pg():
            cursor.execute("""
                UPDATE newsletters SET is_active = FALSE, unsubscribed_at = %s
                WHERE email = %s
            """, (now, email))
        else:
            cursor.execute("""
                UPDATE newsletters SET is_active = 0, unsubscribed_at = ?
                WHERE email = ?
            """, (now, email))
        conn.commit()
        return cursor.rowcount > 0


# ============================================================
# CATEGORY CATEGORIZATION
# ============================================================
def categorize_title(title, summary=""):
    """Categorize a news title using keyword matching rules."""
    text = f"{title} {(summary or '')}".lower()
    best_category = "crypto"
    best_score = 0.5  # minimum threshold

    for category, rule in Config.CATEGORY_RULES.items():
        score = 0
        for keyword in rule["keywords"]:
            if keyword.lower() in text:
                score += 1
        weighted_score = score * rule["weight"]
        if weighted_score > best_score:
            best_score = weighted_score
            best_category = category

    return best_category
