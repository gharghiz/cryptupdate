# -*- coding: utf-8 -*-
"""
CryptositNews v3 - AI News Analysis Service
OpenAI-powered sentiment analysis, summarization, and categorization.
"""

import hashlib
import time
from functools import wraps

from app.config import Config
from app.utils.helpers import setup_logger

logger = setup_logger("ai_service")

# Simple in-memory cache for AI results
_cache = {}


def get_cache():
    """Get the global AI cache dict."""
    return _cache


# Exported as `cache` for batch_analyze_news usage
cache = _cache


def hash_text(text):
    """Create a deterministic hash of text content for caching."""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:32]


def analyze_news(title, summary=""):
    """Analyze a single news article using OpenAI.

    Returns:
        tuple: (ai_summary, ai_sentiment, ai_reason) or (None, None, None)
    """
    if not Config.OPENAI_API_KEY:
        return None, None, None

    content = f"{title}. {(summary or '')[:300]}"
    content_hash = hash_text(content)

    # Check DB cache
    from app.models.db import get_ai_cached
    cached = get_ai_cached(content_hash)
    if cached:
        return (
            cached.get("ai_summary", ""),
            cached.get("ai_sentiment", ""),
            cached.get("ai_reason", ""),
        )

    # Check memory cache
    mem_key = f"ai:{content_hash}"
    if mem_key in _cache:
        c = _cache[mem_key]
        return c.get("ai_summary", ""), c.get("ai_sentiment", ""), c.get("ai_reason", "")

    # Call OpenAI
    try:
        import openai
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)

        prompt = (
            "Analyze this crypto news headline (and summary if provided). "
            "Respond in exactly this JSON format:\n"
            '{"summary":"one-line summary","sentiment":"positive|negative|neutral",'
            '"reason":"brief reason"}\n\n'
            f"Title: {title}\n"
        )
        if summary:
            prompt += f"Summary: {summary[:300]}\n"

        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=Config.AI_MAX_TOKENS,
            temperature=0.3,
        )

        text = response.choices[0].message.content.strip()

        # Parse JSON from response
        import json
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        ai_summary = result.get("summary", "")[:200]
        ai_sentiment = result.get("sentiment", "neutral").lower()
        ai_reason = result.get("reason", "")[:150]

        # Validate sentiment
        if ai_sentiment not in ("positive", "negative", "neutral"):
            ai_sentiment = "neutral"

        # Save to caches
        cache_data = {
            "content_hash": content_hash,
            "ai_summary": ai_summary,
            "ai_sentiment": ai_sentiment,
            "ai_reason": ai_reason,
        }
        _cache[mem_key] = cache_data

        # Save to DB cache
        try:
            from app.models.db import save_ai_cache
            save_ai_cache(content_hash, ai_summary, ai_sentiment, ai_reason)
        except Exception as e:
            logger.warning(f"Failed to save AI cache to DB: {e}")

        logger.debug(f"[ai_service] Analyzed: {title[:50]}... -> {ai_sentiment}")
        return ai_summary, ai_sentiment, ai_reason

    except Exception as e:
        logger.error(f"[ai_service] OpenAI error: {e}")
        return None, None, None


def batch_analyze_news(news_items, batch_size=None):
    """Batch analyze multiple news items efficiently.

    Groups items by content hash to avoid duplicate API calls.
    Uses existing cache before making API calls.

    Args:
        news_items: List of dicts with 'title', 'summary', 'id' keys.
        batch_size: Max concurrent API calls (default from Config).

    Returns:
        list[dict]: List of {id, ai_summary, ai_sentiment, ai_reason, cached} dicts.
    """
    if not news_items:
        return []

    batch_size = batch_size or Config.AI_BATCH_SIZE
    results = []
    uncached = []

    # First pass: check cache for all items
    for item in news_items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        content_hash = hash_text(f"{title}|{(summary or '')[:200]}")

        # Check cache
        cached = get_ai_cached(content_hash)
        cache_key = f"ai:{content_hash}"
        mem_cached = cache.get(cache_key)

        cache_data = cached or mem_cached
        if cache_data:
            results.append({
                "id": item.get("id"),
                "ai_summary": cache_data.get("ai_summary", ""),
                "ai_sentiment": cache_data.get("ai_sentiment", ""),
                "ai_reason": cache_data.get("ai_reason", ""),
                "cached": True,
            })
        else:
            uncached.append({
                "id": item.get("id"),
                "title": title,
                "summary": summary,
                "content_hash": content_hash,
            })

    if not uncached:
        logger.info(f"[ai_service] Batch: all {len(news_items)} items cached")
        return results

    logger.info(f"[ai_service] Batch: {len(results)} cached, {len(uncached)} need analysis")

    # Analyze uncached items sequentially (API rate limits)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _analyze_one(item):
        ai_summary, ai_sentiment, ai_reason = analyze_news(item["title"], item["summary"])
        return {
            "id": item["id"],
            "ai_summary": ai_summary or "",
            "ai_sentiment": ai_sentiment or "",
            "ai_reason": ai_reason or "",
            "cached": False,
        }

    with ThreadPoolExecutor(max_workers=min(batch_size, len(uncached))) as executor:
        futures = {executor.submit(_analyze_one, item): item for item in uncached}
        for future in as_completed(futures, timeout=120):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"[ai_service] Batch analysis error: {e}")

    return results
