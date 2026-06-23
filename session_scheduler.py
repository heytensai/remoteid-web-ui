"""Background session detection scheduler that periodically runs the detection logic"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from session_detect import process_database

logger = logging.getLogger(__name__)


def _oldest_undetected_timestamp(db_path: str) -> Optional[datetime]:
    """Find the oldest record timestamp that still needs session detection.

    Returns None when the database has no NULL computed_session_id
    (all sessions already detected) or when the database/column doesn't
    exist yet.
    """
    try:
        with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
            cursor = conn.execute(
                "SELECT MIN(timestamp) FROM remoteid WHERE computed_session_id IS NULL"
            )
            return cursor.fetchone()[0]
    except (sqlite3.Error, FileNotFoundError):
        return None


class SessionScheduler:
    """Periodically runs session detection against the database in a background thread.

    Reads configuration from a WebConfig object (hot-reloadable) each cycle,
    so toggling ``enabled`` in the YAML file takes effect within one interval.
    """

    def __init__(self, config, db_path: str, alert_engine=None):
        """create"""
        self._config = config
        self._db_path = db_path
        self._alert_engine = alert_engine
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Start from the oldest record that hasn't been session-detected yet,
        # so the first cycle only processes UAS that still need detection.
        # Falls back to "right now" when all records already have sessions.
        self.last_run: Optional[datetime] = (
            _oldest_undetected_timestamp(db_path) or datetime.now(timezone.utc)
        )
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return is running"""
        return self._running

    def start(self):
        """Start the background scheduler thread (no-op if already started)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Session scheduler thread started")

    def stop(self, join_timeout: float = 5.0):
        """Signal the thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)
        self._running = False
        logger.info("Session scheduler thread stopped")

    def _run(self):
        self._running = True
        while not self._stop_event.is_set():
            logger.debug("Session scheduler waking up")
            sd = self._config.session_detection

            # Apply log level to the session_detect logger
            detect_logger = logging.getLogger("session_detect")
            detect_logger.setLevel(getattr(logging, sd.log_level, logging.INFO))

            if sd.enabled:
                try:
                    summary = process_database(
                        self._db_path,
                        sd.gap_threshold,
                        dry_run=False,
                        since=self.last_run,
                    )
                    logger.info(
                        "Session detection complete (gap=%ds): %s",
                        sd.gap_threshold, summary,
                    )
                except sqlite3.Error:
                    logger.exception("Session detection run failed")
            else:
                logger.debug("Session detection disabled, skipping")

            # Run alert engine checks regardless of session detection enabled state
            if self._alert_engine:
                try:
                    self._alert_engine.evaluate_all(since=self.last_run)
                    self._alert_engine.check_stale()
                except sqlite3.Error:
                    logger.exception("Alert engine check failed")

            self.last_run = datetime.now(timezone.utc)

            # Sleep in small increments so stop() is responsive
            for _ in range(sd.interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self._running = False
