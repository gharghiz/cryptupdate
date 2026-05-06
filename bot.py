# -*- coding: utf-8 -*-
"""
CryptositNews - Telegram Bot
Send news to Telegram channel with retry logic.
"""

import time

import requests

import config
from utils import setup_logger

logger = setup_logger("bot")


def send_message(text, parse_mode="HTML", disable_web_page_preview=True):
    """Send a message to the configured Telegram channel."""
    if not config.BOT_TOKEN or not config.CHANNEL_ID:
        logger.warning("BOT_TOKEN or CHANNEL_ID not configured")
        return False, "Not configured"

    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.CHANNEL_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    # Exponential backoff retry
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=30)
            data = response.json()

            if data.get("ok"):
                logger.info("Message sent successfully to Telegram")
                return True, None

            error = data.get("description", "Unknown error")
            if "429" in str(response.status_code):
                retry_after = int(data.get("parameters", {}).get("retry_after", 5))
                logger.warning(f"Rate limited, retrying in {retry_after}s...")
                time.sleep(retry_after)
                continue

            logger.error(f"Telegram API error: {error}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, error

        except requests.exceptions.Timeout:
            logger.warning(f"Telegram timeout, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, "Timeout"

        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return False, str(e)

    return False, "Max retries exceeded"


def send_important_news(title, summary="", url="", sentiment="", ai_summary=""):
    """Format and send important news to Telegram."""
    from processor import format_message

    message = format_message(title, summary, url, sentiment, ai_summary)
    return send_message(message)


def send_alert_message(text):
    """Send a price alert or system notification."""
    return send_message(f"⚠️ <b>Alert</b>\n\n{text}")
