# -*- coding: utf-8 -*-
"""
CryptositNews v3 - Security Utilities
JWT tokens, password hashing, admin key verification.
STRICT: No fallback for JWT_SECRET in production.
"""

import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import jwt

from app.utils.helpers import setup_logger

logger = setup_logger("security")


def get_jwt_secret():
    """Get JWT secret - STRICT: uses only JWT_SECRET env var.

    In production, JWT_SECRET must be explicitly set.
    In development, generates a warning if not set.
    """
    from app.config import Config
    if Config.JWT_SECRET and len(Config.JWT_SECRET) >= 32:
        return Config.JWT_SECRET
    # Development fallback only
    if not os.environ.get("RAILWAY_ENVIRONMENT") and os.environ.get("FLASK_ENV") != "production":
        logger.warning("[SECURITY] JWT_SECRET not set - using development-only fallback")
        return Config.SECRET_KEY or "dev-only-secret-change-me-in-production!!"
    raise RuntimeError("JWT_SECRET environment variable is required in production (min 32 chars)")


def generate_access_token(user_id, email, role="free"):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "email": email, "role": role,
        "type": "access", "iat": now,
        "exp": now + timedelta(seconds=3600), "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def generate_refresh_token(user_id):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "type": "refresh",
        "iat": now, "exp": now + timedelta(days=30), "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def verify_token(token):
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
    payload = verify_token(token)
    if payload and payload.get("type") == "access":
        return payload
    return None


def verify_refresh_token(token):
    payload = verify_token(token)
    if payload and payload.get("type") == "refresh":
        return payload
    return None


def verify_admin_key(provided_key):
    from app.config import Config
    if not provided_key:
        return False
    if Config.ADMIN_KEY_HASH:
        provided_hash = hashlib.sha256(provided_key.encode()).hexdigest()
        return hmac.compare_digest(provided_hash, Config.ADMIN_KEY_HASH)
    if Config.ADMIN_KEY:
        return hmac.compare_digest(provided_key, Config.ADMIN_KEY)
    return False


def hash_admin_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def hash_password(password):
    import bcrypt
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password, hashed_password):
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_token_from_header():
    from flask import request
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def get_current_user():
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
