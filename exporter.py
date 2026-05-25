import smtplib
import threading
import logging
import os
from datetime import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fpdf import FPDF
import db

try:
    import keyring as _keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

logger = logging.getLogger(__name__)
BRIEFINGS_DIR = Path.home() / "Documents" / "briefings"


def _ensure_dir():
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)


_KEYRING_SERVICE = "aurum-briefing"


def get_email_password(email_addr: str) -> str:
    """Retrieve Gmail password from Keychain if available, else fall back to DB."""
    if _HAS_KEYRING and email_addr:
        try:
            pwd = _keyring.get_password(_KEYRING_SERVICE, email_addr)
            if pwd:
                return pwd
        except Exception:
            pass
    return db.get_setting("email_password")


def set_email_password(email_addr: str, password: str):
    """Store Gmail password in Keychain if available; always clear DB copy."""
    if _HAS_KEYRING and email_addr and password:
        try:
            _keyring.set_password(_KEYRING_SERVICE, email_addr, password)
            db.set_setting("email_password", "")  # don't store in plaintext
            return
        except Exception:
            pass
    db.set_setting("email_password", password)


def _safe(text: str) -> str:
    """Strip control characters for PDF output; Unicode is handled by DejaVuSans."""
    return str(text).replace("\x00", "").replace("\r", "")


# ── PDF ───────────────────────────────────────────────────────────────────────

class BriefingPDF(FPDF):
    def header(self):
        self.set_font("DejaVu", "B", 16)
        self.cell(0, 10, self._title_str, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 10)
        self.cell(
            0, 6, self._subtitle_str, align="C", new_x="LMARGIN", new_y="NEXT"
        )
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("DejaVu", "", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def export_pdf(ai_result: dict, annotations: dict, date_str: str = "") -> str:
    _ensure_dir()
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"briefing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    path = str(BRIEFINGS_DIR / filename)

    pdf = BriefingPDF()
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf", uni=True)
    pdf.add_font("DejaVu", "I", "DejaVuSans-Oblique.ttf", uni=True)
    pdf.add_font("DejaVu", "BI", "DejaVuSans-BoldOblique.ttf", uni=True)
    pdf._title_str = _safe(f"Morning Briefing - {date_str}")
    mood = ai_result.get("macro", {}).get("market_mood", "mixed")
    score = ai_result.get("sentiment_score", 50)
    sentiment = ai_result.get("sentiment", "neutral")
    pdf._subtitle_str = _safe(f"Sentiment: {sentiment.title()} ({score}/100)  |  Market Mood: {mood}")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Summary
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(0, 8, "Overview", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(0, 6, _safe(ai_result.get("summary", "")))
    pdf.ln(4)

    # Key themes
    themes = ai_result.get("macro", {}).get("key_themes", [])
    if themes:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 8, "Key Themes", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 10)
        pdf.multi_cell(0, 6, _safe("  •  " + "\n  •  ".join(themes)))
        pdf.ln(4)

    # Sector heatmap
    heatmap = ai_result.get("sector_heatmap", {})
    if heatmap:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 8, "Sector Heatmap", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 10)
        for sector, temp in heatmap.items():
            pdf.cell(0, 6, _safe(f"  {sector}: {temp.upper()}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # Stories
    pdf.set_font("DejaVu", "B", 11)
    pdf.cell(0, 8, "Stories", new_x="LMARGIN", new_y="NEXT")
    for story in ai_result.get("stories", []):
        pdf.set_font("DejaVu", "B", 10)
        title = _safe(f"[{story.get('cat', '').upper()}] {story.get('title', '')}")
        pdf.multi_cell(0, 6, title)
        pdf.set_font("DejaVu", "", 10)
        pdf.multi_cell(0, 6, _safe(story.get("body", "")))

        for stock in story.get("stocks", []):
            line = _safe(
                f"  {stock.get('ticker','')} ({stock.get('name','')}) - "
                f"Signal: {stock.get('signal','').upper()} - {stock.get('reason','')}"
            )
            pdf.set_font("DejaVu", "I", 9)
            pdf.multi_cell(0, 5, line)

        note = annotations.get(story.get("title", ""), "")
        if note:
            pdf.set_font("DejaVu", "BI", 9)
            pdf.multi_cell(0, 5, _safe(f"  Note: {note}"))
        pdf.ln(3)

    pdf.output(path)
    logger.info("PDF saved: %s", path)
    return path


# ── Email ─────────────────────────────────────────────────────────────────────

def _build_html(ai_result: dict, annotations: dict, date_str: str) -> str:
    mood = ai_result.get("macro", {}).get("market_mood", "mixed")
    score = ai_result.get("sentiment_score", 50)
    sentiment = ai_result.get("sentiment", "neutral")
    themes = ai_result.get("macro", {}).get("key_themes", [])

    rows = ""
    for story in ai_result.get("stories", []):
        stocks_html = ""
        for s in story.get("stocks", []):
            stocks_html += (
                f"<span style='background:#334;padding:2px 6px;border-radius:4px;"
                f"margin:2px;display:inline-block;font-size:12px;'>"
                f"{s.get('ticker','')} | {s.get('signal','').upper()}</span> "
            )
        note = annotations.get(story.get("title", ""), "")
        note_html = (
            f"<p style='font-style:italic;color:#888;font-size:12px;'>Note: {note}</p>"
            if note else ""
        )
        rows += f"""
        <div style='border:1px solid #333;border-radius:8px;padding:12px;margin:8px 0;background:#1a1a2e;'>
          <span style='background:#2a4;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;'>{story.get('cat','').upper()}</span>
          <span style='background:#244;color:#aef;padding:2px 8px;border-radius:4px;font-size:11px;margin-left:6px;'>{story.get('sentiment','neutral').title()}</span>
          <h3 style='margin:8px 0 4px;color:#fff;'>{story.get('title','')}</h3>
          <p style='color:#ccc;font-size:13px;'>{story.get('body','')}</p>
          {stocks_html}
          {note_html}
        </div>
        """

    heatmap_html = ""
    for sector, temp in ai_result.get("sector_heatmap", {}).items():
        color = {"hot": "#2a5", "warm": "#a82", "cold": "#a33"}.get(temp, "#444")
        heatmap_html += (
            f"<div style='background:{color};color:#fff;padding:8px;border-radius:6px;"
            f"text-align:center;font-size:12px;'>{sector}<br/><b>{temp.upper()}</b></div>"
        )

    themes_html = "".join(
        f"<span style='background:#334;color:#aef;padding:3px 10px;border-radius:12px;"
        f"margin:3px;display:inline-block;font-size:12px;'>{t}</span>"
        for t in themes
    )

    return f"""
    <html><body style='background:#0d0d1a;color:#eee;font-family:Arial,sans-serif;padding:20px;'>
      <h1 style='color:#7af;'>Morning Briefing — {date_str}</h1>
      <p style='color:#aaa;'>Sentiment: <b>{sentiment.title()} ({score}/100)</b> &nbsp;|&nbsp; Mood: <b>{mood}</b></p>
      <hr style='border-color:#333;'/>
      <h2 style='color:#cdf;'>Overview</h2>
      <p>{ai_result.get('summary','')}</p>
      <div style='display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;'>{themes_html}</div>
      <h2 style='color:#cdf;'>Sectors</h2>
      <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:500px;margin-bottom:20px;'>{heatmap_html}</div>
      <h2 style='color:#cdf;'>Stories</h2>
      {rows}
      <p style='color:#555;font-size:11px;margin-top:30px;'>Generated by Morning Briefing App</p>
    </body></html>
    """


def send_email(ai_result: dict, annotations: dict, date_str: str = "", callback=None):
    def _send():
        try:
            addr      = db.get_setting("email_address")
            pwd       = get_email_password(addr)
            to_addr   = db.get_setting("email_to") or addr
            smtp_host = db.get_setting("smtp_server", "smtp.gmail.com")
            smtp_port = int(db.get_setting("smtp_port", "587"))

            if not addr or not pwd:
                raise ValueError("Email credentials not configured in Settings → Email.")

            ds = date_str or datetime.now().strftime("%Y-%m-%d")

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Your Morning Briefing — {ds}"
            msg["From"]    = addr
            msg["To"]      = to_addr

            msg.attach(MIMEText(_build_html(ai_result, annotations, ds), "html"))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(addr, pwd)
                server.sendmail(addr, to_addr, msg.as_string())

            if callback:
                callback(None)
        except smtplib.SMTPAuthenticationError as e:
            msg = _friendly_auth_error(str(e))
            logger.error("Email auth failed: %s", e)
            if callback:
                callback(msg)
        except Exception as e:
            logger.error("Email send failed: %s", e)
            if callback:
                callback(str(e))

    threading.Thread(target=_send, daemon=True).start()


def _friendly_auth_error(raw: str) -> str:
    if "535" in raw or "BadCredentials" in raw or "Username and Password" in raw:
        return (
            "Gmail rejected the password.\n\n"
            "Gmail requires an App Password — your regular password won't work.\n\n"
            "How to create one:\n"
            "1. Go to myaccount.google.com\n"
            "2. Security → 2-Step Verification (must be ON)\n"
            "3. Search 'App passwords' → create one for 'Mail'\n"
            "4. Paste the 16-character code into Settings → Email → App password"
        )
    return f"Authentication failed: {raw}"
