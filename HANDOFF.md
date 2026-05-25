# Aurum — Indian Markets Briefing App
## Handoff Document

---

## What This Is

A native macOS desktop app that gives you an AI-powered Indian stock market morning briefing. One window, no browser needed. Built with Flask (backend) + pywebview (native window) + a single-page HTML frontend.

---

## How to Run

```bash
cd /Users/amogh/briefing-app
.venv/bin/python app.py
```

The window opens automatically. No browser, no extra steps.

**Prerequisite — Ollama must be running to generate briefings:**
```bash
ollama serve
```

---

## Project Structure

```
briefing-app/
├── app.py            — Entry point. Starts Flask + opens pywebview window.
├── web_server.py     — All Flask API routes. Single source of truth for the backend.
├── ai.py             — Ollama integration. System prompt, JSON parsing, status check.
├── db.py             — SQLite helpers. All database reads/writes go through here.
├── fetcher.py        — RSS feed fetcher. Pulls from Indian news sources via feedparser.
├── indices.py        — yfinance fetch for NIFTY/SENSEX/etc. + IndicesPoller background thread.
├── portfolio.py      — Per-ticker price fetch via yfinance. NSE/BSE only.
├── scheduler.py      — BriefingScheduler. Wraps the `schedule` library for auto-briefings.
├── exporter.py       — PDF export (fpdf2) + email (SMTP/Gmail).
├── static/
│   └── index.html    — The entire frontend. One HTML file, ~1300 lines.
├── briefing.db       — SQLite database (auto-created on first run).
└── launch.sh         — Double-click launcher script.
```

---

## Architecture

```
app.py
  └── db.init_db()              — creates briefing.db if missing
  └── web_server.start_server() — starts Flask on port 7477 in a daemon thread
                                  also starts BriefingScheduler
  └── webview.start()           — opens native macOS WKWebView window
                                  pointing at http://127.0.0.1:7477

Frontend (index.html)
  └── Loads in pywebview window (no browser tab)
  └── Calls Flask APIs via fetch()
  └── SSE /api/events for real-time briefing progress
  └── Chart.js 4.4.1 for price charts (CDN)
  └── Tabler Icons 3.19 for icons (CDN)
  └── DM Mono + Playfair Display fonts (Google Fonts CDN)
```

---

## API Endpoints (web_server.py)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Serves index.html |
| GET | `/api/events` | SSE stream — briefing_status, briefing_ready, error |
| POST | `/api/briefing/generate` | Fetch feeds → AI analysis → save → broadcast |
| GET | `/api/briefing/latest` | Most recent briefing |
| GET | `/api/briefing/list` | All briefing IDs + timestamps |
| GET | `/api/briefing/<id>` | Single briefing by ID |
| POST | `/api/briefing/<id>/annotate` | Save notes/bookmarks to a briefing |
| GET | `/api/indices` | NIFTY 50, SENSEX, BANKNIFTY, NIFTY IT, INDIA VIX, USD/INR |
| GET/POST | `/api/portfolio` | Get holdings with live prices / add a stock |
| DELETE | `/api/portfolio/<ticker>` | Remove a stock |
| GET | `/api/stock/<symbol>` | Full stock detail + chart history |
| GET/POST | `/api/bookmarks` | Get all / add bookmark |
| DELETE | `/api/bookmarks/<id>` | Delete bookmark |
| GET/POST | `/api/settings` | Get all settings / update settings |
| GET/POST | `/api/feeds` | List feeds / add feed |
| DELETE | `/api/feeds/<id>` | Delete feed |
| PATCH | `/api/feeds/<id>/toggle` | Enable/disable feed |
| GET/POST | `/api/keywords` | List / add watchlist keywords |
| DELETE | `/api/keywords/<id>` | Delete keyword |
| GET/POST | `/api/schedule` | List / add schedule times |
| PATCH | `/api/schedule/<id>/toggle` | Enable/disable time |
| DELETE | `/api/schedule/<id>` | Delete schedule time |
| POST | `/api/tts/speak` | macOS `say` command TTS |
| POST | `/api/tts/stop` | Kill TTS process |
| POST | `/api/export/pdf` | Generate PDF → ~/Documents/briefings/ |
| POST | `/api/export/email` | Send briefing via SMTP |
| GET | `/api/ollama/status` | Check if Ollama is running + list installed models |

---

## Database (briefing.db)

SQLite, auto-created at `briefing-app/briefing.db` on first run.

| Table | Contents |
|-------|----------|
| `briefings` | id, created_at, headlines_json, ai_json, annotations_json |
| `feeds` | id, name, url, category, enabled |
| `portfolio` | id, ticker, shares, exchange (NSE/BSE), grp |
| `bookmarks` | id, saved_at, briefing_id, title, body, cat, sentiment, story_json |
| `keywords` | id, keyword |
| `settings` | key, value |
| `schedule_times` | id, time_str (UNIQUE), enabled |

---

## AI / Ollama (ai.py)

- Uses the `ollama` Python library (`ollama.chat`)
- `format="json"` enforced — eliminates most parse failures
- Temperature: 0.15 (low = consistent structured output)
- Context window: 8192 tokens
- Max output: 4096 tokens
- On parse failure: retries once, then falls back to EMPTY_RESPONSE

**System prompt highlights:**
- India-only — explicitly bans US stocks
- Requires NSE ticker symbols (no .NS suffix)
- 6–10 stories per briefing
- Forces specific numbers (%, ₹, bps) in story bodies
- Sectors: IT, Banking, Energy, FMCG, Auto, Pharma

**To change model:** Settings → AI Model → select → Save

**Installed models example:**
```bash
ollama pull llama3.2    # recommended — fast, good JSON
ollama pull llama3.3    # best quality, needs 16GB+ RAM
ollama pull mistral     # compact, fast
```

---

## News Feeds (Default)

All Indian sources, configurable in Settings → Feeds:

| Feed | Category |
|------|----------|
| ET Markets Stocks | market |
| ET Markets | market |
| ET Tech | tech |
| ET Economy | finance |
| Moneycontrol News | finance |
| Moneycontrol Market | market |
| LiveMint Markets | market |
| LiveMint Companies | finance |
| Business Standard Markets | market |
| NDTV Profit | finance |

---

## Indian Indices (indices.py)

| Display Name | yfinance Symbol |
|---|---|
| NIFTY 50 | ^NSEI |
| SENSEX | ^BSESN |
| BANKNIFTY | ^NSEBANK |
| NIFTY IT | ^CNXIT |
| INDIA VIX | ^INDIAVIX |
| USD/INR | USDINR=X |

NSE market hours: 09:15–15:30 IST, Mon–Fri

---

## Portfolio (portfolio.py)

- Exchanges: NSE (`.NS` suffix) and BSE (`.BO` suffix) only
- Fetches via `yf.Ticker.fast_info` (faster than `yf.Ticker.info`)
- Valid symbol examples: `RELIANCE`, `TCS`, `HDFCBANK`, `TATAMOTORS`, `SBIN`, `INFY`
- Invalid: `TATA` (use `TATAMOTORS` or `TATASTEEL`)

**Quick-add chips (Portfolio page):** RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, TATAMOTORS, SBIN, WIPRO, MARUTI, ITC

---

## Frontend (static/index.html)

Single HTML file (~1300 lines). No build step, no npm.

**Pages:**
1. **Briefing** — Generate + view AI briefing. Stories collapse/expand on click. Bookmark, copy, annotate.
2. **Markets** — Watchlist tab (your portfolio) + NIFTY 50 tab (25 blue chips by sector). Price chart with 1D/5D/1M/3M/1Y/5Y tabs. Simulated order book + trades.
3. **Portfolio** — Holdings table with live LTP, value, day P&L. Quick-add chips.
4. **Saved** — Bookmarked stories.
5. **Settings** — Feeds, Email, TTS, AI Model (with live Ollama status), Schedule, Keywords.

**CDN dependencies** (requires internet on first load, then cached):
- Chart.js 4.4.1
- Tabler Icons 3.19
- DM Mono + Playfair Display (Google Fonts)

---

## PDF Export

Saved to: `~/Documents/briefings/briefing_YYYYMMDD_HHMMSS.pdf`

Finder reveals the file automatically after export. Uses fpdf2. Em-dashes and smart quotes are sanitized via `_safe()` for Latin-1 compatibility.

---

## Email (Gmail)

Requires a Gmail App Password (not your regular password):
1. myaccount.google.com → Security → 2-Step Verification (must be ON)
2. Search "App passwords" → Mail → generate
3. Paste the 16-char code in Settings → Email → App password

---

## Auto-Schedule

Default times: 09:10, 12:00, 16:00 (configurable in Settings → Schedule).
The app must be running for auto-briefings to fire. Uses the `schedule` library.

**Note:** The `schedule_times` table has a UNIQUE constraint on `time_str` to prevent duplicate scheduled jobs accumulating across restarts.

---

## Known Limitations

1. **CDN fonts/icons** — Tabler Icons and Google Fonts load from CDN. If offline, icons fall back to missing glyphs but buttons still have text labels.
2. **Simulated order book** — The order book and recent trades on the Markets page are randomly generated around the real price. Not live L2 data.
3. **yfinance rate limits** — Fetching many stocks quickly may trigger Yahoo Finance throttling. Add stocks gradually.
4. **Ollama speed** — On CPU, `llama3.2` takes ~30–90 seconds to generate a briefing. `llama3.3` can take 3–5 minutes. Use a Mac with Apple Silicon for best performance.

---

## Dependencies (key packages)

```
flask
pywebview
yfinance
feedparser
requests
certifi
ollama
fpdf2
schedule
```

Full list: `.venv/lib/python3.14/site-packages/` or regenerate with:
```bash
.venv/bin/pip freeze > requirements.txt
```

---

## Quick Reference — Common Tasks

**Add a stock to portfolio:**
Portfolio page → type NSE symbol (e.g. `TATAMOTORS`) → Add, or click a Quick Add chip.

**Generate a briefing:**
Briefing page → Get Briefing (Ollama must be running: `ollama serve`)

**Change AI model:**
Settings → AI Model → select from dropdown → Save

**Add a news feed:**
Settings → Feeds → enter name + RSS URL + category → +

**Export to PDF:**
Briefing page → PDF button → file opens in Finder

**Check Ollama is working:**
Settings → AI Model → Ollama Status panel shows green/red

---

*Last updated: May 2026*
