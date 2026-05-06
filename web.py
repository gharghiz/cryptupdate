# -*- coding: utf-8 -*-
"""
CryptositNews - Flask Web Server
Full web API with admin dashboard, search, newsletter, SSE, i18n, and all P3 features.
"""

import os
import time
import json
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps

import requests
from flask import (
    Flask, render_template, jsonify, request, Response,
    send_from_directory, abort, make_response,
)

import config
from utils import (
    setup_logger, now_utc, hours_ago, time_ago, safe_html,
    clean_url, format_number, generate_request_id, sanitize_search,
    format_price, format_percent,
)
from database import (
    cache, get_news, get_news_by_id, search_news, get_related_news,
    get_stats, get_health_info, get_active_alerts, add_price_alert,
    newsletter_subscribe, newsletter_unsubscribe, get_newsletter_stats,
    get_newsletter_subscribers, get_db, _coingecko_wait, cache_stats,
    init_db,
)
from ai import generate_market_intelligence

logger = setup_logger("web")
app = Flask(__name__)

# ============================================================
# MIDDLEWARE
# ============================================================

@app.before_request
def add_request_id():
    """Add request ID and start timer to request context."""
    request.request_id = generate_request_id()
    request.start_time = time.time()


@app.after_request
def add_headers(response):
    """Add security and timing headers."""
    response.headers["X-Request-ID"] = getattr(request, "request_id", "unknown")
    elapsed = time.time() - getattr(request, "start_time", time.time())
    response.headers["X-Response-Time"] = f"{elapsed:.3f}s"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ============================================================
# API RATE LIMITER
# ============================================================
_rate_store = {}
_rate_lock = threading.Lock()


def check_rate_limit():
    """Check per-IP rate limiting. Returns (allowed, remaining, reset_time)."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
    now = time.time()

    with _rate_lock:
        if ip not in _rate_store:
            _rate_store[ip] = {"timestamps": [], "burst": 0}

        entry = _rate_store[ip]
        # Remove old timestamps
        entry["timestamps"] = [t for t in entry["timestamps"] if now - t < config.RATE_LIMIT_WINDOW]

        if len(entry["timestamps"]) >= config.RATE_LIMIT_REQUESTS:
            oldest = min(entry["timestamps"])
            reset_time = int(oldest + config.RATE_LIMIT_WINDOW - now)
            return False, 0, reset_time

        entry["timestamps"].append(now)
        remaining = config.RATE_LIMIT_REQUESTS - len(entry["timestamps"])
        return True, remaining, 0


def rate_limit(f):
    """Decorator for API rate limiting."""
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, remaining, reset = check_rate_limit()
        if not allowed:
            response = jsonify({
                "error": "Rate limit exceeded",
                "code": 429,
                "reset_in": reset,
                "request_id": getattr(request, "request_id", ""),
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(max(reset, 1))
            response.headers["X-RateLimit-Remaining"] = "0"
            return response
        response = f(*args, **kwargs)
        if isinstance(response, tuple):
            resp, code = response
        else:
            resp, code = response, 200
        if hasattr(resp, 'headers'):
            resp.headers["X-RateLimit-Remaining"] = str(remaining)
        return resp
    return decorated


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def admin_auth_required(f):
    """Decorator for admin endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Admin-Key", request.args.get("key", ""))
        if key != config.ADMIN_KEY:
            return jsonify({"error": "Unauthorized", "code": 401}), 401
        return f(*args, **kwargs)
    return decorated


def get_coin_prices():
    """Get prices for tracked coins from CoinGecko."""
    cache_key = "prices:all"
    result = cache.get(cache_key)
    if result:
        return result

    _coingecko_wait()
    try:
        coin_ids = list(set(config.COIN_MAP.values()))
        ids_str = ",".join(coin_ids[:30])

        resp = requests.get(
            f"{config.COINGECKO_BASE}/simple/price",
            params={"ids": ids_str, "vs_currencies": "usd", "include_24hr_change": "true",
                    "include_market_cap": "true", "include_24hr_vol": "true"},
            timeout=15,
        )
        data = resp.json()

        # Map back to symbols
        prices = {}
        for symbol, coin_id in config.COIN_MAP.items():
            if coin_id in data:
                coin_data = data[coin_id]
                prices[symbol] = {
                    "usd": coin_data.get("usd", 0),
                    "usd_24h_change": round(coin_data.get("usd_24h_change", 0), 2),
                    "usd_market_cap": coin_data.get("usd_market_cap", 0),
                    "usd_24h_vol": coin_data.get("usd_24h_vol", 0),
                }

        cache.set(cache_key, prices, config.CACHE_PRICES_TTL)
        return prices

    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
        return {}


def get_fear_greed():
    """Get Fear & Greed Index."""
    cache_key = "fear_greed"
    result = cache.get(cache_key)
    if result:
        return result

    try:
        resp = requests.get(config.FEAR_GREED_URL, timeout=10)
        data = resp.json()
        if data.get("data"):
            value = data["data"][0]
            fg = {
                "value": int(value.get("value", 50)),
                "classification": value.get("value_classification", "Neutral"),
                "timestamp": value.get("timestamp", ""),
            }
            cache.set(cache_key, fg, config.CACHE_FEAR_TTL)
            return fg
    except Exception as e:
        logger.error(f"Error fetching fear & greed: {e}")
    return {"value": 50, "classification": "Neutral", "timestamp": ""}


def get_global_market():
    """Get global cryptocurrency market data."""
    cache_key = "global_market"
    result = cache.get(cache_key)
    if result:
        return result

    _coingecko_wait()
    try:
        resp = requests.get(f"{config.COINGECKO_BASE}/global", timeout=10)
        data = resp.json()
        market = data.get("data", {})
        result = {
            "active_cryptocurrencies": market.get("active_cryptocurrencies", 0),
            "total_market_cap_usd": market.get("total_market_cap", {}).get("usd", 0),
            "total_volume_usd": market.get("total_volume", {}).get("usd", 0),
            "market_cap_change_24h": round(market.get("market_cap_change_percentage_24h_usd", 0), 2),
            "btc_dominance": round(market.get("market_cap_percentage", {}).get("btc", 0), 2),
            "eth_dominance": round(market.get("market_cap_percentage", {}).get("eth", 0), 2),
        }
        cache.set(cache_key, result, config.CACHE_GLOBAL_TTL)
        return result
    except Exception as e:
        logger.error(f"Error fetching global market: {e}")
        return {}


def get_trending_coins():
    """Get trending coins from CoinGecko."""
    cache_key = "trending_coins"
    result = cache.get(cache_key)
    if result:
        return result

    _coingecko_wait()
    try:
        resp = requests.get(f"{config.COINGECKO_BASE}/search/trending", timeout=10)
        data = resp.json()
        coins = []
        for item in data.get("coins", [])[:10]:
            coin = item.get("item", {})
            coins.append({
                "id": coin.get("id", ""),
                "symbol": coin.get("symbol", "").upper(),
                "name": coin.get("name", ""),
                "market_cap_rank": coin.get("market_cap_rank", 0),
                "price_btc": coin.get("price_btc", 0),
                "thumb": coin.get("thumb", ""),
            })
        cache.set(cache_key, coins, config.CACHE_TRENDING_TTL)
        return coins
    except Exception as e:
        logger.error(f"Error fetching trending: {e}")
        return []


# ============================================================
# SSE (Server-Sent Events) for Live Updates
# ============================================================
_sse_clients = set()
_sse_lock = threading.Lock()


@app.route("/api/sse")
def sse_stream():
    """Server-Sent Events endpoint for real-time updates."""
    def generate():
        client_id = generate_request_id()
        with _sse_lock:
            _sse_clients.add(client_id)

        try:
            # Send initial data
            prices = get_coin_prices()
            fg = get_fear_greed()
            initial = {
                "type": "init",
                "prices": {k: v for k, v in list(prices.items())[:10]},
                "fear_greed": fg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield f"data: {json.dumps(initial)}\n\n"

            # Send periodic updates
            counter = 0
            while True:
                counter += 1
                time.sleep(30)  # Update every 30 seconds

                # Check if client is still connected
                if client_id not in _sse_clients:
                    break

                update = {"type": "update", "timestamp": datetime.now(timezone.utc).isoformat()}

                # Refresh prices every 2nd update
                if counter % 2 == 0:
                    cache.delete("prices:all")
                    prices = get_coin_prices()
                    update["prices"] = {k: v for k, v in list(prices.items())[:10]}

                # Refresh fear & greed every 5th update
                if counter % 5 == 0:
                    cache.delete("fear_greed")
                    fg = get_fear_greed()
                    update["fear_greed"] = fg

                # Check for new news
                if counter % 3 == 0:
                    latest = get_news(limit=3)
                    update["latest_news"] = [{
                        "id": n["id"],
                        "title": n["title"][:100],
                        "category": n.get("category", ""),
                        "time": time_ago(n.get("published")),
                    } for n in latest]

                yield f"data: {json.dumps(update)}\n\n"

        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard(client_id)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============================================================
# PAGE ROUTES
# ============================================================

@app.route("/")
def index():
    """Main page."""
    lang = request.args.get("lang", request.cookies.get("lang", config.DEFAULT_LANGUAGE))
    theme = request.cookies.get("theme", "dark")
    return render_template("index.html", lang=lang, theme=theme)


@app.route("/article/<int:news_id>")
def article(news_id):
    """Article detail page."""
    news = get_news_by_id(news_id)
    if not news:
        abort(404)

    related = get_related_news(news_id, limit=5)
    lang = request.args.get("lang", request.cookies.get("lang", config.DEFAULT_LANGUAGE))
    theme = request.cookies.get("theme", "dark")

    return render_template("article.html", news=news, related=related, lang=lang, theme=theme)


@app.route("/admin")
def admin_page():
    """Admin dashboard page."""
    key = request.args.get("key", "")
    if key != config.ADMIN_KEY:
        abort(401)
    theme = request.cookies.get("theme", "dark")
    return render_template("admin.html", theme=theme)


# ============================================================
# API ROUTES
# ============================================================

@app.route("/api/news")
@rate_limit
def api_news():
    """Get news articles with optional filtering."""
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = max(int(request.args.get("offset", 0)), 0)
    category = request.args.get("category", "all")
    important = request.args.get("important", "false").lower() == "true"

    news = get_news(limit=limit, offset=offset, category=category, important_only=important)
    return jsonify({"news": news, "count": len(news), "request_id": request.request_id})


@app.route("/api/news/<int:news_id>")
@rate_limit
def api_news_detail(news_id):
    """Get single news article."""
    news = get_news_by_id(news_id)
    if not news:
        return jsonify({"error": "Not found", "code": 404}), 404
    return jsonify({"news": news, "request_id": request.request_id})


@app.route("/api/search")
@rate_limit
def api_search():
    """Search news articles."""
    query = request.args.get("q", "")
    limit = min(int(request.args.get("limit", 20)), 50)

    if not query or len(query.strip()) < 2:
        return jsonify({"error": "Search query too short (min 2 chars)", "code": 400}), 400

    results = search_news(query, limit=limit)
    return jsonify({"results": results, "query": query, "count": len(results),
                     "request_id": request.request_id})


@app.route("/api/related/<int:news_id>")
@rate_limit
def api_related(news_id):
    """Get related articles."""
    related = get_related_news(news_id, limit=5)
    return jsonify({"related": related, "count": len(related), "request_id": request.request_id})


@app.route("/api/prices")
@rate_limit
def api_prices():
    """Get cryptocurrency prices."""
    prices = get_coin_prices()
    return jsonify({"prices": prices, "timestamp": datetime.now(timezone.utc).isoformat(),
                     "request_id": request.request_id})


@app.route("/api/fear-greed")
@rate_limit
def api_fear_greed():
    """Get Fear & Greed Index."""
    fg = get_fear_greed()
    return jsonify({"fear_greed": fg, "request_id": request.request_id})


@app.route("/api/global")
@rate_limit
def api_global():
    """Get global market data."""
    data = get_global_market()
    return jsonify({"global": data, "request_id": request.request_id})


@app.route("/api/trending")
@rate_limit
def api_trending():
    """Get trending coins."""
    coins = get_trending_coins()
    return jsonify({"trending": coins, "request_id": request.request_id})


@app.route("/api/stats")
@rate_limit
def api_stats():
    """Get news statistics."""
    stats = get_stats()
    return jsonify({"stats": stats, "request_id": request.request_id})


# ============================================================
# NEWSLETTER API
# ============================================================

@app.route("/api/newsletter/subscribe", methods=["POST"])
@rate_limit
def api_newsletter_subscribe():
    """Subscribe to newsletter."""
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()

    success, message = newsletter_subscribe(email)
    status = 200 if success else 400
    return jsonify({"success": success, "message": message, "request_id": request.request_id}), status


@app.route("/api/newsletter/unsubscribe", methods=["POST"])
@rate_limit
def api_newsletter_unsubscribe():
    """Unsubscribe from newsletter."""
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()

    success, message = newsletter_unsubscribe(email)
    status = 200 if success else 400
    return jsonify({"success": success, "message": message, "request_id": request.request_id}), status


# ============================================================
# PRICE ALERTS API
# ============================================================

@app.route("/api/alerts", methods=["GET"])
@rate_limit
def api_get_alerts():
    """Get active price alerts."""
    alerts = get_active_alerts()
    return jsonify({"alerts": alerts, "request_id": request.request_id})


@app.route("/api/alerts", methods=["POST"])
@rate_limit
def api_create_alert():
    """Create a new price alert."""
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "").upper()
    target = data.get("target_price", 0)
    condition = data.get("condition", "above")

    if not symbol or symbol not in config.COIN_MAP:
        return jsonify({"error": "Invalid symbol", "code": 400}), 400

    if not target or target <= 0:
        return jsonify({"error": "Invalid target price", "code": 400}), 400

    coin = symbol  # Default
    for s, name in config.COIN_MAP.items():
        if s == symbol:
            coin = name.replace("-", " ").title()
            break

    success = add_price_alert(coin, symbol, target, condition)
    if success:
        return jsonify({"success": True, "message": "Alert created", "request_id": request.request_id})
    return jsonify({"error": "Failed to create alert", "code": 500}), 500


# ============================================================
# MARKET INTELLIGENCE API
# ============================================================

@app.route("/api/market-intelligence")
@rate_limit
def api_market_intelligence():
    """Generate AI market intelligence summary."""
    news = get_news(limit=15)
    if not news:
        return jsonify({"intelligence": "", "request_id": request.request_id})

    # Check cache first
    cache_key = "market_intelligence"
    cached = cache.get(cache_key)
    if cached:
        return jsonify({"intelligence": cached, "cached": True, "request_id": request.request_id})

    intelligence = generate_market_intelligence(news)
    if intelligence:
        cache.set(cache_key, intelligence, 600)  # 10 min cache

    return jsonify({"intelligence": intelligence, "cached": False, "request_id": request.request_id})


# ============================================================
# HEALTH & FEEDS
# ============================================================

@app.route("/health")
def health():
    """Enhanced health check endpoint."""
    health_info = get_health_info()
    health_info["request_id"] = request.request_id
    health_info["cache_stats"] = cache_stats.to_dict()
    status = 200 if health_info["status"] == "healthy" else 503
    return jsonify(health_info), status


@app.route("/rss")
def rss_feed():
    """RSS feed for the news."""
    news = get_news(limit=30)
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
    xml += '  <channel>\n'
    xml += f'    <title>CryptositNews</title>\n'
    xml += f'    <link>{request.url_root}</link>\n'
    xml += f'    <description>Real-time Cryptocurrency News & Analysis</description>\n'
    xml += f'    <language>en-us</language>\n'
    xml += f'    <lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>\n'

    for item in news:
        title = safe_html(item.get("title", ""))
        link = item.get("url", "")
        desc = safe_html(item.get("summary", "") or item.get("ai_summary", ""))
        pub = item.get("published", item.get("created_at", ""))
        if pub:
            try:
                dt = datetime.fromisoformat(str(pub))
                pub_str = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            except Exception:
                pub_str = str(pub)
        else:
            pub_str = ""

        xml += '    <item>\n'
        xml += f'      <title>{title}</title>\n'
        xml += f'      <link>{link}</link>\n'
        xml += f'      <description>{desc}</description>\n'
        xml += f'      <category>{item.get("category", "market")}</category>\n'
        if pub_str:
            xml += f'      <pubDate>{pub_str}</pubDate>\n'
        xml += '    </item>\n'

    xml += '  </channel>\n</rss>'
    return Response(xml, mimetype="application/xml")


@app.route("/sitemap.xml")
def sitemap():
    """Generate sitemap.xml."""
    base = request.url_root
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += f'  <url><loc>{base}</loc><changefreq>always</changefreq><priority>1.0</priority></url>\n'
    xml += f'  <url><loc>{base}rss</loc><changefreq>always</changefreq><priority>0.8</priority></url>\n'

    # Add recent news articles
    news = get_news(limit=50)
    for item in news:
        url = item.get("url", "")
        if url:
            xml += f'  <url><loc>{url}</loc><priority>0.6</priority></url>\n'

    xml += '</urlset>'
    return Response(xml, mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    """Generate robots.txt."""
    base = request.url_root
    txt = f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n"
    return Response(txt, mimetype="text/plain")


# ============================================================
# PWA ROUTES
# ============================================================

@app.route("/manifest.json")
def manifest():
    """Serve PWA manifest."""
    return jsonify({
        "name": config.PWA_APP_NAME,
        "short_name": config.PWA_SHORT_NAME,
        "description": config.PWA_DESCRIPTION,
        "start_url": "/",
        "display": "standalone",
        "background_color": config.PWA_BACKGROUND_COLOR,
        "theme_color": config.PWA_THEME_COLOR,
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.route("/sw.js")
def service_worker():
    """Serve service worker."""
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


# ============================================================
# ADMIN ROUTES
# ============================================================

@app.route("/api/admin/stats")
@admin_auth_required
def admin_stats():
    """Get admin statistics."""
    stats = get_stats()
    stats["cache_stats"] = cache.get_stats()
    stats["newsletter"] = get_newsletter_stats()
    stats["alerts"] = len(get_active_alerts())
    return jsonify({"stats": stats, "request_id": request.request_id})


@app.route("/api/admin/sources")
@admin_auth_required
def admin_sources():
    """Get active news sources."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT source, COUNT(*) as cnt
                FROM news WHERE source != ''
                GROUP BY source ORDER BY cnt DESC LIMIT 30
            """)
            sources = [{"source": row["source"], "count": row["cnt"]} for row in cursor.fetchall()]
            return jsonify({"sources": sources, "request_id": request.request_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.route("/api/admin/init", methods=["POST"])
@admin_auth_required
def admin_init():
    """Initialize database."""
    try:
        init_db()
        return jsonify({"success": True, "message": "Database initialized"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/clear", methods=["POST"])
@admin_auth_required
def admin_clear():
    """Clear all news data."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM news")
            cursor.execute("DELETE FROM telegram_log")
            conn.commit()
            cache.clear()
            return jsonify({"success": True, "message": "All news cleared"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/cache/clear", methods=["POST"])
@admin_auth_required
def admin_cache_clear():
    """Clear all cache."""
    cache.clear()
    return jsonify({"success": True, "message": "Cache cleared"})


@app.route("/api/admin/cache/stats")
@admin_auth_required
def admin_cache_stats():
    """Get cache statistics."""
    stats = cache.get_stats()
    return jsonify({"cache_stats": stats})


@app.route("/api/admin/newsletter/subscribers")
@admin_auth_required
def admin_newsletter_subscribers():
    """Get newsletter subscribers."""
    subscribers = get_newsletter_subscribers()
    return jsonify({"subscribers": subscribers})


# ============================================================
# LANGUAGE & THEME PREFERENCES
# ============================================================

@app.route("/api/lang", methods=["POST"])
def set_language():
    """Set user language preference."""
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", config.DEFAULT_LANGUAGE)
    if lang not in config.SUPPORTED_LANGUAGES:
        lang = config.DEFAULT_LANGUAGE
    response = jsonify({"success": True, "lang": lang})
    response.set_cookie("lang", lang, max_age=365 * 86400)
    return response


@app.route("/api/theme", methods=["POST"])
def set_theme():
    """Set user theme preference."""
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "dark")
    if theme not in ["dark", "light"]:
        theme = "dark"
    response = jsonify({"success": True, "theme": theme})
    response.set_cookie("theme", theme, max_age=365 * 86400)
    return response


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad Request", "code": 400, "request_id": getattr(request, 'request_id', '')}), 400


@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": "Unauthorized", "code": 401, "request_id": getattr(request, 'request_id', '')}), 401


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not Found", "code": 404, "request_id": getattr(request, 'request_id', '')}), 404
    theme = request.cookies.get("theme", "dark")
    return render_template("404.html", theme=theme), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method Not Allowed", "code": 405, "request_id": getattr(request, 'request_id', '')}), 405


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "Too Many Requests", "code": 429, "request_id": getattr(request, 'request_id', '')}), 429


@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({"error": "Internal Server Error", "code": 500, "request_id": getattr(request, 'request_id', '')}), 500


# ============================================================
# INITIALIZATION
# ============================================================
init_db()
logger.info("CryptositNews Web Server ready")
