"""
Flask API server backing the single-page super-app.
"""
import json, logging, queue, threading, subprocess, re, secrets
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request, Response, abort
import yfinance as yf
import db, fetcher, ai as ai_mod, exporter
import indices as indices_mod
import scheduler as sched_mod
from portfolio import _fetch_ticker, _yf_ticker, _currency_for

logger = logging.getLogger(__name__)
app    = Flask(__name__, static_folder="static")
app.secret_key = secrets.token_hex(32)
app.config["DEBUG"] = False
PORT   = 7477

# ── CSRF guard ────────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS = {"http://127.0.0.1:7477", "http://localhost:7477"}

@app.before_request
def csrf_guard():
    if request.method in ("POST", "DELETE", "PATCH", "PUT"):
        origin  = request.headers.get("Origin", "")
        referer = request.headers.get("Referer", "")
        ok = (origin in _ALLOWED_ORIGINS or
              any(referer.startswith(o) for o in _ALLOWED_ORIGINS) or
              not origin)  # allow pywebview which sends no Origin
        if not ok:
            abort(403, "CSRF check failed")

# ── Input validators ──────────────────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,20}$")

def safe_id(val):
    try:
        n = int(val)
        if n < 1: raise ValueError
        return n
    except (ValueError, TypeError):
        abort(400, "Invalid ID")

def safe_ticker(val):
    v = str(val).strip().upper()
    if not _TICKER_RE.match(v):
        abort(400, "Invalid ticker symbol")
    return v

# ── Briefing generation lock ──────────────────────────────────────────────────
_briefing_lock = threading.Lock()

# ── SSE broadcast ─────────────────────────────────────────────────────────────
_clients: set = set()

def broadcast(event: dict):
    for q in list(_clients):
        try: q.put_nowait(event)
        except Exception: _clients.discard(q)

@app.route("/api/events")
def api_events():
    def stream():
        q = queue.Queue(maxsize=50)
        _clients.add(q)
        try:
            while True:
                try:
                    yield f"data: {json.dumps(q.get(timeout=20))}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"  # SSE comment — keeps TCP alive, ignored by client
        except GeneratorExit:
            _clients.discard(q)
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── static ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── briefing ──────────────────────────────────────────────────────────────────
def _briefing_payload(b):
    return {"id": b["id"], "created_at": b["created_at"],
            "ai": json.loads(b["ai_json"]),
            "annotations": json.loads(b.get("annotations_json") or "{}")}

@app.route("/api/briefing/latest")
def api_briefing_latest():
    b = db.get_latest_briefing()
    return jsonify(_briefing_payload(b) if b else None)

@app.route("/api/briefing/list")
def api_briefing_list():
    return jsonify(db.get_briefing_list())

@app.route("/api/briefing/<raw_id>")
def api_briefing_get(raw_id):
    b = db.get_briefing(safe_id(raw_id))
    return jsonify(_briefing_payload(b)) if b else abort(404)

@app.route("/api/briefing/generate", methods=["POST"])
def api_briefing_generate():
    if not _briefing_lock.acquire(blocking=False):
        return jsonify({"error": "Briefing already in progress"}), 409
    try:
        broadcast({"type":"briefing_status","status":"fetching"})
        items, _ = fetcher.fetch_all_feeds()
        if not items:
            return jsonify({"error":"No news items fetched."}), 400

        topic_filters = {t for t in ["tech","finance","world","market"]
                         if db.get_setting(f"topic_{t}","1") == "1"}
        filtered = [h for h in items
                    if not topic_filters or h.get("category","world") in topic_filters]
        headlines = "\n".join(f"[{h.get('category','world').upper()}] {h['title']}"
                              for h in filtered[:80])

        broadcast({"type":"briefing_status","status":"analyzing","count":len(filtered)})
        watchlist = [r["ticker"] for r in db.get_portfolio()]
        keywords  = [k["keyword"] for k in db.get_keywords()]
        result, _ = ai_mod.analyze(headlines, watchlist + keywords)

        ts  = datetime.now().isoformat(timespec="seconds")
        bid = db.save_briefing(ts, json.dumps(filtered[:80]), json.dumps(result))
        broadcast({"type":"briefing_ready","id":bid})
        return jsonify({"id":bid,"briefing":result,"created_at":ts,"annotations":{}})
    except ai_mod.OllamaNotRunning:
        return jsonify({"error":"Ollama is not running. Start it with: ollama serve"}), 503
    except Exception as e:
        logger.error("generate error: %s", e)
        return jsonify({"error":str(e)}), 500
    finally:
        _briefing_lock.release()

@app.route("/api/briefing/<raw_id>/annotate", methods=["POST"])
def api_annotate(raw_id):
    bid = safe_id(raw_id)
    b = db.get_briefing(bid)
    if not b: abort(404)
    ann = json.loads(b.get("annotations_json") or "{}")
    ann.update(request.json or {})
    db.update_annotation(bid, ann)
    return jsonify({"ok":True})

# ── indices ───────────────────────────────────────────────────────────────────
@app.route("/api/indices")
def api_indices():
    return jsonify(indices_mod.get_cached_indices())

# ── portfolio ─────────────────────────────────────────────────────────────────
@app.route("/api/portfolio", methods=["GET"])
def api_portfolio_get():
    items = db.get_portfolio()
    results, total_value, total_cost = [], 0.0, 0.0
    for item in items:
        ticker   = item["ticker"]
        exchange = item.get("exchange","US")
        shares   = item.get("shares", 0)
        info     = _fetch_ticker(_yf_ticker(ticker, exchange))
        value    = info["price"] * shares
        cost     = info["prev_close"] * shares
        pnl      = value - cost
        total_value += value; total_cost += cost
        results.append({
            "ticker": ticker, "exchange": exchange,
            "currency": _currency_for(exchange),
            "shares": shares, "grp": item.get("grp","Holdings"),
            "price": info["price"], "change": info["change"],
            "change_pct": info["change_pct"],
            "value": value, "pnl": pnl,
            "pnl_pct": (pnl/cost*100) if cost else 0,
            "error": info["error"],
        })
    day_pnl = total_value - total_cost
    return jsonify({"holdings": results, "summary": {
        "total_value": total_value, "day_pnl": day_pnl,
        "day_pnl_pct": (day_pnl/total_cost*100) if total_cost else 0,
    }})

@app.route("/api/portfolio", methods=["POST"])
def api_portfolio_add():
    d = request.json or {}
    ticker   = safe_ticker(d.get("ticker",""))
    exchange = d.get("exchange","NSE").upper()
    if exchange not in ("NSE","BSE"):
        return jsonify({"error":"Exchange must be NSE or BSE"}), 400
    yf_sym = _yf_ticker(ticker, exchange)
    info   = _fetch_ticker(yf_sym)
    if info["error"] or not info["price"]:
        return jsonify({
            "error": f"Could not find {ticker} on {exchange}. "
                     f"Check the symbol — e.g. TATAMOTORS not TATA."
        }), 422
    db.add_portfolio_item(ticker, float(d.get("shares",0)), exchange, d.get("grp","Holdings"))
    return jsonify({"ok":True, "ticker":ticker})

@app.route("/api/portfolio/<raw_ticker>", methods=["DELETE"])
def api_portfolio_delete(raw_ticker):
    db.delete_portfolio_item(safe_ticker(raw_ticker))
    return jsonify({"ok":True})

# ── stock detail ──────────────────────────────────────────────────────────────
@app.route("/api/stock/<symbol>")
def api_stock(symbol):
    symbol   = symbol.upper()
    period   = request.args.get("period","1mo")
    interval = request.args.get("interval","1d")
    port     = {r["ticker"]:r for r in db.get_portfolio()}
    exchange = port.get(symbol,{}).get("exchange","NSE")
    yf_sym   = _yf_ticker(symbol, exchange)
    try:
        tk = yf.Ticker(yf_sym)
        fi = tk.fast_info
        price      = float(fi.last_price or 0)
        prev_close = float(fi.previous_close or price)
        change     = price - prev_close
        change_pct = (change/prev_close*100) if prev_close else 0
        info = {}
        try: info = tk.info or {}
        except Exception: pass
        hist = tk.history(period=period, interval=interval, auto_adjust=True)
        try: lo52 = float(fi.fifty_two_week_low or 0); hi52 = float(fi.fifty_two_week_high or 0)
        except Exception: lo52 = hi52 = 0
        return jsonify({
            "symbol": symbol, "exchange": exchange,
            "company": info.get("longName") or info.get("shortName") or symbol,
            "sector": info.get("sector",""),
            "currency": _currency_for(exchange),
            "price": price, "change": change, "change_pct": change_pct,
            "open": float(fi.open or 0), "low_52w": lo52, "high_52w": hi52,
            "volume": int(fi.last_volume or 0),
            "market_cap": info.get("marketCap") or 0,
            "pe_ratio": info.get("trailingPE") or info.get("forwardPE") or 0,
            "chart": {
                "labels": [str(d.date()) for d in hist.index],
                "prices": [round(float(v),2) for v in hist["Close"]],
            },
        })
    except Exception as e:
        abort(500, description=str(e))

# ── bookmarks ─────────────────────────────────────────────────────────────────
@app.route("/api/bookmarks", methods=["GET"])
def api_bookmarks_get():
    return jsonify(db.get_bookmarks())

@app.route("/api/bookmarks", methods=["POST"])
def api_bookmarks_add():
    d = request.json or {}
    db.add_bookmark(title=d.get("title",""), body=d.get("body",""),
                    cat=d.get("cat","world"), sentiment=d.get("sentiment","neutral"),
                    story_json=json.dumps(d.get("story",{})),
                    briefing_id=d.get("briefing_id"))
    return jsonify({"ok":True})

@app.route("/api/bookmarks/<raw_id>", methods=["DELETE"])
def api_bookmarks_delete(raw_id):
    bid=safe_id(raw_id)
    db.delete_bookmark(bid)
    return jsonify({"ok":True})

# ── settings ──────────────────────────────────────────────────────────────────
_SETTING_KEYS = [
    "tts_enabled","tts_speed","email_address","email_password","email_to",
    "smtp_server","smtp_port","ollama_model","custom_ai_prompt",
    "topic_tech","topic_finance","topic_world","topic_market",
    "weekly_digest_day","weekly_digest_time","price_alert_threshold",
]

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    result = {k: db.get_setting(k) for k in _SETTING_KEYS}
    # Return a sentinel if a password is stored in Keychain
    addr = result.get("email_address", "")
    if exporter.get_email_password(addr):
        result["email_password"] = "••••••••"
    return jsonify(result)

@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.json or {}
    # Intercept email_password — store in Keychain, not DB
    if "email_password" in data:
        addr = data.get("email_address") or db.get_setting("email_address")
        exporter.set_email_password(addr, str(data.pop("email_password")))
    for k, v in data.items():
        if k in _SETTING_KEYS:
            db.set_setting(k, str(v))
    return jsonify({"ok":True})

# ── feeds ─────────────────────────────────────────────────────────────────────
@app.route("/api/feeds", methods=["GET"])
def api_feeds_get():
    return jsonify(db.get_feeds(enabled_only=False))

@app.route("/api/feeds", methods=["POST"])
def api_feeds_add():
    d = request.json or {}
    db.add_feed(d.get("name",""), d.get("url",""), d.get("category","custom"))
    return jsonify({"ok":True})

@app.route("/api/feeds/<raw_id>", methods=["DELETE"])
def api_feeds_delete(raw_id):
    fid=safe_id(raw_id)
    db.delete_feed(fid)
    return jsonify({"ok":True})

@app.route("/api/feeds/<raw_id>/toggle", methods=["PATCH"])
def api_feeds_toggle(raw_id):
    fid=safe_id(raw_id)
    db.toggle_feed(fid, (request.json or {}).get("enabled", True))
    return jsonify({"ok":True})

# ── keywords ──────────────────────────────────────────────────────────────────
@app.route("/api/keywords", methods=["GET"])
def api_keywords_get():
    return jsonify(db.get_keywords())

@app.route("/api/keywords", methods=["POST"])
def api_keywords_add():
    kw = (request.json or {}).get("keyword","").strip()
    if kw: db.add_keyword(kw)
    return jsonify({"ok":True})

@app.route("/api/keywords/<raw_id>", methods=["DELETE"])
def api_keywords_delete(raw_id):
    kid=safe_id(raw_id)
    db.delete_keyword(kid)
    return jsonify({"ok":True})

# ── schedule ──────────────────────────────────────────────────────────────────
@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    return jsonify(db.get_schedule_times())

@app.route("/api/schedule", methods=["POST"])
def api_schedule_add():
    t = (request.json or {}).get("time_str","").strip()
    if t: db.upsert_schedule_time(t)
    return jsonify({"ok":True})

@app.route("/api/schedule/<raw_id>/toggle", methods=["PATCH"])
def api_schedule_toggle(raw_id):
    tid=safe_id(raw_id)
    db.set_schedule_time_enabled(tid, (request.json or {}).get("enabled",True))
    return jsonify({"ok":True})

@app.route("/api/schedule/<raw_id>", methods=["DELETE"])
def api_schedule_delete(raw_id):
    tid=safe_id(raw_id)
    db.delete_schedule_time(tid)
    return jsonify({"ok":True})

# ── ollama status ────────────────────────────────────────────────────────────
@app.route("/api/ollama/status")
def api_ollama_status():
    return jsonify(ai_mod.check_ollama())

# ── TTS (macOS say) ───────────────────────────────────────────────────────────
_tts_proc = None

@app.route("/api/tts/speak", methods=["POST"])
def api_tts_speak():
    global _tts_proc
    text = (request.json or {}).get("text","")
    rate = db.get_setting("tts_speed","175")
    if _tts_proc:
        try: _tts_proc.terminate()
        except Exception: pass
    _tts_proc = subprocess.Popen(["say","-r",rate,text])
    return jsonify({"ok":True})

@app.route("/api/tts/stop", methods=["POST"])
def api_tts_stop():
    global _tts_proc
    if _tts_proc:
        try: _tts_proc.terminate()
        except Exception: pass
        _tts_proc = None
    return jsonify({"ok":True})

# ── export ────────────────────────────────────────────────────────────────────
@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    bid = (request.json or {}).get("briefing_id")
    b   = db.get_briefing(bid) if bid else db.get_latest_briefing()
    if not b: return jsonify({"error":"No briefing found"}), 404
    path = exporter.export_pdf(json.loads(b["ai_json"]),
                               json.loads(b.get("annotations_json") or "{}"),
                               b["created_at"])
    subprocess.Popen(["open","-R",path])
    return jsonify({"path":path})

@app.route("/api/export/email", methods=["POST"])
def api_export_email():
    bid = (request.json or {}).get("briefing_id")
    b   = db.get_briefing(bid) if bid else db.get_latest_briefing()
    if not b: return jsonify({"error":"No briefing found"}), 404
    result = {"error":None}
    ev     = threading.Event()
    def cb(err): result["error"] = err; ev.set()
    exporter.send_email(json.loads(b["ai_json"]),
                        json.loads(b.get("annotations_json") or "{}"),
                        b["created_at"], callback=cb)
    ev.wait(timeout=30)
    return (jsonify({"error":result["error"]}), 400) if result["error"] else jsonify({"ok":True})

# ── server start ──────────────────────────────────────────────────────────────
def start_server():
    def _auto_generate():
        try:
            broadcast({"type":"briefing_status","status":"fetching"})
            items, _ = fetcher.fetch_all_feeds()
            topic_filters = {t for t in ["tech","finance","world","market"]
                             if db.get_setting(f"topic_{t}","1") == "1"}
            filtered  = [h for h in items
                         if not topic_filters or h.get("category","world") in topic_filters]
            headlines = "\n".join(f"[{h.get('category','world').upper()}] {h['title']}"
                                  for h in filtered[:80])
            broadcast({"type":"briefing_status","status":"analyzing","count":len(filtered)})
            watchlist = [r["ticker"] for r in db.get_portfolio()]
            keywords  = [k["keyword"] for k in db.get_keywords()]
            result, _ = ai_mod.analyze(headlines, watchlist + keywords)
            ts  = datetime.now().isoformat(timespec="seconds")
            bid = db.save_briefing(ts, json.dumps(filtered[:80]), json.dumps(result))
            broadcast({"type":"briefing_ready","id":bid})
        except Exception as e:
            logger.error("auto-generate: %s", e)
            broadcast({"type":"error","message":str(e)})

    sched = sched_mod.BriefingScheduler(trigger_callback=_auto_generate)
    sched.start()

    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT,
                               debug=False, use_reloader=False),
        daemon=True,
    ).start()
    return PORT
