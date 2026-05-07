# -*- coding: utf-8 -*-
"""
CryptositNews - AI Analysis Service
OpenAI GPT-4o-mini integration for news sentiment analysis with database caching.
"""

from app.config import Config
from app.utils.helpers import setup_logger, hash_text
from app.models.db import get_ai_cached, save_ai_cache, cache

logger = setup_logger("ai_service")

# Lazy-initialized OpenAI client
_client = None


def _get_client():
    """Get or create the OpenAI client singleton."""
    global _client
    if _client is None and Config.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _client = OpenAI(api_key=Config.OPENAI_API_KEY)
            logger.info("[ai_service] OpenAI client initialized")
        except Exception as e:
            logger.error(f"[ai_service] Failed to initialize OpenAI: {e}")
    return _client


def _build_analysis_prompt(title, summary=""):
    """Build the analysis prompt for the AI model."""
    prompt = f"""Analyze this cryptocurrency news article. Provide a brief response in this EXACT format:
Summary: [one sentence summary of the key point, max 25 words]
Sentiment: [positive/negative/neutral/bullish/bearish]
Reason: [brief explanation, max 20 words]

Title: {title}
"""
    if summary:
        prompt += f"\nSummary: {summary[:500]}"
    return prompt


def _parse_ai_response(text):
    """Parse the AI response into structured fields.

    Returns:
        tuple[str, str, str]: (ai_summary, ai_sentiment, ai_reason)
    """
    ai_summary = ""
    ai_sentiment = "neutral"
    ai_reason = ""

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("summary:"):
            ai_summary = line[len("Summary:"):].strip()
        elif line.lower().startswith("sentiment:"):
            ai_sentiment = line[len("Sentiment:"):].strip().lower()
        elif line.lower().startswith("reason:"):
            ai_reason = line[len("Reason:"):].strip()

    # Validate sentiment value
    valid_sentiments = {"positive", "negative", "neutral", "bullish", "bearish"}
    if ai_sentiment not in valid_sentiments:
        ai_sentiment = "neutral"

    return ai_summary, ai_sentiment, ai_reason


def analyze_news(title, summary=""):
    """Analyze a news item with AI for sentiment, summary, and reasoning.

    Uses a three-layer caching strategy:
    1. Database cache (persistent, keyed by content hash)
    2. Redis/memory cache (fast, keyed by content hash)

    Args:
        title: News headline.
        summary: News body or description.

    Returns:
        tuple[str|None, str|None, str|None]: (ai_summary, ai_sentiment, ai_reason)
            All None if analysis fails or title is empty.
    """
    if not title:
        return None, None, None

    # Build content hash for cache key
    content_hash = hash_text(f"{title}|{(summary or '')[:200]}")

    # Layer 1: Check database cache
    cached = get_ai_cached(content_hash)
    if cached:
        logger.debug(f"[ai_service] Cache hit for: {title[:50]}...")
        return (
            cached.get("ai_summary", ""),
            cached.get("ai_sentiment", ""),
            cached.get("ai_reason", ""),
        )

    # Layer 2: Check Redis/memory cache
    cache_key = f"ai:{content_hash}"
    mem_cached = cache.get(cache_key)
    if mem_cached:
        logger.debug(f"[ai_service] Memory cache hit for: {title[:50]}...")
        return (
            mem_cached.get("ai_summary", ""),
            mem_cached.get("ai_sentiment", ""),
            mem_cached.get("ai_reason", ""),
        )

    # No cache hit — call OpenAI
    client = _get_client()
    if not client:
        return None, None, None

    try:
        prompt = _build_analysis_prompt(title, summary)

        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a crypto news analyst. Be concise and accurate. "
                               "Respond in the exact format requested.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=Config.AI_MAX_TOKENS,
            temperature=0.3,
        )

        text = response.choices[0].message.content.strip()
        ai_summary, ai_sentiment, ai_reason = _parse_ai_response(text)

        # Cache in database (persistent)
        try:
            save_ai_cache(content_hash, ai_summary, ai_sentiment, ai_reason)
        except Exception as e:
            logger.debug(f"[ai_service] DB cache save: {e}")

        # Cache in Redis/memory (fast)
        try:
            cache.set(
                cache_key,
                {
                    "ai_summary": ai_summary,
                    "ai_sentiment": ai_sentiment,
                    "ai_reason": ai_reason,
                },
                ttl=Config.CACHE_AI_TTL,
            )
        except Exception as e:
            logger.debug(f"[ai_service] Memory cache save: {e}")

        logger.info(f"[ai_service] Analyzed: {title[:50]}... [{ai_sentiment}]")
        return ai_summary, ai_sentiment, ai_reason

    except Exception as e:
        logger.error(f"[ai_service] Analysis failed: {e}")
        return None, None, None


def generate_market_intelligence(news_items):
    """Generate an overall market intelligence summary from recent news.

    Sends the top 10 headlines to the AI model and returns a concise
    market summary covering mood, trends, and notable events.

    Args:
        news_items: List of news dicts with 'title' and 'ai_sentiment' keys.

    Returns:
        str: Market intelligence summary, or empty string on failure.
    """
    if not news_items:
        return ""

    client = _get_client()
    if not client:
        return ""

    try:
        # Collect top headlines with sentiment annotations
        headlines = []
        for item in news_items[:10]:
            title = item.get("title", "")
            sentiment = item.get("ai_sentiment", "")
            if title:
                headlines.append(f"- {title} [{sentiment}]")

        if not headlines:
            return ""

        prompt = (
            "Based on these latest crypto news headlines, provide a brief market "
            "intelligence summary. Include: overall market mood, key trends, and any "
            "notable events. Be concise (max 100 words).\n\n"
            + "\n".join(headlines)
        )

        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior crypto market analyst. Provide concise, "
                               "actionable market intelligence.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.4,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ai_service] Market intelligence generation failed: {e}")
        return ""
