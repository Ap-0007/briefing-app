"""
Morning Briefing — main GUI (customtkinter)
"""
import threading
import logging
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import tkinter as tk

import customtkinter as ctk
from tkinter import messagebox, simpledialog

import db, fetcher, ai as ai_module, history
import portfolio as portfolio_module
import exporter, tts as tts_module
import scheduler as scheduler_module
import indices as indices_module
import calendar_data, charts as charts_module
import notifications, weekly_digest as weekly_digest_module

# ── logging ───────────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "errors.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── design tokens ─────────────────────────────────────────────────────────────
CAT_COLORS = {
    "tech":    "#3b82f6",
    "finance": "#10b981",
    "world":   "#8b5cf6",
    "market":  "#f59e0b",
    "custom":  "#6b7280",
}
SENTIMENT_COLORS = {"bullish": "#22c55e", "bearish": "#ef4444", "neutral": "#9ca3af"}
HEAT_COLORS      = {"hot": "#16a34a",     "warm": "#ca8a04",    "cold": "#dc2626"}
SIGNAL_COLORS    = {"buy": "#22c55e",     "watch": "#f59e0b",   "avoid": "#ef4444"}
DIFF_COLORS      = {"new": "#22c55e",     "trending": "#fbbf24","gone": "#6b7280"}
PERF_COLORS      = {"outperformed": "#22c55e", "underperformed": "#ef4444", "flat": "#9ca3af"}

DARK_BG   = "#0f1117"
CARD_BG   = "#1a1d27"
CARD_BG2  = "#1f2335"
BORDER    = "#2d3148"
MUTED     = "#6b7280"
TEXT      = "#e2e8f0"
SUBTEXT   = "#94a3b8"


# ── collapsible section helper ────────────────────────────────────────────────
class _Section:
    """A sidebar section with a clickable header that collapses the content."""
    def __init__(self, parent, title: str, start_open: bool = True):
        self._open = start_open
        self.header = ctk.CTkFrame(parent, fg_color="transparent")
        self.header.pack(fill="x", padx=6, pady=(10, 0))

        self._arrow = ctk.CTkLabel(
            self.header,
            text="▾ " + title if start_open else "▸ " + title,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=SUBTEXT, cursor="hand2",
            anchor="w",
        )
        self._arrow.pack(fill="x")
        self._arrow.bind("<Button-1>", self._toggle)

        # Thin separator line
        ctk.CTkFrame(parent, height=1, fg_color=BORDER).pack(
            fill="x", padx=6, pady=(2, 4))

        self.body = ctk.CTkFrame(parent, fg_color="transparent")
        if start_open:
            self.body.pack(fill="x", padx=8, pady=2)

    def _toggle(self, _=None):
        self._open = not self._open
        title = self._arrow.cget("text")[2:]  # strip arrow prefix
        if self._open:
            self._arrow.configure(text="▾ " + title)
            self.body.pack(fill="x", padx=8, pady=2)
        else:
            self._arrow.configure(text="▸ " + title)
            self.body.pack_forget()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        db.init_db()

        # ── state ─────────────────────────────────────────────────────────────
        self._current_briefing: Optional[dict] = None
        self._current_briefing_id: Optional[int] = None
        self._annotations: dict = {}
        self._diff_tags: dict = {}
        self._headlines: list[dict] = []
        self._compact_mode: bool = db.get_setting("compact_view", "0") == "1"
        self._font_size: int = int(db.get_setting("font_size", "12"))
        self._active_cat_filter: Optional[str] = None   # None = show all
        self._search_query: str = ""

        # ── sub-systems ───────────────────────────────────────────────────────
        self._tts = tts_module.TTSEngine()
        self._portfolio = portfolio_module.PortfolioTracker(
            alert_callback=self._on_price_alert)
        self._scheduler = scheduler_module.BriefingScheduler(
            trigger_callback=self._trigger_auto_briefing,
            weekly_callback=self._trigger_weekly_digest)
        self._indices_poller = indices_module.IndicesPoller(
            callback=self._on_indices_update, interval=60)

        # ── window ────────────────────────────────────────────────────────────
        theme = db.get_setting("theme", "dark")
        ctk.set_appearance_mode(theme)
        ctk.set_default_color_theme("blue")
        self.title("Morning Briefing")
        self.geometry("1360x900")
        self.minsize(1100, 720)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._bind_shortcuts()
        self._start_clock()
        self._portfolio.start()
        self._scheduler.start()
        self._indices_poller.start()
        self._refresh_portfolio_display()

        # Start local web server for Aurum stock dashboard
        try:
            import web_server as _ws
            self._web_port = _ws.start_server()
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning("Web server not started: %s", _e)
            self._web_port = 7477

        if db.get_setting("onboarding_done", "0") == "0":
            self.after(500, self._show_onboarding)

    # ═════════════════════════════════════════════════════════════════════════
    # KEYBOARD SHORTCUTS
    # ═════════════════════════════════════════════════════════════════════════

    def _bind_shortcuts(self):
        self.bind("<Command-r>",   lambda e: self._run_briefing())
        self.bind("<Control-r>",   lambda e: self._run_briefing())
        self.bind("<Command-k>",   lambda e: self._focus_search())
        self.bind("<Control-k>",   lambda e: self._focus_search())
        self.bind("<Command-b>",   lambda e: self._toggle_compact())
        self.bind("<Control-b>",   lambda e: self._toggle_compact())
        self.bind("<Escape>",      lambda e: self._tts.stop())
        self.bind("<Command-p>",   lambda e: self._export_pdf())
        self.bind("<Command-w>",   lambda e: self._open_weekly_digest())

    def _focus_search(self):
        self._search_entry.focus_set()

    # ═════════════════════════════════════════════════════════════════════════
    # UI BUILD
    # ═════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_topbar()       # row 0
        self._build_indices_bar()  # row 1
        self._build_sidebar()      # row 2 col 0
        self._build_main()         # row 2 col 1
        self._build_bottombar()    # row 3

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color=CARD_BG2)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)

        # Logo + title
        title_frame = ctk.CTkFrame(bar, fg_color="transparent")
        title_frame.grid(row=0, column=0, padx=16, pady=8)
        ctk.CTkLabel(title_frame, text="☀",
                     font=ctk.CTkFont(size=22)).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(title_frame, text="Morning Briefing",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")

        self._clock_label = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=12), text_color=SUBTEXT)
        self._clock_label.grid(row=0, column=1)

        # Right controls
        ctrl = ctk.CTkFrame(bar, fg_color="transparent")
        ctrl.grid(row=0, column=2, padx=12, pady=8)
        ctk.CTkLabel(ctrl, text="⌘R briefing  ⌘K search  ⌘B compact  Esc stop TTS",
                     font=ctk.CTkFont(size=9), text_color=MUTED).pack(side="left", padx=(0, 12))
        self._theme_btn = ctk.CTkButton(
            ctrl, text="🌙 Dark", width=88, height=30,
            fg_color=BORDER, hover_color="#374151",
            command=self._toggle_theme)
        self._theme_btn.pack(side="left")

    # ── Indices bar ───────────────────────────────────────────────────────────

    def _build_indices_bar(self):
        bar = ctk.CTkFrame(self, height=38, corner_radius=0, fg_color="#080c14")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._indices_bar = bar
        self._index_labels: dict[str, ctk.CTkLabel] = {}
        self._status_labels: dict[str, ctk.CTkLabel] = {}

        for name in list(indices_module.INDEX_SYMBOLS.keys()) + ["USD/INR"]:
            lbl = ctk.CTkLabel(bar, text=f"{name}  —",
                               font=ctk.CTkFont(size=11), text_color=MUTED)
            lbl.pack(side="left", padx=14, pady=8)
            self._index_labels[name] = lbl

        ctk.CTkFrame(bar, width=1, fg_color=BORDER).pack(
            side="left", fill="y", padx=6)

        for mkt in ("NSE", "NYSE"):
            lbl = ctk.CTkLabel(bar, text=f"{mkt}: —",
                               font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=MUTED)
            lbl.pack(side="left", padx=8)
            self._status_labels[mkt] = lbl

        # Last updated time
        self._indices_time = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=9), text_color=MUTED)
        self._indices_time.pack(side="right", padx=12)

    def _on_indices_update(self, indices: dict, status: dict):
        self.after(0, self._render_indices, indices, status)

    def _render_indices(self, indices: dict, status: dict):
        for name, lbl in self._index_labels.items():
            info = indices.get(name, {})
            if info.get("error"):
                lbl.configure(text=f"{name}  —", text_color=MUTED)
                continue
            price, pct = info["price"], info["change_pct"]
            sym   = "▲" if pct >= 0 else "▼"
            color = "#22c55e" if pct >= 0 else "#ef4444"
            if name == "USD/INR":
                lbl.configure(text=f"₹{price:.2f}/USD", text_color=TEXT)
            else:
                lbl.configure(
                    text=f"{name}  {price:,.0f}  {sym}{abs(pct):.2f}%",
                    text_color=color)
        for mkt, lbl in self._status_labels.items():
            s = status.get(mkt, "—")
            lbl.configure(text=f"{mkt}: {s}",
                          text_color="#22c55e" if s == "OPEN" else "#ef4444")
        self._indices_time.configure(
            text=f"Updated {datetime.now().strftime('%H:%M')}")

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        outer = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=CARD_BG)
        outer.grid(row=2, column=0, sticky="nsew")
        outer.grid_propagate(False)

        sb = ctk.CTkScrollableFrame(outer, corner_radius=0,
                                    fg_color="transparent", width=225)
        sb.pack(fill="both", expand=True)
        sb.grid_columnconfigure(0, weight=1)
        self._sidebar = sb

        # ── Schedule ──────────────────────────────────────────────────────────
        sec = _Section(sb, "SCHEDULE")
        self._schedule_frame = sec.body
        self._rebuild_schedule_ui()

        # ── Topics ────────────────────────────────────────────────────────────
        sec2 = _Section(sb, "TOPICS")
        self._topic_vars: dict[str, ctk.BooleanVar] = {}
        for topic in ["tech", "finance", "world", "market"]:
            val = db.get_setting(f"topic_{topic}", "1") == "1"
            var = ctk.BooleanVar(value=val)
            self._topic_vars[topic] = var
            row = ctk.CTkFrame(sec2.body, fg_color="transparent")
            row.pack(fill="x", pady=1)
            dot = ctk.CTkFrame(row, width=8, height=8, corner_radius=4,
                               fg_color=CAT_COLORS.get(topic, MUTED))
            dot.pack(side="left", padx=(2, 6))
            ctk.CTkCheckBox(
                row, text=topic.title(), variable=var,
                font=ctk.CTkFont(size=11),
                command=lambda t=topic, v=var: db.set_setting(
                    f"topic_{t}", "1" if v.get() else "0"),
            ).pack(side="left")

        # ── Keywords ──────────────────────────────────────────────────────────
        sec3 = _Section(sb, "WATCHLIST KEYWORDS")
        kw_row = ctk.CTkFrame(sec3.body, fg_color="transparent")
        kw_row.pack(fill="x", pady=(0, 4))
        kw_row.grid_columnconfigure(0, weight=1)
        self._kw_entry = ctk.CTkEntry(
            kw_row, placeholder_text="Add keyword…",
            height=28, font=ctk.CTkFont(size=11))
        self._kw_entry.grid(row=0, column=0, sticky="ew")
        self._kw_entry.bind("<Return>", lambda e: self._add_keyword())
        ctk.CTkButton(kw_row, text="+", width=28, height=28,
                      command=self._add_keyword).grid(row=0, column=1, padx=(3, 0))
        self._kw_frame = ctk.CTkFrame(sec3.body, fg_color="transparent")
        self._kw_frame.pack(fill="x")
        self._rebuild_keywords_ui()

        # ── Portfolio ─────────────────────────────────────────────────────────
        sec4 = _Section(sb, "PORTFOLIO")

        # Group tabs
        self._port_group_var = ctk.StringVar(value="Holdings")
        seg = ctk.CTkSegmentedButton(
            sec4.body, values=["Holdings", "Watchlist"],
            variable=self._port_group_var,
            command=lambda _: self._rebuild_portfolio_ui(),
            height=26, font=ctk.CTkFont(size=11))
        seg.pack(fill="x", pady=(0, 6))

        # Input row
        inp = ctk.CTkFrame(sec4.body, fg_color="transparent")
        inp.pack(fill="x")
        inp.grid_columnconfigure(0, weight=2)
        inp.grid_columnconfigure(1, weight=1)
        self._port_ticker = ctk.CTkEntry(
            inp, placeholder_text="Ticker", height=28, font=ctk.CTkFont(size=11))
        self._port_ticker.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._port_ticker.bind("<Return>", lambda e: self._add_portfolio())
        self._port_shares = ctk.CTkEntry(
            inp, placeholder_text="Qty", height=28, font=ctk.CTkFont(size=11))
        self._port_shares.grid(row=0, column=1, sticky="ew")

        exch_row = ctk.CTkFrame(sec4.body, fg_color="transparent")
        exch_row.pack(fill="x", pady=(4, 0))
        self._port_exchange = ctk.CTkOptionMenu(
            exch_row, values=["US", "NSE", "BSE"],
            height=26, width=80, font=ctk.CTkFont(size=11))
        self._port_exchange.set("US")
        self._port_exchange.pack(side="left")
        ctk.CTkButton(
            exch_row, text="＋ Add", height=26,
            font=ctk.CTkFont(size=11),
            command=self._add_portfolio).pack(side="right")

        self._port_frame = ctk.CTkFrame(sec4.body, fg_color="transparent")
        self._port_frame.pack(fill="x", pady=(6, 0))

        # Portfolio total value label
        self._port_total_label = ctk.CTkLabel(
            sec4.body, text="", font=ctk.CTkFont(size=10), text_color=SUBTEXT)
        self._port_total_label.pack(anchor="w", pady=(2, 0))

        self._rebuild_portfolio_ui()

        # ── Settings ──────────────────────────────────────────────────────────
        ctk.CTkButton(
            sb, text="⚙  Settings", height=32,
            fg_color=BORDER, hover_color="#374151",
            command=self._open_settings,
        ).pack(fill="x", padx=8, pady=(14, 8))

    # ── Main area ─────────────────────────────────────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=DARK_BG)
        main.grid(row=2, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(7, weight=1)
        self._main_frame = main

        # ── Action bar ────────────────────────────────────────────────────────
        action = ctk.CTkFrame(main, fg_color=CARD_BG2, corner_radius=0, height=52)
        action.grid(row=0, column=0, sticky="ew")
        action.grid_propagate(False)

        left = ctk.CTkFrame(action, fg_color="transparent")
        left.pack(side="left", padx=10, pady=8)

        self._get_btn = ctk.CTkButton(
            left, text="⚡  Get Briefing",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=36, width=150,
            fg_color="#1d4ed8", hover_color="#1e40af",
            command=self._run_briefing)
        self._get_btn.pack(side="left", padx=(0, 8))

        # TTS group
        tts_grp = ctk.CTkFrame(left, fg_color=BORDER, corner_radius=6)
        tts_grp.pack(side="left", padx=(0, 8))
        for txt, cmd in [("▶", self._read_aloud), ("⏸", self._tts.pause),
                         ("⏹", self._tts.stop)]:
            ctk.CTkButton(tts_grp, text=txt, width=32, height=32,
                          fg_color="transparent", hover_color="#374151",
                          font=ctk.CTkFont(size=13),
                          command=cmd).pack(side="left", padx=2, pady=2)

        # Action group
        for txt, cmd, w in [
            ("📄 PDF",     self._export_pdf,          80),
            ("✉ Email",    self._send_email,           80),
            ("📊 Weekly",  self._open_weekly_digest,   90),
        ]:
            ctk.CTkButton(left, text=txt, width=w, height=32,
                          fg_color=BORDER, hover_color="#374151",
                          command=cmd).pack(side="left", padx=2)

        right = ctk.CTkFrame(action, fg_color="transparent")
        right.pack(side="right", padx=10, pady=8)

        for txt, cmd, w in [
            ("📈 Stocks",   self._open_stock_dashboard, 82),
            ("🕐 History",  self._open_history,    82),
            ("🔖 Saved",    self._open_bookmarks,  74),
        ]:
            ctk.CTkButton(right, text=txt, width=w, height=32,
                          fg_color=BORDER, hover_color="#374151",
                          command=cmd).pack(side="right", padx=2)

        self._compact_btn = ctk.CTkButton(
            right,
            text="⊟ Compact" if not self._compact_mode else "⊞ Expand",
            width=90, height=32,
            fg_color="#1f2335" if not self._compact_mode else "#1d4ed8",
            hover_color="#374151",
            command=self._toggle_compact)
        self._compact_btn.pack(side="right", padx=2)

        # ── Progress bar (hidden until fetch) ─────────────────────────────────
        self._progress = ctk.CTkProgressBar(main, height=3, corner_radius=0)
        self._progress.set(0)
        self._progress.grid(row=1, column=0, sticky="ew")
        self._progress.grid_remove()   # hidden by default

        # ── Sentiment + heatmap row ───────────────────────────────────────────
        metrics = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=0)
        metrics.grid(row=2, column=0, sticky="ew")
        metrics.grid_columnconfigure(1, weight=1)

        # Sentiment
        sent_left = ctk.CTkFrame(metrics, fg_color="transparent")
        sent_left.grid(row=0, column=0, padx=12, pady=8, sticky="w")
        ctk.CTkLabel(sent_left, text="SENTIMENT",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=MUTED).pack(anchor="w")
        sent_row = ctk.CTkFrame(sent_left, fg_color="transparent")
        sent_row.pack(fill="x")
        ctk.CTkLabel(sent_row, text="Bear", text_color="#ef4444",
                     font=ctk.CTkFont(size=10)).pack(side="left")
        self._sentiment_bar = ctk.CTkProgressBar(sent_row, height=10, width=160)
        self._sentiment_bar.set(0.5)
        self._sentiment_bar.pack(side="left", padx=6)
        ctk.CTkLabel(sent_row, text="Bull", text_color="#22c55e",
                     font=ctk.CTkFont(size=10)).pack(side="left")
        self._sentiment_label = ctk.CTkLabel(
            sent_left, text="Neutral  50/100",
            font=ctk.CTkFont(size=10), text_color=SUBTEXT)
        self._sentiment_label.pack(anchor="w", pady=(2, 0))

        # Heatmap
        heat_right = ctk.CTkFrame(metrics, fg_color="transparent")
        heat_right.grid(row=0, column=1, padx=(0, 12), pady=8, sticky="e")
        ctk.CTkLabel(heat_right, text="SECTOR HEATMAP",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=MUTED).pack(anchor="w", pady=(0, 4))
        tiles_row = ctk.CTkFrame(heat_right, fg_color="transparent")
        tiles_row.pack()
        self._heat_tiles: dict[str, ctk.CTkLabel] = {}
        for sec in ["Technology", "Finance", "Energy",
                    "Healthcare", "Consumer", "Industrials"]:
            tile = ctk.CTkLabel(
                tiles_row, text=f"{sec[:6]}\n—",
                width=90, height=40, corner_radius=6,
                fg_color="#1f2335",
                font=ctk.CTkFont(size=10))
            tile.pack(side="left", padx=3)
            self._heat_tiles[sec] = tile

        # ── FII/DII strip ─────────────────────────────────────────────────────
        fii_row = ctk.CTkFrame(main, fg_color="#0c1020", height=28)
        fii_row.grid(row=3, column=0, sticky="ew")
        fii_row.grid_propagate(False)
        self._fiidii_label = ctk.CTkLabel(
            fii_row, text="FII/DII  —  fetching…",
            font=ctk.CTkFont(size=10), text_color=MUTED)
        self._fiidii_label.pack(side="left", padx=12, pady=4)
        ctk.CTkButton(fii_row, text="↻", width=24, height=22,
                      fg_color="transparent", hover_color=BORDER,
                      command=self._refresh_fiidii).pack(side="right", padx=8)
        self.after(2000, self._refresh_fiidii)

        # ── Summary ───────────────────────────────────────────────────────────
        sum_frame = ctk.CTkFrame(main, fg_color=CARD_BG, corner_radius=0)
        sum_frame.grid(row=4, column=0, sticky="ew")
        sum_frame.grid_columnconfigure(0, weight=1)

        sum_top = ctk.CTkFrame(sum_frame, fg_color="transparent")
        sum_top.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        sum_top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(sum_top, text="OVERVIEW",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=MUTED).grid(row=0, column=0, sticky="w")

        # Themes pills (in summary row)
        self._themes_frame = ctk.CTkFrame(sum_top, fg_color="transparent")
        self._themes_frame.grid(row=0, column=1, sticky="e")

        self._summary_text = ctk.CTkTextbox(
            sum_frame, height=60, wrap="word",
            fg_color="transparent",
            font=ctk.CTkFont(size=12),
            border_width=0)
        self._summary_text.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 10))
        self._summary_text.configure(state="disabled")

        # ── Search + filter bar ───────────────────────────────────────────────
        filter_bar = ctk.CTkFrame(main, fg_color=CARD_BG2, corner_radius=0, height=40)
        filter_bar.grid(row=5, column=0, sticky="ew")
        filter_bar.grid_propagate(False)
        filter_bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(filter_bar, text="🔍",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=(10, 4), pady=6)

        self._search_var = tk.StringVar()
        self._search_entry = ctk.CTkEntry(
            filter_bar, textvariable=self._search_var,
            placeholder_text="Search stories…  (⌘K)",
            height=28, border_width=0,
            fg_color=BORDER,
            font=ctk.CTkFont(size=11))
        self._search_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=6)
        self._search_var.trace_add("write",
                                   lambda *_: self._on_search_change())

        # Category filter pills
        pills = ctk.CTkFrame(filter_bar, fg_color="transparent")
        pills.grid(row=0, column=2, padx=8, pady=4)
        self._cat_pill_btns: dict[str, ctk.CTkButton] = {}
        for cat in ["all", "tech", "finance", "world", "market"]:
            color = CAT_COLORS.get(cat, "#1d4ed8") if cat != "all" else "#374151"
            btn = ctk.CTkButton(
                pills, text=cat.title(),
                width=58, height=26,
                fg_color=color if cat == "all" else BORDER,
                hover_color=color,
                font=ctk.CTkFont(size=10),
                command=lambda c=cat: self._set_cat_filter(c))
            btn.pack(side="left", padx=2)
            self._cat_pill_btns[cat] = btn

        # Story count badge
        self._story_count_label = ctk.CTkLabel(
            filter_bar, text="",
            font=ctk.CTkFont(size=10), text_color=MUTED)
        self._story_count_label.grid(row=0, column=3, padx=(0, 10))

        # ── Story cards ───────────────────────────────────────────────────────
        self._cards_scroll = ctk.CTkScrollableFrame(
            main, fg_color=DARK_BG, corner_radius=0)
        self._cards_scroll.grid(row=7, column=0, sticky="nsew", padx=0, pady=0)
        self._cards_scroll.grid_columnconfigure(0, weight=1)

        # Empty state (shown when no briefing yet)
        self._empty_state = ctk.CTkFrame(
            self._cards_scroll, fg_color="transparent")
        self._empty_state.pack(expand=True, pady=60)
        ctk.CTkLabel(self._empty_state, text="☀",
                     font=ctk.CTkFont(size=48)).pack()
        ctk.CTkLabel(self._empty_state, text="No briefing yet",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=SUBTEXT).pack(pady=(8, 4))
        ctk.CTkLabel(self._empty_state,
                     text="Press ⌘R or click Get Briefing to fetch today's news.",
                     font=ctk.CTkFont(size=12), text_color=MUTED).pack()

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottombar(self):
        bar = ctk.CTkFrame(self, height=28, corner_radius=0, fg_color=CARD_BG2)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        self._updated_label = ctk.CTkLabel(
            bar, text="Not yet run", font=ctk.CTkFont(size=10), text_color=MUTED)
        self._updated_label.grid(row=0, column=0, padx=12, pady=4)
        self._status_label = ctk.CTkLabel(
            bar, text="Ready", font=ctk.CTkFont(size=10), text_color=SUBTEXT)
        self._status_label.grid(row=0, column=1, pady=4)

    # ═════════════════════════════════════════════════════════════════════════
    # CLOCK
    # ═════════════════════════════════════════════════════════════════════════

    def _start_clock(self):
        def tick():
            self._clock_label.configure(
                text=datetime.now().strftime("%a, %d %b %Y  %H:%M:%S"))
            self.after(1000, tick)
        tick()

    # ═════════════════════════════════════════════════════════════════════════
    # PROGRESS BAR
    # ═════════════════════════════════════════════════════════════════════════

    def _show_progress(self):
        self._progress.grid()
        self._progress.configure(mode="indeterminate")
        self._progress.start()

    def _hide_progress(self):
        self._progress.stop()
        self._progress.grid_remove()

    # ═════════════════════════════════════════════════════════════════════════
    # FII / DII
    # ═════════════════════════════════════════════════════════════════════════

    def _refresh_fiidii(self):
        def _fetch():
            data = calendar_data.get_fii_dii()
            self.after(0, self._render_fiidii, data)
        threading.Thread(target=_fetch, daemon=True).start()

    def _render_fiidii(self, data: Optional[dict]):
        if not data:
            self._fiidii_label.configure(
                text="FII/DII  ·  NSE data unavailable", text_color=MUTED)
            return
        fii, dii = data["fii_net"], data["dii_net"]
        sf = lambda v: ("+" if v >= 0 else "") + f"₹{v:,.1f} Cr"
        fc = "#22c55e" if fii >= 0 else "#ef4444"
        dc = "#22c55e" if dii >= 0 else "#ef4444"
        self._fiidii_label.configure(
            text=f"FII: {sf(fii)}   ·   DII: {sf(dii)}   ·   {data.get('date','')}",
            text_color=TEXT)

    # ═════════════════════════════════════════════════════════════════════════
    # BRIEFING LOGIC
    # ═════════════════════════════════════════════════════════════════════════

    def _run_briefing(self):
        if self._get_btn.cget("state") == "disabled":
            return
        self._set_status("Fetching news feeds…")
        self._get_btn.configure(state="disabled", text="⏳  Fetching…")
        self._show_progress()
        threading.Thread(target=self._briefing_worker, daemon=True).start()

    def _trigger_auto_briefing(self):
        self.after(0, self._run_briefing)

    def _briefing_worker(self):
        try:
            headlines, warnings = fetcher.fetch_all_feeds(
                status_callback=lambda m: self.after(0, self._set_status, m))
            for w in warnings:
                logger.warning(w)
            if not headlines:
                self.after(0, self._set_status, "⚠  No headlines fetched")
                return

            enabled_cats = [t for t, v in self._topic_vars.items() if v.get()]
            filtered = [h for h in headlines if h.get("category") in enabled_cats]
            headlines = filtered or headlines
            self._headlines = headlines

            self.after(0, self._set_status,
                       f"AI analysing {len(headlines)} headlines…")
            keywords = [k["keyword"] for k in db.get_keywords()]
            tickers  = list(self._portfolio.get_data().keys())
            result, _ = ai_module.analyze(
                fetcher.headlines_text(headlines), keywords + tickers)

            prev_row = db.get_previous_briefing()
            prev_ai  = json.loads(prev_row["ai_json"]) if prev_row else None
            self._diff_tags = history.compute_diff(result, prev_ai)

            bid = history.save(headlines, result)
            self._current_briefing_id = bid
            self._current_briefing    = result
            self._annotations         = {}

            self.after(0, self._render_briefing, result)
            self.after(0, self._set_status, "✓  Ready")
            self.after(0, self._updated_label.configure,
                       {"text": f"Updated {datetime.now().strftime('%H:%M:%S')}"})

            notifications.notify_briefing_ready(
                result.get("sentiment", "neutral"),
                result.get("sentiment_score", 50))

            if db.get_setting("tts_enabled", "1") == "1":
                self._tts.speak_briefing(result)

        except ai_module.OllamaNotRunning:
            self.after(0, self._show_ollama_banner)
            self.after(0, self._set_status, "⚠  Ollama not running")
        except Exception as e:
            logger.error("Briefing worker: %s", e)
            self.after(0, self._set_status, f"Error: {e}")
        finally:
            self.after(0, self._hide_progress)
            self.after(0, self._get_btn.configure,
                       {"state": "normal", "text": "⚡  Get Briefing"})

    def _render_briefing(self, result: dict):
        # Hide empty state
        self._empty_state.pack_forget()

        # Sentiment
        score     = result.get("sentiment_score", 50)
        sentiment = result.get("sentiment", "neutral")
        color     = SENTIMENT_COLORS.get(sentiment, MUTED)
        self._sentiment_bar.set(score / 100)
        self._sentiment_bar.configure(progress_color=color)
        self._sentiment_label.configure(
            text=f"{sentiment.title()}  {score}/100", text_color=color)

        # Heatmap
        for sec, tile in self._heat_tiles.items():
            temp = result.get("sector_heatmap", {}).get(sec, "warm")
            tile.configure(
                fg_color=HEAT_COLORS.get(temp, "#1f2335"),
                text=f"{sec[:6]}\n{temp.upper()}",
                text_color="#fff")

        # Summary
        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0", "end")
        self._summary_text.insert("1.0", result.get("summary", ""))
        self._summary_text.configure(state="disabled")

        # Theme pills
        for w in self._themes_frame.winfo_children():
            w.destroy()
        mood = result.get("macro", {}).get("market_mood", "")
        if mood:
            _pill(self._themes_frame, f"⚡ {mood.upper()}", "#1d4ed8")
        for t in result.get("macro", {}).get("key_themes", []):
            _pill(self._themes_frame, t, BORDER)

        # Cards
        self._redraw_cards()

    def _redraw_cards(self):
        for w in self._cards_scroll.winfo_children():
            w.destroy()
        if not self._current_briefing:
            self._empty_state.pack(expand=True, pady=60)
            return

        stories = self._current_briefing.get("stories", [])
        # Apply category filter
        if self._active_cat_filter and self._active_cat_filter != "all":
            stories = [s for s in stories
                       if s.get("cat", "") == self._active_cat_filter]
        # Apply search filter
        q = self._search_query.strip().lower()
        if q:
            stories = [s for s in stories if
                       q in s.get("title", "").lower() or
                       q in s.get("body", "").lower()]

        self._story_count_label.configure(
            text=f"{len(stories)} stor{'y' if len(stories)==1 else 'ies'}")

        if not stories:
            ctk.CTkLabel(self._cards_scroll,
                         text="No stories match the current filter.",
                         text_color=MUTED,
                         font=ctk.CTkFont(size=12)).pack(pady=40)
            return
        for story in stories:
            self._add_story_card(story)

    # ── Story card ────────────────────────────────────────────────────────────

    def _add_story_card(self, story: dict):
        fs      = self._font_size
        compact = self._compact_mode
        title   = story.get("title", "")
        cat     = story.get("cat", "world")
        sent    = story.get("sentiment", "neutral")
        sc      = SENTIMENT_COLORS.get(sent, MUTED)
        acc     = CAT_COLORS.get(cat, MUTED)

        # Outer card
        card = ctk.CTkFrame(self._cards_scroll, corner_radius=8,
                            fg_color=CARD_BG, border_width=1,
                            border_color=BORDER)
        card.pack(fill="x", padx=8, pady=4)
        card.grid_columnconfigure(1, weight=1)

        # Left accent strip (category colour)
        strip = ctk.CTkFrame(card, width=4, corner_radius=0,
                             fg_color=acc)
        strip.grid(row=0, column=0, rowspan=6, sticky="ns",
                   padx=(0, 0), pady=0)
        strip.grid_propagate(False)

        # Content area
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=(10, 8), pady=(8, 6))
        content.grid_columnconfigure(0, weight=1)

        # Header: category pill | sentiment | diff tag | watchlist | bookmark
        hdr = ctk.CTkFrame(content, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        ctk.CTkLabel(hdr, text=cat.upper(),
                     fg_color=acc, text_color="#fff",
                     corner_radius=4, padx=6, pady=1,
                     font=ctk.CTkFont(size=9, weight="bold")).pack(side="left", padx=(0, 5))
        ctk.CTkLabel(hdr, text=f"● {sent.title()}",
                     text_color=sc, font=ctk.CTkFont(size=10)).pack(side="left", padx=(0, 6))

        diff = self._diff_tags.get(title, "")
        if diff == "new":
            ctk.CTkLabel(hdr, text="🆕 New",
                         text_color=DIFF_COLORS["new"],
                         font=ctk.CTkFont(size=9)).pack(side="left", padx=(0, 4))
        elif diff == "trending":
            ctk.CTkLabel(hdr, text="🔥 Trending",
                         text_color=DIFF_COLORS["trending"],
                         font=ctk.CTkFont(size=9)).pack(side="left", padx=(0, 4))

        if story.get("affects_watchlist"):
            ctk.CTkLabel(hdr, text="👁 Watchlist",
                         text_color="#f59e0b",
                         font=ctk.CTkFont(size=9)).pack(side="left")

        # Bookmark + copy buttons (right side of header)
        bm_row = ctk.CTkFrame(hdr, fg_color="transparent")
        bm_row.pack(side="right")
        ctk.CTkButton(
            bm_row, text="⧉", width=26, height=24,
            fg_color="transparent", hover_color=BORDER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._copy_story(story)).pack(side="left", padx=1)
        is_bm = db.is_bookmarked(title)
        ctk.CTkButton(
            bm_row, text="★" if is_bm else "☆", width=26, height=24,
            fg_color="transparent",
            hover_color=BORDER,
            text_color="#f59e0b" if is_bm else SUBTEXT,
            font=ctk.CTkFont(size=14),
            command=lambda s=story: self._toggle_bookmark(s)).pack(side="left", padx=1)

        # Title
        ctk.CTkLabel(content, text=title,
                     font=ctk.CTkFont(size=fs + 2, weight="bold"),
                     wraplength=680, anchor="w", justify="left",
                     text_color=TEXT).grid(row=1, column=0, sticky="ew", pady=(0, 2))

        if not compact:
            # Body
            ctk.CTkLabel(content, text=story.get("body", ""),
                         font=ctk.CTkFont(size=fs),
                         wraplength=680, anchor="w", justify="left",
                         text_color=SUBTEXT).grid(
                row=2, column=0, sticky="ew", pady=(0, 6))

            # Stock chips
            if story.get("stocks"):
                chips = ctk.CTkFrame(content, fg_color="transparent")
                chips.grid(row=3, column=0, sticky="ew", pady=(0, 4))
                for stock in story["stocks"]:
                    self._make_stock_chip(chips, stock)

            # Annotation
            ann_row = ctk.CTkFrame(content, fg_color="transparent")
            ann_row.grid(row=4, column=0, sticky="ew", pady=(2, 0))
            ann_row.grid_columnconfigure(0, weight=1)
            note    = self._annotations.get(title, "")
            ann_var = tk.StringVar(value=note)
            ctk.CTkEntry(ann_row, textvariable=ann_var,
                         placeholder_text="Add note…",
                         height=26, font=ctk.CTkFont(size=10),
                         fg_color=BORDER, border_width=0).grid(
                row=0, column=0, sticky="ew")
            ctk.CTkButton(ann_row, text="💾", width=28, height=26,
                          fg_color="transparent", hover_color=BORDER,
                          command=lambda t=title, v=ann_var: self._save_annotation(
                              t, v.get())).grid(row=0, column=1, padx=(3, 0))

    def _make_stock_chip(self, parent, stock: dict):
        ticker = stock.get("ticker", "")
        signal = stock.get("signal", "watch")
        sc     = SIGNAL_COLORS.get(signal, MUTED)
        chip   = ctk.CTkLabel(
            parent,
            text=f"{ticker}  {signal.upper()}",
            fg_color=CARD_BG2, corner_radius=6,
            padx=8, pady=2,
            text_color=sc, font=ctk.CTkFont(size=10),
            cursor="hand2")
        chip.pack(side="left", padx=(0, 4))
        _bind_tooltip(chip,
            f"{stock.get('name','')}\n"
            f"Signal: {signal.upper()}\n"
            f"Reason: {stock.get('reason','')}\n"
            f"▲ Bull: {stock.get('bull_case','')}\n"
            f"▼ Bear: {stock.get('bear_case','')}")

        # Click chip → quick add to portfolio
        chip.bind("<Button-1>", lambda e, t=ticker: self._quick_add_portfolio(t))

    def _copy_story(self, story: dict):
        text = f"{story.get('title','')}\n{story.get('body','')}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("✓  Copied to clipboard")

    def _quick_add_portfolio(self, ticker: str):
        if messagebox.askyesno("Add to Portfolio",
                               f"Add {ticker} to your portfolio?"):
            db.add_portfolio_item(ticker, 0)
            self._portfolio.refresh_now()
            self.after(2000, self._refresh_portfolio_display)
            self._set_status(f"Added {ticker} to portfolio")

    # ═════════════════════════════════════════════════════════════════════════
    # SEARCH & FILTER
    # ═════════════════════════════════════════════════════════════════════════

    def _on_search_change(self):
        self._search_query = self._search_var.get()
        self._redraw_cards()

    def _set_cat_filter(self, cat: str):
        self._active_cat_filter = None if cat == "all" else cat
        for c, btn in self._cat_pill_btns.items():
            active = (c == cat)
            color  = CAT_COLORS.get(c, "#1d4ed8") if c != "all" else "#374151"
            btn.configure(
                fg_color=color if active else BORDER,
                border_width=2 if active else 0,
                border_color=color if active else BORDER)
        self._redraw_cards()

    # ═════════════════════════════════════════════════════════════════════════
    # COMPACT VIEW
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_compact(self):
        self._compact_mode = not self._compact_mode
        db.set_setting("compact_view", "1" if self._compact_mode else "0")
        self._compact_btn.configure(
            text="⊞ Expand" if self._compact_mode else "⊟ Compact",
            fg_color="#1d4ed8" if self._compact_mode else CARD_BG2)
        self._redraw_cards()

    # ═════════════════════════════════════════════════════════════════════════
    # BOOKMARKS
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_bookmark(self, story: dict):
        title = story.get("title", "")
        if db.is_bookmarked(title):
            for bm in db.get_bookmarks():
                if bm["title"] == title:
                    db.delete_bookmark(bm["id"])
                    break
            self._set_status("Bookmark removed")
        else:
            db.add_bookmark(
                title=title, body=story.get("body", ""),
                cat=story.get("cat", "world"),
                sentiment=story.get("sentiment", "neutral"),
                story_json=json.dumps(story),
                briefing_id=self._current_briefing_id)
            self._set_status("🔖  Saved to bookmarks")
        self._redraw_cards()

    def _open_bookmarks(self):
        win = ctk.CTkToplevel(self)
        win.title("Saved Stories")
        win.geometry("640x540")
        win.grab_set()
        ctk.CTkLabel(win, text="🔖  Saved Stories",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(pady=12)
        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        bms = db.get_bookmarks()
        if not bms:
            ctk.CTkLabel(scroll, text="No saved stories yet.",
                         text_color=MUTED).pack(pady=30)
            return
        for bm in bms:
            card = ctk.CTkFrame(scroll, corner_radius=8,
                                fg_color=CARD_BG, border_width=1, border_color=BORDER)
            card.pack(fill="x", pady=4)
            card.grid_columnconfigure(0, weight=1)

            acc = CAT_COLORS.get(bm["cat"], MUTED)
            sc  = SENTIMENT_COLORS.get(bm["sentiment"], MUTED)
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 2))
            ctk.CTkLabel(hdr, text=bm["cat"].upper(),
                         fg_color=acc, text_color="#fff",
                         corner_radius=4, padx=6, pady=1,
                         font=ctk.CTkFont(size=9)).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(hdr, text=bm["sentiment"].title(),
                         text_color=sc, font=ctk.CTkFont(size=10)).pack(side="left")
            ctk.CTkLabel(hdr, text=bm["saved_at"][:16],
                         text_color=MUTED, font=ctk.CTkFont(size=9)).pack(side="right")

            ctk.CTkLabel(card, text=bm["title"],
                         font=ctk.CTkFont(size=12, weight="bold"),
                         wraplength=550, anchor="w", justify="left").grid(
                row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
            ctk.CTkLabel(card, text=bm["body"],
                         font=ctk.CTkFont(size=11), wraplength=550,
                         anchor="w", justify="left",
                         text_color=SUBTEXT).grid(
                row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
            ctk.CTkButton(card, text="🗑", width=30, height=26,
                          fg_color="#7f1d1d", hover_color="#991b1b",
                          command=lambda bid=bm["id"], c=card: [
                              db.delete_bookmark(bid), c.destroy()]).grid(
                row=1, column=1, padx=(0, 10))

    # ═════════════════════════════════════════════════════════════════════════
    # ANNOTATIONS
    # ═════════════════════════════════════════════════════════════════════════

    def _save_annotation(self, title: str, note: str):
        self._annotations[title] = note
        if self._current_briefing_id:
            history.save_annotation(self._current_briefing_id, title, note)
        self._set_status("✓  Note saved")

    # ═════════════════════════════════════════════════════════════════════════
    # SIDEBAR — SCHEDULE / KEYWORDS / PORTFOLIO
    # ═════════════════════════════════════════════════════════════════════════

    def _rebuild_schedule_ui(self):
        for w in self._schedule_frame.winfo_children():
            w.destroy()
        for t in db.get_schedule_times():
            row = ctk.CTkFrame(self._schedule_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            var = ctk.BooleanVar(value=bool(t["enabled"]))
            ctk.CTkCheckBox(row, text=t["time_str"], variable=var,
                            font=ctk.CTkFont(size=11),
                            command=lambda tid=t["id"], v=var: (
                                db.set_schedule_time_enabled(tid, v.get()),
                                self._scheduler.reload())).pack(side="left")
            ctk.CTkButton(row, text="✕", width=22, height=22,
                          fg_color="transparent", hover_color=BORDER,
                          command=lambda tid=t["id"]: self._delete_schedule_time(
                              tid)).pack(side="right")
        if len(db.get_schedule_times()) < 3:
            ctk.CTkButton(self._schedule_frame, text="＋ Add time",
                          height=24, fg_color=BORDER,
                          hover_color="#374151", font=ctk.CTkFont(size=10),
                          command=self._add_schedule_time).pack(fill="x", pady=(4, 0))

    def _add_schedule_time(self):
        t = simpledialog.askstring("Add Schedule", "Enter time (HH:MM):", parent=self)
        if t:
            db.upsert_schedule_time(t.strip())
            self._scheduler.reload()
            self._rebuild_schedule_ui()

    def _delete_schedule_time(self, tid):
        db.delete_schedule_time(tid)
        self._scheduler.reload()
        self._rebuild_schedule_ui()

    def _rebuild_keywords_ui(self):
        for w in self._kw_frame.winfo_children():
            w.destroy()
        for kw in db.get_keywords():
            row = ctk.CTkFrame(self._kw_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=kw["keyword"],
                         font=ctk.CTkFont(size=11), text_color=SUBTEXT).pack(side="left")
            ctk.CTkButton(row, text="✕", width=22, height=20,
                          fg_color="transparent", hover_color=BORDER,
                          command=lambda kid=kw["id"]: self._delete_keyword(
                              kid)).pack(side="right")

    def _add_keyword(self):
        kw = self._kw_entry.get().strip()
        if kw:
            db.add_keyword(kw)
            self._kw_entry.delete(0, "end")
            self._rebuild_keywords_ui()

    def _delete_keyword(self, kw_id):
        db.delete_keyword(kw_id)
        self._rebuild_keywords_ui()

    def _rebuild_portfolio_ui(self):
        for w in self._port_frame.winfo_children():
            w.destroy()
        data  = self._portfolio.get_data()
        items = db.get_portfolio()
        grp   = self._port_group_var.get()
        grp_items = [i for i in items if i.get("grp", "Holdings") == grp]

        total_value = 0.0
        inr_items   = 0

        if not grp_items:
            ctk.CTkLabel(self._port_frame,
                         text=f"No {grp} items. Add above.",
                         text_color=MUTED, font=ctk.CTkFont(size=10)).pack(
                anchor="w", pady=4)
            self._port_total_label.configure(text="")
            return

        for item in grp_items:
            ticker = item["ticker"]
            info   = data.get(ticker, {})
            self._make_portfolio_row(ticker, info, item)
            val = info.get("value", 0)
            if info.get("currency") == "₹":
                inr_items += 1
            total_value += val

        # Total
        if total_value > 0:
            sym = "₹" if inr_items == len(grp_items) else "$"
            self._port_total_label.configure(
                text=f"Total  {sym}{total_value:,.0f}")
        else:
            self._port_total_label.configure(text="")

    def _make_portfolio_row(self, ticker: str, info: dict, item: dict):
        price        = info.get("price", 0)
        change_pct   = info.get("change_pct", 0)
        error        = info.get("error")
        earnings_soon = info.get("earnings_soon", False)
        earnings_date = info.get("earnings_date", "")
        exchange     = info.get("exchange", item.get("exchange", "US"))
        currency     = info.get("currency", "$")

        color = "#22c55e" if change_pct >= 0 else "#ef4444"
        row   = ctk.CTkFrame(self._port_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        row.grid_columnconfigure(0, weight=1)

        if error:
            price_str = "⚠ unavailable"
            color     = "#f59e0b"
        else:
            sign      = "+" if change_pct >= 0 else ""
            p_fmt     = f"₹{price:,.0f}" if currency == "₹" else f"${price:.2f}"
            exch_tag  = f" [{exchange}]" if exchange != "US" else ""
            price_str = f"{p_fmt}  {sign}{change_pct:.1f}%{exch_tag}"

        ctk.CTkLabel(row,
                     text=f"{ticker}  {price_str}",
                     text_color=color,
                     font=ctk.CTkFont(size=10)).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(row, text="✕", width=20, height=18,
                      fg_color="transparent", hover_color=BORDER,
                      font=ctk.CTkFont(size=9),
                      command=lambda t=ticker: self._remove_portfolio(t)).grid(
            row=0, column=1)

        if earnings_soon and earnings_date:
            ctk.CTkLabel(row, text=f"📅 Earnings {earnings_date}",
                         text_color="#f59e0b",
                         font=ctk.CTkFont(size=9)).grid(row=1, column=0, sticky="w")

        # Sparkline
        from portfolio import _yf_ticker
        self._load_sparkline(row, _yf_ticker(ticker, exchange))

    def _load_sparkline(self, parent, ticker: str):
        def _fetch():
            try:
                pil_img = charts_module.sparkline(ticker)
                if pil_img:
                    self.after(0, _attach, pil_img)
            except Exception:
                pass

        def _attach(pil_img):
            try:
                img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img,
                                   size=(72, 22))
                lbl = ctk.CTkLabel(parent, image=img, text="")
                lbl.grid(row=0, column=2, padx=(4, 0))
                lbl._ctk_image = img
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _add_portfolio(self):
        ticker   = self._port_ticker.get().strip().upper()
        shares_s = self._port_shares.get().strip()
        exchange = self._port_exchange.get()
        grp      = self._port_group_var.get()
        if not ticker:
            return
        try:
            shares = float(shares_s) if shares_s else 0
        except ValueError:
            messagebox.showerror("Error", "Qty must be a number")
            return
        db.add_portfolio_item(ticker, shares, exchange, grp)
        self._port_ticker.delete(0, "end")
        self._port_shares.delete(0, "end")
        self._portfolio.refresh_now()
        self.after(2000, self._refresh_portfolio_display)

    def _remove_portfolio(self, ticker: str):
        db.delete_portfolio_item(ticker)
        charts_module.invalidate_cache(ticker)
        self._rebuild_portfolio_ui()

    def _refresh_portfolio_display(self):
        self._rebuild_portfolio_ui()
        self.after(5000, self._refresh_portfolio_display)

    # ═════════════════════════════════════════════════════════════════════════
    # PRICE ALERT
    # ═════════════════════════════════════════════════════════════════════════

    def _on_price_alert(self, ticker: str, info: dict):
        pct, price, currency = (info.get("change_pct", 0),
                                info.get("price", 0),
                                info.get("currency", "$"))
        notifications.notify_price_alert(ticker, pct, price, currency)
        self.after(0, lambda: messagebox.showinfo(
            "Price Alert",
            f"{ticker}  moved  {'+' if pct>=0 else ''}{pct:.2f}%\n"
            f"Current: {currency}{price:,.2f}"))

    # ═════════════════════════════════════════════════════════════════════════
    # EXPORT / TTS
    # ═════════════════════════════════════════════════════════════════════════

    def _read_aloud(self):
        if not self._current_briefing:
            messagebox.showinfo("Info", "Run a briefing first.")
            return
        self._tts.speak_briefing(self._current_briefing)

    def _export_pdf(self):
        if not self._current_briefing:
            messagebox.showinfo("Info", "Run a briefing first.")
            return
        self._set_status("Exporting PDF…")
        try:
            path = exporter.export_pdf(
                self._current_briefing, self._annotations,
                datetime.now().strftime("%Y-%m-%d %H:%M"))
            self._set_status(f"✓  PDF saved: {path}")
            messagebox.showinfo("PDF Exported", f"Saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))
            self._set_status("PDF export failed")

    def _send_email(self):
        if not self._current_briefing:
            messagebox.showinfo("Info", "Run a briefing first.")
            return
        self._set_status("Sending email…")
        exporter.send_email(
            self._current_briefing, self._annotations,
            datetime.now().strftime("%Y-%m-%d"),
            callback=self._email_callback)

    def _email_callback(self, error: Optional[str]):
        if error:
            self.after(0, messagebox.showerror, "Email Error", error)
            self.after(0, self._set_status, "Email failed")
        else:
            self.after(0, self._set_status, "✓  Email sent")

    # ═════════════════════════════════════════════════════════════════════════
    # HISTORY
    # ═════════════════════════════════════════════════════════════════════════

    def _open_stock_dashboard(self):
        import threading
        import webview
        def _open():
            webview.create_window(
                "Aurum — Stock Dashboard",
                f"http://127.0.0.1:{self._web_port}",
                width=1280, height=820,
                resizable=True,
            )
            webview.start()
        threading.Thread(target=_open, daemon=True).start()

    def _open_history(self):
        win = ctk.CTkToplevel(self)
        win.title("Briefing History")
        win.geometry("400x500")
        win.grab_set()
        ctk.CTkLabel(win, text="🕐  History",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=12)
        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for entry in history.list_briefings():
            ctk.CTkButton(
                scroll, text=entry["created_at"],
                fg_color=CARD_BG, hover_color=BORDER,
                anchor="w", font=ctk.CTkFont(size=11),
                command=lambda bid=entry["id"]: self._load_history_entry(bid, win),
            ).pack(fill="x", pady=2)

    def _load_history_entry(self, bid: int, win):
        row = history.load(bid)
        if not row:
            return
        self._current_briefing    = row["ai"]
        self._current_briefing_id = bid
        self._annotations         = row["annotations"]
        self._render_briefing(row["ai"])
        self._updated_label.configure(text=f"Loaded  {row['created_at']}")
        win.destroy()

    # ═════════════════════════════════════════════════════════════════════════
    # WEEKLY DIGEST
    # ═════════════════════════════════════════════════════════════════════════

    def _trigger_weekly_digest(self):
        self.after(0, self._open_weekly_digest)

    def _open_weekly_digest(self):
        win = ctk.CTkToplevel(self)
        win.title("Weekly Digest")
        win.geometry("720x640")
        win.grab_set()
        ctk.CTkLabel(win, text="📊  Weekly Digest",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=12)
        status = ctk.CTkLabel(win, text="Generating with AI…",
                              text_color=SUBTEXT, font=ctk.CTkFont(size=11))
        status.pack()
        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        scroll.grid_columnconfigure(0, weight=1)

        def _gen():
            try:
                result = weekly_digest_module.generate()
                self.after(0, _render, result)
                notifications.notify_weekly_digest()
            except Exception as e:
                self.after(0, status.configure,
                           {"text": f"Error: {e}", "text_color": "#ef4444"})

        def _render(r: dict):
            status.configure(text="")
            for w in scroll.winfo_children():
                w.destroy()

            trend = r.get("sentiment_trend", "stable")
            tc = {"improving": "#22c55e", "worsening": "#ef4444",
                  "stable": MUTED}.get(trend, MUTED)
            top_row = ctk.CTkFrame(scroll, fg_color="transparent")
            top_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            ctk.CTkLabel(top_row, text=f"Trend: {trend.title()}",
                         text_color=tc, font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
            mood = r.get("macro", {}).get("market_mood", "") if "macro" in r else ""

            sb = ctk.CTkTextbox(scroll, height=90, wrap="word",
                                fg_color=CARD_BG, border_width=0)
            sb.grid(row=1, column=0, sticky="ew", pady=4)
            sb.insert("1.0", r.get("week_summary", ""))
            sb.configure(state="disabled")

            tf = ctk.CTkFrame(scroll, fg_color="transparent")
            tf.grid(row=2, column=0, sticky="ew", pady=(0, 8))
            for t in r.get("week_themes", []):
                _pill(tf, t, BORDER)

            perf = r.get("sector_performance", {})
            if perf:
                ctk.CTkLabel(scroll, text="Sector Performance",
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=SUBTEXT).grid(
                    row=3, column=0, sticky="w", pady=(0, 4))
                pf = ctk.CTkFrame(scroll, fg_color=CARD_BG, corner_radius=6)
                pf.grid(row=4, column=0, sticky="ew", pady=(0, 8))
                for i, (sec, pv) in enumerate(perf.items()):
                    c = PERF_COLORS.get(pv, MUTED)
                    ctk.CTkLabel(pf, text=f"  {sec}",
                                 font=ctk.CTkFont(size=11),
                                 anchor="w").grid(row=i, column=0, sticky="w", padx=12, pady=2)
                    ctk.CTkLabel(pf, text=pv,
                                 text_color=c,
                                 font=ctk.CTkFont(size=11)).grid(row=i, column=1, padx=12, pady=2)

            stories = r.get("top_stories", [])
            if stories:
                ctk.CTkLabel(scroll, text="Top Stories",
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=SUBTEXT).grid(
                    row=5, column=0, sticky="w", pady=(0, 4))
                for i, s in enumerate(stories):
                    sf = ctk.CTkFrame(scroll, corner_radius=6,
                                      fg_color=CARD_BG, border_width=1,
                                      border_color=BORDER)
                    sf.grid(row=6+i, column=0, sticky="ew", pady=3)
                    sf.grid_columnconfigure(0, weight=1)
                    badge = "🔄 Still relevant" if s.get("still_relevant") else "📁 Archived"
                    ctk.CTkLabel(sf, text=badge, font=ctk.CTkFont(size=9),
                                 text_color=MUTED).grid(row=0, column=0, sticky="w", padx=10, pady=(6,0))
                    ctk.CTkLabel(sf, text=s.get("title",""),
                                 font=ctk.CTkFont(size=12, weight="bold"),
                                 wraplength=600, anchor="w").grid(
                        row=1, column=0, sticky="ew", padx=10, pady=(0,2))
                    ctk.CTkLabel(sf, text=s.get("why_it_matters",""),
                                 font=ctk.CTkFont(size=11), wraplength=600,
                                 anchor="w", text_color=SUBTEXT).grid(
                        row=2, column=0, sticky="ew", padx=10, pady=(0,8))

            if r.get("outlook"):
                off = 6 + len(stories)
                ctk.CTkLabel(scroll, text="Outlook",
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=SUBTEXT).grid(
                    row=off, column=0, sticky="w", pady=(8, 4))
                ob = ctk.CTkTextbox(scroll, height=70, wrap="word",
                                    fg_color=CARD_BG, border_width=0)
                ob.grid(row=off+1, column=0, sticky="ew", pady=4)
                ob.insert("1.0", r.get("outlook",""))
                ob.configure(state="disabled")

        threading.Thread(target=_gen, daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # SETTINGS
    # ═════════════════════════════════════════════════════════════════════════

    def _open_settings(self):
        win = ctk.CTkToplevel(self)
        win.title("Settings")
        win.geometry("540x720")
        win.grab_set()
        tab = ctk.CTkTabview(win)
        tab.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_feed_settings(tab.add("RSS Feeds"))
        self._build_email_settings(tab.add("Email"))
        self._build_tts_settings(tab.add("TTS"))
        self._build_alert_settings(tab.add("Display"))
        self._build_ai_settings(tab.add("AI / Model"))
        self._build_weekly_settings(tab.add("Weekly"))

    def _build_feed_settings(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Active Feeds",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(8, 4))
        fs = ctk.CTkScrollableFrame(frame, height=200, fg_color=CARD_BG)
        fs.pack(fill="x", pady=4)

        def _refresh():
            for w in fs.winfo_children():
                w.destroy()
            for feed in db.get_feeds(enabled_only=False):
                row = ctk.CTkFrame(fs, fg_color="transparent")
                row.pack(fill="x", pady=2)
                row.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(row, text=f"{feed['name']}",
                             font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")
                ctk.CTkLabel(row, text=feed["category"],
                             text_color=CAT_COLORS.get(feed["category"], MUTED),
                             font=ctk.CTkFont(size=9)).grid(row=0, column=1, padx=6)
                ctk.CTkButton(row, text="Delete", width=56, height=22,
                              fg_color="#7f1d1d", hover_color="#991b1b",
                              font=ctk.CTkFont(size=10),
                              command=lambda fid=feed["id"]: [
                                  db.delete_feed(fid), _refresh()]).grid(
                    row=0, column=2, padx=4)
        _refresh()

        ctk.CTkLabel(frame, text="Add Feed",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(10, 4))
        ne = ctk.CTkEntry(frame, placeholder_text="Name")
        ne.pack(fill="x", pady=2)
        ue = ctk.CTkEntry(frame, placeholder_text="URL")
        ue.pack(fill="x", pady=2)
        ce = ctk.CTkEntry(frame, placeholder_text="Category (tech/finance/world/market)")
        ce.pack(fill="x", pady=2)

        def _add():
            n, u, c = ne.get().strip(), ue.get().strip(), ce.get().strip() or "custom"
            if n and u:
                db.add_feed(n, u, c)
                ne.delete(0,"end"); ue.delete(0,"end"); ce.delete(0,"end")
                _refresh()
        ctk.CTkButton(frame, text="Add Feed", command=_add).pack(pady=6)

    def _build_email_settings(self, frame):
        notice = ctk.CTkFrame(frame, fg_color="#0f2e0f", corner_radius=6)
        notice.pack(fill="x", pady=(8, 8), padx=2)
        ctk.CTkLabel(
            notice,
            text="⚠  Gmail needs an App Password, not your regular password.\n"
                 "Google Account → Security → 2-Step Verification → App passwords",
            text_color="#86efac", font=ctk.CTkFont(size=11),
            wraplength=470, justify="left").pack(padx=10, pady=8)

        fields = [("email_address", "Gmail address", False),
                  ("email_password", "App password (16 chars)", True),
                  ("email_to",       "Send-to address",         False),
                  ("smtp_server",    "SMTP server",             False),
                  ("smtp_port",      "SMTP port",               False)]
        entries = {}
        for key, label, hide in fields:
            ctk.CTkLabel(frame, text=label,
                         font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 0))
            e = ctk.CTkEntry(frame, show="●" if hide else "",
                             height=30, font=ctk.CTkFont(size=11))
            e.insert(0, db.get_setting(key, ""))
            e.pack(fill="x", pady=2)
            entries[key] = e

        def _save():
            for key, e in entries.items():
                db.set_setting(key, e.get())
            messagebox.showinfo("Saved", "Email settings saved.")
        ctk.CTkButton(frame, text="Save", command=_save).pack(pady=10)

    def _build_tts_settings(self, frame):
        en = ctk.BooleanVar(value=db.get_setting("tts_enabled","1")=="1")
        ctk.CTkCheckBox(frame, text="Enable text-to-speech", variable=en).pack(
            anchor="w", pady=10)
        ctk.CTkLabel(frame, text="Speed").pack(anchor="w")
        sp = ctk.IntVar(value=int(db.get_setting("tts_speed","175")))
        ctk.CTkSlider(frame, from_=80, to=300, variable=sp).pack(fill="x", pady=4)
        sl = ctk.CTkLabel(frame, text=f"{sp.get()} wpm", text_color=SUBTEXT)
        sl.pack()
        sp.trace_add("write", lambda *_: sl.configure(text=f"{sp.get()} wpm"))

        def _save():
            db.set_setting("tts_enabled", "1" if en.get() else "0")
            db.set_setting("tts_speed", str(sp.get()))
            self._tts.update_speed(sp.get())
            messagebox.showinfo("Saved", "TTS settings saved.")
        ctk.CTkButton(frame, text="Save", command=_save).pack(pady=10)

    def _build_alert_settings(self, frame):
        ctk.CTkLabel(frame, text="Price alert threshold (%)",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(12,0))
        te = ctk.CTkEntry(frame, height=30)
        te.insert(0, db.get_setting("price_alert_threshold","3.0"))
        te.pack(fill="x", pady=4)

        ctk.CTkLabel(frame, text="Card font size",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(12,0))
        fv = ctk.IntVar(value=self._font_size)
        ctk.CTkSlider(frame, from_=10, to=16, variable=fv,
                      number_of_steps=6).pack(fill="x", pady=4)
        fl = ctk.CTkLabel(frame, text=f"{fv.get()} px", text_color=SUBTEXT)
        fl.pack()
        fv.trace_add("write", lambda *_: fl.configure(text=f"{fv.get()} px"))

        def _save():
            try:
                db.set_setting("price_alert_threshold", te.get())
                self._font_size = fv.get()
                db.set_setting("font_size", str(self._font_size))
                messagebox.showinfo("Saved", "Display settings saved.")
            except ValueError:
                messagebox.showerror("Error", "Enter valid numbers.")
        ctk.CTkButton(frame, text="Save", command=_save).pack(pady=10)

    def _build_ai_settings(self, frame):
        ctk.CTkLabel(frame, text="Model",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(8,2))
        mr = ctk.CTkFrame(frame, fg_color="transparent")
        mr.pack(fill="x", pady=(0,6))
        mo = ctk.CTkOptionMenu(mr, values=ai_module.AVAILABLE_MODELS, width=150)
        mo.set(db.get_setting("ollama_model", ai_module.DEFAULT_MODEL))
        mo.pack(side="left", padx=(0,8))
        ctk.CTkLabel(mr, text="llama3.2 recommended",
                     text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="left")

        ctk.CTkLabel(frame, text="Custom system prompt",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(8,2))
        ctk.CTkLabel(frame, text="Leave blank to use the built-in prompt.",
                     text_color=MUTED, font=ctk.CTkFont(size=10)).pack(anchor="w")
        pb = ctk.CTkTextbox(frame, height=250, wrap="word",
                            fg_color=CARD_BG, border_width=0)
        pb.pack(fill="both", expand=True, pady=6)
        pb.insert("1.0", db.get_setting("custom_ai_prompt",""))

        def _save():
            db.set_setting("ollama_model", mo.get())
            db.set_setting("custom_ai_prompt", pb.get("1.0","end").strip())
            messagebox.showinfo("Saved", "AI settings saved.")

        def _reset():
            pb.delete("1.0","end")
            db.set_setting("custom_ai_prompt","")
            messagebox.showinfo("Reset","Prompt reset to built-in default.")

        br = ctk.CTkFrame(frame, fg_color="transparent")
        br.pack(fill="x", pady=4)
        ctk.CTkButton(br, text="Save", command=_save).pack(side="left", padx=(0,6))
        ctk.CTkButton(br, text="Reset to Default",
                      fg_color=BORDER, hover_color="#374151",
                      command=_reset).pack(side="left")

    def _build_weekly_settings(self, frame):
        ctk.CTkLabel(frame, text="Weekly Digest Schedule",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(8,4))
        ctk.CTkLabel(frame, text="Day").pack(anchor="w", pady=(8,0))
        do = ctk.CTkOptionMenu(
            frame, values=["Monday","Tuesday","Wednesday",
                           "Thursday","Friday","Saturday","Sunday"])
        do.set(db.get_setting("weekly_digest_day","Sunday"))
        do.pack(fill="x", pady=4)
        ctk.CTkLabel(frame, text="Time (HH:MM)").pack(anchor="w", pady=(8,0))
        te = ctk.CTkEntry(frame, height=30)
        te.insert(0, db.get_setting("weekly_digest_time","08:00"))
        te.pack(fill="x", pady=4)

        ctk.CTkButton(frame, text="▶  Run Weekly Digest Now",
                      command=self._open_weekly_digest).pack(fill="x", pady=(12,4))

        def _save():
            db.set_setting("weekly_digest_day", do.get())
            db.set_setting("weekly_digest_time", te.get().strip())
            self._scheduler.reload()
            messagebox.showinfo("Saved","Weekly digest schedule saved.")
        ctk.CTkButton(frame, text="Save Schedule", command=_save).pack(pady=4)

    # ═════════════════════════════════════════════════════════════════════════
    # ONBOARDING
    # ═════════════════════════════════════════════════════════════════════════

    def _show_onboarding(self):
        win = ctk.CTkToplevel(self)
        win.title("Welcome")
        win.geometry("480x480")
        win.grab_set()

        ctk.CTkLabel(win, text="☀  Welcome to Morning Briefing",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=16)
        ok = _check_ollama()
        ctk.CTkLabel(win,
                     text="✓  Ollama is running" if ok else
                          "⚠  Ollama not detected — run: ollama serve",
                     text_color="#22c55e" if ok else "#ef4444").pack(pady=4)
        ctk.CTkLabel(win, text="Portfolio tickers (comma-separated)").pack(
            anchor="w", padx=24, pady=(12,0))
        te = ctk.CTkEntry(win, placeholder_text="AAPL, RELIANCE, INFY",
                          height=32)
        te.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(win, text="Watchlist keywords (comma-separated)").pack(
            anchor="w", padx=24, pady=(8,0))
        ke = ctk.CTkEntry(win, placeholder_text="AI, Fed, interest rates",
                          height=32)
        ke.pack(fill="x", padx=24, pady=4)

        def _finish():
            for t in te.get().split(","):
                t = t.strip().upper()
                if t:
                    db.add_portfolio_item(t, 0)
            for k in ke.get().split(","):
                k = k.strip()
                if k:
                    db.add_keyword(k)
            db.set_setting("onboarding_done","1")
            self._rebuild_portfolio_ui()
            self._rebuild_keywords_ui()
            self._portfolio.refresh_now()
            win.destroy()
        ctk.CTkButton(win, text="Get Started  →",
                      height=36, command=_finish).pack(pady=16)

    # ═════════════════════════════════════════════════════════════════════════
    # OLLAMA BANNER
    # ═════════════════════════════════════════════════════════════════════════

    def _show_ollama_banner(self):
        if hasattr(self, "_ollama_banner"):
            return
        b = ctk.CTkFrame(self._main_frame,
                         fg_color="#450a0a", corner_radius=0)
        b.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(b,
                     text="⚠  Ollama not running  ·  Start it with:  ollama serve",
                     text_color="#fca5a5",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=14, pady=6)
        ctk.CTkButton(b, text="✕", width=26, height=26,
                      fg_color="transparent", hover_color="#7f1d1d",
                      command=lambda: [b.destroy(),
                                       delattr(self,"_ollama_banner")]).pack(
            side="right", padx=8)
        self._ollama_banner = b

    # ═════════════════════════════════════════════════════════════════════════
    # THEME / STATUS / CLOSE
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_theme(self):
        cur = db.get_setting("theme", "dark")
        new = "light" if cur == "dark" else "dark"
        ctk.set_appearance_mode(new)
        db.set_setting("theme", new)
        self._theme_btn.configure(
            text="☀ Light" if new == "light" else "🌙 Dark")

    def _set_status(self, msg: str):
        self._status_label.configure(text=msg)

    def _on_close(self):
        self._tts.stop()
        self._portfolio.stop()
        self._scheduler.stop()
        self._indices_poller.stop()
        self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _pill(parent, text: str, color: str):
    ctk.CTkLabel(parent, text=text, fg_color=color,
                 corner_radius=10, padx=8, pady=2,
                 font=ctk.CTkFont(size=10)).pack(side="left", padx=2)


class _Tooltip:
    def __init__(self, widget, text: str):
        self._w = widget
        self._t = text
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self._tip:
            return
        x = self._w.winfo_rootx() + 22
        y = self._w.winfo_rooty() + 22
        self._tip = tk.Toplevel(self._w)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self._t, justify="left",
                 background="#1e2235", foreground="#e2e8f0",
                 relief="flat", font=("Helvetica", 10),
                 padx=10, pady=7, wraplength=320).pack()

    def _hide(self, _=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


def _bind_tooltip(w, text: str):
    _Tooltip(w, text)


def _check_ollama() -> bool:
    try:
        import requests
        return requests.get("http://localhost:11434", timeout=2).status_code < 500
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    db.init_db()
    app = App()
    app.mainloop()
