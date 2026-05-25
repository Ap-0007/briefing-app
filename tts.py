import threading
import logging
import queue
from typing import Optional
import pyttsx3
import db

logger = logging.getLogger(__name__)


class TTSEngine:
    def __init__(self):
        self._engine: Optional[pyttsx3.Engine] = None
        self._thread: Optional[threading.Thread] = None
        self._q: queue.Queue = queue.Queue()
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self._paused.set()  # not paused initially
        self._stopped.clear()
        self._lock = threading.Lock()
        self._current_text = ""
        self._init_engine()

    def _init_engine(self):
        try:
            self._engine = pyttsx3.init()
            self._apply_settings()
        except Exception as e:
            logger.error("TTS init failed: %s", e)
            self._engine = None

    def _apply_settings(self):
        if not self._engine:
            return
        try:
            speed = int(db.get_setting("tts_speed", "175"))
            self._engine.setProperty("rate", speed)
        except Exception as e:
            logger.error("TTS settings error: %s", e)

    def speak(self, text: str):
        if not self._is_enabled():
            return
        self._stopped.clear()
        self._paused.set()
        self._q.put(text)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        while not self._q.empty():
            if self._stopped.is_set():
                break
            text = self._q.get()
            self._current_text = text
            if not self._paused.wait(timeout=30):
                break
            if self._stopped.is_set():
                break
            if self._engine:
                try:
                    self._apply_settings()
                    self._engine.say(text)
                    self._engine.runAndWait()
                except Exception as e:
                    logger.error("TTS speak error: %s", e)
                    self._reinit()

    def _reinit(self):
        try:
            self._engine = pyttsx3.init()
            self._apply_settings()
        except Exception:
            self._engine = None

    def pause(self):
        self._paused.clear()
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass

    def resume(self):
        self._paused.set()

    def stop(self):
        self._stopped.set()
        self._paused.set()
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass

    def speak_briefing(self, ai_result: dict):
        if not self._is_enabled():
            return
        self.stop()
        lines = []
        lines.append(ai_result.get("summary", ""))
        for story in ai_result.get("stories", []):
            lines.append(story.get("title", ""))
            lines.append(story.get("body", ""))
        for line in lines:
            if line.strip():
                self._q.put(line)
        self._stopped.clear()
        self._paused.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update_speed(self, speed: int):
        db.set_setting("tts_speed", str(speed))
        self._apply_settings()

    def _is_enabled(self) -> bool:
        return db.get_setting("tts_enabled", "1") == "1"

    def is_speaking(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
