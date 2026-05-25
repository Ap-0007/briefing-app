"""
Sparkline chart generation using matplotlib (headless Agg backend).
Returns PIL Images that can be wrapped in CTkImage for display.
"""
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    from PIL import Image as _PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import yfinance as yf

# Simple in-process cache: {ticker: PIL.Image}
_cache: dict[str, "_PILImage.Image"] = {}


def sparkline(ticker: str, period: str = "5d", width: int = 90, height: int = 30) -> Optional[object]:
    """
    Return a PIL Image sparkline for the ticker.
    Returns None if matplotlib/pillow unavailable or data fetch fails.
    """
    if not HAS_MPL or not HAS_PIL:
        return None

    cache_key = f"{ticker}_{period}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1h")
        if hist.empty:
            return None
        prices = hist["Close"].dropna().tolist()
        if len(prices) < 2:
            return None

        dpi = 72
        fig, ax = plt.subplots(
            figsize=(width / dpi, height / dpi), dpi=dpi
        )
        fig.patch.set_alpha(0)
        ax.patch.set_alpha(0)

        color = "#22c55e" if prices[-1] >= prices[0] else "#ef4444"
        ax.plot(prices, color=color, linewidth=1.4, solid_capstyle="round")
        ax.fill_between(range(len(prices)), prices, min(prices),
                        alpha=0.15, color=color)
        ax.set_axis_off()
        ax.margins(x=0.02, y=0.15)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight",
                    pad_inches=0, transparent=True)
        plt.close(fig)
        buf.seek(0)
        img = _PILImage.open(buf).copy()
        _cache[cache_key] = img
        return img
    except Exception as e:
        logger.debug("Sparkline error %s: %s", ticker, e)
        return None


def invalidate_cache(ticker: str = None):
    """Clear cached sparkline(s). Pass None to clear all."""
    if ticker is None:
        _cache.clear()
    else:
        for key in [k for k in _cache if k.startswith(ticker)]:
            del _cache[key]
