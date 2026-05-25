"""
macOS native notifications via osascript.
Falls back silently on non-Mac platforms.
"""
import platform
import subprocess
import logging

logger = logging.getLogger(__name__)

_IS_MAC = platform.system() == "Darwin"


def notify(title: str, message: str, subtitle: str = ""):
    if not _IS_MAC:
        logger.debug("notify skipped (non-Mac): %s — %s", title, message)
        return
    # Sanitise: escape double-quotes
    title   = title.replace('"', '\\"')
    message = message.replace('"', '\\"')
    subtitle = subtitle.replace('"', '\\"')

    sub_clause = f'subtitle "{subtitle}"' if subtitle else ""
    script = f'display notification "{message}" with title "{title}" {sub_clause}'
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error("notify error: %s", e)


def notify_briefing_ready(sentiment: str, score: int):
    notify(
        title="Morning Briefing Ready ☀",
        message=f"Sentiment: {sentiment.title()} ({score}/100)",
        subtitle="Click to view",
    )


def notify_price_alert(ticker: str, change_pct: float, price: float, currency: str = "$"):
    sign = "+" if change_pct >= 0 else ""
    notify(
        title=f"Price Alert — {ticker}",
        message=f"{currency}{price:,.2f}  ({sign}{change_pct:.1f}%)",
        subtitle="Portfolio tracker",
    )


def notify_weekly_digest():
    notify(
        title="Weekly Digest Ready 📊",
        message="Your 7-day market summary is available.",
    )
