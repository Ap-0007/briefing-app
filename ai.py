import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Optional
import ollama as _ollama
import db

_OLLAMA_TIMEOUT = 300  # seconds

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama3.2"

AVAILABLE_MODELS = [
    "llama3.2",
    "llama3.1",
    "llama3.3",
    "mistral",
    "qwen2.5",
    "gemma2",
    "phi3",
    "deepseek-r1",
]

SYSTEM_PROMPT = """You are Aurum, an expert Indian stock market analyst providing morning briefings for NSE/BSE investors.

Today you will analyse Indian financial news headlines and produce a structured JSON briefing.

OUTPUT FORMAT — return ONLY this JSON, no markdown, no explanation:
{
  "summary": "3-4 sentence executive summary covering: overall market direction, key macro driver today, sector rotation, and one actionable insight for Indian retail investors",
  "sentiment": "bullish|bearish|neutral",
  "sentiment_score": 55,
  "stories": [
    {
      "cat": "market|finance|tech|macro",
      "title": "exact or lightly edited headline",
      "body": "2-3 sentences: what happened, why it matters specifically for NSE/BSE investors, and which sectors/stocks are impacted",
      "sentiment": "bullish|bearish|neutral",
      "affects_watchlist": true,
      "stocks": [
        {
          "ticker": "RELIANCE",
          "name": "Reliance Industries Ltd",
          "signal": "buy|watch|avoid",
          "reason": "one concise reason for the signal",
          "bull_case": "specific upside scenario for this stock",
          "bear_case": "specific downside risk for this stock"
        }
      ]
    }
  ],
  "sector_heatmap": {
    "IT": "hot|warm|cold",
    "Banking": "hot|warm|cold",
    "Energy": "hot|warm|cold",
    "FMCG": "hot|warm|cold",
    "Auto": "hot|warm|cold",
    "Pharma": "hot|warm|cold"
  },
  "macro": {
    "market_mood": "risk-on|risk-off|mixed",
    "key_themes": ["3 to 5 concise themes driving markets today"]
  }
}

STRICT RULES:
- ONLY cover NSE/BSE listed companies. Never mention NYSE, NASDAQ, or US stocks unless they directly affect Indian ADRs or FII flows.
- Use exact NSE ticker symbols (no .NS suffix). Examples: RELIANCE, TCS, HDFCBANK, INFY, TATAMOTORS, SBIN, WIPRO, AXISBANK.
- sentiment_score: integer 0–100. Bearish = 0–35, Neutral = 36–64, Bullish = 65–100.
- affects_watchlist: true only if the story directly impacts a ticker in the user's WATCHLIST KEYWORDS.
- stocks array: only include tickers explicitly named or strongly implied by the story. Leave empty [] if none.
- sector_heatmap values: "hot" (strong buying), "warm" (mild positive/neutral), "cold" (selling pressure).
- key_themes: 3–5 short phrases, e.g. "FII buying in banking", "Rupee weakness", "RBI rate outlook".
- stories: include 6–10 most market-moving stories. Prioritise: index movements, FII/DII data, RBI/SEBI actions, quarterly results, sector news.
- body text: be specific — include percentage moves, rupee values, basis points where relevant.
- Return ONLY the JSON object."""

EMPTY_RESPONSE = {
    "summary": "No market data available at this time.",
    "sentiment": "neutral",
    "sentiment_score": 50,
    "stories": [],
    "sector_heatmap": {
        "IT": "warm",
        "Banking": "warm",
        "Energy": "warm",
        "FMCG": "warm",
        "Auto": "warm",
        "Pharma": "warm",
    },
    "macro": {"market_mood": "mixed", "key_themes": []},
}


def _get_model() -> str:
    return db.get_setting("ollama_model", DEFAULT_MODEL) or DEFAULT_MODEL


def _get_system_prompt() -> str:
    custom = db.get_setting("custom_ai_prompt", "").strip()
    return custom if custom else SYSTEM_PROMPT


def _call_ollama(headlines: str, watchlist: list[str]) -> str:
    watchlist_str = ", ".join(watchlist) if watchlist else "none"
    user_msg = (
        f"WATCHLIST KEYWORDS (mention these if relevant): {watchlist_str}\n\n"
        f"TODAY'S INDIAN MARKET NEWS HEADLINES:\n{headlines}"
    )
    model = _get_model()

    def _do_chat():
        return _ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": _get_system_prompt()},
                {"role": "user",   "content": user_msg},
            ],
            format="json",
            options={
                "temperature": 0.15,
                "num_predict": 4096,
                "num_ctx": 8192,
                "repeat_penalty": 1.1,
            },
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_do_chat)
        try:
            response = future.result(timeout=_OLLAMA_TIMEOUT)
        except FuturesTimeout:
            future.cancel()
            raise TimeoutError(f"Ollama did not respond within {_OLLAMA_TIMEOUT}s")

    return response["message"]["content"]


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


def check_ollama() -> dict:
    """Returns {running: bool, models: list[str], error: str|None}"""
    try:
        result = _ollama.list()
        models = [m.model for m in result.models] if hasattr(result, 'models') else []
        return {"running": True, "models": models, "error": None}
    except Exception as e:
        err = str(e)
        if "connection" in err.lower() or "refused" in err.lower():
            return {"running": False, "models": [], "error": "Ollama is not running. Start it with: ollama serve"}
        return {"running": False, "models": [], "error": err}


def analyze(headlines: str, watchlist: list[str]) -> tuple[dict, str]:
    """Returns (parsed_dict, raw_text). Raises OllamaNotRunning if unreachable."""
    try:
        raw = _call_ollama(headlines, watchlist)
    except Exception as e:
        err = str(e).lower()
        if "connection" in err or "refused" in err or "connect" in err:
            raise OllamaNotRunning()
        logger.error("Ollama error: %s", e)
        raise

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("JSON parse failed on first attempt, retrying…")
        try:
            raw = _call_ollama(headlines, watchlist)
            parsed = _extract_json(raw)
        except Exception as e:
            logger.error("Retry failed: %s", e)

    if parsed is None:
        fallback = dict(EMPTY_RESPONSE)
        fallback["summary"] = raw[:500] if raw else "Parse error — model returned non-JSON output."
        return fallback, raw or ""

    return parsed, raw


class OllamaNotRunning(Exception):
    pass
