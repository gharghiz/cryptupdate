# -*- coding: utf-8 -*-
"""
CryptositNews - Security Utilities
JWT tokens, password hashing, admin key verification.
"""

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone, timedelta

import jwt

from app.utils.helpers import setup_logger

logger = setup_logger("security")


# ============================================================
# JWT
# ============================================================
def get_jwt_secret():
    """Get JWT secret - use JWT_SECRET env var or fall back to APP_SECRET."""
    from app.config import Config
    return Config.JWT_SECRET or Config.SECRET_KEY


def generate_access_token(user_id, email, role="free"):
    """Generate a short-lived JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def generate_refresh_token(user_id):
    """Generate a long-lived JWT refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=30),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def verify_token(token):
    """Verify a JWT token. Returns payload dict or None."""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        return None


def verify_access_token(token):
    """Verify an access token specifically."""
    payload = verify_token(token)
    if payload and payload.get("type") == "access":
        return payload
    return None


def verify_refresh_token(token):
    """Verify a refresh token specifically."""
    payload = verify_token(token)
    if payload and payload.get("type") == "refresh":
        return payload
    return None


# ============================================================
# ADMIN KEY (hashed comparison)
# ============================================================
def verify_admin_key(provided_key):
    """Verify admin key using constant-time comparison with hash."""
    from app.config import Config

    if not provided_key:
        return False

    # Method 1: Compare with stored hash (RECOMMENDED)
    if Config.ADMIN_KEY_HASH:
        provided_hash = hashlib.sha256(provided_key.encode()).hexdigest()
        return hmac.compare_digest(provided_hash, Config.ADMIN_KEY_HASH)

    # Method 2: Fallback to direct comparison (backward compatibility)
    if Config.ADMIN_KEY:
        return hmac.compare_digest(provided_key, Config.ADMIN_KEY)

    return False


def hash_admin_key(key):
    """Generate SHA-256 hash of an admin key. Use this to set ADMIN_KEY_HASH env var."""
    return hashlib.sha256(key.encode()).hexdigest()


# ============================================================
# PASSWORD HASHING (for user accounts)
# ============================================================
def hash_password(password):
    """Hash a password using bcrypt."""
    import bcrypt
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password, hashed_password):
    """Verify a password against its bcrypt hash."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


# ============================================================
# TOKEN EXTRACTION FROM REQUEST
# ============================================================
def get_token_from_header():
    """Extract JWT token from Authorization header."""
    from flask import request
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def get_current_user():
    """Get current authenticated user from JWT token in request."""
    token = get_token_from_header()
    if not token:
        return None

    payload = verify_access_token(token)
    if not payload:
        return None

    return {
        "id": int(payload["sub"]),
        "email": payload.get("email", ""),
        "role": payload.get("role", "free"),
    }
