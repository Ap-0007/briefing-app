"""
Single entry point for the Aurum super-app.
Starts Flask API server + opens one native pywebview window.
"""
import logging
import time
import db
import web_server
import webview

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    db.init_db()
    port = web_server.start_server()

    # Give Flask a moment to bind
    time.sleep(0.8)

    window = webview.create_window(
        "Aurum — Morning Briefing",
        f"http://127.0.0.1:{port}",
        width=1400,
        height=900,
        min_size=(1000, 700),
        resizable=True,
    )
    webview.start()
