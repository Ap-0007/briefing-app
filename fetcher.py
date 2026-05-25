import re
import ipaddress
import logging
import socket
from urllib.parse import urlparse
import feedparser
import requests
import certifi
import db

logger = logging.getLogger(__name__)
MAX_ITEMS = 5

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "MorningBriefing/1.0"})
_SESSION.verify = certifi.where()

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_safe_url(url: str) -> bool:
    """Block private/loopback/SSRF targets; only allow http/https."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        try:
            addr = ipaddress.ip_address(socket.gethostbyname(host))
        except (socket.gaierror, ValueError):
            return False
        return not any(addr in net for net in _PRIVATE_NETS)
    except Exception:
        return False


def _parse_feed(url: str, category: str) -> list[dict]:
    if not _is_safe_url(url):
        logger.warning("Blocked SSRF/private URL: %s", url)
        return []
    try:
        # Fetch via requests (handles SSL with certifi) then parse the content
        resp = _SESSION.get(url, timeout=10)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if not feed.entries:
            raise ValueError("No entries in feed")
        items = []
        for entry in feed.entries[:MAX_ITEMS]:
            summary = entry.get("summary") or entry.get("description") or ""
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:300]
            items.append({
                "title": entry.get("title", "No title").strip(),
                "summary": summary,
                "link": entry.get("link", ""),
                "category": category,
                "source": feed.feed.get("title", url),
            })
        return items
    except Exception as e:
        logger.error("Feed fetch failed %s: %s", url, e)
        return []


def fetch_all_feeds(status_callback=None) -> tuple[list[dict], list[str]]:
    feeds = db.get_feeds(enabled_only=True)
    all_items: list[dict] = []
    warnings: list[str] = []

    for feed in feeds:
        if status_callback:
            status_callback(f"Fetching {feed['name']}…")
        items = _parse_feed(feed["url"], feed["category"])
        if not items:
            warnings.append(f"No data from {feed['name']}")
        all_items.extend(items)

    return all_items, warnings


def headlines_text(items: list[dict]) -> str:
    lines = []
    for item in items:
        lines.append(f"[{item['category'].upper()}] {item['title']}")
        if item["summary"]:
            lines.append(f"  {item['summary']}")
    return "\n".join(lines)
