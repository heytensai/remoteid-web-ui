"""Background retention scheduler for data archival and cleanup.

This script runs periodic archival and cleanup tasks to manage data retention
according to configuration settings. It archives old data to files and performs
gas-saving VACUUM operations on a schedule.

Usage:
    The RetentionScheduler is started as a background thread by the main Flask app.
    Run from _init_app() in app.py when the app initializes.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import psycopg2

from database import WebDatabase

logger = logging.getLogger(__name__)


class RetentionScheduler:
    """Periodically runs archival and cleanup tasks for data retention.

    Reads configuration from a WebConfig object (hot-reloadable) each cycle,
    so toggling ``enabled`` in the YAML file takes effect within one interval.
    """

    def __init__(self, config, database: WebDatabase):
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
        """Start the background retention scheduler thread (no-op if already started)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Retention scheduler thread started")

    def stop(self, join_timeout: float = 5.0):
        """Signal the thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)
        self._running = False
        logger.info("Retention scheduler thread stopped")

    def reload_config(self, config):
        """Update the config reference for the next retention cycle."""
        self._config = config

    def _run(self):
        self._running = True
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()
            logger.debug("Retention scheduler waking up")
            retention = self._config.retention

            if not retention.enabled:
                logger.debug("Retention disabled, skipping")
            else:
                try:
                    # Get current retention stats for logging
                    stats = self._database.get_retention_stats()
                    oldest_age_days = (
                        (datetime.now(timezone.utc) - stats["oldest_timestamp"].replace(tzinfo=timezone.utc))
                        .total_seconds() / 86400
                    ) if stats["oldest_timestamp"] else None
                    logger.info(
                        "Retention check: %d records, %d unique drones, "
                        "oldest: %.1f days, db size: %s"
                        if stats["oldest_timestamp"]
                        else "Retention check: %d records, %d unique drones, db size: %s",
                        stats["total_records"],
                        stats["unique_uas_count"],
                        oldest_age_days,
                        stats["database_size"],
                    )

                    # Archive old data
                    if retention.days_to_keep is not None and retention.archive_to:
                        archive_stats = self._database.archive_old_data(
                            retention.days_to_keep,
                            retention.archive_to,
                            retention.archive_format,
                        )
                        logger.info(
                            "Archival complete: %d records, %d alerts, %d sync_logs, "
                            "files: %s",
                            archive_stats["archived_records"], archive_stats["archived_alerts"],
                            archive_stats["archived_sync_logs"], archive_stats["archive_files"],
                        )

                    # Clean up old data (delete already archived data)
                    if retention.days_to_keep is not None:
                        cleanup_stats = self._database.cleanup_old_data(retention.days_to_keep)
                        if cleanup_stats:
                            logger.info(
                                "Cleanup complete: %d deleted records, %d deleted alerts",
                                cleanup_stats["deleted_records"], cleanup_stats["deleted_alerts"],
                            )

                    # Run VACUUM if enabled
                    if retention.vacuum_enabled:
                        vacuum_stats = self._database.run_vacuum()
                        logger.info(
                            "VACUUM complete: initial_records=%d, final_records=%d, removed=%d",
                            vacuum_stats["initial_records"], vacuum_stats["final_records"],
                            vacuum_stats["records_removed"],
                        )

                except (OSError, ValueError, json.JSONDecodeError, psycopg2.Error) as exc:
                    logger.exception("Retention cycle failed: %s", exc)

            self.last_run = datetime.now(timezone.utc)

            # Backpressure: only sleep for remaining interval time.
            # If a cycle takes longer than the interval, skip sleep to
            # avoid cycles stacking up.
            elapsed = time.monotonic() - cycle_start
            remaining = retention.interval - int(elapsed)
            if remaining <= 0:
                logger.warning(
                    "Retention cycle took %ds (interval=%ds), "
                    "starting next cycle immediately",
                    int(elapsed), retention.interval,
                )
            else:
                for _ in range(remaining):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        self._running = False
