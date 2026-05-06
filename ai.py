# -*- coding: utf-8 -*-
"""
CryptositNews - AI Analysis
OpenAI GPT-4o-mini integration for news sentiment analysis.
"""

import os

import config
from utils import setup_logger

logger = setup_logger("ai")

_client = None


def get_client():
    """Get or create OpenAI client."""
    global _client
    if _client is None and config.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _client = OpenAI(api_key=config.OPENAI_API_KEY)
            logger.info("OpenAI client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI: {e}")
    return _client


def analyze_news(title, summary=""):
    """Analyze news with AI for sentiment, summary, and reasoning."""
    if not title:
        return None, None, None

    client = get_client()
    if not client:
        return None, None, None

    try:
        prompt = f"""Analyze this cryptocurrency news article. Provide a brief response in this EXACT format:
Summary: [one sentence summary of the key point, max 25 words]
Sentiment: [positive/negative/neutral/bullish/bearish]
Reason: [brief explanation, max 20 words]

Title: {title}
"""
        if summary:
            prompt += f"\nSummary: {summary[:500]}"

        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[
                {"role": "system", "content": "You are a crypto news analyst. Be concise and accurate. Respond in the exact format requested."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=150,
            temperature=0.3,
        )

        text = response.choices[0].message.content.strip()

        ai_summary = ""
        ai_sentiment = "neutral"
        ai_reason = ""

        for line in text.split("\n"):
            line = line.strip()
            if line.lower().startswith("summary:"):
                ai_summary = line[len("Summary:"):].strip()
            elif line.lower().startswith("sentiment:"):
                ai_sentiment = line[len("Sentiment:"):].strip().lower()
            elif line.lower().startswith("reason:"):
                ai_reason = line[len("Reason:"):].strip()

        # Validate sentiment
        valid = ["positive", "negative", "neutral", "bullish", "bearish"]
        if ai_sentiment not in valid:
            ai_sentiment = "neutral"

        return ai_summary, ai_sentiment, ai_reason

    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        return None, None, None


def generate_market_intelligence(news_items, prices=None, fear_greed=None):
    """Generate overall market intelligence summary from recent news."""
    if not news_items:
        return ""

    client = get_client()
    if not client:
        return ""

    try:
        # Collect top headlines
        headlines = []
        for item in news_items[:10]:
            title = item.get("title", "")
            sentiment = item.get("ai_sentiment", "")
            if title:
                headlines.append(f"- {title} [{sentiment}]")

        if not headlines:
            return ""

        prompt = f"""Based on these latest crypto news headlines, provide a brief market intelligence summary.
Include: overall market mood, key trends, and any notable events. Be concise (max 100 words).

{chr(10).join(headlines)}
"""

        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[
                {"role": "system", "content": "You are a senior crypto market analyst. Provide concise, actionable market intelligence."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.4,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Market intelligence generation failed: {e}")
        return ""
