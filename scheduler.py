import schedule as schedule_lib
import threading
import time
import logging
from typing import Callable, Optional
import db

logger = logging.getLogger(__name__)


class BriefingScheduler:
    def __init__(self, trigger_callback: Callable, weekly_callback: Callable = None):
        self._callback = trigger_callback
        self._weekly_callback = weekly_callback
        self._scheduler = schedule_lib.Scheduler()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        self._running = True
        self._reload_schedule()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def reload(self):
        self._scheduler.clear()
        self._reload_schedule()

    def _reload_schedule(self):
        self._scheduler.clear()
        # Daily briefing times
        for t in db.get_schedule_times():
            if t["enabled"]:
                self._scheduler.every().day.at(t["time_str"]).do(self._callback)
                logger.info("Scheduled briefing at %s", t["time_str"])
        # Weekly digest — every Sunday at configured time
        if self._weekly_callback:
            day  = db.get_setting("weekly_digest_day", "Sunday").lower()
            wtime = db.get_setting("weekly_digest_time", "08:00")
            day_map = {
                "monday": self._scheduler.every().monday,
                "tuesday": self._scheduler.every().tuesday,
                "wednesday": self._scheduler.every().wednesday,
                "thursday": self._scheduler.every().thursday,
                "friday": self._scheduler.every().friday,
                "saturday": self._scheduler.every().saturday,
                "sunday": self._scheduler.every().sunday,
            }
            job = day_map.get(day, self._scheduler.every().sunday)
            job.at(wtime).do(self._weekly_callback)
            logger.info("Scheduled weekly digest: %s %s", day, wtime)

    def _loop(self):
        while self._running:
            self._scheduler.run_pending()
            time.sleep(30)
