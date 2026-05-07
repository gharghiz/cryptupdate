# -*- coding: utf-8 -*-
"""
CryptositNews - Price Alerts API Routes
Create and retrieve cryptocurrency price alerts.
"""

from flask import (
    Blueprint, request, jsonify,
)

from app.config import Config
from app.utils.helpers import setup_logger, format_price
from app.models.db import (
    get_active_alerts, add_price_alert,
)
from app.middleware.rate_limit import rate_limit

logger = setup_logger("api_alerts")

api_alerts = Blueprint("api_alerts", __name__, url_prefix="/api")

VALID_CONDITIONS = ("above", "below")


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
# GET /api/alerts — list active price alerts
# ============================================================
@api_alerts.route("/alerts", methods=["GET"])
@rate_limit
def list_alerts():
    """Get all active (non-triggered) price alerts."""
    try:
        alerts = get_active_alerts()
        return _ok(data={"alerts": alerts, "count": len(alerts)})
    except Exception as e:
        logger.error(f"[api_alerts] list_alerts error: {e}")
        return _err("Failed to fetch alerts", 500, "internal_error")


# ============================================================
# POST /api/alerts — create a new price alert
# ============================================================
@api_alerts.route("/alerts", methods=["POST"])
@rate_limit
def create_alert():
    """Create a new price alert for a cryptocurrency."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        symbol = (body.get("symbol") or "").strip().upper()
        target_price = body.get("target_price")
        condition = (body.get("condition") or "above").strip().lower()

        # Resolve coin name from symbol via Config.COIN_MAP
        coin = Config.COIN_MAP.get(symbol, symbol.lower())

        # Validate symbol
        if not symbol or len(symbol) > 10:
            return _err("A valid coin symbol is required (e.g. BTC, ETH)", 400, "invalid_symbol")

        # Validate target_price
        try:
            target_price = float(target_price)
            if target_price <= 0:
                raise ValueError("Price must be positive")
        except (ValueError, TypeError):
            return _err("target_price must be a positive number", 400, "invalid_price")

        # Validate condition
        if condition not in VALID_CONDITIONS:
            return _err(f"condition must be one of: {', '.join(VALID_CONDITIONS)}", 400, "invalid_condition")

        # Create the alert
        success = add_price_alert(coin, symbol, target_price, condition)
        if success:
            logger.info(f"[api_alerts] Alert created: {symbol} {condition} {format_price(target_price)}")
            return _ok(data={
                "symbol": symbol,
                "coin": coin,
                "target_price": target_price,
                "target_price_formatted": format_price(target_price),
                "condition": condition,
            }, message="Price alert created successfully", status=201)
        else:
            return _err("Failed to create price alert", 500, "internal_error")

    except Exception as e:
        logger.error(f"[api_alerts] create_alert error: {e}")
        return _err("Failed to create price alert", 500, "internal_error")
