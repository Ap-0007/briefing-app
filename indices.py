"""
Live market indices, market-hours status, and USD/INR rate.
All fetches are best-effort — failures return None gracefully.
"""
import logging
import threading
from datetime import datetime, time, timezone, timedelta
from typing import Optional
import yfinance as yf

logger = logging.getLogger(__name__)

import time as _time
_cache: dict = {"data": None, "status": None, "updated_at": 0.0}
_CACHE_TTL = 60  # seconds

# Symbols to fetch
INDEX_SYMBOLS = {
    "NIFTY 50":   "^NSEI",
    "SENSEX":     "^BSESN",
    "BANKNIFTY":  "^NSEBANK",
    "NIFTY IT":   "^CNXIT",
    "INDIA VIX":  "^INDIAVIX",
}
USDINR_SYMBOL = "USDINR=X"

# Timezone offsets (no pytz needed — stdlib zoneinfo on 3.9+)
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
    _EST = ZoneInfo("America/New_York")
except Exception:
    _IST = timezone(timedelta(hours=5, minutes=30))
    _EST = timezone(timedelta(hours=-5))

# Market sessions (local time, Mon-Fri only)
_NSE_OPEN  = time(9, 15)
_NSE_CLOSE = time(15, 30)
_NYSE_OPEN  = time(9, 30)
_NYSE_CLOSE = time(16, 0)


def _is_market_open(tz, open_t: time, close_t: time) -> bool:
    now = datetime.now(tz)
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    return open_t <= now.time() <= close_t


def market_status() -> dict[str, str]:
    nse_open  = _is_market_open(_IST, _NSE_OPEN,  _NSE_CLOSE)
    nyse_open = _is_market_open(_EST, _NYSE_OPEN, _NYSE_CLOSE)
    return {
        "NSE":  "OPEN"   if nse_open  else "CLOSED",
        "NYSE": "OPEN"   if nyse_open else "CLOSED",
    }


def fetch_indices() -> dict[str, dict]:
    """Returns {name: {price, change_pct, error}}"""
    result: dict[str, dict] = {}
    tickers = list(INDEX_SYMBOLS.values()) + [USDINR_SYMBOL]
    try:
        data = yf.download(
            tickers,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        closes = data["Close"] if "Close" in data else data
        for name, sym in INDEX_SYMBOLS.items():
            try:
                series = closes[sym].dropna()
                if len(series) < 2:
                    raise ValueError("Not enough data")
                prev, last = float(series.iloc[-2]), float(series.iloc[-1])
                pct = (last - prev) / prev * 100
                result[name] = {"price": last, "change_pct": pct, "error": None}
            except Exception as e:
                result[name] = {"price": 0, "change_pct": 0, "error": str(e)}
        # USD/INR
        try:
            series = closes[USDINR_SYMBOL].dropna()
            result["USD/INR"] = {"price": float(series.iloc[-1]), "change_pct": 0, "error": None}
        except Exception:
            result["USD/INR"] = {"price": 0, "change_pct": 0, "error": "unavailable"}
    except Exception as e:
        logger.error("indices fetch error: %s", e)
        for name in list(INDEX_SYMBOLS) + ["USD/INR"]:
            result.setdefault(name, {"price": 0, "change_pct": 0, "error": str(e)})
    return result


def get_cached_indices() -> dict:
    """Return cached indices + status, refreshing only when TTL expired."""
    now = _time.time()
    if _cache["data"] is None or (now - _cache["updated_at"]) > _CACHE_TTL:
        _cache["data"] = fetch_indices()
        _cache["status"] = market_status()
        _cache["updated_at"] = now
    return {
        "indices": _cache["data"],
        "status": _cache["status"],
        "last_updated": _cache["updated_at"],
    }


class IndicesPoller:
    """Background poller — calls callback(indices_dict, status_dict) on update."""

    def __init__(self, callback, interval: int = 60):
        self._callback = callback
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        import time as _time
        while self._running:
            try:
                indices = fetch_indices()
                status  = market_status()
                self._callback(indices, status)
            except Exception as e:
                logger.error("IndicesPoller error: %s", e)
            _time.sleep(self._interval)
