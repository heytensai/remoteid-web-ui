"""Background maintenance scheduler for periodic auth token/user cleanup."""
# pylint: disable=duplicate-code

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MaintenanceScheduler:
    """Periodically runs cleanup tasks in a background thread.

    Reads configuration from a WebConfig object (hot-reloadable) each cycle,
    so toggling cleanup flags in the YAML file takes effect within one interval.
    """

    def __init__(self, config, database):
        """create"""
        self._config = config
        self._database = database
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self.last_run: Optional[datetime] = None

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
        logger.info("Maintenance scheduler thread started")

    def stop(self, join_timeout: float = 5.0):
        """Signal the thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)
        self._running = False
        logger.info("Maintenance scheduler thread stopped")

    def _run(self):
        self._running = True
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()
            logger.debug("Maintenance scheduler waking up")
            maint = self._config.maintenance

            if maint.enabled:
                try:
                    if maint.delete_expired_tokens:
                        count = self._database.cleanup_expired_auth_tokens()
                        if count:
                            logger.info("Cleaned up %d expired auth token(s)", count)

                    if maint.delete_expired_login_tokens:
                        count = self._database.cleanup_expired_login_tokens()
                        if count:
                            logger.info("Cleaned up %d expired login token(s)", count)

                    if maint.delete_orphaned_ephemeral_users:
                        count = self._database.cleanup_orphaned_ephemeral_users()
                        if count:
                            logger.info("Cleaned up %d orphaned ephemeral user(s)", count)
                except sqlite3.Error:
                    logger.exception("Maintenance cycle failed")
            else:
                logger.debug("Maintenance disabled, skipping")

            self.last_run = datetime.now(timezone.utc)

            # Backpressure: only sleep for remaining interval time.
            elapsed = time.monotonic() - cycle_start
            remaining = maint.interval - int(elapsed)
            if remaining <= 0:
                logger.warning(
                    "Maintenance cycle took %ds (interval=%ds), "
                    "starting next cycle immediately",
                    int(elapsed), maint.interval,
                )
            else:
                for _ in range(remaining):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        self._running = False
