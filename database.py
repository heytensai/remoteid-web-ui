"""Database layer for web interface"""
# pylint: disable=too-many-lines

import hashlib
import secrets as _secrets
import sqlite3
import threading
import uuid
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Current schema version — bump this and add a migration in _migrate()
SCHEMA_VERSION = 4


def _adapt_datetime(dt: datetime) -> str:
    """Adapt datetime to ISO format string for SQLite"""
    return dt.isoformat()


def _convert_datetime(s: bytes) -> datetime:
    """Convert ISO format string from SQLite to datetime"""
    return datetime.fromisoformat(s.decode())


# Register adapters for datetime handling
sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("DATETIME", _convert_datetime)


class WebDatabase:
    """Manages SQLite database for web interface"""

    def __init__(self, db_path: str):
        """Initialize the web database, creating schema if needed."""
        self.db_path = Path(db_path)
        self._tlocal = threading.local()
        self._init_db()

    def _init_db(self):
        """Initialize the database schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_conn()
        # Enable WAL mode for better concurrent access
        conn.execute("PRAGMA journal_mode=WAL")

        # Create remoteid table with source column
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS remoteid(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            timestamp DATETIME,
            mac_address TEXT,
            uas_id TEXT,
            session_id TEXT,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            height REAL,
            height_type TEXT,
            operator_id TEXT,
            operator_latitude REAL,
            operator_longitude REAL,
            computed_session_id TEXT,
            session_detected_at DATETIME,
            collector_latitude REAL,
            collector_longitude REAL
        )
        """
        )

        # Create schema version tracking table
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_version(
            version INTEGER NOT NULL
        )
        """
        )

        # Create sync log table
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            last_sync DATETIME,
            records_imported INTEGER
        )
        """
        )

        # Create session tracking table for real-time detection
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_tracking(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uas_id TEXT UNIQUE,
            last_seen DATETIME,
            current_session_id TEXT,
            updated_at DATETIME
        )
        """
        )

        # Create geozone events table for alerting
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS geozone_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uas_id TEXT NOT NULL,
            geozone_name TEXT NOT NULL,
            entered_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL,
            exited_at DATETIME,
            exited_reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Create collector positions table
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collector_positions(
            name TEXT PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Create users table
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            role_name TEXT NOT NULL DEFAULT 'guest',
            is_ephemeral INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            login_token_hash TEXT UNIQUE,
            login_token_expires_at DATETIME,
            auth_method TEXT NOT NULL DEFAULT 'ephemeral',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Create auth_tokens table
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_tokens(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
        )

        # Create push subscriptions table for Web Push notifications
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh_key TEXT NOT NULL,
            auth_key TEXT NOT NULL,
            user_agent TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        )

        # Create indexes
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_uas_time ON remoteid(uas_id, timestamp)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON remoteid(source)")
        conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_uas_time_unique "
        "ON remoteid(uas_id, timestamp)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timestamp ON remoteid(timestamp)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_computed_session ON remoteid(computed_session_id)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_geozone_events_active "
        "ON geozone_events(uas_id, geozone_name, exited_at)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_geozone_events_stale "
        "ON geozone_events(exited_at, last_seen_at)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash "
        "ON auth_tokens(token_hash)"
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user "
        "ON auth_tokens(user_id)"
        )

        # Materialized latest_positions table — O(sessions) instead of O(rows)
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_positions(
            uas_id TEXT NOT NULL,
            computed_session_id TEXT NOT NULL DEFAULT '',
            max_ts DATETIME NOT NULL,
            min_ts DATETIME NOT NULL,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            height REAL,
            height_type TEXT,
            operator_id TEXT,
            operator_latitude REAL,
            operator_longitude REAL,
            source TEXT,
            collector_latitude REAL,
            collector_longitude REAL,
            PRIMARY KEY (uas_id, computed_session_id)
        )
        """
        )
        conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lp_max_ts "
        "ON latest_positions(max_ts)"
        )

        conn.commit()
        self._ensure_schema_version(conn)
        logger.debug("Database initialized at %s", self.db_path)

    def _ensure_schema_version(self, conn: sqlite3.Connection):
        """Check the schema version and apply any pending migrations.

        The ``_schema_version`` table is guaranteed to exist by
        ``_init_db()`` (``CREATE TABLE IF NOT EXISTS``).
        """
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM _schema_version"
        ).fetchone()[0]

        if current < SCHEMA_VERSION:
            self._migrate(conn, current, SCHEMA_VERSION)
            conn.execute(
                "INSERT INTO _schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            conn.commit()

    @staticmethod
    def _migrate(  # pylint: disable=unused-argument
        conn: sqlite3.Connection, from_version: int, to_version: int
    ):
        """Apply schema migrations between *from_version* and *to_version*.

        Each ``if version == X`` branch applies the changes needed to go
        from version X to version X+1.  The version table is updated
        separately by the caller.
        """
        if from_version == 1:
            conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                role_name TEXT NOT NULL DEFAULT 'guest',
                is_ephemeral INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                login_token_hash TEXT UNIQUE,
                login_token_expires_at DATETIME,
                auth_method TEXT NOT NULL DEFAULT 'ephemeral',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
            )
            conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash "
                "ON auth_tokens(token_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user "
                "ON auth_tokens(user_id)"
            )
            from_version = 2

        if from_version == 2:
            WebDatabase._ensure_latest_positions_table(conn)
            WebDatabase._backfill_latest_positions(conn)
            from_version = 3

        if from_version == 3:
            for table in ("remoteid", "latest_positions"):
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN height REAL")
                except sqlite3.OperationalError:
                    pass  # column already exists
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN height_type TEXT")
                except sqlite3.OperationalError:
                    pass
            from_version = 4

    @staticmethod
    def _ensure_latest_positions_table(conn: sqlite3.Connection):
        """Create the latest_positions table and index (idempotent)."""
        conn.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_positions(
            uas_id TEXT NOT NULL,
            computed_session_id TEXT NOT NULL DEFAULT '',
            max_ts DATETIME NOT NULL,
            min_ts DATETIME NOT NULL,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            height REAL,
            height_type TEXT,
            operator_id TEXT,
            operator_latitude REAL,
            operator_longitude REAL,
            source TEXT,
            collector_latitude REAL,
            collector_longitude REAL,
            PRIMARY KEY (uas_id, computed_session_id)
        )
        """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lp_max_ts "
            "ON latest_positions(max_ts)"
        )

    @staticmethod
    def _backfill_latest_positions(conn: sqlite3.Connection):
        """Populate latest_positions from existing remoteid data (one-time migration)."""
        conn.execute(
        """
        INSERT INTO latest_positions
            (uas_id, computed_session_id, max_ts, min_ts,
             latitude, longitude, altitude, height, height_type,
             operator_id, operator_latitude, operator_longitude, source,
             collector_latitude, collector_longitude)
        SELECT
            uas_id,
            COALESCE(computed_session_id, ''),
            timestamp,
            MIN(timestamp) OVER (
                PARTITION BY uas_id, COALESCE(computed_session_id, '')
            ),
            latitude, longitude, altitude, height, height_type,
            operator_id, operator_latitude, operator_longitude, source,
            collector_latitude, collector_longitude
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY uas_id, COALESCE(computed_session_id, '')
                    ORDER BY timestamp DESC
                ) as rn
            FROM remoteid
        )
        WHERE rn = 1
        """
        )

    def rebuild_latest_positions(self, uas_ids: Optional[List[str]] = None):
        """Rebuild latest_positions from remoteid for specific UAS IDs (or all).

        Called after session re-detection to fix up the materialized table.
        """
        conn = self._get_conn()
        if uas_ids:
            placeholders = ','.join('?' for _ in uas_ids)
            conn.execute(
                f"DELETE FROM latest_positions WHERE uas_id IN ({placeholders})",
                uas_ids,
            )
            conn.execute(
                f"""
                INSERT INTO latest_positions
                    (uas_id, computed_session_id, max_ts, min_ts,
                     latitude, longitude, altitude, height, height_type,
                     operator_id, operator_latitude, operator_longitude, source,
                     collector_latitude, collector_longitude)
                SELECT
                    uas_id,
                    COALESCE(computed_session_id, ''),
                    timestamp,
                    MIN(timestamp) OVER (
                        PARTITION BY uas_id, COALESCE(computed_session_id, '')
                    ),
                    latitude, longitude, altitude, height, height_type,
                    operator_id, operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY uas_id, COALESCE(computed_session_id, '')
                            ORDER BY timestamp DESC
                        ) as rn
                    FROM remoteid
                    WHERE uas_id IN ({placeholders})
                )
                WHERE rn = 1
                """,
                uas_ids,
            )
        else:
            conn.execute("DELETE FROM latest_positions")
            self._backfill_latest_positions(conn)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local database connection, creating one if needed.

        Each calling thread gets its own persistent connection so there is
        no cross-thread sharing and no repeated connect/disconnect overhead.
        """
        conn = getattr(self._tlocal, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=10,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._tlocal.conn = conn
        return conn

    @staticmethod
    def _validate_record(row: tuple) -> Optional[tuple]:
        """Validate and sanitize a record before import.

        Returns sanitized tuple or None if record is invalid.
        row: (id, timestamp, mac_address, uas_id, session_id, lat, lon, alt, op_id, op_lat, op_lon, height, height_type)
        """
        # pylint: disable=too-many-return-statements,too-many-branches

        # timestamp must be present
        if not row[1]:
            return None

        # uas_id must be present
        if not row[3]:
            return None

        # latitude and longitude must be valid numbers
        try:
            lat = float(row[5]) if row[5] is not None else None
            if lat is not None and (lat < -90 or lat > 90):
                logger.debug(
                    "Invalid latitude %s for uas_id %s, skipping", row[5], row[3]
                )
                return None
        except (TypeError, ValueError):
            logger.debug(
                "Non-numeric latitude %s for uas_id %s, skipping", row[5], row[3]
            )
            return None

        try:
            lon = float(row[6]) if row[6] is not None else None
            if lon is not None and (lon < -180 or lon > 180):
                logger.debug(
                    "Invalid longitude %s for uas_id %s, skipping", row[6], row[3]
                )
                return None
        except (TypeError, ValueError):
            logger.debug(
                "Non-numeric longitude %s for uas_id %s, skipping", row[6], row[3]
            )
            return None

        # altitude is optional but must be numeric if present
        alt = None
        if row[7] is not None:
            try:
                alt = float(row[7])
            except (TypeError, ValueError):
                logger.debug(
                    "Non-numeric altitude %s for uas_id %s, skipping", row[7], row[3]
                )
                return None

        # operator coordinates are optional but must be valid if present
        op_lat = None
        if row[9] is not None:
            try:
                op_lat = float(row[9])
                if op_lat < -90 or op_lat > 90:
                    op_lat = None
            except (TypeError, ValueError):
                op_lat = None

        op_lon = None
        if row[10] is not None:
            try:
                op_lon = float(row[10])
                if op_lon < -180 or op_lon > 180:
                    op_lon = None
            except (TypeError, ValueError):
                op_lon = None

        # height is optional but must be numeric if present
        height = None
        if len(row) > 11 and row[11] is not None:
            try:
                height = float(row[11])
            except (TypeError, ValueError):
                logger.debug(
                    "Non-numeric height %s for uas_id %s, skipping", row[11], row[3]
                )
                return None

        # height_type is optional text
        height_type = None
        if len(row) > 12 and row[12] is not None:
            height_type = str(row[12]).strip()
            if not height_type:
                height_type = None

        return (
            row[1],  # timestamp
            row[2] if len(row) > 2 else None,  # mac_address
            row[3],  # uas_id
            row[4] if len(row) > 4 else None,  # session_id
            lat,  # latitude
            lon,  # longitude
            alt,  # altitude
            row[8] if len(row) > 8 else None,  # operator_id
            op_lat,  # operator_latitude
            op_lon,  # operator_longitude
            height,  # height
            height_type,  # height_type
        )

    def import_from_collector(
        self, source_db_path: str, source_name: str,
        session_gap_threshold: int = 600,
        source_tz: Optional[str] = None,
        collector_lat: Optional[float] = None,
        collector_lon: Optional[float] = None,
    ) -> int:
        # pylint: disable=too-many-locals,too-many-positional-arguments
        """Import new records from a collector's database with session detection

        Args:
            source_db_path: Path to the source collector database
            source_name: Name of the source collector
            session_gap_threshold: Time gap in seconds to trigger a new session (default: 600)
            source_tz: IANA timezone name (e.g. "America/Denver") to interpret
                       naive timestamps from this source. If None, naive
                       timestamps are assumed to be UTC.
            collector_lat: Collector latitude to stamp on each record (optional)
            collector_lon: Collector longitude to stamp on each record (optional)
        """
        count = 0
        skipped = 0
        try:
            # Get last sync time for this source
            last_sync = self._get_last_sync(source_name)

            # Connect to source database and query new records
            columns = (
                "id, timestamp, mac_address, uas_id, session_id, latitude, longitude, "
                "altitude, operator_id, operator_latitude, operator_longitude"
            )

            with sqlite3.connect(
                source_db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5
            ) as src_conn:
                if last_sync:
                    cursor = src_conn.execute(
                        f"SELECT {columns} FROM remoteid WHERE timestamp > ? "
                        "ORDER BY uas_id, timestamp",
                        (last_sync,),
                    )
                else:
                    cursor = src_conn.execute(
                        f"SELECT {columns} FROM remoteid ORDER BY uas_id, timestamp"
                    )

                # Import into web database using named parameters
                dest_conn = self._get_conn()
                # Track session state per UAS for this import batch
                uas_sessions = {}
                affected_uas_ids = set()

                for row in cursor:
                    # Skip if already exists (check uas_id + timestamp)
                    existing = dest_conn.execute(
                        "SELECT 1 FROM remoteid WHERE uas_id = ? AND timestamp = ?",
                        (row[3], row[1]),
                    ).fetchone()

                    if not existing:
                        # Validate and sanitize the record
                        validated = self._validate_record(row)
                        if validated is None:
                            skipped += 1
                            continue

                        timestamp = validated[0]
                        if source_tz and timestamp.tzinfo is None:
                            timestamp = timestamp.replace(
                                tzinfo=ZoneInfo(source_tz)
                            ).astimezone(timezone.utc)
                        uas_id = validated[2]

                        # Determine computed_session_id based on time gap
                        computed_session_id = self._detect_session(
                            dest_conn,
                            uas_id,
                            timestamp,
                            uas_sessions,
                            session_gap_threshold,
                        )

                        dest_conn.execute(
                            """
                            INSERT INTO remoteid
                            (source, timestamp, mac_address, uas_id, session_id,
                             latitude, longitude, altitude, height, height_type,
                             operator_id,
                             operator_latitude, operator_longitude,
                             computed_session_id, session_detected_at,
                             collector_latitude, collector_longitude)
                            VALUES (:source, :timestamp, :mac_address, :uas_id, :session_id,
                                    :latitude, :longitude, :altitude, :height, :height_type,
                                    :operator_id,
                                    :operator_latitude, :operator_longitude,
                                    :computed_session_id, :session_detected_at,
                                    :collector_latitude, :collector_longitude)
                        """,
                            {
                                "source": source_name,
                                "timestamp": timestamp,
                                "mac_address": validated[1],
                                "uas_id": uas_id,
                                "session_id": validated[3],
                                "latitude": validated[4],
                                "longitude": validated[5],
                                "altitude": validated[6],
                                "height": validated[10],
                                "height_type": validated[11],
                                "operator_id": validated[7],
                                "operator_latitude": validated[8],
                                "operator_longitude": validated[9],
                                "computed_session_id": computed_session_id,
                                "session_detected_at": datetime.now(timezone.utc),
                                "collector_latitude": collector_lat,
                                "collector_longitude": collector_lon,
                            },
                        )
                        count += 1
                        affected_uas_ids.add(uas_id)

                        # Update session tracking for this batch
                        uas_sessions[uas_id] = (timestamp, computed_session_id)

                dest_conn.commit()

            # Update materialized latest_positions for affected UAS IDs
            if count > 0:
                self.rebuild_latest_positions(list(affected_uas_ids))

            # Update sync log
            self._update_sync_log(source_name, count)
            if skipped > 0:
                logger.info(
                    "Imported %d records from %s (skipped %d invalid)",
                    count,
                    source_name,
                    skipped,
                )
            else:
                logger.info("Imported %d records from %s", count, source_name)
            return count

        except sqlite3.Error as e:
            logger.error("Database import error from %s: %s", source_name, e)
            return 0

    def _detect_session(
        self,
        conn: sqlite3.Connection,
        uas_id: str,
        timestamp: datetime,
        uas_sessions: dict,
        gap_threshold: int,
    ) -> str:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Detect session based on time gap from last seen record

        Args:
            conn: Database connection
            uas_id: The UAS ID
            timestamp: Current record timestamp
            uas_sessions: Dictionary tracking session state for current import batch
            gap_threshold: Gap threshold in seconds

        Returns:
            Computed session ID string
        """
        # First check if we have this UAS in the current batch
        if uas_id in uas_sessions:
            last_seen, current_session = uas_sessions[uas_id]
            gap = (timestamp - last_seen).total_seconds()
            if gap <= gap_threshold:
                return current_session

            # New session due to gap
            new_session = f"session_{uuid.uuid4().hex[:12]}"
            logger.debug(
                "New session for %s at %s (gap: %.1fs)", uas_id, timestamp, gap
            )
            return new_session

        # Check the database for most recent record of this UAS
        cursor = conn.execute(
            "SELECT timestamp, computed_session_id FROM remoteid "
            "WHERE uas_id = ? ORDER BY timestamp DESC LIMIT 1",
            (uas_id,),
        )
        row = cursor.fetchone()

        if row:
            last_seen = row[0]
            if isinstance(last_seen, datetime) and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            last_session = row[1]
            gap = (timestamp - last_seen).total_seconds()

            if gap <= gap_threshold and last_session:
                return last_session

            # New session
            new_session = f"session_{uuid.uuid4().hex[:12]}"
            logger.debug(
                "New session for %s at %s (gap: %.1fs)", uas_id, timestamp, gap
            )
            return new_session

        # First time seeing this UAS
        return f"session_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _sanitize_record(record: dict) -> dict:
        """Sanitize a record for API response, ensuring safe values.

        Converts any non-numeric values to None, ensuring frontend doesn't crash.
        """
        sanitized = {}
        coord_keys = [
            "latitude",
            "longitude",
            "altitude",
            "height",
            "operator_latitude",
            "operator_longitude",
        ]
        for key, value in record.items():
            if key in coord_keys:
                sanitized[key] = WebDatabase._sanitize_float(value, key)
            elif key in ("timestamp", "session_start"):
                sanitized[key] = WebDatabase._sanitize_timestamp(value)
            else:
                sanitized[key] = value
        return sanitized

    @staticmethod
    def _sanitize_float(value, key) -> Optional[float]:
        """Sanitize a coordinate or altitude value."""
        if value is None:
            return None
        try:
            fval = float(value)
            if key in ("latitude", "operator_latitude") and (fval < -90 or fval > 90):
                return None
            if key in ("longitude", "operator_longitude") and (
                fval < -180 or fval > 180
            ):
                return None
            return fval
        except (TypeError, ValueError):
            logger.debug("Non-numeric value for %s, setting to None", key)
            return None

    @staticmethod
    def _sanitize_timestamp(value) -> Optional[str]:
        """Sanitize a timestamp value, ensuring timezone info is present.

        Naive datetimes are assumed to be UTC (the standard for stored data).
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.isoformat() + "Z"
            return value.isoformat()
        # Handle string values from SQL aggregates - add Z if it looks like a naive datetime
        s = str(value)
        if s and len(s) >= 19:
            # Check if it matches ISO datetime pattern YYYY-MM-DDTHH:MM:SS
            is_datetime = (s[4] == '-' and s[7] == '-' and s[10] == 'T'
                        and s[13] == ':' and s[16] == ':')
            has_tz = s.endswith("Z") or "+" in s[-6:]
            if is_datetime and not has_tz:
                return s + "Z"
        return s

    def _get_last_sync(self, source_name: str) -> Optional[datetime]:
        """Get the last sync time for a source"""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT last_sync FROM sync_log WHERE source = ? ORDER BY last_sync DESC LIMIT 1",
            (source_name,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _update_sync_log(self, source_name: str, count: int):
        """Update the sync log for a source"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sync_log (source, last_sync, records_imported) VALUES (?, ?, ?)",
            (source_name, datetime.now(timezone.utc), count),
        )
        conn.commit()

    def log_submission(self, source_name: str, records_count: int):
        """Log an HTTP data submission to the sync log"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sync_log (source, last_sync, records_imported) VALUES (?, ?, ?)",
            (source_name, datetime.now(timezone.utc), records_count),
        )
        conn.commit()

    def cleanup_expired_auth_tokens(self) -> int:
        """Delete session tokens whose expiry has passed.

        Returns the number of rows deleted.
        """
        conn = self._get_conn()
        count = conn.execute(
            "DELETE FROM auth_tokens WHERE expires_at < ?",
            (datetime.now(timezone.utc),),
        ).rowcount
        conn.commit()
        return count

    def cleanup_expired_login_tokens(self) -> int:
        """Delete pre-created user records whose one-time login token has expired.

        This covers both:
        - Users with an expired login_token_expires_at (login window closed)
        - Deactivated users (is_active = 0) whose accounts were soft-deleted

        Returns the number of rows deleted.
        """
        conn = self._get_conn()
        count = conn.execute(
            "DELETE FROM users WHERE login_token_expires_at < ?",
            (datetime.now(timezone.utc),),
        ).rowcount
        # Also clean up dangling auth_tokens for the deleted users
        # (SQLite ON DELETE CASCADE is not set, so we do it manually)
        conn.commit()
        return count

    def cleanup_orphaned_ephemeral_users(self) -> int:
        """Delete ephemeral (guest) users whose session tokens have all expired.

        A guest user with no valid session tokens can never authenticate again,
        so its record and any leftover auth_tokens are safe to remove.

        Returns the number of users deleted.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        # Find ephemeral users whose MAX(expires_at) < now (no valid tokens)
        count = conn.execute(
            """
            DELETE FROM users WHERE id IN (
                SELECT u.id FROM users u
                LEFT JOIN auth_tokens t ON t.user_id = u.id
                WHERE u.is_ephemeral = 1
                GROUP BY u.id
                HAVING MAX(t.expires_at) IS NULL
                    OR MAX(t.expires_at) < ?
            )
            """,
            (now,),
        ).rowcount
        # Clean up any orphaned auth_tokens (users deleted but tokens remain)
        conn.execute(
            "DELETE FROM auth_tokens WHERE user_id NOT IN (SELECT id FROM users)"
        )
        conn.commit()
        return count

    def get_all_sources(self) -> List[Dict]:
        """Get all unique data sources from sync_log and remoteid tables.

        Each entry includes ``last_data`` (most recent position timestamp)
        so callers don't need an extra per-source query.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        # Get sources from sync_log
        sync_cursor = conn.execute(
            "SELECT source, MAX(last_sync) as last_sync, "
            "SUM(records_imported) as total_records "
            "FROM sync_log GROUP BY source ORDER BY source"
        )
        sync_rows = sync_cursor.fetchall()

        # Also get sources that have data in remoteid but may not be in sync_log
        data_cursor = conn.execute(
            "SELECT source, MAX(timestamp) as last_ts "
            "FROM remoteid WHERE source IS NOT NULL "
            "GROUP BY source ORDER BY source"
        )
        data_rows = data_cursor.fetchall()

        def _parse_ts(val):
            """Parse a timestamp value into a datetime object."""
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    return None
            return None

        # Build last_data lookup from remoteid data
        data_lookup = {}
        for row in data_rows:
            data_lookup[row["source"]] = _parse_ts(row["last_ts"])

        # Merge: sync_log entries take priority, supplement with remoteid-only sources
        source_map = {}
        for row in sync_rows:
            name = row["source"]
            source_map[name] = {
                "source": name,
                "last_sync": _parse_ts(row["last_sync"]),
                "total_records": row["total_records"],
                "last_data": data_lookup.get(name),
            }

        for row in data_rows:
            name = row["source"]
            if name not in source_map:
                source_map[name] = {
                    "source": name,
                    "last_sync": _parse_ts(row["last_ts"]),
                    "total_records": None,
                    "last_data": _parse_ts(row["last_ts"]),
                }

        return sorted(source_map.values(), key=lambda s: s["source"])

    def _get_drones_query(
        self, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Return latest position per session in the time window.

        Uses the materialized ``latest_positions`` table (O(sessions) instead of
        O(rows) in the time window).
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT
                uas_id,
                NULLIF(computed_session_id, '') as computed_session_id,
                max_ts as timestamp,
                min_ts as session_start,
                latitude, longitude, altitude, height, height_type,
                operator_id,
                operator_latitude, operator_longitude, source,
                collector_latitude, collector_longitude
            FROM latest_positions
            WHERE max_ts BETWEEN ? AND ?
            ORDER BY uas_id, computed_session_id
        """,
            (start_time, end_time),
        )
        return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_drones(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get list of unique drones seen in time window with latest positions"""
        return self._get_drones_query(start_time, end_time)

    def get_sessions_for_uas(
        self, uas_id: str, limit: int = 10, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        """Get all sessions for a UAS ID with pagination, ignoring time constraints."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            "SELECT COUNT(*) FROM latest_positions WHERE uas_id = ?",
            (uas_id,),
        )
        total = cursor.fetchone()[0]

        cursor = conn.execute(
            """
            SELECT
                uas_id,
                NULLIF(computed_session_id, '') as computed_session_id,
                max_ts as timestamp,
                min_ts as session_start,
                latitude, longitude, altitude, height, height_type,
                operator_id, operator_latitude, operator_longitude, source,
                collector_latitude, collector_longitude
            FROM latest_positions
            WHERE uas_id = ?
            ORDER BY max_ts DESC
            LIMIT ? OFFSET ?
            """,
            (uas_id, limit, offset),
        )
        sessions = [self._sanitize_record(dict(row)) for row in cursor.fetchall()]
        return sessions, total

    def get_drones_incremental(
        self,
        start_time: datetime,
        end_time: datetime,
        known_timestamps: Dict[str, str],
    ) -> List[Dict]:
        """Get drones that have newer data than the client's known timestamps.

        Uses the materialized ``latest_positions`` table instead of GROUP BY
        on the full ``remoteid`` table, reducing scans from O(rows-in-window)
        to O(sessions).

        Args:
            start_time: Start of time window
            end_time: End of time window
            known_timestamps: Map of "uas_id:session_id" -> last known timestamp ISO string

        Returns:
            List of drones with data newer than known_timestamps, or all drones if known_timestamps is empty
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        if not known_timestamps:
            return self._get_drones_query(start_time, end_time)

        # Early exit: nothing changed since client's latest known
        most_recent = self.get_most_recent_timestamp()
        if most_recent and isinstance(most_recent, datetime):
            most_recent_str = most_recent.isoformat()
            known_vals = list(known_timestamps.values())
            if known_vals and most_recent_str <= max(known_vals):
                return []

        known_uas_sessions = set(known_timestamps.keys())
        oldest_known = (
            min(known_timestamps.values()) if known_timestamps
            else start_time.isoformat()
        )

        conditions = []
        params = []

        for key, ts in known_timestamps.items():
            if ':' in key:
                uas_id, session_id = key.split(':', 1)
                if session_id != 'unknown':
                    conditions.append(
                        "(uas_id = ? AND computed_session_id = ? AND max_ts > ?)"
                    )
                    params.extend([uas_id, session_id, ts])
                else:
                    conditions.append(
                        "(uas_id = ? AND computed_session_id = '' AND max_ts > ?)"
                    )
                    params.extend([uas_id, ts])
            else:
                conditions.append(
                    "(uas_id = ? AND computed_session_id = '' AND max_ts > ?)"
                )
                params.extend([key, ts])

        results = []

        # Known sessions — query latest_positions directly
        if conditions:
            where_clause = " OR ".join(conditions)
            cursor = conn.execute(
                f"""
                SELECT
                    uas_id,
                    NULLIF(computed_session_id, '') as computed_session_id,
                    max_ts as timestamp, min_ts as session_start,
                    latitude, longitude, altitude, height, height_type,
                    operator_id,
                    operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM latest_positions
                WHERE ({where_clause})
                ORDER BY uas_id, computed_session_id
            """,
                params,
            )
            results.extend(cursor.fetchall())

        # New sessions (not in known_timestamps).
        # Only scan sessions with max_ts > client's oldest known ts.
        cursor = conn.execute(
            """
            SELECT
                uas_id,
                NULLIF(computed_session_id, '') as computed_session_id,
                max_ts as timestamp, min_ts as session_start,
                latitude, longitude, altitude, height, height_type,
                operator_id,
                operator_latitude, operator_longitude, source,
                collector_latitude, collector_longitude
            FROM latest_positions
            WHERE max_ts BETWEEN ? AND ?
              AND max_ts > ?
            ORDER BY uas_id, computed_session_id
        """,
            (start_time, end_time, oldest_known),
        )
        for row in cursor.fetchall():
            sid = row['computed_session_id'] or 'unknown'
            key = f"{row['uas_id']}:{sid}"
            if key not in known_uas_sessions:
                results.append(row)

        return [self._sanitize_record(dict(row)) for row in results]

    def get_positions(
        self,
        start_time: datetime,
        end_time: datetime,
        uas_id: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict]:
        """Get positions within time window"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        if uas_id:
            cursor = conn.execute(
                """
                SELECT * FROM remoteid
                WHERE uas_id = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (uas_id, start_time, end_time, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM remoteid
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (start_time, end_time, limit),
            )

        return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_track(
        self, uas_id: str, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Get track (ordered positions) for a specific drone with session info"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            """
            SELECT latitude, longitude, altitude, height, height_type, timestamp,
                   operator_id, operator_latitude, operator_longitude,
                   computed_session_id
            FROM remoteid
            WHERE uas_id = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """,
            (uas_id, start_time, end_time),
        )

        return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_track_session_positions(
        self, uas_id: str, session_id: str
    ) -> List[Dict]:
        """Get positions for a specific session using indexed lookup.

        Uses the computed_session_id index directly instead of scanning
        the full time window. Much faster when loading individual sessions.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            """
            SELECT latitude, longitude, altitude, height, height_type, timestamp,
                   operator_id, operator_latitude, operator_longitude,
                   computed_session_id, collector_latitude, collector_longitude
            FROM remoteid
            WHERE uas_id = ? AND computed_session_id = ?
            ORDER BY timestamp ASC
        """,
            (uas_id, session_id),
        )

        return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_track_sessions(
        self, uas_id: str, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Get track grouped by session

        Returns a list of session objects, each containing positions for that session.
        This is useful when a UAS has multiple sessions in the time window.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        # Get all positions with session info
        cursor = conn.execute(
            """
            SELECT latitude, longitude, altitude, height, height_type, timestamp,
                   operator_id, operator_latitude, operator_longitude,
                   computed_session_id
            FROM remoteid
            WHERE uas_id = ? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """,
            (uas_id, start_time, end_time),
        )

        positions = [dict(row) for row in cursor.fetchall()]

        # Group by session
        sessions = {}
        for pos in positions:
            session_id = pos.get('computed_session_id') or 'unknown'
            if session_id not in sessions:
                sessions[session_id] = {
                    'session_id': session_id,
                    'positions': []
                }
            sessions[session_id]['positions'].append(pos)

        # Sort sessions by start time
        result = list(sessions.values())
        result.sort(key=lambda s: s['positions'][0]['timestamp'] if s['positions'] else datetime.min)

        return result

    def get_operators(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get latest operator positions for drones in time window"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            """
            SELECT r1.uas_id, r1.operator_id, r1.operator_latitude,
                   r1.operator_longitude, r1.timestamp
            FROM remoteid r1
            INNER JOIN (
                SELECT uas_id, MAX(timestamp) as max_ts
                FROM remoteid
                WHERE timestamp BETWEEN ? AND ?
                AND operator_latitude IS NOT NULL
                AND operator_latitude != 0
                GROUP BY uas_id
            ) r2 ON r1.uas_id = r2.uas_id AND r1.timestamp = r2.max_ts
            ORDER BY r1.uas_id
        """,
            (start_time, end_time),
        )

        return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_bounds(self, start_time: datetime, end_time: datetime) -> Optional[Tuple]:
        """Get bounding box of all positions in time window"""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude)
            FROM remoteid
            WHERE timestamp BETWEEN ? AND ?
        """,
            (start_time, end_time),
        )

        row = cursor.fetchone()
        if row and row[0] is not None:
            return row
        return None

    def insert_remoteid_records(
        self,
        source: str,
        records: List[Dict],
        session_gap_threshold: int = 600,
        source_tz: Optional[str] = None,
        collector_lat: Optional[float] = None,
        collector_lon: Optional[float] = None,
    ) -> Tuple[int, List[Dict], Optional[datetime]]:
        # pylint: disable=too-many-locals,too-many-positional-arguments
        """Insert multiple records into remoteid table with session detection.

        Uses INSERT OR IGNORE with a UNIQUE index on (uas_id, timestamp)
        to skip duplicates without per-record SELECT checks.

        Args:
            source: The source name to associate with records
            records: List of record dictionaries
            session_gap_threshold: Time gap in seconds to trigger a new session (default: 600)
            source_tz: IANA timezone name (e.g. "America/Denver") to interpret
                       naive timestamps from this source. If None, naive
                       timestamps are assumed to be UTC.
            collector_lat: Collector latitude to stamp on each record (optional)
            collector_lon: Collector longitude to stamp on each record (optional)

        Returns:
            Tuple of (inserted_count, errors, most_recent_timestamp)
        """
        errors = []
        batch_params = []
        uas_sessions = {}
        most_recent = None

        # Phase 1: validate records and build batch params (no DB I/O)
        for idx, record in enumerate(records):
            try:
                if not record.get("uas_id"):
                    errors.append({"index": idx, "reason": "Missing uas_id"})
                    continue

                ts_str = record.get("timestamp")
                if not ts_str:
                    errors.append({"index": idx, "reason": "Missing timestamp"})
                    continue

                try:
                    timestamp = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    )
                    if timestamp.tzinfo is None:
                        if source_tz:
                            timestamp = timestamp.replace(
                                tzinfo=ZoneInfo(source_tz)
                            ).astimezone(timezone.utc)
                        else:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                    else:
                        timestamp = timestamp.astimezone(timezone.utc)
                except ValueError:
                    errors.append(
                        {"index": idx, "reason": f"Invalid timestamp: {ts_str}"}
                    )
                    continue

                # Strict validation: reject records with invalid lat/lon/alt
                lat = self._sanitize_float(record.get("latitude"), "latitude")
                if record.get("latitude") is not None and lat is None:
                    errors.append({"index": idx, "reason": "Invalid latitude"})
                    continue
                lon = self._sanitize_float(record.get("longitude"), "longitude")
                if record.get("longitude") is not None and lon is None:
                    errors.append({"index": idx, "reason": "Invalid longitude"})
                    continue
                alt = self._sanitize_float(record.get("altitude"), "altitude")
                if record.get("altitude") is not None and alt is None:
                    errors.append({"index": idx, "reason": "Invalid altitude"})
                    continue
                height = self._sanitize_float(record.get("height"), "height")
                height_type = record.get("height_type")
                # Operator coordinates: permissive (set to None if invalid)
                op_lat = self._sanitize_float(
                    record.get("operator_latitude"), "operator_latitude"
                )
                op_lon = self._sanitize_float(
                    record.get("operator_longitude"), "operator_longitude"
                )

                batch_params.append({
                    "source": source,
                    "timestamp": timestamp,
                    "uas_id": record["uas_id"],
                    "mac_address": record.get("mac_address"),
                    "session_id": record.get("session_id"),
                    "latitude": lat,
                    "longitude": lon,
                    "altitude": alt,
                    "height": height,
                    "height_type": height_type,
                    "operator_id": record.get("operator_id"),
                    "operator_latitude": op_lat,
                    "operator_longitude": op_lon,
                    "session_detected_at": datetime.now(timezone.utc),
                    "collector_latitude": collector_lat,
                    "collector_longitude": collector_lon,
                })

                if most_recent is None or timestamp > most_recent:
                    most_recent = timestamp

            except (ValueError, TypeError) as e:
                errors.append({"index": idx, "reason": str(e)})

        if not batch_params:
            return 0, errors, most_recent

        # Phase 2: batch insert with session detection (single DB round-trip)
        conn = self._get_conn()
        before = conn.execute("SELECT COUNT(*) FROM remoteid").fetchone()[0]

        rows = []
        for rec in batch_params:
            uas_id = rec["uas_id"]
            timestamp = rec["timestamp"]
            computed_session_id = self._detect_session(
                conn, uas_id, timestamp, uas_sessions, session_gap_threshold
            )
            rec["computed_session_id"] = computed_session_id
            rows.append((
                rec["source"], rec["timestamp"], rec["mac_address"],
                rec["uas_id"], rec["session_id"], rec["latitude"],
                rec["longitude"], rec["altitude"], rec["height"],
                rec["height_type"], rec["operator_id"],
                rec["operator_latitude"], rec["operator_longitude"],
                rec["computed_session_id"], rec["session_detected_at"],
                rec["collector_latitude"], rec["collector_longitude"],
            ))
            uas_sessions[uas_id] = (timestamp, computed_session_id)

        # Collect UAS IDs with new data for latest_positions rebuild
        affected_uas_ids = list(dict.fromkeys(r[3] for r in rows))

        conn.executemany(
            """
            INSERT OR IGNORE INTO remoteid
            (source, timestamp, mac_address, uas_id, session_id,
             latitude, longitude, altitude, height, height_type,
             operator_id, operator_latitude, operator_longitude,
             computed_session_id, session_detected_at,
             collector_latitude, collector_longitude)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM remoteid").fetchone()[0]
        inserted = after - before

        # Update materialized latest_positions for affected UAS IDs
        if inserted > 0:
            self.rebuild_latest_positions(affected_uas_ids)

        return inserted, errors, most_recent

    def get_most_recent_timestamp(
        self, source: Optional[str] = None
    ) -> Optional[datetime]:
        """Get the most recent timestamp in the database.

        Args:
            source: Optional source name to filter by. If None, returns max across all sources.

        Returns:
            Most recent datetime or None if no records
        """
        conn = self._get_conn()
        if source:
            cursor = conn.execute(
                "SELECT MAX(timestamp) FROM remoteid WHERE source = ?",
                (source,),
            )
        else:
            cursor = conn.execute("SELECT MAX(timestamp) FROM remoteid")

        row = cursor.fetchone()
        if row and row[0]:
            val = row[0]
            if isinstance(val, str):
                return datetime.fromisoformat(val)
            return val
        return None

    def get_stats(self, start_time: datetime, end_time: datetime) -> Dict:
        """Get aggregate statistics for the given time window.

        Returns dict with total_drones, total_sessions, total_positions,
        active_alerts, and total_alerts_in_window.
        """
        conn = self._get_conn()
        # Total unique drones, distinct sessions, and total positions in one pass
        cursor = conn.execute(
            """
            SELECT
                COUNT(DISTINCT uas_id),
                COUNT(DISTINCT CASE WHEN computed_session_id IS NOT NULL THEN computed_session_id END),
                COUNT(*)
            FROM remoteid WHERE timestamp BETWEEN ? AND ?
            """,
            (start_time, end_time),
        )
        row = cursor.fetchone()
        total_drones = row[0] or 0
        total_sessions = row[1] or 0
        total_positions = row[2] or 0

        # Active geozone events
        cursor = conn.execute(
            "SELECT COUNT(*) FROM geozone_events WHERE exited_at IS NULL"
        )
        active_alerts = cursor.fetchone()[0] or 0

        # Total geozone events in time window (by entered_at)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM geozone_events WHERE entered_at BETWEEN ? AND ?",
            (start_time, end_time),
        )
        total_alerts = cursor.fetchone()[0] or 0

        return {
        "total_drones": total_drones,
        "total_sessions": total_sessions,
        "total_positions": total_positions,
        "active_alerts": active_alerts,
        "total_alerts": total_alerts,
        }

    def get_drones_for_alert_check(
        self, since: Optional[datetime] = None
    ) -> List[str]:
        """Get distinct UAS IDs with positions since *since* (for alert evaluation)."""
        conn = self._get_conn()
        if since:
            cursor = conn.execute(
                "SELECT DISTINCT uas_id FROM remoteid WHERE timestamp >= ?",
                (since,),
            )
        else:
            cursor = conn.execute("SELECT DISTINCT uas_id FROM remoteid")
        return [row[0] for row in cursor.fetchall()]

    def get_positions_for_alert_check(
        self, uas_id: str, since: Optional[datetime] = None
    ) -> List[Dict]:
        """Get positions for a UAS since *since* (for alert evaluation)."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        if since:
            cursor = conn.execute(
                """SELECT latitude, longitude, timestamp
                   FROM remoteid
                   WHERE uas_id = ? AND timestamp >= ?
                   ORDER BY timestamp ASC""",
                (uas_id, since),
            )
        else:
            cursor = conn.execute(
                """SELECT latitude, longitude, timestamp
                   FROM remoteid
                   WHERE uas_id = ?
                   ORDER BY timestamp ASC""",
                (uas_id,),
            )
        return [dict(row) for row in cursor.fetchall()]

    # --- Session tracking helpers ---

    def get_latest_session_id(self, uas_id: str) -> Optional[str]:
        """Get the most recent ``computed_session_id`` for a UAS, or ``None``."""
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT computed_session_id FROM remoteid
               WHERE uas_id = ? AND computed_session_id IS NOT NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (uas_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_all_current_sessions(self) -> Dict[str, str]:
        """Return the latest ``computed_session_id`` for every UAS that has one.

        Used by ``AlertEngine`` to pre-populate known sessions at startup so
        existing flights don't trigger false "new session" notifications.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT r.uas_id, r.computed_session_id
               FROM remoteid r
               INNER JOIN (
                   SELECT uas_id, MAX(timestamp) AS max_ts
                   FROM remoteid WHERE computed_session_id IS NOT NULL
                   GROUP BY uas_id
               ) latest ON r.uas_id = latest.uas_id AND r.timestamp = latest.max_ts
               WHERE r.computed_session_id IS NOT NULL"""
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    # --- Geozone event methods ---

    def get_active_geozone_events(self) -> List[Dict]:
        """Get all active (not yet exited) geozone events."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT * FROM geozone_events
            WHERE exited_at IS NULL
            ORDER BY entered_at DESC
            """
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_geozone_events_for_uas(self, uas_id: str) -> List[Dict]:
        """Get all events for a specific UAS, active first."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT * FROM geozone_events
            WHERE uas_id = ?
            ORDER BY exited_at IS NULL DESC, entered_at DESC
            """,
            (uas_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def enter_geozone(
        self, uas_id: str, geozone_name: str, timestamp: datetime
    ) -> int:
        """Create a new geozone entry event. Returns event id."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO geozone_events (uas_id, geozone_name, entered_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (uas_id, geozone_name, timestamp, timestamp),
        )
        conn.commit()
        return cursor.lastrowid

    def update_geozone_last_seen(self, event_id: int, timestamp: datetime):
        """Update last_seen_at for an active event."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE geozone_events SET last_seen_at = ? WHERE id = ?",
            (timestamp, event_id),
        )
        conn.commit()

    def exit_geozone(self, event_id: int, timestamp: datetime, reason: str = "left"):
        """Mark a geozone event as exited."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE geozone_events SET exited_at = ?, exited_reason = ? WHERE id = ?",
            (timestamp, reason, event_id),
        )
        conn.commit()

    def get_geozone_event_history(
        self,
        uas_id: Optional[str] = None,
        geozone_name: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        # pylint: disable=too-many-positional-arguments
        """Get geozone event history with filtering and pagination.

        Returns (events, total_count) tuple.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        conditions = []
        params = []

        if uas_id:
            conditions.append("uas_id = ?")
            params.append(uas_id)
        if geozone_name:
            conditions.append("geozone_name = ?")
            params.append(geozone_name)
        if from_date:
            conditions.append("entered_at >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("entered_at <= ?")
            params.append(to_date)

        where = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        count_cursor = conn.execute(
            f"SELECT COUNT(*) FROM geozone_events WHERE {where}", params
        )
        total = count_cursor.fetchone()[0]

        # Get paginated results
        query_params = params + [limit, offset]
        cursor = conn.execute(
            f"""
            SELECT * FROM geozone_events
            WHERE {where}
            ORDER BY entered_at DESC
            LIMIT ? OFFSET ?
            """,
            query_params,
        )
        events = [dict(row) for row in cursor.fetchall()]

        return events, total

    def check_stale_geozone_events(
        self, stale_timeout: int, reference_time: datetime
    ) -> int:
        """Mark events stale (timed out) where last_seen_at is older than timeout.

        Returns the number of events marked as stale.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """
            UPDATE geozone_events
            SET exited_at = last_seen_at, exited_reason = 'timeout'
            WHERE exited_at IS NULL
            AND last_seen_at < ?
            """,
            (reference_time - timedelta(seconds=stale_timeout),),
        )
        conn.commit()
        return cursor.rowcount

    def update_collector_position(self, name: str, lat: float, lon: float):
        """Insert or replace a collector's current position"""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO collector_positions
            (name, latitude, longitude, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (name, lat, lon),
        )
        conn.commit()

    def get_collector_positions(self) -> List[Dict]:
        """Get all current collector positions"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT name, latitude, longitude, updated_at FROM collector_positions ORDER BY name"
        )
        return [dict(row) for row in cursor.fetchall()]

    # --- Push subscription methods ---

    def save_push_subscription(
        self, endpoint: str, p256dh_key: str, auth_key: str, user_agent: Optional[str] = None
    ):
        """Save or update a push notification subscription."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO push_subscriptions
            (endpoint, p256dh_key, auth_key, user_agent, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (endpoint, p256dh_key, auth_key, user_agent),
        )
        conn.commit()

    def remove_push_subscription(self, endpoint: str):
        """Remove a push notification subscription."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        )
        conn.commit()

    def get_all_push_subscriptions(self) -> List[Dict]:
        """Get all push notification subscriptions."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT endpoint, p256dh_key, auth_key FROM push_subscriptions ORDER BY created_at"
        )
        return [dict(row) for row in cursor.fetchall()]

    # --- Auth methods ---

    def create_user(  # pylint: disable=too-many-positional-arguments
        self, name: str, email: str, role_name: str,
        login_token: str, login_token_expires_at: datetime
    ) -> dict:
        """Create a pre-created user with a login token.

        Returns the user row as a dict.
        """
        token_hash = hashlib.sha256(login_token.encode()).hexdigest()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO users (name, email, role_name, is_ephemeral, is_active,
                               login_token_hash, login_token_expires_at, auth_method)
            VALUES (?, ?, ?, 0, 1, ?, ?, 'login_link')
            """,
            (name, email, role_name, token_hash, login_token_expires_at),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM users WHERE login_token_hash = ?", (token_hash,))
        return dict(cursor.fetchone())

    def create_ephemeral_user(self) -> Tuple[str, int]:
        """Create an ephemeral visitor user and an auth token.

        Returns (session_token, user_id).
        """
        name = f"Guest-{_secrets.token_hex(4)}"
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO users (name, role_name, is_ephemeral, is_active, auth_method)
               VALUES (?, 'guest', 1, 1, 'ephemeral')""",
            (name,),
        )
        user_id = cursor.lastrowid
        session_token = _secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(session_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=90)
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, token_hash, expires_at),
        )
        conn.commit()
        return session_token, user_id

    def exchange_login_token(self, login_token: str) -> Optional[Tuple[str, dict]]:
        """Exchange a one-time login token for a session token.

        Returns (session_token, user_dict) on success, or None if the token is
        invalid or expired.
        """
        token_hash = hashlib.sha256(login_token.encode()).hexdigest()
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM users WHERE login_token_hash = ? AND login_token_expires_at > ? AND is_active = 1",
            (token_hash, datetime.now(timezone.utc)),
        )
        user = cursor.fetchone()
        if not user:
            return None

        # Clear the one-time login token
        conn.execute(
            "UPDATE users SET login_token_hash = NULL, login_token_expires_at = NULL WHERE id = ?",
            (user["id"],),
        )

        # Create session token
        session_token = _secrets.token_urlsafe(32)
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=90)
        conn.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user["id"], session_hash, expires_at),
        )
        conn.commit()

        return session_token, dict(user)

    def get_user_by_auth_token(self, token: str) -> Optional[dict]:
        """Look up a user by their session auth token.

        Returns user dict (including role info) or None if the token is invalid
        or expired.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """SELECT u.* FROM users u JOIN auth_tokens t ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.expires_at > ? AND u.is_active = 1""",
            (token_hash, datetime.now(timezone.utc)),
        )
        user = cursor.fetchone()
        return dict(user) if user else None

    def revoke_token(self, token: str):
        """Revoke (delete) an auth token."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._get_conn()
        conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (token_hash,))
        conn.commit()

    def revoke_all_user_tokens(self, user_id: int):
        """Revoke all auth tokens for a given user."""
        conn = self._get_conn()
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
        conn.commit()

    def upgrade_ephemeral_user(self, ephemeral_user_id: int, target_user_id: int) -> bool:
        """Merge a pre-created user into an ephemeral user record.

        Transfers name, email, role_name from the target to the ephemeral,
        deletes any session tokens created for the target, and deactivates
        the target record so the ephemeral becomes a full account.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        target = conn.execute(
            "SELECT name, email, role_name FROM users WHERE id = ? AND is_active = 1",
            (target_user_id,),
        ).fetchone()
        if not target:
            return False

        conn.execute(
            "UPDATE users SET name=?, email=?, role_name=?, auth_method='upgraded' WHERE id=?",
            (target["name"], target["email"], target["role_name"], ephemeral_user_id),
        )
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (target_user_id,))
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (target_user_id,))
        conn.commit()
        return True
