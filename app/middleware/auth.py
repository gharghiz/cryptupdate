# -*- coding: utf-8 -*-
"""
CryptositNews - Authentication Middleware
Decorators for admin key verification and JWT-based user authentication.
"""

from functools import wraps

from flask import request, jsonify

from app.utils.helpers import setup_logger
from app.utils.security import verify_admin_key, get_current_user

logger = setup_logger("auth")


def admin_required(f):
    """Decorator: require a valid admin key.

    Checks the X-Admin-Key header first, then falls back to the ?key=
    query parameter. Uses constant-time hash comparison via verify_admin_key.

    Returns 401 on missing/invalid key.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_key = request.headers.get("X-Admin-Key", "") or request.args.get("key", "")

        if not verify_admin_key(admin_key):
            req_id = getattr(request, "request_id", "unknown")
            logger.warning(f"[auth] Invalid admin key attempt (request_id: {req_id})")
            return jsonify({
                "error": "Unauthorized",
                "code": 401,
                "message": "Invalid or missing admin key",
            }), 401

        request.admin_authenticated = True
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    """Decorator: require a valid JWT access token.

    Extracts the Bearer token from the Authorization header and verifies
    it via get_current_user(). On success, the user dict is attached to
    request.current_user.

    Returns 401 on missing/expired/invalid token.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()

        if not user:
            req_id = getattr(request, "request_id", "unknown")
            logger.debug(f"[auth] Unauthenticated request (request_id: {req_id})")
            return jsonify({
                "error": "Unauthorized",
                "code": 401,
                "message": "Valid access token required",
            }), 401

        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def optional_login(f):
    """Decorator: try to authenticate via JWT but do not require it.

    If a valid token is present, the user dict is attached to
    request.current_user. Otherwise request.current_user is set to None
    and the view proceeds normally.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        request.current_user = user
        return f(*args, **kwargs)
    return decorated
