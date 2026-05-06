"""
database.py - جدولين منفصلين:
- posted_news: للموقع — ما تتحذفش أبداً
- telegram_log: للبوت — تتحذف كل 6 ساعات
"""

import os
import time
import logging
import sqlite3
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("database")

def now_utc():
    return datetime.now(timezone.utc)

def categorize_title(title: str) -> str:
    t = (title or "").lower()
    if any(k in t for k in ["breaking", "urgent", "hack", "hacked", "exploit", "crash", "ban", "banned", "scam"]):
        return "breaking"
    if "bitcoin" in t or "btc" in t:
        return "bitcoin"
    if "ethereum" in t or " eth " in t or "ether " in t:
        return "ethereum"
    if any(k in t for k in ["defi", "uniswap", "aave", "compound", "protocol", "staking", "liquidity"]):
        return "defi"
    if any(k in t for k in ["nft", "non-fungible", "opensea", "metaverse"]):
        return "nft"
    if any(k in t for k in ["sec", "regulation", "legal", "congress", "government", "legislation", "lawsuit"]):
        return "regulation"
    if any(k in t for k in ["solana", "cardano", "ripple", "dogecoin", "bnb", "binance", "xrp", "doge", "ada", " sol "]):
        return "altcoin"
    return "market"

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH      = os.environ.get("DB_PATH", "cryptobot.db")
USE_POSTGRES = bool(DATABASE_URL and "postgres" in DATABASE_URL)

if USE_POSTGRES:
    try:
        import psycopg2
        import psycopg2.extras
        logger.info("✅ PostgreSQL mode")
    except ImportError:
        logger.warning("⚠️ psycopg2 — SQLite fallback")
        USE_POSTGRES = False
else:
    logger.info("✅ SQLite mode")

# ============================================================
# Cache
# ============================================================

_cache = {}
CACHE_TTL = 60

def cache_get(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None

def cache_set(key, data):
    _cache[key] = (data, time.time())

def cache_clear():
    _cache.clear()

# ============================================================
# Connection
# ============================================================

def get_pg_conn():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode" not in url:
        url += "?sslmode=require" if "?" not in url else "&sslmode=require"
    return psycopg2.connect(url)

def get_sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================
# Init — جدولين
# ============================================================

def init_db():
    try:
        if USE_POSTGRES:
            conn = get_pg_conn()
            cur  = conn.cursor()

            # الجدول الأول — للموقع، ما يتحذفش
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posted_news (
                    id        TEXT PRIMARY KEY,
                    title     TEXT,
                    source    TEXT,
                    posted_at TEXT,
                    summary   TEXT,
                    sentiment TEXT,
                    reason    TEXT,
                    category  TEXT
                )
            """)

            # الجدول الثاني — لتتبع ما نشرناه في تيليغرام
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telegram_log (
                    id         TEXT PRIMARY KEY,
                    posted_at  TEXT
                )
            """)

            for col in ["summary", "sentiment", "reason", "category"]:
                try:
                    cur.execute(f"ALTER TABLE posted_news ADD COLUMN {col} TEXT")
                except Exception:
                    pass

            cur.execute("CREATE INDEX IF NOT EXISTS idx_posted_news_category_posted_at ON posted_news (category, posted_at DESC)")
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS posted_news (
                        id TEXT PRIMARY KEY, title TEXT, source TEXT, posted_at TEXT,
                        summary TEXT, sentiment TEXT, reason TEXT, category TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_log (
                        id TEXT PRIMARY KEY, posted_at TEXT
                    )
                """)
                for col in ["summary", "sentiment", "reason", "category"]:
                    try:
                        conn.execute(f"ALTER TABLE posted_news ADD COLUMN {col} TEXT")
                    except Exception:
                        pass
                conn.execute("CREATE INDEX IF NOT EXISTS idx_posted_news_category_posted_at ON posted_news (category, posted_at DESC)")
                conn.commit()

        logger.info("✅ DB جاهزة (posted_news + telegram_log)")
    except Exception as e:
        logger.error(f"❌ init_db: {e}")

# ============================================================
# تيليغرام — هل نشرنا هاد الخبر مؤخراً؟
# ============================================================

def is_telegram_posted(news_id: str) -> bool:
    """تحقق من telegram_log فقط"""
    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM telegram_log WHERE id = %s", (news_id,))
            result = cur.fetchone() is not None
            cur.close(); conn.close()
            return result
        else:
            with get_sqlite_conn() as conn:
                return conn.execute("SELECT 1 FROM telegram_log WHERE id = ?", (news_id,)).fetchone() is not None
    except Exception as e:
        logger.error(f"❌ is_telegram_posted: {e}")
        return False

def mark_telegram_posted(news_id: str):
    """سجل في telegram_log"""
    posted_at = now_utc().isoformat()
    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("INSERT INTO telegram_log (id, posted_at) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                       (news_id, posted_at))
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                conn.execute("INSERT OR IGNORE INTO telegram_log (id, posted_at) VALUES (?, ?)", (news_id, posted_at))
                conn.commit()
    except Exception as e:
        logger.error(f"❌ mark_telegram_posted: {e}")

# ============================================================
# موقع — حفظ الخبر للأبد
# ============================================================

def save_news(news_id: str, title: str, source: str,
              summary: str = "", sentiment: str = "", reason: str = ""):
    """حفظ في posted_news — ما يتحذفش أبداً"""
    posted_at = now_utc().isoformat()
    category = categorize_title(title)
    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO posted_news (id, title, source, posted_at, summary, sentiment, reason, category)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
            """, (news_id, title, source, posted_at, summary, sentiment, reason, category))
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO posted_news (id, title, source, posted_at, summary, sentiment, reason, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (news_id, title, source, posted_at, summary, sentiment, reason, category))
                conn.commit()
        cache_clear()
    except Exception as e:
        logger.error(f"❌ save_news: {e}")

# ============================================================
# تنظيف telegram_log فقط — كل X ساعات
# ============================================================

def cleanup_telegram_log(hours: int = 6):
    """حذف من telegram_log فقط — الموقع ما يتأثرش"""
    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("""
                DELETE FROM telegram_log
                WHERE posted_at < to_char(
                    NOW() AT TIME ZONE 'UTC' - (interval '1 hour' * %s),
                    'YYYY-MM-DD"T"HH24:MI:SS'
                )
            """, (hours,))
            deleted = cur.rowcount
            conn.commit(); cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                cur = conn.execute("DELETE FROM telegram_log WHERE posted_at < datetime('now', ?)", (f'-{hours} hours',))
                deleted = cur.rowcount
                conn.commit()

        if deleted > 0:
            logger.info(f"🗑️ telegram_log: حذفنا {deleted} سجل قديم (>{hours}h)")
        cache_clear()
    except Exception as e:
        logger.error(f"❌ cleanup_telegram_log: {e}")

# ============================================================
# get_recent_titles — للـ duplicate detection
# ============================================================

def get_recent_titles(limit: int = 200) -> list:
    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("SELECT title FROM posted_news ORDER BY posted_at DESC LIMIT %s", (limit,))
            rows = [r[0] for r in cur.fetchall()]
            cur.close(); conn.close()
            return rows
        else:
            with get_sqlite_conn() as conn:
                return [r["title"] for r in conn.execute(
                    "SELECT title FROM posted_news ORDER BY posted_at DESC LIMIT ?", (limit,)
                ).fetchall()]
    except Exception as e:
        logger.error(f"❌ get_recent_titles: {e}")
        return []

# ============================================================
# get_news_by_id
# ============================================================

def get_news_by_id(news_id: str):
    try:
        if USE_POSTGRES:
            conn = get_pg_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM posted_news WHERE id = %s", (news_id,))
            row = cur.fetchone()
            cur.close(); conn.close()
            return dict(row) if row else None
        else:
            with get_sqlite_conn() as conn:
                row = conn.execute("SELECT * FROM posted_news WHERE id = ?", (news_id,)).fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ get_news_by_id: {e}")
        return None

# ============================================================
# get_news — مع cache
# ============================================================

def get_news(page: int = 1, per_page: int = 20, search: str = None, category: str = None):
    cache_key = f"news_{page}_{per_page}_{search}_{category}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    offset = (page - 1) * per_page
    try:
        if USE_POSTGRES:
            conn = get_pg_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            where = []
            params = []
            if search:
                where.append("title ILIKE %s")
                params.append(f"%{search}%")
            if category:
                where.append("category = %s")
                params.append(category)
            where_sql = (" WHERE " + " AND ".join(where)) if where else ""
            cur.execute(f"SELECT * FROM posted_news{where_sql} ORDER BY posted_at DESC LIMIT %s OFFSET %s", tuple(params + [per_page, offset]))
            rows = cur.fetchall()
            cur.execute(f"SELECT COUNT(*) as count FROM posted_news{where_sql}", tuple(params))
            total = cur.fetchone()["count"]
            cur.close(); conn.close()
            result = [dict(r) for r in rows], total
        else:
            with get_sqlite_conn() as conn:
                where = []
                params = []
                if search:
                    where.append("title LIKE ?")
                    params.append(f"%{search}%")
                if category:
                    where.append("category = ?")
                    params.append(category)
                where_sql = (" WHERE " + " AND ".join(where)) if where else ""
                rows  = conn.execute(f"SELECT * FROM posted_news{where_sql} ORDER BY posted_at DESC LIMIT ? OFFSET ?", tuple(params + [per_page, offset])).fetchall()
                total = conn.execute(f"SELECT COUNT(*) FROM posted_news{where_sql}", tuple(params)).fetchone()[0]
                result = [dict(r) for r in rows], total

        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"❌ get_news: {e}")
        return [], 0

# ============================================================
# get_stats — مع cache
# ============================================================

def get_stats():
    cached = cache_get("stats")
    if cached:
        return cached

    try:
        if USE_POSTGRES:
            conn = get_pg_conn(); cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM posted_news")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM posted_news WHERE posted_at >= to_char(NOW() - INTERVAL '24 hours', 'YYYY-MM-DD\"T\"HH24:MI:SS')")
            today = cur.fetchone()[0]
            cur.execute("SELECT source, COUNT(*) as c FROM posted_news GROUP BY source ORDER BY c DESC")
            sources = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
            cur.close(); conn.close()
        else:
            with get_sqlite_conn() as conn:
                total   = conn.execute("SELECT COUNT(*) FROM posted_news").fetchone()[0]
                today   = conn.execute("SELECT COUNT(*) FROM posted_news WHERE posted_at >= datetime('now','-1 day')").fetchone()[0]
                sources = [{"name": r[0], "count": r[1]} for r in conn.execute(
                    "SELECT source, COUNT(*) as c FROM posted_news GROUP BY source ORDER BY c DESC").fetchall()]

        result = {"total": total, "today": today, "sources": sources}
        cache_set("stats", result)
        return result
    except Exception as e:
        logger.error(f"❌ get_stats: {e}")
        return {"total": 0, "today": 0, "sources": []}
