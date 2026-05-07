# -*- coding: utf-8 -*-
"""
CryptositNews - User API Routes
Authentication (register, login, refresh, logout), bookmarks, and preferences.
"""

from flask import (
    Blueprint, request, jsonify, make_response,
)

from app.config import Config
from app.utils.helpers import setup_logger, is_valid_email
from app.utils.security import (
    generate_access_token, generate_refresh_token,
    verify_refresh_token, hash_password, verify_password, hash_text,
    get_token_from_header,
)
from app.models.db import (
    create_user, get_user_by_email, get_user_with_password,
    update_last_login, save_refresh_token, verify_refresh_token_db,
    delete_refresh_token, revoke_all_user_tokens,
    add_bookmark, remove_bookmark, get_user_bookmarks, is_bookmarked,
    get_user_by_id,
)
from app.middleware.auth import login_required
from app.middleware.rate_limit import rate_limit

logger = setup_logger("api_user")

api_user = Blueprint("api_user", __name__, url_prefix="/api")

# Max age constants
MAX_PASSWORD_LEN = 128
MIN_PASSWORD_LEN = 8


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


def _safe_user(user_dict):
    """Return a user dict safe for API responses (strips sensitive fields)."""
    if not user_dict:
        return None
    return {
        "id": user_dict.get("id"),
        "email": user_dict.get("email"),
        "display_name": user_dict.get("display_name", ""),
        "role": user_dict.get("role", "free"),
        "created_at": user_dict.get("created_at"),
        "last_login": user_dict.get("last_login"),
    }


# ============================================================
# POST /api/auth/register — create user account
# ============================================================
@api_user.route("/auth/register", methods=["POST"])
@rate_limit
def register():
    """Register a new user account."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        display_name = (body.get("display_name") or "").strip()

        # Validate email
        if not is_valid_email(email):
            return _err("A valid email address is required", 400, "invalid_email")

        # Validate password
        if len(password) < MIN_PASSWORD_LEN:
            return _err(f"Password must be at least {MIN_PASSWORD_LEN} characters", 400, "weak_password")
        if len(password) > MAX_PASSWORD_LEN:
            return _err(f"Password must not exceed {MAX_PASSWORD_LEN} characters", 400, "weak_password")

        # Hash password
        password_hash = hash_password(password)

        # Create user
        user = create_user(email, password_hash, display_name)
        if not user:
            return _err("An account with this email already exists", 409, "email_exists")

        # Generate tokens
        access_token = generate_access_token(user["id"], user["email"], user["role"])
        refresh_token = generate_refresh_token(user["id"])

        # Save refresh token to DB
        token_hash = hash_text(refresh_token)
        device = request.headers.get("User-Agent", "")[:100]
        ip = request.remote_addr or ""
        save_refresh_token(user["id"], token_hash, device=device, ip=ip)

        logger.info(f"[api_user] User registered: {email} (ID: {user['id']})")

        return _ok(data={
            "user": _safe_user(user),
            "access_token": access_token,
            "refresh_token": refresh_token,
        }, message="Account created successfully", status=201)

    except Exception as e:
        logger.error(f"[api_user] register error: {e}")
        return _err("Registration failed. Please try again.", 500, "internal_error")


# ============================================================
# POST /api/auth/login — login user
# ============================================================
@api_user.route("/auth/login", methods=["POST"])
@rate_limit
def login():
    """Authenticate a user and return tokens."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""

        if not email or not password:
            return _err("Email and password are required", 400, "missing_credentials")

        # Look up user with password hash
        user = get_user_with_password(email)
        if not user:
            return _err("Invalid email or password", 401, "invalid_credentials")

        # Verify password
        if not verify_password(password, user.get("password_hash", "")):
            return _err("Invalid email or password", 401, "invalid_credentials")

        # Check if user is active
        if not user.get("is_active", True):
            return _err("Account is deactivated", 403, "account_deactivated")

        # Update last login
        update_last_login(user["id"])

        # Generate tokens
        access_token = generate_access_token(user["id"], user["email"], user["role"])
        refresh_token = generate_refresh_token(user["id"])

        # Save refresh token to DB
        token_hash = hash_text(refresh_token)
        device = request.headers.get("User-Agent", "")[:100]
        ip = request.remote_addr or ""
        save_refresh_token(user["id"], token_hash, device=device, ip=ip)

        logger.info(f"[api_user] User logged in: {email} (ID: {user['id']})")

        return _ok(data={
            "user": _safe_user(user),
            "access_token": access_token,
            "refresh_token": refresh_token,
        })

    except Exception as e:
        logger.error(f"[api_user] login error: {e}")
        return _err("Login failed. Please try again.", 500, "internal_error")


# ============================================================
# POST /api/auth/refresh — refresh access token
# ============================================================
@api_user.route("/auth/refresh", methods=["POST"])
@rate_limit
def refresh_token():
    """Refresh an access token using a valid refresh token."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        refresh_tok = body.get("refresh_token", "")
        if not refresh_tok:
            return _err("Refresh token is required", 400, "missing_token")

        # Hash the token to look it up in the DB
        token_hash = hash_text(refresh_tok)
        record = verify_refresh_token_db(token_hash)

        if not record:
            return _err("Invalid or expired refresh token", 401, "invalid_refresh_token")

        # Verify the JWT itself for extra safety
        payload = verify_refresh_token(refresh_tok)
        if not payload:
            # Token signature invalid — remove from DB
            delete_refresh_token(token_hash)
            return _err("Invalid refresh token", 401, "invalid_refresh_token")

        # Ensure user_id matches
        user_id = int(payload["sub"])
        if user_id != record.get("user_id"):
            delete_refresh_token(token_hash)
            return _err("Token mismatch", 401, "invalid_refresh_token")

        # Get user info
        user = get_user_by_id(user_id)
        if not user or not user.get("is_active", True):
            delete_refresh_token(token_hash)
            return _err("User not found or deactivated", 401, "user_invalid")

        # Generate new access token
        new_access = generate_access_token(user["id"], user["email"], user["role"])

        logger.info(f"[api_user] Token refreshed for user {user['id']}")

        return _ok(data={
            "access_token": new_access,
            "user": _safe_user(user),
        })

    except Exception as e:
        logger.error(f"[api_user] refresh_token error: {e}")
        return _err("Token refresh failed", 500, "internal_error")


# ============================================================
# GET /api/auth/me — get current user profile
# ============================================================
@api_user.route("/auth/me", methods=["GET"])
@login_required
def me():
    """Get the current authenticated user's profile."""
    try:
        user = request.current_user
        user_data = get_user_by_id(user["id"])
        if not user_data:
            return _err("User not found", 404, "not_found")

        return _ok(data={"user": _safe_user(user_data)})
    except Exception as e:
        logger.error(f"[api_user] me error: {e}")
        return _err("Failed to fetch user profile", 500, "internal_error")


# ============================================================
# POST /api/auth/logout — logout and revoke tokens
# ============================================================
@api_user.route("/auth/logout", methods=["POST"])
@login_required
def logout():
    """Logout the current user: delete the refresh token and revoke all tokens."""
    try:
        user = request.current_user

        # Get refresh token from body if provided, otherwise revoke all
        body = request.get_json(silent=True) or {}
        refresh_tok = body.get("refresh_token", "")

        if refresh_tok:
            token_hash = hash_text(refresh_tok)
            delete_refresh_token(token_hash)

        # Revoke ALL refresh tokens for this user (full logout)
        revoke_all_user_tokens(user["id"])

        logger.info(f"[api_user] User logged out: {user['id']}")

        return _ok(message="Logged out successfully")
    except Exception as e:
        logger.error(f"[api_user] logout error: {e}")
        return _err("Logout failed", 500, "internal_error")


# ============================================================
# GET /api/bookmarks — get user bookmarks
# ============================================================
@api_user.route("/bookmarks", methods=["GET"])
@login_required
def list_bookmarks():
    """Get the current user's bookmarked articles."""
    try:
        user = request.current_user
        limit = request.args.get("limit", 50, type=int)
        limit = min(max(limit, 1), 200)

        bookmarks = get_user_bookmarks(user["id"], limit=limit)
        return _ok(data={"bookmarks": bookmarks, "count": len(bookmarks)})
    except Exception as e:
        logger.error(f"[api_user] list_bookmarks error: {e}")
        return _err("Failed to fetch bookmarks", 500, "internal_error")


# ============================================================
# POST /api/bookmarks — add bookmark
# ============================================================
@api_user.route("/bookmarks", methods=["POST"])
@login_required
def bookmark_add():
    """Add a bookmark for a news article."""
    try:
        user = request.current_user
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        news_id = body.get("news_id")
        if not news_id:
            return _err("news_id is required", 400, "missing_field")

        try:
            news_id = int(news_id)
        except (ValueError, TypeError):
            return _err("news_id must be an integer", 400, "invalid_field")

        success = add_bookmark(user["id"], news_id)
        if success:
            return _ok(message="Bookmark added", status=201)
        else:
            return _err("Failed to add bookmark", 500, "internal_error")

    except Exception as e:
        logger.error(f"[api_user] bookmark_add error: {e}")
        return _err("Failed to add bookmark", 500, "internal_error")


# ============================================================
# DELETE /api/bookmarks/<int:news_id> — remove bookmark
# ============================================================
@api_user.route("/bookmarks/<int:news_id>", methods=["DELETE"])
@login_required
def bookmark_remove(news_id):
    """Remove a bookmark for a news article."""
    try:
        user = request.current_user
        success = remove_bookmark(user["id"], news_id)
        if success:
            return _ok(message="Bookmark removed")
        else:
            return _err("Bookmark not found or already removed", 404, "not_found")

    except Exception as e:
        logger.error(f"[api_user] bookmark_remove error: {e}")
        return _err("Failed to remove bookmark", 500, "internal_error")


# ============================================================
# GET /api/bookmarks/check/<int:news_id> — check if bookmarked
# ============================================================
@api_user.route("/bookmarks/check/<int:news_id>", methods=["GET"])
@login_required
def bookmark_check(news_id):
    """Check if the current user has bookmarked a specific article."""
    try:
        user = request.current_user
        bookmarked = is_bookmarked(user["id"], news_id)
        return _ok(data={"bookmarked": bookmarked, "news_id": news_id})
    except Exception as e:
        logger.error(f"[api_user] bookmark_check error: {e}")
        return _err("Failed to check bookmark", 500, "internal_error")


# ============================================================
# POST /api/lang — set language cookie
# ============================================================
@api_user.route("/lang", methods=["POST"])
@rate_limit
def set_language():
    """Set the user's language preference cookie."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        lang = (body.get("lang") or "").strip().lower()
        supported = Config.SUPPORTED_LANGUAGES

        if lang not in supported:
            return _err(f"Unsupported language. Supported: {', '.join(supported)}", 400, "invalid_lang")

        resp = _ok(message=f"Language set to {lang}")
        resp_obj = resp[0]
        resp_obj.set_cookie("lang", lang, max_age=365 * 24 * 3600, httponly=False, samesite="Lax")
        return resp

    except Exception as e:
        logger.error(f"[api_user] set_language error: {e}")
        return _err("Failed to set language", 500, "internal_error")


# ============================================================
# POST /api/theme — set theme cookie
# ============================================================
@api_user.route("/theme", methods=["POST"])
@rate_limit
def set_theme():
    """Set the user's theme preference cookie."""
    try:
        body = request.get_json(silent=True)
        if not body:
            return _err("Request body must be JSON", 400, "invalid_body")

        theme = (body.get("theme") or "").strip().lower()
        valid_themes = ("dark", "light")

        if theme not in valid_themes:
            return _err(f"Invalid theme. Must be one of: {', '.join(valid_themes)}", 400, "invalid_theme")

        resp = _ok(message=f"Theme set to {theme}")
        resp_obj = resp[0]
        resp_obj.set_cookie("theme", theme, max_age=365 * 24 * 3600, httponly=False, samesite="Lax")
        return resp

    except Exception as e:
        logger.error(f"[api_user] set_theme error: {e}")
        return _err("Failed to set theme", 500, "internal_error")
