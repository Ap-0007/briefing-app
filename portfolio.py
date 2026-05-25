import threading
import logging
import time
from datetime import datetime, date
from typing import Callable, Optional
import yfinance as yf
import db

logger = logging.getLogger(__name__)
REFRESH_INTERVAL = 300  # 5 minutes


class PortfolioTracker:
    def __init__(self, alert_callback: Optional[Callable] = None):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._alert_callback = alert_callback
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── public API ────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def get_data(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._data)

    def refresh_now(self):
        threading.Thread(target=self._fetch_all, daemon=True).start()

    # ── internals ─────────────────────────────────────────────────────────────

    def _loop(self):
        self._fetch_all()
        while self._running:
            time.sleep(REFRESH_INTERVAL)
            self._fetch_all()

    def _fetch_all(self):
        items = db.get_portfolio()
        threshold = float(db.get_setting("price_alert_threshold", "3.0"))
        new_data: dict[str, dict] = {}
        for item in items:
            ticker = item["ticker"]
            exchange = item.get("exchange", "NSE")
            yf_ticker = _yf_ticker(ticker, exchange)
            info = _fetch_ticker(yf_ticker)
            info["shares"] = item["shares"]
            info["value"] = info.get("price", 0) * item["shares"]
            info["exchange"] = exchange
            info["display_ticker"] = ticker
            info["currency"] = _currency_for(exchange)
            new_data[ticker] = info
            change = abs(info.get("change_pct", 0))
            if change >= threshold and self._alert_callback:
                self._alert_callback(ticker, info)
        with self._lock:
            self._data = new_data


# ── exchange helpers ──────────────────────────────────────────────────────────

# Maps exchange label → yfinance suffix and currency symbol
EXCHANGE_META = {
    "NSE": {"suffix": ".NS", "currency": "₹"},
    "BSE": {"suffix": ".BO", "currency": "₹"},
}


def _yf_ticker(ticker: str, exchange: str) -> str:
    suffix = EXCHANGE_META.get(exchange.upper(), {}).get("suffix", "")
    # Don't double-append if user already typed e.g. RELIANCE.NS
    if suffix and not ticker.upper().endswith(suffix.upper()):
        return ticker.upper() + suffix
    return ticker.upper()


def _currency_for(exchange: str) -> str:
    return EXCHANGE_META.get(exchange.upper(), {}).get("currency", "$")


def _fetch_ticker(ticker: str) -> dict:
    try:
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        # fast_info can raise AttributeError on delisted/unknown symbols
        try:
            price = float(info.last_price or 0)
            prev_close = float(info.previous_close or price)
        except Exception:
            raise ValueError(f"No price data for {ticker}")
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        # Earnings date
        earnings_date: Optional[str] = None
        try:
            cal = tk.calendar
            if cal is not None and not cal.empty:
                if "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"].iloc[0]
                    if hasattr(ed, "date"):
                        earnings_date = ed.date().isoformat()
                    else:
                        earnings_date = str(ed)
        except Exception:
            pass

        earnings_soon = False
        if earnings_date:
            try:
                diff = (date.fromisoformat(earnings_date) - date.today()).days
                earnings_soon = 0 <= diff <= 7
            except Exception:
                pass

        return {
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "earnings_date": earnings_date,
            "earnings_soon": earnings_soon,
            "error": None,
        }
    except Exception as e:
        logger.error("yfinance error for %s: %s", ticker, e)
        return {
            "price": 0,
            "prev_close": 0,
            "change": 0,
            "change_pct": 0,
            "earnings_date": None,
            "earnings_soon": False,
            "error": str(e),
        }
