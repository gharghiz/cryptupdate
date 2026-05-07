# -*- coding: utf-8 -*-
"""
CryptositNews - Telegram Bot Service
Send news and alerts to a Telegram channel with retry logic and rate-limit handling.
"""

import time

import requests

from app.config import Config
from app.utils.helpers import setup_logger

logger = setup_logger("telegram")

# Maximum number of retry attempts
_MAX_RETRIES = 3


def send_message(text, parse_mode="HTML", disable_web_page_preview=True):
    """Send a message to the configured Telegram channel.

    Implements exponential backoff retry and handles Telegram 429 rate-limit
    responses by waiting for the specified retry_after duration.

    Args:
        text: Message body (supports HTML formatting).
        parse_mode: Telegram parse mode (default: "HTML").
        disable_web_page_preview: Whether to hide link previews.

    Returns:
        tuple[bool, str|None]: (success, error_message).
    """
    if not Config.BOT_TOKEN or not Config.CHANNEL_ID:
        logger.warning("[telegram] BOT_TOKEN or CHANNEL_ID not configured")
        return False, "Not configured"

    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": Config.CHANNEL_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.post(url, json=payload, timeout=30)
            data = response.json()

            if data.get("ok"):
                logger.info("[telegram] Message sent successfully")
                return True, None

            error = data.get("description", "Unknown error")

            # Handle rate-limit (HTTP 429)
            if response.status_code == 429:
                retry_after = int(data.get("parameters", {}).get("retry_after", 5))
                logger.warning(f"[telegram] Rate limited, retrying in {retry_after}s...")
                time.sleep(retry_after)
                continue

            logger.error(f"[telegram] API error: {error}")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, error

        except requests.exceptions.Timeout:
            logger.warning(f"[telegram] Timeout, attempt {attempt + 1}/{_MAX_RETRIES}")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, "Timeout"

        except requests.exceptions.ConnectionError:
            logger.warning(f"[telegram] Connection error, attempt {attempt + 1}/{_MAX_RETRIES}")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, "Connection error"

        except Exception as e:
            logger.error(f"[telegram] Send error: {e}")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, str(e)

    return False, "Max retries exceeded"


def send_important_news(title, summary="", url="", sentiment="", ai_summary=""):
    """Format and send an important news item to the Telegram channel.

    Args:
        title: News headline.
        summary: Raw news summary.
        url: Link to the original article.
        sentiment: AI-assessed sentiment string.
        ai_summary: AI-generated summary.

    Returns:
        tuple[bool, str|None]: (success, error_message).
    """
    from app.services.processor import format_message

    message = format_message(title, summary, url, sentiment, ai_summary)
    return send_message(message)


def send_alert_message(text):
    """Send a price alert or system notification to the Telegram channel.

    Prepends an alert emoji and bold header.

    Args:
        text: Alert body text.

    Returns:
        tuple[bool, str|None]: (success, error_message).
    """
    return send_message(f"\u26a0\ufe0f <b>Alert</b>\n\n{text}")
