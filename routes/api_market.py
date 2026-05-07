# -*- coding: utf-8 -*-
"""
CryptositNews - Market Data API Routes
CoinGecko price data, Fear & Greed Index, global market data, and trending coins.
"""

import requests as http_requests

from flask import (
    Blueprint, request, jsonify,
)

from app.config import Config
from app.utils.helpers import setup_logger, format_price, format_percent, format_number
from app.models.db import coingecko_wait, cache
from app.middleware.rate_limit import rate_limit

logger = setup_logger("api_market")

api_market = Blueprint("api_market", __name__, url_prefix="/api")

# Session for CoinGecko requests with User-Agent
_cg_session = None


def _get_cg_session():
    """Lazy-init a requests session for CoinGecko API calls."""
    global _cg_session
    if _cg_session is None:
        _cg_session = http_requests.Session()
        _cg_session.headers.update({
            "User-Agent": "CryptositNews/2.0",
            "Accept": "application/json",
        })
    return _cg_session


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
# GET /api/prices — Coin prices from CoinGecko
# ============================================================
@api_market.route("/prices", methods=["GET"])
@rate_limit
def get_prices():
    """Get cryptocurrency prices from CoinGecko (cached)."""
    try:
        cache_key = "market:prices"
        cached = cache.get(cache_key)
        if cached:
            return _ok(data=cached)

        # Enforce CoinGecko rate limit
        coingecko_wait()

        session = _get_cg_session()
        coin_ids = ",".join(Config.COIN_MAP.values())

        params = {
            "ids": coin_ids,
            "vs_currencies": "usd",
            "order": "market_cap_desc",
            "sparkline": "false",
            "price_change_percentage": "24h",
        }

        resp = session.get(
            f"{Config.COINGECKO_BASE}/coins/markets",
            params=params,
            timeout=15,
        )

        if resp.status_code == 429:
            logger.warning("[api_market] CoinGecko rate limited")
            return _err("CoinGecko API rate limited. Please try again later.", 429, "rate_limited")

        resp.raise_for_status()
        data = resp.json()

        # Build a lookup from coin_id to symbol
        id_to_symbol = {v: k for k, v in Config.COIN_MAP.items()}

        prices = []
        for coin in data:
            symbol = id_to_symbol.get(coin.get("id", ""), "")
            prices.append({
                "id": coin.get("id", ""),
                "symbol": symbol,
                "name": coin.get("name", ""),
                "price": coin.get("current_price", 0),
                "price_formatted": format_price(coin.get("current_price", 0)),
                "change_24h": coin.get("price_change_percentage_24h", 0),
                "change_24h_formatted": format_percent(coin.get("price_change_percentage_24h", 0)),
                "market_cap": coin.get("market_cap", 0),
                "market_cap_formatted": format_number(coin.get("market_cap", 0)),
                "image": coin.get("image", ""),
            })

        cache.set(cache_key, prices, Config.CACHE_PRICES_TTL)
        return _ok(data=prices)

    except http_requests.Timeout:
        logger.error("[api_market] CoinGecko timeout")
        return _err("CoinGecko API request timed out", 504, "gateway_timeout")
    except http_requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 502
        logger.error(f"[api_market] CoinGecko HTTP error: {status_code}")
        return _err(f"CoinGecko API returned {status_code}", 502, "upstream_error")
    except Exception as e:
        logger.error(f"[api_market] get_prices error: {e}")
        return _err("Failed to fetch prices", 500, "internal_error")


# ============================================================
# GET /api/fear-greed — Fear & Greed Index
# ============================================================
@api_market.route("/fear-greed", methods=["GET"])
@rate_limit
def get_fear_greed():
    """Get the Crypto Fear & Greed Index (cached)."""
    try:
        cache_key = "market:fear_greed"
        cached = cache.get(cache_key)
        if cached:
            return _ok(data=cached)

        session = _get_cg_session()
        resp = session.get(Config.FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # The API returns {"data": [{"value": "...", "value_classification": "...", ...}]}
        entries = data.get("data", [])
        current = entries[0] if entries else {}

        result = {
            "value": int(current.get("value", 0)),
            "classification": current.get("value_classification", "Neutral"),
            "timestamp": current.get("timestamp", ""),
        }

        cache.set(cache_key, result, Config.CACHE_FEAR_TTL)
        return _ok(data=result)

    except http_requests.Timeout:
        logger.error("[api_market] Fear & Greed timeout")
        return _err("Fear & Greed API request timed out", 504, "gateway_timeout")
    except http_requests.HTTPError as e:
        logger.error(f"[api_market] Fear & Greed HTTP error: {e}")
        return _err("Fear & Greed API error", 502, "upstream_error")
    except Exception as e:
        logger.error(f"[api_market] get_fear_greed error: {e}")
        return _err("Failed to fetch Fear & Greed Index", 500, "internal_error")


# ============================================================
# GET /api/global — Global market data
# ============================================================
@api_market.route("/global", methods=["GET"])
@rate_limit
def get_global():
    """Get global cryptocurrency market data from CoinGecko (cached)."""
    try:
        cache_key = "market:global"
        cached = cache.get(cache_key)
        if cached:
            return _ok(data=cached)

        coingecko_wait()

        session = _get_cg_session()
        resp = session.get(
            f"{Config.COINGECKO_BASE}/global",
            timeout=15,
        )

        if resp.status_code == 429:
            logger.warning("[api_market] CoinGecko global rate limited")
            return _err("CoinGecko API rate limited. Please try again later.", 429, "rate_limited")

        resp.raise_for_status()
        data = resp.json().get("data", {})

        result = {
            "total_market_cap_usd": data.get("total_market_cap", {}).get("usd", 0),
            "total_volume_usd": data.get("total_volume", {}).get("usd", 0),
            "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd", 0),
            "active_cryptocurrencies": data.get("active_cryptocurrencies", 0),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
            "eth_dominance": data.get("market_cap_percentage", {}).get("eth", 0),
        }

        cache.set(cache_key, result, Config.CACHE_GLOBAL_TTL)
        return _ok(data=result)

    except http_requests.Timeout:
        logger.error("[api_market] CoinGecko global timeout")
        return _err("CoinGecko API request timed out", 504, "gateway_timeout")
    except http_requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 502
        logger.error(f"[api_market] CoinGecko global HTTP error: {status_code}")
        return _err(f"CoinGecko API returned {status_code}", 502, "upstream_error")
    except Exception as e:
        logger.error(f"[api_market] get_global error: {e}")
        return _err("Failed to fetch global market data", 500, "internal_error")


# ============================================================
# GET /api/trending — Trending coins
# ============================================================
@api_market.route("/trending", methods=["GET"])
@rate_limit
def get_trending():
    """Get trending coins from CoinGecko (cached)."""
    try:
        cache_key = "market:trending"
        cached = cache.get(cache_key)
        if cached:
            return _ok(data=cached)

        coingecko_wait()

        session = _get_cg_session()
        resp = session.get(
            f"{Config.COINGECKO_BASE}/search/trending",
            timeout=15,
        )

        if resp.status_code == 429:
            logger.warning("[api_market] CoinGecko trending rate limited")
            return _err("CoinGecko API rate limited. Please try again later.", 429, "rate_limited")

        resp.raise_for_status()
        data = resp.json()

        coins = data.get("coins", [])
        trending = []
        for entry in coins:
            coin = entry.get("item", {})
            trending.append({
                "id": coin.get("id", ""),
                "symbol": coin.get("symbol", "").upper(),
                "name": coin.get("name", ""),
                "market_cap_rank": coin.get("market_cap_rank"),
                "score": coin.get("score", 0),
                "image": coin.get("small", "") or coin.get("thumb", ""),
            })

        result = {"coins": trending, "count": len(trending)}
        cache.set(cache_key, result, Config.CACHE_TRENDING_TTL)
        return _ok(data=result)

    except http_requests.Timeout:
        logger.error("[api_market] CoinGecko trending timeout")
        return _err("CoinGecko API request timed out", 504, "gateway_timeout")
    except http_requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 502
        logger.error(f"[api_market] CoinGecko trending HTTP error: {status_code}")
        return _err(f"CoinGecko API returned {status_code}", 502, "upstream_error")
    except Exception as e:
        logger.error(f"[api_market] get_trending error: {e}")
        return _err("Failed to fetch trending coins", 500, "internal_error")
