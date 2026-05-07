# -*- coding: utf-8 -*-
"""
CryptositNews - Rate Limiting Middleware
In-memory sliding-window rate limiter for API endpoints.
"""

import time
import threading
from functools import wraps

from flask import request, jsonify

from app.config import Config
from app.utils.helpers import setup_logger, generate_request_id

logger = setup_logger("rate_limit")

# In-memory rate-limit store: {ip: [timestamp, ...]}
_rate_store = {}
_rate_lock = threading.Lock()


def _get_client_ip():
    """Extract the client IP, respecting X-Forwarded-For for proxied requests."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _prune_old_entries(ip, timestamps, window):
    """Remove timestamps outside the current window."""
    cutoff = time.time() - window
    return [t for t in timestamps if t > cutoff]


def check_rate_limit(max_requests=None, window=None):
    """Check if the current request is within rate limits.

    Uses a sliding-window algorithm with in-memory storage.

    Args:
        max_requests: Max requests allowed in the window (default from Config).
        window: Time window in seconds (default from Config).

    Returns:
        tuple[bool, dict]: (allowed, info_dict) where info_dict contains
            remaining requests, reset time, and limit.
    """
    max_requests = max_requests or Config.RATE_LIMIT_REQUESTS
    window = window or Config.RATE_LIMIT_WINDOW

    ip = _get_client_ip()
    now = time.time()
    cutoff = now - window

    with _rate_lock:
        if ip not in _rate_store:
            _rate_store[ip] = []

        # Prune old entries
        _rate_store[ip] = _prune_old_entries(ip, _rate_store[ip], window)

        timestamps = _rate_store[ip]

        if len(timestamps) >= max_requests:
            reset_time = int(timestamps[0] + window - now) + 1
            return False, {
                "limit": max_requests,
                "remaining": 0,
                "reset": reset_time,
                "window": window,
            }

        # Record this request
        timestamps.append(now)
        _rate_store[ip] = timestamps

    remaining = max_requests - len(_rate_store.get(ip, []))
    return True, {
        "limit": max_requests,
        "remaining": max(0, remaining),
        "reset": window,
        "window": window,
    }


def rate_limit(f):
    """Decorator: apply standard API rate limiting.

    Uses Config.RATE_LIMIT_REQUESTS and Config.RATE_LIMIT_WINDOW.
    Adds standard rate-limit headers to the response.

    Returns 429 with Retry-After header when limit exceeded.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, info = check_rate_limit()

        # Add rate-limit headers to all responses
        response = f(*args, **kwargs)

        # Support both direct response and tuple (response, status_code)
        if isinstance(response, tuple):
            resp_obj, status_code = response[0], response[1]
        else:
            resp_obj, status_code = response, 200

        # Add headers if response supports them
        if hasattr(resp_obj, "headers"):
            resp_obj.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp_obj.headers["X-RateLimit-Remaining"] = str(info["remaining"])
            resp_obj.headers["X-RateLimit-Window"] = str(info["window"])

        if not allowed:
            req_id = getattr(request, "request_id", "unknown")
            logger.debug(f"[rate_limit] Blocked {request.remote_addr} (request_id: {req_id})")

            resp = jsonify({
                "error": "Too Many Requests",
                "code": 429,
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
    """Decorator: apply stricter rate limiting for admin endpoints.

    Uses Config.ADMIN_RATE_LIMIT requests per 60-second window.
    Adds standard rate-limit headers to the response.

    Returns 429 with Retry-After header when limit exceeded.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, info = check_rate_limit(
            max_requests=Config.ADMIN_RATE_LIMIT,
            window=60,
        )

        if not allowed:
            req_id = getattr(request, "request_id", "unknown")
            logger.warning(
                f"[rate_limit] Admin rate limit hit for {request.remote_addr} "
                f"(request_id: {req_id})"
            )

            resp = jsonify({
                "error": "Too Many Requests",
                "code": 429,
                "message": f"Admin rate limit exceeded. Try again in {info['reset']}s.",
                "request_id": req_id,
            })
            resp.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["Retry-After"] = str(info["reset"])
            return resp, 429

        response = f(*args, **kwargs)

        # Add rate-limit info headers
        if isinstance(response, tuple):
            resp_obj = response[0]
        else:
            resp_obj = response

        if hasattr(resp_obj, "headers"):
            resp_obj.headers["X-RateLimit-Limit"] = str(info["limit"])
            resp_obj.headers["X-RateLimit-Remaining"] = str(info["remaining"])

        return response
    return decorated
