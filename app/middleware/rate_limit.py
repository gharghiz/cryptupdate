# -*- coding: utf-8 -*-
"""
CryptositNews v3 - Redis-based Rate Limiting
Uses Redis for multi-instance safety, falls back to in-memory for dev.
"""

import time
import threading
from functools import wraps

from flask import request, jsonify

from app.config import Config
from app.utils.helpers import setup_logger, generate_request_id

logger = setup_logger("rate_limit")

# In-memory fallback: {ip: [timestamp, ...]}
_memory_store = {}
_memory_lock = threading.Lock()


def _get_redis_rate_limit():
    """Get Redis client for rate limiting. Returns None if unavailable."""
    try:
        from app.models.db import _get_redis
        return _get_redis()
    except Exception:
        return None


def _get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(max_requests=None, window=None):
    """Redis-based sliding-window rate limiter with in-memory fallback.

    In production with Redis: uses Redis INCR + EXPIRE for atomic counting.
    Falls back to in-memory sliding window for development.
    """
    max_requests = max_requests or Config.RATE_LIMIT_REQUESTS
    window = window or Config.RATE_LIMIT_WINDOW
    ip = _get_client_ip()

    redis_client = _get_redis_rate_limit()

    if redis_client:
        return _redis_check(redis_client, ip, max_requests, window)
    else:
        return _memory_check(ip, max_requests, window)


def _redis_check(redis_client, ip, max_requests, window):
    """Redis-based rate limiting - atomic, multi-instance safe."""
    key = f"cn:rl:{ip}"
    now = time.time()

    try:
        pipe = redis_client.pipeline()
        # Remove old entries
        pipe.zremrangebyscore(key, 0, now - window)
        # Count current entries
        pipe.zcard(key)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Set expiry
        pipe.expire(key, window + 1)
        results = pipe.execute()

        current_count = results[1]

        if current_count >= max_requests:
            # Find oldest entry to calculate reset time
            oldest = redis_client.zrange(key, 0, 0, withscores=True)
            if oldest:
                reset_time = int(oldest[0][1] + window - now) + 1
            else:
                reset_time = window

            # Remove the entry we just added (over limit)
            redis_client.zrem(key, str(now))

            return False, {
                "limit": max_requests, "remaining": 0,
                "reset": reset_time, "window": window,
            }

        remaining = max_requests - current_count
        return True, {
            "limit": max_requests,
            "remaining": max(0, remaining),
            "reset": window, "window": window,
        }
    except Exception as e:
        logger.warning(f"Redis rate limit error, falling back to memory: {e}")
        return _memory_check(ip, max_requests, window)


def _memory_check(ip, max_requests, window):
    """In-memory sliding-window rate limiting (dev fallback)."""
    now = time.time()
    cutoff = now - window

    with _memory_lock:
        if ip not in _memory_store:
            _memory_store[ip] = []

        _memory_store[ip] = [t for t in _memory_store[ip] if t > cutoff]

        if len(_memory_store[ip]) >= max_requests:
            reset_time = int(_memory_store[ip][0] + window - now) + 1
            return False, {
                "limit": max_requests, "remaining": 0,
                "reset": reset_time, "window": window,
            }

        _memory_store[ip].append(now)
        remaining = max_requests - len(_memory_store[ip])

    return True, {
        "limit": max_requests, "remaining": max(0, remaining),
        "reset": window, "window": window,
    }


def rate_limit(f):
    """Decorator: apply standard API rate limiting."""
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, info = check_rate_limit()
        response = f(*args, **kwargs)

        if isinstance(response, tuple):
            resp_obj, status_code = response[0], response[1]
        else:
            resp_obj, status_code = response, 200

        if hasattr(resp_obj, "headers"):
            resp_obj.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp_obj.headers["X-RateLimit-Remaining"] = str(info["remaining"])
            resp_obj.headers["X-RateLimit-Window"] = str(info["window"])

        if not allowed:
            req_id = getattr(request, "request_id", "unknown")
            logger.debug(f"[rate_limit] Blocked {request.remote_addr} (request_id: {req_id})")
            resp = jsonify({
                "error": "Too Many Requests", "code": 429,
                "message": f"Rate limit exceeded. Try again in {info['reset']}s.",
                "request_id": req_id,
            })
            resp.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["Retry-After"] = str(info["reset"])
            resp.headers["X-RateLimit-Window"] = str(info["window"])
            return resp, 429

        return response
    return decorated


def admin_rate_limit(f):
    """Decorator: stricter rate limiting for admin endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, info = check_rate_limit(
            max_requests=Config.ADMIN_RATE_LIMIT, window=60,
        )
        if not allowed:
            req_id = getattr(request, "request_id", "unknown")
            logger.warning(f"[rate_limit] Admin rate limit hit for {request.remote_addr}")
            resp = jsonify({
                "error": "Too Many Requests", "code": 429,
                "message": f"Admin rate limit exceeded. Try again in {info['reset']}s.",
                "request_id": req_id,
            })
            resp.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["Retry-After"] = str(info["reset"])
            return resp, 429

        response = f(*args, **kwargs)
        if isinstance(response, tuple):
            resp_obj = response[0]
        else:
            resp_obj = response
        if hasattr(resp_obj, "headers"):
            resp_obj.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp_obj.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        return response
    return decorated
