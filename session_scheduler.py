"""Background session detection scheduler that periodically runs the detection logic"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from session_detect import process_database

logger = logging.getLogger(__name__)


class SessionScheduler:
    """Periodically runs session detection against the database in a background thread.

    Reads configuration from a WebConfig object (hot-reloadable) each cycle,
    so toggling ``enabled`` in the YAML file takes effect within one interval.
    """

    def __init__(self, config, db_path: str):
        self._config = config
        self._db_path = db_path
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_run: Optional[datetime] = None
        self._running = False

    @property
    def is_running(self) -> bool:
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
                    logger.info(
                        "Session detection run starting (gap=%ds)", sd.gap_threshold
                    )
                    process_database(
                        self._db_path,
                        sd.gap_threshold,
                        dry_run=False,
                        since=self.last_run,
                    )
                    self.last_run = datetime.now(timezone.utc)
                    logger.info("Session detection run complete")
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Session detection run failed")
            else:
                logger.debug("Session detection disabled, skipping")

            # Sleep in small increments so stop() is responsive
            for _ in range(sd.interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self._running = False
