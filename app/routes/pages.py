# -*- coding: utf-8 -*-
"""
CryptositNews - Page Routes
Renders HTML pages, RSS feed, sitemap, robots.txt, PWA manifest, and health check.
"""

import json
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, jsonify,
    Response, make_response, current_app,
)

from app.config import Config
from app.utils.helpers import setup_logger, time_ago, safe_html
from app.models.db import (
    get_news, get_news_by_id, get_related_news, get_health, cache,
)
from app.utils.security import verify_admin_key

logger = setup_logger("pages")

pages = Blueprint("pages", __name__)


# ============================================================
# Helper: read language/theme from cookies
# ============================================================
def _get_lang():
    return request.cookies.get("lang", Config.DEFAULT_LANGUAGE)


def _get_theme():
    return request.cookies.get("theme", "dark")


def _site_url():
    """Derive the base site URL from the request."""
    scheme = request.scheme
    host = request.host
    return f"{scheme}://{host}"


# ============================================================
# HTML Pages
# ============================================================
@pages.route("/")
def index():
    """Render the main news index page."""
    try:
        lang = _get_lang()
        theme = _get_theme()
        return render_template("index.html", lang=lang, theme=theme)
    except Exception as e:
        logger.error(f"[pages] Error rendering index: {e}")
        return render_template("index.html", lang="en", theme="dark")


@pages.route("/article/<int:news_id>")
def article(news_id):
    """Render a single news article page."""
    try:
        news = get_news_by_id(news_id)
        if not news:
            return render_template("404.html", theme=_get_theme()), 404

        related = get_related_news(news_id, limit=5)
        news["time_ago"] = time_ago(news.get("published") or news.get("created_at"))
        news["summary_safe"] = safe_html(news.get("summary", ""))

        return render_template(
            "article.html",
            news=news,
            related=related,
            lang=_get_lang(),
            theme=_get_theme(),
        )
    except Exception as e:
        logger.error(f"[pages] Error rendering article {news_id}: {e}")
        return render_template("404.html", theme="dark"), 404


@pages.route("/admin")
def admin_page():
    """Render the admin dashboard (requires valid admin key via ?key= param)."""
    try:
        admin_key = request.args.get("key", "")
        if not verify_admin_key(admin_key):
            return jsonify({
                "error": "Unauthorized",
                "code": 401,
                "message": "Invalid or missing admin key",
                "request_id": getattr(request, "request_id", ""),
            }), 401

        return render_template("admin.html", theme=_get_theme())
    except Exception as e:
        logger.error(f"[pages] Error rendering admin: {e}")
        return jsonify({"error": "Internal Server Error", "code": 500}), 500


@pages.route("/market")
def market():
    """Render the market data page."""
    try:
        lang = _get_lang()
        theme = _get_theme()
        return render_template("market.html", lang=lang, theme=theme)
    except Exception as e:
        logger.error(f"[pages] Error rendering market: {e}")
        return render_template("market.html", lang="en", theme="dark")


@pages.route("/news")
def news_page():
    """Render the news listing page with filters."""
    try:
        lang = _get_lang()
        theme = _get_theme()
        return render_template("news.html", lang=lang, theme=theme)
    except Exception as e:
        logger.error(f"[pages] Error rendering news page: {e}")
        return render_template("news.html", lang="en", theme="dark")


@pages.route("/telegram")
def telegram_info():
    """Render the Telegram bot information page."""
    try:
        lang = _get_lang()
        theme = _get_theme()
        return render_template("telegram.html", lang=lang, theme=theme)
    except Exception as e:
        logger.error(f"[pages] Error rendering telegram page: {e}")
        return render_template("telegram.html", lang="en", theme="dark")


@pages.route("/about")
def about():
    """Render the about and contact page."""
    try:
        lang = _get_lang()
        theme = _get_theme()
        return render_template("about.html", lang=lang, theme=theme)
    except Exception as e:
        logger.error(f"[pages] Error rendering about page: {e}")
        return render_template("about.html", lang="en", theme="dark")


# ============================================================
# RSS Feed
# ============================================================
@pages.route("/rss")
def rss_feed():
    """Generate an XML RSS feed of the latest news."""
    try:
        articles = get_news(limit=50, offset=0)
        site_url = _site_url()

        xml_items = []
        for item in articles:
            title = item.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            link = item.get("url", "")
            description = (item.get("summary", "") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            pub_date = item.get("published", item.get("created_at", ""))
            if pub_date and hasattr(pub_date, "strftime"):
                pub_date = pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
            category = item.get("category", "market")

            xml_items.append(
                f"    <item>\n"
                f"      <title>{title}</title>\n"
                f"      <link>{link}</link>\n"
                f"      <description>{description}</description>\n"
                f"      <category>{category}</category>\n"
                f"      <pubDate>{pub_date}</pubDate>\n"
                f"    </item>"
            )

        items_xml = "\n".join(xml_items)
        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{Config.PWA_APP_NAME}</title>
    <link>{site_url}</link>
    <description>{Config.PWA_DESCRIPTION}</description>
    <language>en</language>
    <lastBuildDate>{now_str}</lastBuildDate>
    <atom:link href="{site_url}/rss" rel="self" type="application/rss+xml"/>
{items_xml}
  </channel>
</rss>"""

        return Response(rss_xml, mimetype="application/rss+xml")
    except Exception as e:
        logger.error(f"[pages] RSS feed error: {e}")
        return Response("<?xml version=\"1.0\"?><rss><channel></channel></rss>", mimetype="application/rss+xml")


# ============================================================
# Sitemap
# ============================================================
@pages.route("/sitemap.xml")
def sitemap():
    """Generate an XML sitemap."""
    try:
        articles = get_news(limit=500, offset=0)
        site_url = _site_url()

        urls = [f"  <url><loc>{site_url}/</loc><changefreq>always</changefreq><priority>1.0</priority></url>"]
        for item in articles:
            nid = item.get("id", "")
            if nid:
                urls.append(f"  <url><loc>{site_url}/article/{nid}</loc><changefreq>hourly</changefreq><priority>0.8</priority></url>")

        urls_xml = "\n".join(urls)
        sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}
</urlset>"""

        return Response(sitemap_xml, mimetype="application/xml")
    except Exception as e:
        logger.error(f"[pages] Sitemap error: {e}")
        return Response("<?xml version=\"1.0\"?><urlset></urlset>", mimetype="application/xml")


# ============================================================
# Robots.txt
# ============================================================
@pages.route("/robots.txt")
def robots():
    """Serve robots.txt."""
    site_url = _site_url()
    txt = (
        f"User-agent: *\n"
        f"Allow: /\n"
        f"Disallow: /api/admin/\n"
        f"Disallow: /admin\n"
        f"Sitemap: {site_url}/sitemap.xml\n"
    )
    return Response(txt, mimetype="text/plain")


# ============================================================
# PWA Manifest
# ============================================================
@pages.route("/manifest.json")
def manifest():
    """Serve the PWA manifest JSON."""
    site_url = _site_url()
    data = {
        "name": Config.PWA_APP_NAME,
        "short_name": Config.PWA_SHORT_NAME,
        "description": Config.PWA_DESCRIPTION,
        "start_url": "/",
        "display": "standalone",
        "background_color": Config.PWA_BACKGROUND_COLOR,
        "theme_color": Config.PWA_THEME_COLOR,
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    resp = jsonify(data)
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


# ============================================================
# Service Worker
# ============================================================
@pages.route("/sw.js")
def service_worker():
    """Serve the service worker JavaScript file from static."""
    try:
        return current_app.send_static_file("sw.js")
    except Exception:
        return Response("// Service Worker not found", mimetype="application/javascript")


# ============================================================
# Health Check
# ============================================================
@pages.route("/health")
def health():
    """Health check endpoint with service status information."""
    try:
        health_data = get_health()
        cache_stats = cache.stats()

        health_data.update({
            "telegram_configured": bool(Config.BOT_TOKEN and Config.CHANNEL_ID),
            "openai_configured": bool(Config.OPENAI_API_KEY),
            "rss_feeds_count": len(Config.RSS_FEEDS),
            "cache": cache_stats,
            "request_id": getattr(request, "request_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        status_code = 200 if health_data.get("status") == "healthy" else 503
        return jsonify(health_data), status_code
    except Exception as e:
        logger.error(f"[pages] Health check error: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)[:200],
            "request_id": getattr(request, "request_id", ""),
        }), 503
