# -*- coding: utf-8 -*-
"""
CryptositNews - Admin API Routes
Protected endpoints for system stats, database management, cache control, and scraping.
All routes require admin authentication and admin rate limiting.
"""

import time
import threading

from flask import (
    Blueprint, request, jsonify,
)

from app.config import Config
from app.utils.helpers import setup_logger
from app.models.db import (
    get_db, is_pg, P, get_stats, get_newsletter_stats,
    cache, init_db, get_scrape_logs,
)
from app.services.scraper import scrape_all
from app.middleware.auth import admin_required
from app.middleware.rate_limit import admin_rate_limit

logger = setup_logger("api_admin")

api_admin = Blueprint("api_admin", __name__, url_prefix="/api/admin")

# Throttle scraper to prevent concurrent runs
_scrape_lock = threading.Lock()
_last_scrape_time = 0.0
SCRAPE_COOLDOWN = 60  # seconds


# ============================================================
# Helper: standard JSON response wrapper
# ============================================================
def _ok(data=None, message="success", status=200):
    payload = {"success": True, "message": message, "request_id": getattr(request, "request_id", "")}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status


def _err(message="Error", status=400, code="bad_request"):
    return jsonify({
        "success": False,
        "error": message,
        "code": code,
        "request_id": getattr(request, "request_id", ""),
    }), status


# ============================================================
# GET /api/admin/stats — system statistics
# ============================================================
@api_admin.route("/stats", methods=["GET"])
@admin_required
@admin_rate_limit
def stats():
    """Get comprehensive system statistics."""
    try:
        stats_data = get_stats()
        cache_stats = cache.stats()
        newsletter_stats = get_newsletter_stats()

        result = {
            **stats_data,
            "cache": cache_stats,
            "newsletter": newsletter_stats,
        }

        return _ok(data=result)
    except Exception as e:
        logger.error(f"[api_admin] stats error: {e}")
        return _err("Failed to fetch stats", 500, "internal_error")


# ============================================================
# GET /api/admin/sources — news source distribution
# ============================================================
@api_admin.route("/sources", methods=["GET"])
@admin_required
@admin_rate_limit
def sources():
    """Get news source distribution."""
    try:
        pg = is_pg()
        p = P()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source, COUNT(*) as cnt FROM news "
                "WHERE source IS NOT NULL AND source != '' "
                "GROUP BY source ORDER BY cnt DESC LIMIT 50"
            )
            rows = cursor.fetchall()
            sources = []
            for row in rows:
                d = dict(row) if hasattr(row, "keys") else {"source": row[0], "cnt": row[1]}
                sources.append({
                    "source": d.get("source", ""),
                    "count": d.get("cnt", 0),
                })

        return _ok(data={"sources": sources, "total": len(sources)})
    except Exception as e:
        logger.error(f"[api_admin] sources error: {e}")
        return _err("Failed to fetch sources", 500, "internal_error")


# ============================================================
# POST /api/admin/init — initialize database
# ============================================================
@api_admin.route("/init", methods=["POST"])
@admin_required
@admin_rate_limit
def init_database():
    """Initialize the database schema."""
    try:
        init_db()
        logger.info("[api_admin] Database initialized via admin API")
        return _ok(message="Database initialized successfully")
    except Exception as e:
        logger.error(f"[api_admin] init_db error: {e}")
        return _err(f"Database initialization failed: {str(e)[:200]}", 500, "internal_error")


# ============================================================
# POST /api/admin/clear — clear all news data and cache
# ============================================================
@api_admin.route("/clear", methods=["POST"])
@admin_required
@admin_rate_limit
def clear_data():
    """Clear all news data and cache. USE WITH CAUTION."""
    try:
        pg = is_pg()
        p = P()

        with get_db() as conn:
            cursor = conn.cursor()

            # Clear news table
            cursor.execute("DELETE FROM news")
            deleted = cursor.rowcount

            # Clear other data tables
            for table in ("telegram_log", "scrape_log", "ai_cache"):
                try:
                    cursor.execute(f"DELETE FROM {table}")
                except Exception:
                    pass

            conn.commit()

        # Clear cache
        cache.clear()

        logger.warning(f"[api_admin] Data cleared: {deleted} news articles deleted")
        return _ok(data={"deleted_news": deleted}, message="All data cleared successfully")
    except Exception as e:
        logger.error(f"[api_admin] clear_data error: {e}")
        return _err(f"Failed to clear data: {str(e)[:200]}", 500, "internal_error")


# ============================================================
# POST /api/admin/cache/clear — clear cache
# ============================================================
@api_admin.route("/cache/clear", methods=["POST"])
@admin_required
@admin_rate_limit
def clear_cache():
    """Clear the application cache."""
    try:
        cache.clear()
        logger.info("[api_admin] Cache cleared via admin API")
        return _ok(message="Cache cleared successfully")
    except Exception as e:
        logger.error(f"[api_admin] clear_cache error: {e}")
        return _err("Failed to clear cache", 500, "internal_error")


# ============================================================
# GET /api/admin/cache/stats — cache statistics
# ============================================================
@api_admin.route("/cache/stats", methods=["GET"])
@admin_required
@admin_rate_limit
def cache_stats():
    """Get cache statistics."""
    try:
        stats = cache.stats()
        return _ok(data=stats)
    except Exception as e:
        logger.error(f"[api_admin] cache_stats error: {e}")
        return _err("Failed to fetch cache stats", 500, "internal_error")


# ============================================================
# GET /api/admin/scrape-logs — recent scrape logs
# ============================================================
@api_admin.route("/scrape-logs", methods=["GET"])
@admin_required
@admin_rate_limit
def scrape_logs():
    """Get recent scrape execution logs."""
    try:
        limit = request.args.get("limit", 50, type=int)
        limit = min(max(limit, 1), 200)

        logs = get_scrape_logs(limit=limit)
        return _ok(data={"logs": logs, "count": len(logs)})
    except Exception as e:
        logger.error(f"[api_admin] scrape_logs error: {e}")
        return _err("Failed to fetch scrape logs", 500, "internal_error")


# ============================================================
# POST /api/admin/trigger-scrape — trigger a manual scrape run
# ============================================================
@api_admin.route("/trigger-scrape", methods=["POST"])
@admin_required
@admin_rate_limit
def trigger_scrape():
    """Manually trigger a scrape run (throttled to prevent abuse)."""
    global _last_scrape_time

    try:
        now = time.time()

        # Throttle: allow one scrape per SCRAPE_COOLDOWN seconds
        if now - _last_scrape_time < SCRAPE_COOLDOWN:
            remaining = int(SCRAPE_COOLDOWN - (now - _last_scrape_time)) + 1
            return _err(
                f"Scrape throttled. Please wait {remaining}s before triggering again.",
                429,
                "throttled",
            )

        # Prevent concurrent scrapes
        if not _scrape_lock.acquire(blocking=False):
            return _err("A scrape is already running", 409, "conflict")

        _last_scrape_time = now

        try:
            result = scrape_all()
            logger.info(f"[api_admin] Manual scrape completed: {result}")
            return _ok(data=result, message="Scrape completed successfully")
        finally:
            _scrape_lock.release()

    except Exception as e:
        logger.error(f"[api_admin] trigger_scrape error: {e}")
        return _err(f"Scrape failed: {str(e)[:200]}", 500, "internal_error")


# ============================================================
# GET /api/admin/newsletter/subscribers — SECURITY BLOCKED
# ============================================================
@api_admin.route("/newsletter/subscribers", methods=["GET"])
@admin_required
@admin_rate_limit
def newsletter_subscribers():
    """Returns 403 for security — subscriber emails are protected data."""
    return jsonify({
        "success": False,
        "error": "NOT IMPLEMENTED",
        "code": 403,
        "message": "Access to subscriber emails is restricted for security reasons.",
        "request_id": getattr(request, "request_id", ""),
    }), 403
