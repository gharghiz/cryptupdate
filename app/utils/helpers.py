# -*- coding: utf-8 -*-
"""
CryptositNews - Utility Functions
"""

import os
import re
import uuid
import hashlib
import logging
import psutil
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, unquote


def setup_logger(name, level=None):
    """Create and configure a logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        log_level = level or os.environ.get("LOG_LEVEL", "INFO")
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
    return logger


def now_utc():
    return datetime.now(timezone.utc)


def hours_ago(n):
    return datetime.now(timezone.utc) - timedelta(hours=n)


def safe_html(text):
    if not text:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<iframe[^>]*>.*?</iframe>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<object[^>]*>.*?</object>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<embed[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<form[^>]*>.*?</form>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'\son\w+\s*=\s*["\'][^"\']*["\']', '', text, flags=re.IGNORECASE)
    return text


def clean_url(url):
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r'[\s\x00-\x1f\x7f]', '', url)
    if url.startswith('//'):
        url = 'https:' + url
    return url


def clean_title(title):
    if not title:
        return ""
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)
    title = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', title)
    for suffix in [' - CryptoSlate', ' - CoinDesk', ' - CoinTelegraph',
                    ' | CoinMarketCap', ' - Decrypt', ' - The Block',
                    ' | Bitcoinist', ' - BeInCrypto', ' - AMBCrypto']:
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
    return title.strip()


def hash_text(text):
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def parse_rfc2822_date(date_str):
    if not date_str:
        return now_utc()
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
        except Exception:
            return now_utc()


def truncate(text, max_length=200):
    if not text or len(text) <= max_length:
        return text or ""
    text = text[:max_length]
    last_space = text.rfind(' ')
    if last_space > max_length * 0.6:
        text = text[:last_space]
    return text.rstrip() + "..."


def format_number(n):
    if n is None:
        return "0"
    try:
        n = float(n)
        if n >= 1e12:
            return f"{n / 1e12:.2f}T"
        elif n >= 1e9:
            return f"{n / 1e9:.2f}B"
        elif n >= 1e6:
            return f"{n / 1e6:.2f}M"
        elif n >= 1e3:
            return f"{n / 1e3:.1f}K"
        return f"{n:.2f}"
    except (ValueError, TypeError):
        return str(n)


def is_valid_title(title):
    if not title:
        return False
    title = title.strip()
    if len(title) < 10:
        return False
    if title.isspace():
        return False
    junk = ["untitled", "no title", "page not found", "404", "access denied",
            "loading...", "subscribe", "sign in", "log in", "register",
            "advertisement", "sponsored", "promo"]
    if title.lower() in junk:
        return False
    return True


def generate_request_id():
    return str(uuid.uuid4())[:12]


def get_memory_usage():
    try:
        process = psutil.Process(os.getpid())
        return round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        return 0


def sanitize_search(query):
    if not query:
        return ""
    query = query.strip()[:200]
    query = re.sub(r'[<>{}()\[\]\\]', '', query)
    query = re.sub(r'\s+', ' ', query)
    return query


def time_ago(dt):
    if not dt:
        return ""
    try:
        if isinstance(dt, str):
            dt = parse_rfc2822_date(dt)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception:
        return ""


def extract_domain(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or ""
        return domain.replace("www.", "")
    except Exception:
        return ""


def is_valid_email(email):
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def format_price(price, currency="$"):
    try:
        price = float(price)
        if price >= 1:
            return f"{currency}{price:,.2f}"
        elif price >= 0.01:
            return f"{currency}{price:.4f}"
        else:
            return f"{currency}{price:.8f}"
    except (ValueError, TypeError):
        return f"{currency}0.00"


def format_percent(pct):
    try:
        pct = float(pct)
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.2f}%"
    except (ValueError, TypeError):
        return "0.00%"
