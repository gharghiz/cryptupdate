"""
utils.py - أدوات مساعدة
"""

import html
import logging
import re
from datetime import datetime, timezone, timedelta


def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s UTC | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
    return logging.getLogger("cryptobot")

logger = setup_logger()


def now_utc():
    return datetime.now(timezone.utc)


def safe_html(text: str) -> str:
    return html.escape(str(text))


def clean_url(url: str) -> str:
    if not url:
        return ""
    url = re.sub(r'\?utm_[^&]*(&[^&]*)*', '', url)
    url = re.sub(r'&utm_[^&]*', '', url)
    return url.rstrip('?&')


def clean_title(title: str) -> str:
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'^[\w\s]+:\s*', '', title) if ':' in title[:30] else title
    return title[:197] + "..." if len(title) > 200 else title


def clean_summary(text: str) -> str:
    """تنظيف ملخص RSS من HTML tags"""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:500]


def time_ago(iso_string: str) -> str:
    """تحويل التاريخ إلى صيغة نسبيّة: 5m ago, 2h ago..."""
    try:
        if not iso_string:
            return ""
        dt = datetime.fromisoformat(str(iso_string).replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        return dt.strftime("%b %d")
    except Exception:
        return ""


def format_number(n) -> str:
    """تنسيق الأرقام الكبيرة"""
    if n is None:
        return "—"
    n = float(n)
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"
