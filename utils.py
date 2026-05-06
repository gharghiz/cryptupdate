# -*- coding: utf-8 -*-
"""
CryptositNews - Utilities
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
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def hours_ago(n):
    """Return datetime n hours ago from now."""
    return datetime.now(timezone.utc) - timedelta(hours=n)


def safe_html(text):
    """Remove dangerous HTML tags, keep basic formatting."""
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
    """Clean and normalize URL."""
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r'[\s\x00-\x1f\x7f]', '', url)
    if url.startswith('//'):
        url = 'https:' + url
    return url


def clean_title(title):
    """Clean article title, preserve important prefixes."""
    if not title:
        return ""
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)
    title = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', title)
    # Remove common suffixes
    for suffix in [' - CryptoSlate', ' - CoinDesk', ' - CoinTelegraph',
                    ' | CoinMarketCap', ' - Decrypt', ' - The Block',
                    ' | Bitcoinist', ' - BeInCrypto', ' - AMBCrypto']:
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
    return title.strip()


def hash_text(text):
    """Generate SHA-256 hash of text."""
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def parse_rfc2822_date(date_str):
    """Parse RFC 2822 date string to UTC datetime."""
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
    """Truncate text to max_length, preserving word boundaries."""
    if not text or len(text) <= max_length:
        return text or ""
    text = text[:max_length]
    last_space = text.rfind(' ')
    if last_space > max_length * 0.6:
        text = text[:last_space]
    return text.rstrip() + "..."


def format_number(n):
    """Format large numbers with K/M/B suffix."""
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
    """Check if title is valid (not empty, not too short)."""
    if not title:
        return False
    title = title.strip()
    return len(title) >= 15 and not title.isspace()


def generate_request_id():
    """Generate a unique request ID for tracing."""
    return str(uuid.uuid4())[:12]


def get_memory_usage():
    """Get current process memory usage in MB."""
    try:
        process = psutil.Process(os.getpid())
        return round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        return 0


def get_db_size(db_path):
    """Get database file size in MB."""
    if not db_path or not os.path.exists(db_path):
        return 0
    try:
        return round(os.path.getsize(db_path) / 1024 / 1024, 2)
    except Exception:
        return 0


def sanitize_search(query):
    """Sanitize search query - remove dangerous characters."""
    if not query:
        return ""
    query = query.strip()[:200]
    query = re.sub(r'[<>{}()\[\]\\]', '', query)
    query = re.sub(r'\s+', ' ', query)
    return query


def time_ago(dt):
    """Return human-readable time ago string."""
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
    """Extract domain name from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or ""
        domain = domain.replace("www.", "")
        return domain
    except Exception:
        return ""


def is_valid_email(email):
    """Basic email validation."""
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def format_price(price, currency="$"):
    """Format price with currency symbol."""
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
    """Format percentage with sign."""
    try:
        pct = float(pct)
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.2f}%"
    except (ValueError, TypeError):
        return "0.00%"
