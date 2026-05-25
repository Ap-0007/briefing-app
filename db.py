import sqlite3
import threading
import os
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "briefing.db"
_local = threading.local()

SCHEMA_VERSION = 2

MIGRATIONS = {
    1: [],  # baseline — tables created by CREATE IF NOT EXISTS
    2: [    # add starred column to briefings
        "ALTER TABLE briefings ADD COLUMN starred INTEGER DEFAULT 0",
    ],
}


def get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


def _run_migrations(conn):
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for v in range(current + 1, SCHEMA_VERSION + 1):
        for sql in MIGRATIONS.get(v, []):
            try:
                conn.execute(sql)
            except Exception as e:
                pass  # column may already exist on re-init
        conn.execute(f"PRAGMA user_version = {v}")
        conn.commit()


def init_db():
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            headlines_json TEXT NOT NULL,
            ai_json TEXT NOT NULL,
            annotations_json TEXT NOT NULL DEFAULT '{}'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'custom',
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            shares REAL NOT NULL DEFAULT 0,
            alert_threshold REAL NOT NULL DEFAULT 3.0,
            exchange TEXT NOT NULL DEFAULT 'US',
            grp TEXT NOT NULL DEFAULT 'Holdings'
        )
    """)
    for col, default in [("exchange", "US"), ("grp", "Holdings")]:
        try:
            c.execute(f"ALTER TABLE portfolio ADD COLUMN {col} TEXT NOT NULL DEFAULT '{default}'")
        except Exception:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at TEXT NOT NULL,
            briefing_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            cat TEXT NOT NULL DEFAULT 'world',
            sentiment TEXT NOT NULL DEFAULT 'neutral',
            story_json TEXT NOT NULL DEFAULT '{}'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS schedule_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_str TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Default feeds
    defaults = [
        ("ET Markets Stocks",  "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", "market"),
        ("ET Markets",         "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",          "market"),
        ("ET Tech",            "https://economictimes.indiatimes.com/tech/technology/rssfeeds/13357270.cms",    "tech"),
        ("ET Economy",         "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms",          "finance"),
        ("Moneycontrol News",  "https://www.moneycontrol.com/rss/latestnews.xml",                               "finance"),
        ("Moneycontrol Market","https://www.moneycontrol.com/rss/marketreports.xml",                            "market"),
        ("LiveMint Markets",   "https://www.livemint.com/rss/markets",                                          "market"),
        ("LiveMint Companies", "https://www.livemint.com/rss/companies",                                        "finance"),
        ("Business Standard",  "https://www.business-standard.com/rss/markets-106.rss",                        "market"),
        ("NDTV Profit",        "https://feeds.feedburner.com/ndtvprofit-latest",                                "finance"),
    ]
    for name, url, cat in defaults:
        c.execute(
            "INSERT OR IGNORE INTO feeds (name, url, category) VALUES (?, ?, ?)",
            (name, url, cat),
        )

    # Default schedule times
    default_times = [("09:10", 1), ("12:00", 1), ("16:00", 1)]
    for t, en in default_times:
        c.execute(
            "INSERT OR IGNORE INTO schedule_times (time_str, enabled) VALUES (?, ?)",
            (t, en),
        )

    # Default settings
    default_settings = [
        ("tts_enabled", "1"),
        ("tts_speed", "175"),
        ("price_alert_threshold", "3.0"),
        ("email_address", ""),
        ("email_password", ""),
        ("email_to", ""),
        ("smtp_server", "smtp.gmail.com"),
        ("smtp_port", "587"),
        ("onboarding_done", "0"),
        ("theme", "dark"),
        ("topic_tech", "1"),
        ("topic_finance", "1"),
        ("topic_world", "1"),
        ("topic_market", "1"),
        ("font_size", "12"),
        ("compact_view", "0"),
        ("custom_ai_prompt", ""),
        ("ollama_model", "llama3.2"),
        ("weekly_digest_day", "Sunday"),
        ("weekly_digest_time", "08:00"),
    ]
    for k, v in default_settings:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    _run_migrations(conn)


# ── settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    pass  # thread-local conn kept open
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
    pass  # thread-local conn kept open


# ── feeds ──────────────────────────────────────────────────────────────────────

def get_feeds(enabled_only=True):
    conn = get_conn()
    if enabled_only:
        rows = conn.execute("SELECT * FROM feeds WHERE enabled=1").fetchall()
    else:
        rows = conn.execute("SELECT * FROM feeds").fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def add_feed(name: str, url: str, category: str = "custom"):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO feeds (name, url, category) VALUES (?, ?, ?)",
        (name, url, category),
    )
    conn.commit()
    pass  # thread-local conn kept open


def delete_feed(feed_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM feeds WHERE id=?", (feed_id,))
    conn.commit()
    pass  # thread-local conn kept open


def toggle_feed(feed_id: int, enabled: bool):
    conn = get_conn()
    conn.execute("UPDATE feeds SET enabled=? WHERE id=?", (1 if enabled else 0, feed_id))
    conn.commit()
    pass  # thread-local conn kept open


# ── portfolio ──────────────────────────────────────────────────────────────────

def get_portfolio():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM portfolio").fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def add_portfolio_item(ticker: str, shares: float, exchange: str = "US", grp: str = "Holdings"):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO portfolio (ticker, shares, exchange, grp) VALUES (?, ?, ?, ?)",
        (ticker.upper(), shares, exchange.upper(), grp),
    )
    conn.commit()
    pass  # thread-local conn kept open


def delete_portfolio_item(ticker: str):
    conn = get_conn()
    conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker.upper(),))
    conn.commit()
    pass  # thread-local conn kept open


# ── keywords ──────────────────────────────────────────────────────────────────

def get_keywords():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM keywords ORDER BY keyword").fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def add_keyword(keyword: str):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword.lower(),))
    conn.commit()
    pass  # thread-local conn kept open


def delete_keyword(kw_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM keywords WHERE id=?", (kw_id,))
    conn.commit()
    pass  # thread-local conn kept open


# ── schedule times ─────────────────────────────────────────────────────────────

def get_schedule_times():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM schedule_times ORDER BY time_str").fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def set_schedule_time_enabled(time_id: int, enabled: bool):
    conn = get_conn()
    conn.execute(
        "UPDATE schedule_times SET enabled=? WHERE id=?", (1 if enabled else 0, time_id)
    )
    conn.commit()
    pass  # thread-local conn kept open


def upsert_schedule_time(time_str: str, enabled: bool = True):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO schedule_times (time_str, enabled) VALUES (?, ?)",
        (time_str, 1 if enabled else 0),
    )
    conn.commit()
    pass  # thread-local conn kept open


def delete_schedule_time(time_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM schedule_times WHERE id=?", (time_id,))
    conn.commit()
    pass  # thread-local conn kept open


# ── briefings ─────────────────────────────────────────────────────────────────

def save_briefing(created_at: str, headlines_json: str, ai_json: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO briefings (created_at, headlines_json, ai_json) VALUES (?, ?, ?)",
        (created_at, headlines_json, ai_json),
    )
    row_id = cur.lastrowid
    conn.commit()
    pass  # thread-local conn kept open
    return row_id


def update_annotation(briefing_id: int, annotations: dict):
    conn = get_conn()
    conn.execute(
        "UPDATE briefings SET annotations_json=? WHERE id=?",
        (json.dumps(annotations), briefing_id),
    )
    conn.commit()
    pass  # thread-local conn kept open


def get_briefing_list():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, created_at FROM briefings ORDER BY created_at DESC"
    ).fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def get_briefing(briefing_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM briefings WHERE id=?", (briefing_id,)).fetchone()
    pass  # thread-local conn kept open
    return dict(row) if row else None


def get_latest_briefing():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM briefings ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    pass  # thread-local conn kept open
    return dict(row) if row else None


def get_previous_briefing():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM briefings ORDER BY created_at DESC LIMIT 1 OFFSET 1"
    ).fetchone()
    pass  # thread-local conn kept open
    return dict(row) if row else None


# ── bookmarks ──────────────────────────────────────────────────────────────────

def add_bookmark(title: str, body: str, cat: str, sentiment: str,
                 story_json: str, briefing_id: int = None):
    from datetime import datetime
    import json as _json
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO bookmarks "
        "(saved_at, briefing_id, title, body, cat, sentiment, story_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"),
         briefing_id, title, body, cat, sentiment, story_json),
    )
    conn.commit()
    pass  # thread-local conn kept open


def get_bookmarks() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM bookmarks ORDER BY saved_at DESC"
    ).fetchall()
    pass  # thread-local conn kept open
    return [dict(r) for r in rows]


def delete_bookmark(bookmark_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
    conn.commit()
    pass  # thread-local conn kept open


def is_bookmarked(title: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM bookmarks WHERE title=?", (title,)
    ).fetchone()
    pass  # thread-local conn kept open
    return row is not None
