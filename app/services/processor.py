# -*- coding: utf-8 -*-
"""
CryptositNews - Processor Service
News filtering, prioritization, and Telegram message formatting.
"""

from app.config import Config
from app.utils.helpers import setup_logger, truncate

logger = setup_logger("processor")


def is_important(title, summary=""):
    """Check if a news item is important based on keyword analysis.

    Returns True if the title+summary contains any ACTION_KEYWORDS
    or at least 2 IMPORTANT_KEYWORDS.

    Args:
        title: News headline.
        summary: News body or description.

    Returns:
        bool: Whether the news is flagged as important.
    """
    if not title:
        return False

    text = f"{title} {summary}".lower()

    # Action/breaking keywords are instant qualifiers
    for kw in Config.ACTION_KEYWORDS:
        if kw.lower() in text:
            return True

    # Two or more important keywords also qualify
    important_count = sum(1 for kw in Config.IMPORTANT_KEYWORDS if kw.lower() in text)
    return important_count >= 2


def prioritize(title, summary=""):
    """Calculate a news priority score (0-100+).

    Higher scores indicate more impactful or noteworthy news.
    Scoring is additive across keyword categories.

    Args:
        title: News headline.
        summary: News body or description.

    Returns:
        int: Priority score.
    """
    if not title:
        return 0

    text = f"{title} {summary}".lower()
    score = 10  # Base score for any valid news

    # Breaking news keywords — highest impact
    for kw in Config.BREAKING_KEYWORDS:
        if kw.lower() in text:
            score += 100
            break  # Only apply once

    # High impact keywords — regulatory, security, major events
    for kw in Config.HIGH_IMPACT_KEYWORDS:
        if kw.lower() in text:
            score += 80
            break

    # Major coins mentioned — broad market relevance
    major_coins = ["bitcoin", "ethereum", "btc", "eth", "solana", "sol", "bnb"]
    for coin in major_coins:
        if coin in text:
            score += 50
            break

    # Important keywords — each one adds weight
    for kw in Config.IMPORTANT_KEYWORDS:
        if kw.lower() in text:
            score += 20

    # Sentiment keywords
    for kw in Config.POSITIVE_KEYWORDS:
        if kw.lower() in text:
            score += 10

    # Negative news is often more important for traders
    for kw in Config.NEGATIVE_KEYWORDS:
        if kw.lower() in text:
            score += 15

    return score


def format_message(title, summary="", url="", sentiment="", ai_summary=""):
    """Format a news item as a Telegram HTML message.

    Includes sentiment emoji, AI summary (preferred) or raw summary,
    sentiment badge, and a read-more link. Respects the 4096 char Telegram limit.

    Args:
        title: News headline.
        summary: Raw news summary.
        url: Link to the original article.
        sentiment: AI-assessed sentiment string.
        ai_summary: AI-generated summary.

    Returns:
        str: HTML-formatted Telegram message.
    """
    # Emoji mapping for sentiment
    emoji_map = {
        "positive": "\U0001f7e2",
        "negative": "\U0001f534",
        "neutral": "\u26aa",
        "bullish": "\U0001f680",
        "bearish": "\U0001f4c9",
        "fear": "\U0001f630",
        "greed": "\U0001f4b0",
    }
    emoji = emoji_map.get(sentiment.lower() if sentiment else "", "\U0001f4f0")

    # Build message body
    msg = f"{emoji} <b>{title}</b>\n\n"

    if ai_summary:
        msg += f"\U0001f4a1 {truncate(ai_summary, 300)}\n\n"
    elif summary:
        msg += f"{truncate(summary, 200)}\n\n"

    if sentiment:
        sentiment_emoji = emoji_map.get(sentiment.lower(), "")
        msg += f"\U0001f4ca Sentiment: {sentiment_emoji} {sentiment.title()}\n"

    if url:
        msg += f"\n\U0001f517 <a href=\"{url}\">Read More</a>"

    # Enforce Telegram's 4096 character limit
    if len(msg) > 4000:
        msg = msg[:3900] + "...\n\n\U0001f517 <a href=\"" + url + "\">Read More</a>"

    return msg


def extract_coins(title, summary=""):
    """Extract cryptocurrency symbols mentioned in the title or summary.

    Matches against Config.COIN_MAP keys (symbols) and values (names).

    Args:
        title: News headline.
        summary: News body or description.

    Returns:
        list[str]: Sorted list of matched coin symbols.
    """
    text = f"{title} {summary}".lower()
    mentioned = []
    for symbol, name in Config.COIN_MAP.items():
        if symbol.lower() in text or name.lower() in text:
            mentioned.append(symbol)
    return mentioned
