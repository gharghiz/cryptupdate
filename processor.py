# -*- coding: utf-8 -*-
"""
CryptositNews - Processor
News filtering, prioritization, and formatting.
"""

import re
import difflib

import config
from utils import setup_logger, truncate

logger = setup_logger("processor")


def is_important(title, summary=""):
    """Check if news is important based on keywords."""
    if not title:
        return False

    text = f"{title} {summary}".lower()

    # Check for action/breaking keywords first
    for kw in config.ACTION_KEYWORDS:
        if kw.lower() in text:
            return True

    # Check for important keywords
    important_count = sum(1 for kw in config.IMPORTANT_KEYWORDS if kw.lower() in text)
    return important_count >= 2


def prioritize(title, summary=""):
    """Calculate news priority score (0-100+)."""
    if not title:
        return 0

    text = f"{title} {summary}".lower()
    score = 10  # Base score for any news

    # Breaking news bonus
    for kw in config.BREAKING_KEYWORDS:
        if kw.lower() in text:
            score += 100
            break

    # High impact keywords
    for kw in config.HIGH_IMPACT_KEYWORDS:
        if kw.lower() in text:
            score += 80
            break

    # Major coins mentioned
    major_coins = ["bitcoin", "ethereum", "btc", "eth", "solana", "sol", "bnb"]
    for coin in major_coins:
        if coin in text:
            score += 50
            break

    # Important keywords
    for kw in config.IMPORTANT_KEYWORDS:
        if kw.lower() in text:
            score += 20

    # Sentiment analysis hints
    for kw in config.POSITIVE_KEYWORDS:
        if kw.lower() in text:
            score += 10

    for kw in config.NEGATIVE_KEYWORDS:
        if kw.lower() in text:
            score += 15  # Negative news often more important

    return score


def is_duplicate(title, existing_titles, threshold=0.85):
    """Check if title is too similar to existing ones."""
    if not title or not existing_titles:
        return False

    title_lower = title.lower().strip()

    for existing in existing_titles:
        if not existing:
            continue
        existing_lower = existing.lower().strip()
        similarity = difflib.SequenceMatcher(None, title_lower, existing_lower).ratio()
        if similarity >= threshold:
            return True

    return False


def format_message(title, summary="", url="", sentiment="", ai_summary=""):
    """Format a Telegram message."""
    # Determine emoji based on sentiment
    emoji_map = {
        "positive": "🟢",
        "negative": "🔴",
        "neutral": "⚪",
        "bullish": "🚀",
        "bearish": "📉",
        "fear": "😰",
        "greed": "💰",
    }
    emoji = emoji_map.get(sentiment.lower(), "📰")

    # Build message
    msg = f"{emoji} <b>{title}</b>\n\n"

    if ai_summary:
        msg += f"💡 {truncate(ai_summary, 300)}\n\n"
    elif summary:
        msg += f"{truncate(summary, 200)}\n\n"

    if sentiment:
        sentiment_emoji = emoji_map.get(sentiment.lower(), "")
        msg += f"📊 Sentiment: {sentiment_emoji} {sentiment.title()}\n"

    if url:
        msg += f"\n🔗 <a href=\"{url}\">Read More</a>"

    # Telegram limit is 4096 chars
    if len(msg) > 4000:
        msg = msg[:3900] + "...\n\n🔗 <a href=\"" + url + "\">Read More</a>"

    return msg


def extract_coins(title, summary=""):
    """Extract mentioned cryptocurrency coins from text."""
    text = f"{title} {summary}".lower()
    mentioned = []
    for symbol, name in config.COIN_MAP.items():
        if symbol.lower() in text or name.lower() in text:
            mentioned.append(symbol)
    return mentioned
