"""
ai.py - OpenAI integration مع JSON output
"""

import json
import logging
from config import OPENAI_API_KEY

logger = logging.getLogger("cryptobot")

def generate_ai_insight(title: str) -> dict:
    """تحليل الخبر — يرجع dict مع summary + sentiment + reason"""
    empty = {"summary": "", "sentiment": "", "reason": ""}

    if not OPENAI_API_KEY:
        return empty

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a crypto news analyst. Always respond with valid JSON only, no extra text."
                },
                {
                    "role": "user",
                    "content": f"""Analyze this crypto news headline and return JSON:

"{title}"

Return exactly this JSON format:
{{
  "summary": "max 20 words summary",
  "sentiment": "Bullish" or "Bearish" or "Neutral",
  "reason": "max 15 words reason"
}}"""
                }
            ],
            temperature=0.3,
            max_tokens=150,
            response_format={"type": "json_object"},
        )

        text   = response.choices[0].message.content.strip()
        result = json.loads(text)

        return {
            "summary":   str(result.get("summary", ""))[:150],
            "sentiment": str(result.get("sentiment", ""))[:20],
            "reason":    str(result.get("reason", ""))[:150],
        }

    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ AI JSON parse error: {e}")
        return empty
    except Exception as e:
        logger.warning(f"⚠️ AI error: {e}")
        return empty
