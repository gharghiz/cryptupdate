# -*- coding: utf-8 -*-
"""
CryptositNews - News API Routes
Public endpoints for fetching news, searching, SSE live updates, and AI intelligence.
"""

import json
import time

from flask import (
    Blueprint, request, jsonify, Response,
)

from app.config import Config
from app.utils.helpers import setup_logger, sanitize_search, time_ago
from app.models.db import (
    get_news, get_news_by_id, search_news, get_related_news, cache,
)
from app.services.ai_service import generate_market_intelligence
from app.middleware.rate_limit import rate_limit

logger = setup_logger("api_news")

api_news = Blueprint("api_news", __name__, url_prefix="/api")


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
# GET /api/news — list news with optional filters
# ============================================================
@api_news.route("/news", methods=["GET"])
@rate_limit
def list_news():
    """Get paginated news with optional category and importance filters."""
    try:
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        category = request.args.get("category", None, type=str)
        important = request.args.get("important", "false", type=str).lower() == "true"

        # Clamp limit to prevent abuse
        limit = min(max(limit, 1), 200)
        offset = max(offset, 0)

        articles = get_news(limit=limit, offset=offset, category=category, important_only=important)

        # Enrich with time_ago
        for article in articles:
            article["time_ago"] = time_ago(article.get("published") or article.get("created_at"))

        return _ok(data={"news": articles, "count": len(articles), "limit": limit, "offset": offset})
    except Exception as e:
        logger.error(f"[api_news] list_news error: {e}")
        return _err("Failed to fetch news", 500, "internal_error")


# ============================================================
# GET /api/news/<int:news_id> — single news article
# ============================================================
@api_news.route("/news/<int:news_id>", methods=["GET"])
@rate_limit
def get_single_news(news_id):
    """Get a single news article by ID."""
    try:
        article = get_news_by_id(news_id)
        if not article:
            return _err("Article not found", 404, "not_found")

        article["time_ago"] = time_ago(article.get("published") or article.get("created_at"))
        return _ok(data=article)
    except Exception as e:
        logger.error(f"[api_news] get_single_news {news_id} error: {e}")
        return _err("Failed to fetch article", 500, "internal_error")


# ============================================================
# GET /api/search — full-text search
# ============================================================
@api_news.route("/search", methods=["GET"])
@rate_limit
def search():
    """Search news articles by query string."""
    try:
        query = request.args.get("q", "", type=str)
        limit = request.args.get("limit", 20, type=int)
        limit = min(max(limit, 1), 100)

        if not query or len(query.strip()) < 2:
            return _err("Search query must be at least 2 characters", 400, "invalid_query")

        results = search_news(query, limit=limit)

        for article in results:
            article["time_ago"] = time_ago(article.get("published") or article.get("created_at"))

        return _ok(data={"results": results, "query": query, "count": len(results)})
    except Exception as e:
        logger.error(f"[api_news] search error: {e}")
        return _err("Search failed", 500, "internal_error")


# ============================================================
# GET /api/related/<int:news_id> — related articles
# ============================================================
@api_news.route("/related/<int:news_id>", methods=["GET"])
@rate_limit
def related(news_id):
    """Get related articles based on category."""
    try:
        articles = get_related_news(news_id, limit=5)
        for article in articles:
            article["time_ago"] = time_ago(article.get("published") or article.get("created_at"))
        return _ok(data={"related": articles, "news_id": news_id})
    except Exception as e:
        logger.error(f"[api_news] related {news_id} error: {e}")
        return _err("Failed to fetch related articles", 500, "internal_error")


# ============================================================
# GET /api/sse — Server-Sent Events live stream
# ============================================================
@api_news.route("/sse", methods=["GET"])
def sse_stream():
    """SSE endpoint for live updates (prices, fear & greed, latest news every 30s)."""

    def event_generator():
        try:
            while True:
                try:
                    # Latest news (max 5)
                    latest = get_news(limit=5, offset=0)
                    for item in latest:
                        item["time_ago"] = time_ago(item.get("published") or item.get("created_at"))

                    # Cached prices
                    prices = cache.get("market:prices") or []
                    fear_greed = cache.get("market:fear_greed") or {}

                    payload = {
                        "type": "update",
                        "latest_news": latest,
                        "prices": prices,
                        "fear_greed": fear_greed,
                        "timestamp": time.time(),
                    }

                    yield f"data: {json.dumps(payload, default=str)}\n\n"

                except Exception as e:
                    logger.error(f"[sse] Stream error: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error'})}\n\n"

                # Send heartbeat every 30 seconds
                time.sleep(30)

        except GeneratorExit:
            logger.debug("[sse] Client disconnected")

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# GET /api/market-intelligence — AI-generated market summary
# ============================================================
@api_news.route("/market-intelligence", methods=["GET"])
@rate_limit
def market_intelligence():
    """Generate AI market intelligence summary from recent news (cached)."""
    try:
        cache_key = "ai:market_intelligence"
        cached = cache.get(cache_key)
        if cached:
            return _ok(data=cached)

        # Fetch recent news for intelligence generation
        recent_news = get_news(limit=10, offset=0, important_only=True)
        if not recent_news:
            recent_news = get_news(limit=10, offset=0)

        if not recent_news:
            return _ok(data={"summary": "No news available for analysis."})

        summary = generate_market_intelligence(recent_news)

        result = {
            "summary": summary or "Unable to generate market intelligence at this time.",
            "articles_analyzed": len(recent_news),
            "cached": False,
        }

        # Cache for 1 hour
        cache.set(cache_key, result, ttl=3600)

        return _ok(data=result)
    except Exception as e:
        logger.error(f"[api_news] market_intelligence error: {e}")
        return _err("Failed to generate market intelligence", 500, "internal_error")
