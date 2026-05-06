"""
utils.py - أدوات مساعدة
"""

import html
import logging
import re
from datetime import datetime, timezone


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
