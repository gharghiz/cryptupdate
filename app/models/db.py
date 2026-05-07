# -*- coding: utf-8 -*-
"""
CryptositNews - Database Layer
PostgreSQL + SQLite dual support, full-text search, user accounts, cloud bookmarks.
"""

import os
import time
import sqlite3
import threading
import json
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

from app.config import Config
from app.utils.helpers import setup_logger, hash_text, hours_ago, now_utc

logger = setup_logger("database")

# ============================================================
# DATABASE DIALECT
# ============================================================
_is_postgres = False


def is_pg():
    global _is_postgres
    return _is_postgres


def P():
    """Parameter placeholder: %s for PostgreSQL, ? for SQLite."""
    return "%s" if _is_postgres else "?"


# ============================================================
# CONNECTION POOL (SQLite)
# ============================================================
_pool = None
_db_path = "cryptositnews.db"


def get_pool():
    global _pool, _db_path, _is_postgres
    if _pool is None:
        database_url = Config.DATABASE_URL
        if database_url and database_url.startswith("postgres"):
            _db_path = database_url
            _is_postgres = True
        else:
            _db_path = database_url or "cryptositnews.db"
            _is_postgres = False
            _pool = _SQLitePool(_db_path, Config.DB_POOL_SIZE)
    return _pool


class _SQLitePool:
    def __init__(self, path, size=8):
        self.path = path
        self.size = size
        self._pool = []
        self._lock = threading.Lock()
        self._created = 0

    def get(self):
        with self._lock:
            if self._pool:
                return self._pool.pop()
            if self._created < self.size:
                self._created += 1
                conn = sqlite3.connect(self.path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                return conn
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def put(self, conn):
        try:
            with self._lock:
                if len(self._pool) < self.size:
                    self._pool.append(conn)
                    return
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# CONTEXT MANAGER
# ============================================================
@contextmanager
def get_db():
    pool = get_pool()
    conn = None
    try:
        if is_pg():
            import psycopg2
            conn = psycopg2.connect(_db_path)
            conn.autocommit = True
            yield conn
            conn.close()
        elif pool:
            conn = pool.get()
            yield conn
            pool.put(conn)
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


def _dict_row(row):
    """Convert a database row to dict, handling both Row and tuple."""
    if hasattr(row, "keys"):
        return dict(row)
    return dict(row) if row else {}


def _serialize(item):
    """Serialize a database row for JSON response."""
    d = _dict_row(item)
    for key in ("published", "created_at", "last_login", "posted_at", "scraped_at"):
        if d.get(key) and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


# ============================================================
# CACHE (Redis + in-memory fallback)
# ============================================================
_redis_client = None
_memory_cache = {}
_cache_lock = threading.Lock()
_cache_hits = 0
_cache_misses = 0


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client if _redis_client else None
    if not Config.REDIS_URL:
        _redis_client = False
        return None
    try:
        import redis
        _redis_client = redis.from_url(Config.REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info("Redis connected")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis unavailable: {e}")
        _redis_client = False
        return None


class Cache:
    def get(self, key):
        global _cache_hits, _cache_misses
        r = _get_redis()
        if r:
            try:
                val = r.get(f"cn:{key}")
                if val is not None:
                    _cache_hits += 1
                    return json.loads(val)
            except Exception:
                pass
        with _cache_lock:
            item = _memory_cache.get(key)
            if item and item["exp"] > time.time():
                _cache_hits += 1
                return item["val"]
            elif item:
                del _memory_cache[key]
        _cache_misses += 1
        return None

    def set(self, key, value, ttl=None):
        ttl = ttl or Config.CACHE_DEFAULT_TTL
        r = _get_redis()
        if r:
            try:
                r.setex(f"cn:{key}", ttl, json.dumps(value, default=str))
            except Exception:
                pass
        with _cache_lock:
            _memory_cache[key] = {"val": value, "exp": time.time() + ttl}
            if len(_memory_cache) > 500:
                expired = [k for k, v in _memory_cache.items() if v["exp"] <= time.time()]
                for k in expired:
                    del _memory_cache[k]

    def delete(self, key):
        r = _get_redis()
        if r:
            try:
                r.delete(f"cn:{key}")
            except Exception:
                pass
        with _cache_lock:
            _memory_cache.pop(key, None)

    def clear(self):
        r = _get_redis()
        if r:
            try:
                for key in r.scan_iter("cn:*"):
                    r.delete(key)
            except Exception:
                pass
        with _cache_lock:
            _memory_cache.clear()

    def stats(self):
        total = _cache_hits + _cache_misses
        return {
            "hits": _cache_hits,
            "misses": _cache_misses,
            "hit_rate": round((_cache_hits / total * 100) if total > 0 else 0, 2),
            "backend": "redis" if _get_redis() else "memory",
            "memory_entries": len(_memory_cache),
        }


cache = Cache()


# ============================================================
# COINGECKO RATE LIMITER
# ============================================================
_cg_lock = threading.Lock()
_cg_times = []


def coingecko_wait():
    global _cg_times
    with _cg_lock:
        now = time.time()
        _cg_times = [t for t in _cg_times if now - t < Config.COINGECKO_RATE_WINDOW]
        if len(_cg_times) >= Config.COINGECKO_RATE_LIMIT:
            wait = Config.COINGECKO_RATE_WINDOW - (now - _cg_times[0]) + 1
            if wait > 0:
                time.sleep(wait)
        _cg_times.append(time.time())


# ============================================================
# CATEGORIZATION
# ============================================================
def categorize_title(title):
    if not title:
        return "market", 0.0
    t = title.lower()
    scores = {}
    for cat, rules in Config.CATEGORY_RULES.items():
        s = sum(rules["weight"] * 2 for kw in rules["keywords"] if kw.lower() in t)
        if s > 0:
            scores[cat] = s
    if not scores:
        return "market", 0.3
    best = max(scores, key=scores.get)
    max_possible = max(r["weight"] * 2 * 5 for r in Config.CATEGORY_RULES.values())
    return best, min(scores[best] / (max_possible * 0.3), 1.0)


# ============================================================
# INIT DATABASE + MIGRATIONS
# ============================================================
def init_db():
    """Create all tables with migrations."""
    with get_db() as conn:
        cursor = conn.cursor()
        pg = is_pg()
        p = P()

        # --- News ---
        if pg:
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
                    image_url TEXT DEFAULT '',
                    search_vector TSVECTOR
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, summary TEXT DEFAULT '',
                    url TEXT UNIQUE, source TEXT DEFAULT '',
                    published TIMESTAMP, category TEXT DEFAULT 'market',
                    category_confidence REAL DEFAULT 0.0,
                    ai_sentiment TEXT DEFAULT '', ai_reason TEXT DEFAULT '',
                    ai_summary TEXT DEFAULT '',
                    is_important BOOLEAN DEFAULT FALSE,
                    telegram_posted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_url TEXT DEFAULT ''
                )
            """)

        # --- Users (NEW) ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    role TEXT DEFAULT 'free',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    role TEXT DEFAULT 'free',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)

        # --- Cloud Bookmarks (NEW) ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, news_id)
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, news_id)
                )
            """)

        # --- Refresh Tokens (NEW) ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL,
                    device TEXT DEFAULT '',
                    ip TEXT DEFAULT '',
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL,
                    device TEXT DEFAULT '',
                    ip TEXT DEFAULT '',
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # --- Telegram Log ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id SERIAL PRIMARY KEY, news_id INTEGER,
                    title TEXT, posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT TRUE, error TEXT DEFAULT ''
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, news_id INTEGER,
                    title TEXT, posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN DEFAULT TRUE, error TEXT DEFAULT ''
                )
            """)

        # --- Price Alerts ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id SERIAL PRIMARY KEY, coin TEXT NOT NULL,
                    symbol TEXT NOT NULL, target_price REAL NOT NULL,
                    condition TEXT DEFAULT 'above',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    triggered_at TIMESTAMP, triggered BOOLEAN DEFAULT FALSE
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT NOT NULL,
                    symbol TEXT NOT NULL, target_price REAL NOT NULL,
                    condition TEXT DEFAULT 'above',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    triggered_at TIMESTAMP, triggered BOOLEAN DEFAULT FALSE
                )
            """)

        # --- Newsletter ---
        if pg:
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

        # --- Scrape Log ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrape_log (
                    id SERIAL PRIMARY KEY, feed_url TEXT,
                    source TEXT DEFAULT '', entries_found INTEGER DEFAULT 0,
                    entries_saved INTEGER DEFAULT 0, error TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scrape_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, feed_url TEXT,
                    source TEXT DEFAULT '', entries_found INTEGER DEFAULT 0,
                    entries_saved INTEGER DEFAULT 0, error TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # --- AI Cache (NEW) ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_cache (
                    id SERIAL PRIMARY KEY,
                    content_hash TEXT UNIQUE NOT NULL,
                    ai_summary TEXT DEFAULT '',
                    ai_sentiment TEXT DEFAULT '',
                    ai_reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE NOT NULL,
                    ai_summary TEXT DEFAULT '',
                    ai_sentiment TEXT DEFAULT '',
                    ai_reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # --- Premium Subscriptions (NEW - monetization foundation) ---
        if pg:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan TEXT DEFAULT 'free',
                    stripe_customer_id TEXT DEFAULT '',
                    current_period_start TIMESTAMP,
                    current_period_end TIMESTAMP,
                    active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan TEXT DEFAULT 'free',
                    stripe_customer_id TEXT DEFAULT '',
                    current_period_start TIMESTAMP,
                    current_period_end TIMESTAMP,
                    active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # --- INDEXES ---
        try:
            idx_sql = [
                f"CREATE INDEX IF NOT EXISTS idx_news_published ON news({'published DESC NULLS LAST' if pg else 'coalesce(published, created_at) DESC'})",
                "CREATE INDEX IF NOT EXISTS idx_news_category ON news(category)",
                "CREATE INDEX IF NOT EXISTS idx_news_important ON news(is_important)",
                "CREATE INDEX IF NOT EXISTS idx_news_telegram ON news(telegram_posted)",
                "CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
                "CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)",
            ]
            if pg:
                idx_sql.append("CREATE INDEX IF NOT EXISTS idx_news_search ON news USING GIN(search_vector)")
                idx_sql.append("CREATE INDEX IF NOT EXISTS idx_ai_cache_hash ON ai_cache(content_hash)")

            for sql in idx_sql:
                cursor.execute(sql)
        except Exception as e:
            logger.warning(f"Index creation: {e}")

        # --- PostgreSQL Full-Text Search Trigger ---
        if pg:
            try:
                cursor.execute("""
                    CREATE OR REPLACE FUNCTION news_search_vector_update() RETURNS trigger AS $$
                    BEGIN
                        NEW.search_vector :=
                            setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
                            setweight(to_tsvector('english', COALESCE(NEW.summary, '')), 'B') ||
                            setweight(to_tsvector('english', COALESCE(NEW.source, '')), 'C');
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                cursor.execute("""
                    DROP TRIGGER IF EXISTS news_search_trigger ON news;
                    CREATE TRIGGER news_search_trigger
                        BEFORE INSERT OR UPDATE ON news
                        FOR EACH ROW EXECUTE FUNCTION news_search_vector_update();
                """)
                logger.info("PostgreSQL full-text search configured")
            except Exception as e:
                logger.warning(f"FTS trigger: {e}")

        conn.commit()
        logger.info(f"Database initialized (PostgreSQL: {pg})")


# ============================================================
# NEWS OPERATIONS
# ============================================================
def is_valid_title(title):
    from app.utils.helpers import is_valid_title as _ivt
    return _ivt(title)


def save_news(title, summary="", url="", source="", published=None, **kwargs):
    if not title or not is_valid_title(title):
        return None
    category, confidence = categorize_title(title)
    pg = is_pg()
    p = P()

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            if pg:
                cursor.execute(f"""
                    INSERT INTO news (title, summary, url, source, published,
                        category, category_confidence, ai_sentiment, ai_reason,
                        ai_summary, is_important, image_url)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                    RETURNING id
                """, (title, summary, url, source, published,
                      category, confidence, kwargs.get("ai_sentiment", ""),
                      kwargs.get("ai_reason", ""), kwargs.get("ai_summary", ""),
                      kwargs.get("is_important", False), kwargs.get("image_url", "")))
                nid = cursor.fetchone()[0]
            else:
                cursor.execute(f"""
                    INSERT INTO news (title, summary, url, source, published,
                        category, category_confidence, ai_sentiment, ai_reason,
                        ai_summary, is_important, image_url)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                """, (title, summary, url, source, published,
                      category, confidence, kwargs.get("ai_sentiment", ""),
                      kwargs.get("ai_reason", ""), kwargs.get("ai_summary", ""),
                      kwargs.get("is_important", False), kwargs.get("image_url", "")))
                nid = cursor.lastrowid

            logger.info(f"Saved [{category}]: {title[:60]}... (ID:{nid})")
            cache.delete("news:latest")
            return nid
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                return None
            logger.error(f"Save news error: {e}")
            return None


def get_news(limit=50, offset=0, category=None, important_only=False):
    cache_key = f"news:{limit}:{offset}:{category}:{important_only}"
    result = cache.get(cache_key)
    if result:
        return result

    pg = is_pg()
    p = P()
    conditions, params = [], []

    if category and category != "all":
        conditions.append(f"category = {p}")
        params.append(category)
    if important_only:
        conditions.append("is_important = TRUE" if pg else "is_important = 1")

    query = "SELECT * FROM news"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY {'published DESC NULLS LAST' if pg else 'coalesce(published, created_at) DESC'} LIMIT {p} OFFSET {p}"
    params.extend([limit, offset])

    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = [_serialize(r) for r in cursor.fetchall()]
            cache.set(cache_key, rows, Config.CACHE_NEWS_TTL)
            return rows
        except Exception as e:
            logger.error(f"Get news error: {e}")
            return []


def get_news_by_id(nid):
    p = P()
    cache_key = f"news:id:{nid}"
    result = cache.get(cache_key)
    if result:
        return result
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM news WHERE id = {p}", (nid,))
            row = cursor.fetchone()
            if row:
                item = _serialize(row)
                cache.set(cache_key, item, Config.CACHE_NEWS_TTL)
                return item
        except Exception as e:
            logger.error(f"Get news by ID error: {e}")
    return None


def search_news(query, limit=20):
    """Full-text search: PostgreSQL tsvector, SQLite LIKE fallback."""
    if not query or len(query.strip()) < 2:
        return []

    from app.utils.helpers import sanitize_search
    query = sanitize_search(query)
    pg = is_pg()
    p = P()

    with get_db() as conn:
        try:
            cursor = conn.cursor()
            if pg:
                # PostgreSQL full-text search with ranking
                cursor.execute(f"""
                    SELECT *, ts_rank(search_vector, plainto_tsquery('english', {p})) as rank
                    FROM news
                    WHERE search_vector @@ plainto_tsquery('english', {p})
                    ORDER BY rank DESC
                    LIMIT {p}
                """, (query, query, limit))
            else:
                search = f"%{query}%"
                cursor.execute(f"""
                    SELECT * FROM news
                    WHERE title LIKE {p} OR summary LIKE {p} OR source LIKE {p}
                    ORDER BY coalesce(published, created_at) DESC
                    LIMIT {p}
                """, (search, search, search, limit))

            return [_serialize(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []


def get_related_news(nid, limit=5):
    article = get_news_by_id(nid)
    if not article:
        return []
    category = article.get("category", "market")
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            if is_pg():
                cursor.execute(f"""
                    SELECT * FROM news WHERE category = {p} AND id != {p}
                    ORDER BY published DESC NULLS LAST LIMIT {p}
                """, (category, nid, limit))
            else:
                cursor.execute("""
                    SELECT * FROM news WHERE category = ? AND id != ?
                    ORDER BY coalesce(published, created_at) DESC LIMIT ?
                """, (category, nid, limit))
            return [_serialize(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Related news error: {e}")
            return []


# ============================================================
# AI CACHE
# ============================================================
def get_ai_cached(content_hash):
    """Get cached AI result by content hash."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM ai_cache WHERE content_hash = {p}", (content_hash,))
            row = cursor.fetchone()
            if row:
                return _serialize(row)
        except Exception:
            pass
    return None


def save_ai_cache(content_hash, ai_summary, ai_sentiment, ai_reason):
    """Cache an AI result."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT (content_hash) DO NOTHING
            """ if is_pg() else f"""
                INSERT OR IGNORE INTO ai_cache (content_hash, ai_summary, ai_sentiment, ai_reason)
                VALUES ({p}, {p}, {p}, {p})
            """, (content_hash, ai_summary, ai_sentiment, ai_reason))
            conn.commit()
        except Exception as e:
            logger.debug(f"AI cache save: {e}")


# ============================================================
# USER ACCOUNTS
# ============================================================
def create_user(email, password_hash, display_name=""):
    """Create a new user. Returns user dict or None."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO users (email, password_hash, display_name)
                VALUES ({p}, {p}, {p})
                RETURNING id
            """ if is_pg() else f"""
                INSERT INTO users (email, password_hash, display_name)
                VALUES ({p}, {p}, {p})
            """, (email, password_hash, display_name))
            if is_pg():
                uid = cursor.fetchone()[0]
            else:
                uid = cursor.lastrowid
            conn.commit()
            logger.info(f"User created: {email} (ID: {uid})")
            return {"id": uid, "email": email, "display_name": display_name, "role": "free"}
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                return None
            logger.error(f"Create user error: {e}")
            return None


def get_user_by_email(email):
    """Get user by email. Returns user dict or None (excludes password_hash)."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT id, email, display_name, role, is_active, created_at, last_login
                FROM users WHERE email = {p}
            """, (email,))
            row = cursor.fetchone()
            return _serialize(row) if row else None
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return None


def get_user_with_password(email):
    """Get user including password_hash for login verification."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE email = {p}", (email,))
            row = cursor.fetchone()
            return _serialize(row) if row else None
        except Exception:
            return None


def update_last_login(user_id):
    """Update user's last login timestamp."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = {p}", (user_id,))
            conn.commit()
        except Exception:
            pass


def get_user_by_id(uid):
    """Get user by ID."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT id, email, display_name, role, is_active, created_at, last_login
                FROM users WHERE id = {p}
            """, (uid,))
            row = cursor.fetchone()
            return _serialize(row) if row else None
        except Exception:
            return None


# ============================================================
# REFRESH TOKENS
# ============================================================
def save_refresh_token(user_id, token_hash, device="", ip="", expires_at=None):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO refresh_tokens (user_id, token_hash, device, ip, expires_at)
                VALUES ({p}, {p}, {p}, {p}, {p})
            """, (user_id, token_hash, device, ip, expires_at))
            conn.commit()
        except Exception as e:
            logger.error(f"Save refresh token: {e}")


def verify_refresh_token_db(token_hash):
    p = P()
    with get_db() as conn:
        try:
            now = now_utc()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT rt.*, u.email, u.role FROM refresh_tokens rt
                JOIN users u ON rt.user_id = u.id
                WHERE rt.token_hash = {p} AND (rt.expires_at IS NULL OR rt.expires_at > {p})
            """, (token_hash, now))
            row = cursor.fetchone()
            return _serialize(row) if row else None
        except Exception:
            return None


def delete_refresh_token(token_hash):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM refresh_tokens WHERE token_hash = {p}", (token_hash,))
            conn.commit()
        except Exception:
            pass


def revoke_all_user_tokens(user_id):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM refresh_tokens WHERE user_id = {p}", (user_id,))
            conn.commit()
        except Exception:
            pass


# ============================================================
# CLOUD BOOKMARKS
# ============================================================
def add_bookmark(user_id, news_id):
    """Add a bookmark. Returns success boolean."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                INSERT INTO bookmarks (user_id, news_id) VALUES ({p}, {p})
            """ if is_pg() else """
                INSERT OR IGNORE INTO bookmarks (user_id, news_id) VALUES (?, ?)
            """, (user_id, news_id))
            conn.commit()
            return True
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                return True
            logger.error(f"Add bookmark error: {e}")
            return False


def remove_bookmark(user_id, news_id):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM bookmarks WHERE user_id = {p} AND news_id = {p}", (user_id, news_id))
            conn.commit()
            return True
        except Exception:
            return False


def get_user_bookmarks(user_id, limit=50):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT n.* FROM bookmarks b
                JOIN news n ON b.news_id = n.id
                WHERE b.user_id = {p}
                ORDER BY b.created_at DESC
                LIMIT {p}
            """, (user_id, limit))
            return [_serialize(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Get bookmarks error: {e}")
            return []


def is_bookmarked(user_id, news_id):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM bookmarks WHERE user_id = {p} AND news_id = {p}", (user_id, news_id))
            return cursor.fetchone() is not None
        except Exception:
            return False


# ============================================================
# STATS
# ============================================================
def get_stats():
    cache_key = "stats:all"
    result = cache.get(cache_key)
    if result:
        return result

    pg = is_pg()
    p = P()
    h24 = hours_ago(24)

    with get_db() as conn:
        try:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as total FROM news")
            total = _dict_row(c.fetchone())["total"]

            c.execute(f"SELECT COUNT(*) as cnt FROM news WHERE published >= {p} OR (published IS NULL AND created_at >= {p})", (h24, h24))
            today = _dict_row(c.fetchone())["cnt"]

            c.execute("SELECT COUNT(*) as cnt FROM news WHERE is_important = TRUE" if pg else "SELECT COUNT(*) as cnt FROM news WHERE is_important = 1")
            important = _dict_row(c.fetchone())["cnt"]

            c.execute("SELECT COUNT(*) as cnt FROM users WHERE is_active = TRUE" if pg else "SELECT COUNT(*) as cnt FROM users WHERE is_active = 1")
            users = _dict_row(c.fetchone())["cnt"]

            c.execute("SELECT COUNT(*) as cnt FROM bookmarks")
            bookmarks = _dict_row(c.fetchone())["cnt"]

            c.execute(f"SELECT COUNT(*) as cnt FROM telegram_log WHERE posted_at >= {p}", (h24,))
            tg_today = _dict_row(c.fetchone())["cnt"]

            c.execute("SELECT category, COUNT(*) as cnt FROM news GROUP BY category ORDER BY cnt DESC LIMIT 14")
            categories = {_dict_row(r)["category"]: _dict_row(r)["cnt"] for r in c.fetchall()}

            stats = {
                "total_news": total, "today_news": today,
                "important_news": important, "users": users,
                "bookmarks": bookmarks, "telegram_today": tg_today,
                "categories": categories,
            }
            cache.set(cache_key, stats, Config.CACHE_STATS_TTL)
            return stats
        except Exception as e:
            logger.error(f"Stats error: {e}")
            return {"total_news": 0, "today_news": 0, "important_news": 0,
                    "users": 0, "bookmarks": 0, "telegram_today": 0, "categories": {}}


# ============================================================
# TELEGRAM LOG
# ============================================================
def is_telegram_posted(title):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT 1 FROM telegram_log WHERE title = {p} AND success = TRUE" if is_pg() else f"SELECT 1 FROM telegram_log WHERE title = {p} AND success = 1", (title,))
            return cursor.fetchone() is not None
        except Exception:
            return False


def mark_telegram_posted(news_id, title, success=True, error=""):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            if is_pg():
                cursor.execute(f"INSERT INTO telegram_log (news_id, title, success, error) VALUES ({p}, {p}, {p}, {p})", (news_id, title, success, error))
                if success and news_id:
                    cursor.execute(f"UPDATE news SET telegram_posted = TRUE WHERE id = {p}", (news_id,))
            else:
                cursor.execute(f"INSERT INTO telegram_log (news_id, title, success, error) VALUES ({p}, {p}, {p}, {p})", (news_id, title, success, error))
                if success and news_id:
                    cursor.execute("UPDATE news SET telegram_posted = 1 WHERE id = ?", (news_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Mark telegram: {e}")


def cleanup_telegram_log(days=3):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM telegram_log WHERE posted_at < {p}", (hours_ago(days * 24),))
            conn.commit()
        except Exception:
            pass


# ============================================================
# NEWSLETTER
# ============================================================
def newsletter_subscribe(email):
    from app.utils.helpers import is_valid_email
    if not is_valid_email(email):
        return False, "Invalid email"
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"INSERT INTO newsletter (email) VALUES ({p})", (email,))
            conn.commit()
            return True, "Subscribed!"
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                cursor.execute(f"UPDATE newsletter SET active = {'TRUE' if is_pg() else '1'} WHERE email = {p}", (email,))
                conn.commit()
                return True, "Re-subscribed!"
            return False, str(e)


def get_newsletter_stats():
    with get_db() as conn:
        try:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as cnt FROM newsletter WHERE active = TRUE" if is_pg() else "SELECT COUNT(*) as cnt FROM newsletter WHERE active = 1")
            active = _dict_row(c.fetchone())["cnt"]
            c.execute("SELECT COUNT(*) as cnt FROM newsletter")
            total = _dict_row(c.fetchone())["cnt"]
            return {"active": active, "total": total}
        except Exception:
            return {"active": 0, "total": 0}


# ============================================================
# PRICE ALERTS
# ============================================================
def get_active_alerts():
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM price_alerts WHERE active = TRUE ORDER BY created_at DESC" if is_pg() else "SELECT * FROM price_alerts WHERE active = 1 ORDER BY created_at DESC")
            return [_serialize(r) for r in cursor.fetchall()]
        except Exception:
            return []


def add_price_alert(coin, symbol, target_price, condition="above"):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"INSERT INTO price_alerts (coin, symbol, target_price, condition) VALUES ({p}, {p}, {p}, {p})", (coin, symbol, float(target_price), condition))
            conn.commit()
            return True
        except Exception:
            return False


def mark_alert_triggered(aid):
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            if is_pg():
                cursor.execute(f"UPDATE price_alerts SET active=FALSE, triggered=TRUE, triggered_at=CURRENT_TIMESTAMP WHERE id={p}", (aid,))
            else:
                cursor.execute(f"UPDATE price_alerts SET active=0, triggered=1, triggered_at=CURRENT_TIMESTAMP WHERE id={p}", (aid,))
            conn.commit()
        except Exception:
            pass


# ============================================================
# SCRAPE LOG
# ============================================================
def log_scrape_result(feed_url, source, entries_found, entries_saved, error="", duration_ms=0):
    p = P()
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"INSERT INTO scrape_log (feed_url, source, entries_found, entries_saved, error, duration_ms) VALUES ({p},{p},{p},{p},{p},{p})", (feed_url, source, entries_found, entries_saved, error, duration_ms))
            conn.commit()
    except Exception:
        pass


def get_scrape_logs(limit=50):
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM scrape_log ORDER BY scraped_at DESC LIMIT {P()}", (limit,))
            return [_serialize(r) for r in cursor.fetchall()]
        except Exception:
            return []


# ============================================================
# HEALTH
# ============================================================
def get_health():
    info = {
        "status": "healthy",
        "database": "connected",
        "database_type": "postgresql" if is_pg() else "sqlite",
        "cache_backend": "redis" if _get_redis() else "memory",
    }
    try:
        import psutil
        info["memory_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 2)
    except Exception:
        info["memory_mb"] = 0
    try:
        with get_db() as conn:
            conn.cursor().execute("SELECT 1")
    except Exception as e:
        info["status"] = "unhealthy"
        info["database"] = f"error: {str(e)[:100]}"
    return info


# ============================================================
# TRENDING NEWS
# ============================================================
def get_trending_news(limit=10):
    """Return most recent news, prioritising is_important=True items.
    If there are fewer important articles than *limit*, pad with the
    most recent general articles (no duplicates).
    """
    cache_key = f"trending:{limit}"
    result = cache.get(cache_key)
    if result:
        return result

    pg = is_pg()
    p = P()
    important_cond = "is_important = TRUE" if pg else "is_important = 1"
    order = "created_at DESC" if pg else "created_at DESC"

    with get_db() as conn:
        try:
            cursor = conn.cursor()
            # Fetch important news first
            cursor.execute(
                f"SELECT * FROM news WHERE {important_cond} "
                f"ORDER BY {order} LIMIT {p}",
                (limit,),
            )
            important_rows = [_serialize(r) for r in cursor.fetchall()]

            if len(important_rows) >= limit:
                cache.set(cache_key, important_rows, Config.CACHE_NEWS_TTL)
                return important_rows

            # Pad with most recent news (excluding already-fetched IDs)
            seen = {r["id"] for r in important_rows}
            remaining = limit - len(important_rows)
            placeholders = ",".join([p] * len(seen)) if seen else ""

            if placeholders:
                cursor.execute(
                    f"SELECT * FROM news WHERE id NOT IN ({placeholders}) "
                    f"ORDER BY {order} LIMIT {p}",
                    (*seen, remaining),
                )
            else:
                cursor.execute(
                    f"SELECT * FROM news ORDER BY {order} LIMIT {p}",
                    (remaining,),
                )
            pad_rows = [_serialize(r) for r in cursor.fetchall()]

            trending = important_rows + pad_rows
            cache.set(cache_key, trending, Config.CACHE_NEWS_TTL)
            return trending
        except Exception as e:
            logger.error(f"Trending news error: {e}")
            return []


# ============================================================
# NEWSLETTER SUBSCRIBE / UNSUBSCRIBE
# ============================================================
def subscribe_newsletter(email):
    """Subscribe an email to the newsletter. Returns (success, message)."""
    from app.utils.helpers import is_valid_email
    if not is_valid_email(email):
        return False, "Invalid email"
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(f"INSERT INTO newsletter (email) VALUES ({p})", (email,))
            conn.commit()
            logger.info(f"Newsletter subscribe: {email}")
            return True, "Subscribed!"
        except Exception as e:
            if "UNIQUE" in str(e) or "duplicate" in str(e).lower():
                cursor.execute(
                    f"UPDATE newsletter SET active = {'TRUE' if is_pg() else '1'} WHERE email = {p}",
                    (email,),
                )
                conn.commit()
                return True, "Already subscribed!"
            logger.error(f"Newsletter subscribe error: {e}")
            return False, str(e)


def unsubscribe_newsletter(email):
    """Unsubscribe an email from the newsletter. Returns (success, message)."""
    p = P()
    with get_db() as conn:
        try:
            cursor = conn.cursor()
            if is_pg():
                cursor.execute(
                    f"UPDATE newsletter SET active = FALSE WHERE email = {p}",
                    (email,),
                )
            else:
                cursor.execute(
                    f"UPDATE newsletter SET active = 0 WHERE email = {p}",
                    (email,),
                )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"Newsletter unsubscribe: {email}")
                return True, "Unsubscribed!"
            return False, "Email not found"
        except Exception as e:
            logger.error(f"Newsletter unsubscribe error: {e}")
            return False, str(e)


# ============================================================
# NEWS SOURCES
# ============================================================
def get_news_sources():
    """Return a list of distinct source names from the news table."""
    cache_key = "news:sources"
    result = cache.get(cache_key)
    if result:
        return result

    with get_db() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT source FROM news WHERE source IS NOT NULL AND source != '' ORDER BY source")
            sources = [_dict_row(r)["source"] for r in cursor.fetchall()]
            cache.set(cache_key, sources, Config.CACHE_STATS_TTL)
            return sources
        except Exception as e:
            logger.error(f"Get news sources error: {e}")
            return []
