"""Background sync thread for pulling data from collectors"""

import logging
import os
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime
from typing import List, Optional

from config import CollectorConfig
from database import WebDatabase

logger = logging.getLogger(__name__)


class SyncManager:
    """Manages background syncing from remote collectors"""

    def __init__(
        self,
        database: WebDatabase,
        collectors: List[CollectorConfig],
        sync_interval: int = 30,
    ):
        self.database = database
        self.collectors = collectors
        self.sync_interval = sync_interval
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._last_sync: dict = {}

    def start(self):
        """Start the sync thread"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Sync thread already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()
        logger.info("Sync thread started with %d collector(s)", len(self.collectors))

    def stop(self):
        """Stop the sync thread"""
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=5)
            logger.info("Sync thread stopped")

    def _sync_loop(self):
        """Main sync loop running in background thread"""
        # Do initial sync immediately
        self._sync_all()

        while not self._stop_event.is_set():
            # Wait for sync interval or until stopped
            if self._stop_event.wait(self.sync_interval):
                break

            if not self._stop_event.is_set():
                self._sync_all()

    def _sync_all(self):
        """Sync from all collectors"""
        for collector in self.collectors:
            try:
                self._sync_collector(collector)
                self._last_sync[collector.name] = datetime.now()
            except (OSError, subprocess.SubprocessError, sqlite3.Error):
                logger.exception("Sync failed for %s", collector.name)

    def _sync_collector(self, collector: CollectorConfig):
        """Sync from a single collector (rsync for remote, direct copy for local)"""
        # Check if local or remote
        if collector.host is None:
            # Local file - import directly
            self._sync_local_collector(collector)
        else:
            # Remote - use rsync
            self._sync_remote_collector(collector)

    def _sync_local_collector(self, collector: CollectorConfig):
        """Sync from a local file collector"""
        try:
            # Check if file exists
            if not os.path.exists(collector.remote_db_path):
                logger.error("Local database not found: %s", collector.remote_db_path)
                return

            # Import directly from local path
            count = self.database.import_from_collector(
                collector.remote_db_path, collector.name
            )
            logger.debug("Synced %d records from local %s", count, collector.name)

        except (OSError, sqlite3.Error) as e:
            logger.error("Error syncing local %s: %s", collector.name, e)

    def _sync_remote_collector(self, collector: CollectorConfig):
        """Sync from a remote collector using rsync"""
        temp_path = None
        try:
            # Create temp file for incoming data
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                temp_path = tmp.name

            # Build rsync command
            remote_path = f"{collector.host}:{collector.remote_db_path}"
            cmd = ["rsync", "-az", "--timeout=30", remote_path, temp_path]

            logger.debug("Running: %s", " ".join(cmd))

            # Run rsync
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)

            if result.returncode != 0:
                logger.error("Rsync failed: %s", result.stderr)
                return

            # Import into web database
            count = self.database.import_from_collector(temp_path, collector.name)
            logger.debug("Synced %d records from %s", count, collector.name)

        except subprocess.TimeoutExpired:
            logger.error("Rsync timeout for %s", collector.name)
        except (OSError, subprocess.SubprocessError, sqlite3.Error) as e:
            logger.error("Error syncing %s: %s", collector.name, e)
        finally:
            # Always clean up temp file
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def force_sync(self):
        """Force an immediate sync (useful for manual refresh)"""
        self._sync_all()

    def get_last_sync(self, collector_name: str) -> Optional[datetime]:
        """Get the last sync time for a specific collector"""
        return self._last_sync.get(collector_name)


def create_sync_manager(
    database: WebDatabase, collectors: List[CollectorConfig], sync_interval: int
) -> Optional[SyncManager]:
    """Factory function to create sync manager if collectors are configured"""
    if not collectors:
        logger.info("No collectors configured, sync disabled")
        return None

    return SyncManager(database, collectors, sync_interval)
