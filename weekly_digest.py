"""
Weekly digest: aggregate the last 7 daily briefings and produce a summary
via Ollama, returned as a parsed dict.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import ollama as _ollama
import db

logger = logging.getLogger(__name__)

WEEKLY_SYSTEM_PROMPT = """
You are a weekly markets and news analyst. Given 7 days of daily briefing summaries,
return ONLY valid JSON:
{
  "week_summary": "3-4 sentence overview of the most important events this week",
  "sentiment_trend": "improving|worsening|stable",
  "top_stories": [
    {
      "title": "story headline",
      "why_it_matters": "one sentence",
      "still_relevant": true
    }
  ],
  "week_themes": ["theme1", "theme2", "theme3"],
  "sector_performance": {
    "Technology": "outperformed|underperformed|flat",
    "Finance": "outperformed|underperformed|flat",
    "Energy": "outperformed|underperformed|flat",
    "Healthcare": "outperformed|underperformed|flat",
    "Consumer": "outperformed|underperformed|flat",
    "Industrials": "outperformed|underperformed|flat"
  },
  "outlook": "one paragraph outlook for the coming week"
}
Return ONLY the JSON object, nothing else.
"""

EMPTY_WEEKLY = {
    "week_summary": "Not enough briefing history for a weekly digest.",
    "sentiment_trend": "stable",
    "top_stories": [],
    "week_themes": [],
    "sector_performance": {},
    "outlook": "—",
}


def _collect_week_text() -> str:
    """Pull last 7 briefings from db and compile into a text block."""
    rows = db.get_briefing_list()
    cutoff = datetime.now() - timedelta(days=7)
    lines: list[str] = []
    count = 0
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["created_at"])
        except Exception:
            continue
        if ts < cutoff:
            break
        briefing = db.get_briefing(row["id"])
        if not briefing:
            continue
        ai = json.loads(briefing["ai_json"])
        lines.append(f"=== {row['created_at']} ===")
        lines.append(ai.get("summary", ""))
        for story in ai.get("stories", []):
            lines.append(f"- {story.get('title', '')} [{story.get('sentiment', '')}]")
        count += 1
        if count >= 7:
            break
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def generate() -> dict:
    """Generate weekly digest. Returns parsed dict."""
    week_text = _collect_week_text()
    if not week_text.strip():
        return dict(EMPTY_WEEKLY)

    try:
        response = _ollama.chat(
            model="llama3.1",
            messages=[
                {"role": "system", "content": WEEKLY_SYSTEM_PROMPT},
                {"role": "user", "content": f"WEEKLY BRIEFINGS:\n{week_text}"},
            ],
        )
        raw = response["message"]["content"]
    except Exception as e:
        err = str(e).lower()
        if "connection" in err or "refused" in err:
            raise
        logger.error("Weekly digest Ollama error: %s", e)
        return dict(EMPTY_WEEKLY)

    parsed = _extract_json(raw)
    if parsed is None:
        fallback = dict(EMPTY_WEEKLY)
        fallback["week_summary"] = raw[:600]
        return fallback
    return parsed
