# -*- coding: utf-8 -*-
"""
CryptositNews v3 - App Factory
"""

import os
import time
import json
import threading
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template, Response
from werkzeug.exceptions import HTTPException

from app.config import Config
from app.utils.helpers import setup_logger, generate_request_id

logger = setup_logger("app")


def create_app(config_class=None):
    config_class = config_class or Config

    # Validate configuration
    config_class.validate()

    BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.config.from_object(config_class)

    @app.before_request
    def add_request_context():
        request.request_id = generate_request_id()
        request.start_time = time.time()

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Request-ID"] = getattr(request, "request_id", "")
        elapsed = time.time() - getattr(request, "start_time", time.time())
        response.headers["X-Response-Time"] = f"{elapsed:.3f}s"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(e):
        req_id = getattr(request, "request_id", "unknown")
        logger.error(f"[{req_id}] Unhandled {type(e).__name__}: {e}", exc_info=True)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal Server Error", "code": 500, "request_id": req_id}), 500
        return render_template("404.html", theme="dark"), 500

    @app.errorhandler(400)
    def handle_400(e):
        req_id = getattr(request, "request_id", "")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Bad Request", "code": 400, "request_id": req_id}), 400
        return render_template("404.html", theme="dark"), 400

    @app.errorhandler(401)
    def handle_401(e):
        req_id = getattr(request, "request_id", "")
        return jsonify({"error": "Unauthorized", "code": 401, "request_id": req_id}), 401

    @app.errorhandler(403)
    def handle_403(e):
        req_id = getattr(request, "request_id", "")
        return jsonify({"error": "Forbidden", "code": 403, "request_id": req_id}), 403

    @app.errorhandler(404)
    def handle_404(e):
        req_id = getattr(request, "request_id", "")
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not Found", "code": 404, "request_id": req_id}), 404
        theme = request.cookies.get("theme", "dark")
        return render_template("404.html", theme=theme), 404

    @app.errorhandler(405)
    def handle_405(e):
        req_id = getattr(request, "request_id", "")
        return jsonify({"error": "Method Not Allowed", "code": 405, "request_id": req_id}), 405

    @app.errorhandler(429)
    def handle_429(e):
        req_id = getattr(request, "request_id", "")
        return jsonify({"error": "Too Many Requests", "code": 429, "request_id": req_id}), 429

    @app.errorhandler(500)
    def handle_500(e):
        req_id = getattr(request, "request_id", "")
        logger.error(f"[{req_id}] Server error: {e}", exc_info=True)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal Server Error", "code": 500, "request_id": req_id}), 500
        return render_template("404.html", theme="dark"), 500

    from app.routes.pages import bp as pages_bp
    from app.routes.api_news import bp as api_news_bp
    from app.routes.api_market import bp as api_market_bp
    from app.routes.api_admin import bp as api_admin_bp
    from app.routes.api_user import bp as api_user_bp
    from app.routes.api_alerts import bp as api_alerts_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_news_bp)
    app.register_blueprint(api_market_bp)
    app.register_blueprint(api_admin_bp, url_prefix="/api/admin")
    app.register_blueprint(api_user_bp, url_prefix="/api")
    app.register_blueprint(api_alerts_bp, url_prefix="/api")

    from app.models.db import init_db
    init_db()

    logger.info("=" * 60)
    logger.info("CryptositNews v3.0 - SaaS Edition (Redesigned)")
    logger.info(f"  Database : {'PostgreSQL (Pooled)' if config_class.DATABASE_URL else 'SQLite'}")
    logger.info(f"  RSS Feeds: {len(config_class.RSS_FEEDS)}")
    logger.info(f"  Telegram : {'Configured' if config_class.BOT_TOKEN else 'NOT CONFIGURED'}")
    logger.info(f"  OpenAI   : {'Configured' if config_class.OPENAI_API_KEY else 'NOT CONFIGURED'}")
    logger.info(f"  JWT Auth : Strict Mode")
    logger.info(f"  Redis    : {config_class.REDIS_URL or 'In-memory cache + rate limit'}")
    logger.info(f"  Worker   : Parallel ({config_class.WORKER_PARALLEL_POSTS} threads)")
    logger.info("=" * 60)

    return app
