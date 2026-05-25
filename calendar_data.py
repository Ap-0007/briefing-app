"""
Economic calendar: today's earnings for portfolio stocks + FII/DII data.
"""
import logging
import requests
import certifi
from datetime import date, timedelta
from typing import Optional
import yfinance as yf
import db

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.verify = certifi.where()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
})


# ── Earnings calendar ─────────────────────────────────────────────────────────

def get_upcoming_earnings(days_ahead: int = 14) -> list[dict]:
    """Return earnings events for all portfolio tickers within next N days."""
    items = db.get_portfolio()
    events: list[dict] = []
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    for item in items:
        ticker = item["ticker"]
        exchange = item.get("exchange", "US")
        from portfolio import _yf_ticker
        yf_sym = _yf_ticker(ticker, exchange)
        try:
            tk = yf.Ticker(yf_sym)
            cal = tk.calendar
            if cal is None or (hasattr(cal, "empty") and cal.empty):
                continue
            if "Earnings Date" in cal.index:
                ed_val = cal.loc["Earnings Date"].iloc[0]
                if hasattr(ed_val, "date"):
                    ed = ed_val.date()
                else:
                    ed = date.fromisoformat(str(ed_val)[:10])
                if today <= ed <= cutoff:
                    days_away = (ed - today).days
                    events.append({
                        "ticker": ticker,
                        "date": ed.isoformat(),
                        "days_away": days_away,
                        "label": "Today" if days_away == 0 else
                                 f"In {days_away}d",
                    })
        except Exception as e:
            logger.debug("Earnings fetch failed %s: %s", ticker, e)

    events.sort(key=lambda x: x["days_away"])
    return events


# ── FII / DII ─────────────────────────────────────────────────────────────────

def get_fii_dii() -> Optional[dict]:
    """
    Attempt to fetch today's FII/DII data from NSE India.
    Returns dict with keys: fii_net, dii_net, date  or None on failure.
    """
    try:
        # Warm up session with the NSE home page first
        _SESSION.get("https://www.nseindia.com", timeout=5)
        r = _SESSION.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        # data is a list; most recent entry first
        entry = data[0] if isinstance(data, list) else data
        fii_net = _safe_float(entry.get("fii_Net") or entry.get("fiiBuy") and
                              (float(entry.get("fiiBuy", 0)) - float(entry.get("fiiSell", 0))))
        dii_net = _safe_float(entry.get("dii_Net") or entry.get("diiBuy") and
                              (float(entry.get("diiBuy", 0)) - float(entry.get("diiSell", 0))))
        trade_date = entry.get("date", date.today().isoformat())
        return {"fii_net": fii_net, "dii_net": dii_net, "date": trade_date}
    except Exception as e:
        logger.debug("FII/DII fetch failed: %s", e)
        return None


def _safe_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0
