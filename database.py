"""Database layer for web interface — PostgreSQL backend"""
# pylint: disable=too-many-lines

import hashlib
import secrets as _secrets
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from psycopg2 import pool

logger = logging.getLogger(__name__)

# Current schema version — bump this and add a migration in _migrate()
SCHEMA_VERSION = 9


class WebDatabase:
    """Manages PostgreSQL database for web interface"""

    def __init__(self, database_url: str):
        """Initialize the web database, creating schema if needed."""
        self.database_url = database_url
        self._pool = pool.ThreadedConnectionPool(0, 10, dsn=database_url)
        self._init_db()

    def reset_pool(self):
        """Replace the connection pool with a fresh one forked workers can use.

        After ``fork()`` the child inherits the parent's pool. Reusing it is
        unsafe two ways:

        - its ``psycopg2`` sockets are shared with the parent (protocol
          corruption when both processes use them), and
        - its internal ``threading.Lock`` may have been inherited in a
          **held** state from a master background thread that was mid-use at
          fork time, so calling ``closeall()``/``getconn()`` on it can
          deadlock forever.

        The fix is to allocate a brand-new lazy pool (minconn=0, no sockets
        opened) and replace the reference without ever locking the inherited
        one. The old pool object is dropped and its inherited file
        descriptors are never touched by this worker.

        Safe to call multiple times.
        """
        self._pool = pool.ThreadedConnectionPool(0, 10, dsn=self.database_url)

    def _get_conn(self):
        """Get a connection from the pool.

        The caller MUST return the connection via ``_put_conn()`` when done,
        ideally in a ``finally`` block.
        """
        return self._pool.getconn()

    def _put_conn(self, conn):
        """Return a connection to the pool."""
        if conn is not None:
            self._pool.putconn(conn)

    def _commit(self, conn):
        """Commit a transaction."""
        conn.commit()

    def _init_db(self):
        """Initialize the database schema"""
        conn = self._get_conn()
        try:
            conn.autocommit = True
            cur = conn.cursor()

            # Create remoteid table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS remoteid(
                id SERIAL PRIMARY KEY,
                source TEXT,
                timestamp TIMESTAMPTZ,
                mac_address TEXT,
                uas_id TEXT,
                session_id TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                altitude DOUBLE PRECISION,
                height DOUBLE PRECISION,
                height_type TEXT,
                operator_id TEXT,
                operator_latitude DOUBLE PRECISION,
                operator_longitude DOUBLE PRECISION,
                computed_session_id TEXT,
                session_detected_at TIMESTAMPTZ,
                collector_latitude DOUBLE PRECISION,
                collector_longitude DOUBLE PRECISION
            )
            """
            )

            # Create schema version tracking table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _schema_version(
                version INTEGER NOT NULL UNIQUE
            )
            """
            )

            # Create sync log table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_log(
                id SERIAL PRIMARY KEY,
                source TEXT,
                last_sync TIMESTAMPTZ,
                records_imported INTEGER
            )
            """
            )

            # Create session tracking table for real-time detection
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_tracking(
                id SERIAL PRIMARY KEY,
                uas_id TEXT UNIQUE,
                last_seen TIMESTAMPTZ,
                current_session_id TEXT,
                updated_at TIMESTAMPTZ
            )
            """
            )

            # Create geozone events table for alerting
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS geozone_events(
                id SERIAL PRIMARY KEY,
                uas_id TEXT NOT NULL,
                geozone_name TEXT NOT NULL,
                entered_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                exited_at TIMESTAMPTZ,
                exited_reason TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
            )

            # Create sent_alerts table for cross-process alert deduplication.
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_alerts(
                id SERIAL PRIMARY KEY,
                alert_type TEXT NOT NULL,
                dedup_key TEXT NOT NULL,
                uas_id TEXT,
                session_id TEXT,
                sent_at TIMESTAMPTZ NOT NULL,
                UNIQUE (alert_type, dedup_key)
            )
            """
            )

            # Create collector positions table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS collector_positions(
                name TEXT PRIMARY KEY,
                latitude DOUBLE PRECISION NOT NULL,
                longitude DOUBLE PRECISION NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
            )

            # Create users table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                role_name TEXT NOT NULL DEFAULT 'guest',
                is_ephemeral INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                login_token_hash TEXT UNIQUE,
                login_token_expires_at TIMESTAMPTZ,
                auth_method TEXT NOT NULL DEFAULT 'ephemeral',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
            )

            # Create auth_tokens table
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
            )

            # Create indexes
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_uas_time ON remoteid(uas_id, timestamp)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_source ON remoteid(source)")
            cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_uas_time_unique "
            "ON remoteid(uas_id, source, timestamp)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON remoteid(timestamp)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_computed_session ON remoteid(computed_session_id)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_geozone_events_active "
            "ON geozone_events(uas_id, geozone_name, exited_at)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_geozone_events_stale "
            "ON geozone_events(exited_at, last_seen_at)"
            )
            # At most one active event per (uas_id, geozone_name). Closing any
            # duplicates left by older per-process races must happen before the
            # unique index is created, otherwise the CREATE would fail on
            # databases that accumulated duplicate active events.
            cur.execute(
                """
                UPDATE geozone_events
                SET exited_at = last_seen_at, exited_reason = 'deduplicated'
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY uas_id, geozone_name
                                   ORDER BY entered_at DESC
                               ) AS rn
                        FROM geozone_events
                        WHERE exited_at IS NULL
                    )
                    WHERE rn > 1
                )
                """
            )
            cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_geozone_events_active_unique "
            "ON geozone_events(uas_id, geozone_name) WHERE exited_at IS NULL"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash "
            "ON auth_tokens(token_hash)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user "
            "ON auth_tokens(user_id)"
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_log_source "
            "ON sync_log(source, last_sync)"
            )

            # Materialized latest_positions table — O(sessions) instead of O(rows)
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS latest_positions(
                uas_id TEXT NOT NULL,
                computed_session_id TEXT NOT NULL DEFAULT '',
                max_ts TIMESTAMPTZ NOT NULL,
                min_ts TIMESTAMPTZ NOT NULL,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                altitude DOUBLE PRECISION,
                height DOUBLE PRECISION,
                height_type TEXT,
                max_height DOUBLE PRECISION,
                operator_id TEXT,
                operator_latitude DOUBLE PRECISION,
                operator_longitude DOUBLE PRECISION,
                source TEXT,
                collector_latitude DOUBLE PRECISION,
                collector_longitude DOUBLE PRECISION,
                PRIMARY KEY (uas_id, computed_session_id)
            )
            """
            )
            cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lp_max_ts "
            "ON latest_positions(max_ts)"
            )

            conn.autocommit = False
            self._ensure_schema_version(conn)
            self._ensure_latest_positions_backfilled(conn)
            self._commit(conn)
            logger.debug("Database initialized")
        finally:
            self._put_conn(conn)

    def _ensure_schema_version(self, conn):
        """Check the schema version and apply any pending migrations."""
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) FROM _schema_version"
        )
        current = cur.fetchone()[0]

        if current < SCHEMA_VERSION:
            self._migrate(conn, current, SCHEMA_VERSION)
            cur.execute(
                "INSERT INTO _schema_version (version) VALUES (%s) "
                "ON CONFLICT (version) DO NOTHING",
                (SCHEMA_VERSION,),
            )
            self._commit(conn)

    @staticmethod
    def _column_exists(cur, table: str, column: str) -> bool:
        """Return True if *table* has *column* (via information_schema).

        Used instead of attempting an ALTER and catching DuplicateColumn,
        which would abort the surrounding transaction.
        """
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None

    @staticmethod
    def _migrate(  # pylint: disable=unused-argument
        conn, from_version: int, to_version: int
    ):
        """Apply schema migrations between *from_version* and *to_version*.

        Each ``if version == X`` branch applies the changes needed to go
        from version X to version X+1.  The version table is updated
        separately by the caller.
        """
        cur = conn.cursor()

        if from_version == 1:
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                role_name TEXT NOT NULL DEFAULT 'guest',
                is_ephemeral INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                login_token_hash TEXT UNIQUE,
                login_token_expires_at TIMESTAMPTZ,
                auth_method TEXT NOT NULL DEFAULT 'ephemeral',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
            )
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens(
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash "
                "ON auth_tokens(token_hash)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user "
                "ON auth_tokens(user_id)"
            )
            from_version = 2

        if from_version == 2:
            WebDatabase._ensure_latest_positions_table(cur)
            WebDatabase._backfill_latest_positions(cur)
            from_version = 3

        if from_version == 3:
            for table in ("remoteid", "latest_positions"):
                if not WebDatabase._column_exists(cur, table, "height"):
                    cur.execute(
                        f"ALTER TABLE {table} ADD COLUMN height DOUBLE PRECISION"
                    )
                if not WebDatabase._column_exists(cur, table, "height_type"):
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN height_type TEXT")
            from_version = 4

        if from_version == 4:
            cur.execute("DROP TABLE IF EXISTS push_subscriptions")
            from_version = 5

        if from_version == 5:
            if not WebDatabase._column_exists(cur, "latest_positions", "max_height"):
                cur.execute(
                    "ALTER TABLE latest_positions ADD COLUMN max_height DOUBLE PRECISION"
                )
            cur.execute("DELETE FROM latest_positions")
            WebDatabase._backfill_latest_positions(cur)
            from_version = 6

        if from_version == 6:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_log_source "
                "ON sync_log(source, last_sync)"
            )
            from_version = 7

        if from_version == 7:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sent_alerts(
                    id SERIAL PRIMARY KEY,
                    alert_type TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    uas_id TEXT,
                    session_id TEXT,
                    sent_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (alert_type, dedup_key)
                )
                """
            )
            cur.execute(
                """
                UPDATE geozone_events
                SET exited_at = last_seen_at, exited_reason = 'deduplicated'
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY uas_id, geozone_name
                                   ORDER BY entered_at DESC
                               ) AS rn
                        FROM geozone_events
                        WHERE exited_at IS NULL
                    )
                    WHERE rn > 1
                )
                """
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_geozone_events_active_unique "
                "ON geozone_events(uas_id, geozone_name) WHERE exited_at IS NULL"
            )
            from_version = 8

        if from_version == 8:
            # v8 unique index deduplicated by (uas_id, timestamp), which
            # wrongly dropped a packet when two collectors (e.g. a 2.4 GHz and
            # a 5.8 GHz interface) observed the same drone packet at the same
            # timestamp. The dedup key now includes the collector source so
            # each collector keeps its own copy (used for per-collector
            # attribution). Drop the old unique index, then create the new one.
            # The defunct (already redundant) composite index is left in place.
            cur.execute("DROP INDEX IF EXISTS idx_uas_time_unique")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_uas_time_unique "
                "ON remoteid(uas_id, source, timestamp)"
            )
            from_version = 9

    @staticmethod
    def _ensure_latest_positions_table(cur):
        """Create the latest_positions table and index (idempotent)."""
        cur.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_positions(
            uas_id TEXT NOT NULL,
            computed_session_id TEXT NOT NULL DEFAULT '',
            max_ts TIMESTAMPTZ NOT NULL,
            min_ts TIMESTAMPTZ NOT NULL,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            altitude DOUBLE PRECISION,
            height DOUBLE PRECISION,
            height_type TEXT,
            max_height DOUBLE PRECISION,
            operator_id TEXT,
            operator_latitude DOUBLE PRECISION,
            operator_longitude DOUBLE PRECISION,
            source TEXT,
            collector_latitude DOUBLE PRECISION,
            collector_longitude DOUBLE PRECISION,
            PRIMARY KEY (uas_id, computed_session_id)
        )
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_lp_max_ts "
            "ON latest_positions(max_ts)"
        )

    def _ensure_latest_positions_backfilled(self, conn):
        """Safety net: if latest_positions is empty but remoteid has rows, backfill."""
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM remoteid")
        remoteid_count = cur.fetchone()[0]
        if remoteid_count == 0:
            return
        cur.execute("SELECT COUNT(*) FROM latest_positions")
        lp_count = cur.fetchone()[0]
        if lp_count > 0:
            return
        logger.warning(
            "latest_positions is empty but remoteid has %d rows — backfilling",
            remoteid_count,
        )
        WebDatabase._backfill_latest_positions(cur)
        self._commit(conn)

    @staticmethod
    def _backfill_latest_positions(cur):
        """Populate latest_positions from existing remoteid data (one-time migration)."""
        cur.execute(
        """
        INSERT INTO latest_positions
            (uas_id, computed_session_id, max_ts, min_ts,
             latitude, longitude, altitude, height, height_type, max_height,
             operator_id, operator_latitude, operator_longitude, source,
             collector_latitude, collector_longitude)
        SELECT
            uas_id,
            COALESCE(computed_session_id, ''),
            timestamp,
            min_ts,
            latitude, longitude, altitude, height, height_type, max_height,
            operator_id, operator_latitude, operator_longitude, source,
            collector_latitude, collector_longitude
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY uas_id, COALESCE(computed_session_id, '')
                    ORDER BY timestamp DESC
                ) as rn,
                MIN(timestamp) OVER (
                    PARTITION BY uas_id, COALESCE(computed_session_id, '')
                ) as min_ts,
                MAX(height) OVER (
                    PARTITION BY uas_id, COALESCE(computed_session_id, '')
                ) as max_height
            FROM remoteid
        )
        sub
        WHERE rn = 1
        """
        )

    def rebuild_latest_positions(self, uas_ids: Optional[List[str]] = None):
        """Rebuild latest_positions from remoteid for specific UAS IDs (or all).

        Called after session re-detection to fix up the materialized table.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if uas_ids:
                placeholders = ','.join(['%s'] * len(uas_ids))
                cur.execute(
                    f"DELETE FROM latest_positions WHERE uas_id IN ({placeholders})",
                    uas_ids,
                )
                cur.execute(
                    f"""
                    INSERT INTO latest_positions
                        (uas_id, computed_session_id, max_ts, min_ts,
                         latitude, longitude, altitude, height, height_type, max_height,
                         operator_id, operator_latitude, operator_longitude, source,
                         collector_latitude, collector_longitude)
                    SELECT
                        uas_id,
                        COALESCE(computed_session_id, ''),
                        timestamp,
                        min_ts,
                        latitude, longitude, altitude, height, height_type, max_height,
                        operator_id, operator_latitude, operator_longitude, source,
                        collector_latitude, collector_longitude
                    FROM (
                        SELECT *,
                            ROW_NUMBER() OVER (
                                PARTITION BY uas_id, COALESCE(computed_session_id, '')
                                ORDER BY timestamp DESC
                            ) as rn,
                            MIN(timestamp) OVER (
                                PARTITION BY uas_id, COALESCE(computed_session_id, '')
                            ) as min_ts,
                            MAX(height) OVER (
                                PARTITION BY uas_id, COALESCE(computed_session_id, '')
                            ) as max_height
                        FROM remoteid
                        WHERE uas_id IN ({placeholders})
                    )
                    sub
                    WHERE rn = 1
                    """,
                    uas_ids,
                )
            else:
                cur.execute("DELETE FROM latest_positions")
                self._backfill_latest_positions(cur)
            self._commit(conn)
        finally:
            self._put_conn(conn)

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
        # pylint: disable=too-many-locals,too-many-positional-arguments,too-many-branches
        """Import new records from a collector's database with session detection

        Args:
            source_db_path: Path to the source collector database (SQLite)
            source_name: Name of the source collector
            session_gap_threshold: Time gap in seconds to trigger a new session (default: 600)
            source_tz: IANA timezone name (e.g. "America/Denver") to interpret
                       naive timestamps from this source. If None, naive
                       timestamps are assumed to be UTC.
            collector_lat: Collector latitude to stamp on each record (optional)
            collector_lon: Collector longitude to stamp on each record (optional)
        """
        import sqlite3 as _sqlite3  # pylint: disable=import-outside-toplevel

        count = 0
        skipped = 0
        dest_conn = None
        try:
            # Get last sync time for this source
            last_sync = self._get_last_sync(source_name)

            # Connect to source SQLite database and query new records
            columns = (
                "id, timestamp, mac_address, uas_id, session_id, latitude, longitude, "
                "altitude, operator_id, operator_latitude, operator_longitude"
            )

            with _sqlite3.connect(
                source_db_path, detect_types=_sqlite3.PARSE_DECLTYPES, timeout=5
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

                # Import into web database using named parameters.
                # Duplicate (uas_id, source, timestamp) rows are skipped via the
                # unique index instead of a SELECT-then-INSERT race.
                dest_conn = self._get_conn()
                # Track session state per UAS for this import batch
                uas_sessions = {}
                affected_uas_ids = set()

                for row in cursor:
                    # Validate and sanitize the record
                    validated = self._validate_record(row)
                    if validated is None:
                        skipped += 1
                        continue

                    timestamp = validated[0]
                    if isinstance(timestamp, str):
                        try:
                            timestamp = datetime.fromisoformat(
                                timestamp.replace("Z", "+00:00")
                            )
                        except ValueError:
                            logger.debug(
                                "Invalid timestamp %s for uas_id %s, skipping",
                                validated[0], validated[2],
                            )
                            skipped += 1
                            continue
                    if timestamp.tzinfo is None:
                        if source_tz:
                            timestamp = timestamp.replace(
                                tzinfo=ZoneInfo(source_tz)
                            ).astimezone(timezone.utc)
                        else:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                    else:
                        timestamp = timestamp.astimezone(timezone.utc)
                    uas_id = validated[2]

                    # Determine computed_session_id based on time gap
                    computed_session_id = self._detect_session(
                        dest_conn,
                        uas_id,
                        timestamp,
                        uas_sessions,
                        session_gap_threshold,
                    )

                    dest_cur = dest_conn.cursor()
                    dest_cur.execute(
                        """
                        INSERT INTO remoteid
                        (source, timestamp, mac_address, uas_id, session_id,
                         latitude, longitude, altitude, height, height_type,
                         operator_id,
                         operator_latitude, operator_longitude,
                         computed_session_id, session_detected_at,
                         collector_latitude, collector_longitude)
                        VALUES (%(source)s, %(timestamp)s, %(mac_address)s, %(uas_id)s, %(session_id)s,
                                %(latitude)s, %(longitude)s, %(altitude)s, %(height)s, %(height_type)s,
                                %(operator_id)s,
                                %(operator_latitude)s, %(operator_longitude)s,
                                %(computed_session_id)s, %(session_detected_at)s,
                                %(collector_latitude)s, %(collector_longitude)s)
                        ON CONFLICT (uas_id, source, timestamp) DO NOTHING
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
                    if dest_cur.rowcount == 1:
                        count += 1
                        affected_uas_ids.add(uas_id)
                        # Update session tracking for this batch
                        uas_sessions[uas_id] = (timestamp, computed_session_id)

                self._commit(dest_conn)

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

        except (psycopg2.DatabaseError, _sqlite3.Error) as e:
            logger.error("Database import error from %s: %s", source_name, e)
            return 0
        finally:
            if dest_conn is not None:
                self._put_conn(dest_conn)

    def _detect_session(
        self,
        conn,
        uas_id: str,
        timestamp: datetime,
        uas_sessions: dict,
        gap_threshold: int,
    ) -> str:
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Detect session based on time gap from last seen record"""
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
        cur = conn.cursor()
        cur.execute(
            "SELECT timestamp, computed_session_id FROM remoteid "
            "WHERE uas_id = %s ORDER BY timestamp DESC LIMIT 1",
            (uas_id,),
        )
        row = cur.fetchone()

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
        """Sanitize a record for API response, ensuring safe values."""
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
        """Sanitize a timestamp value, ensuring timezone info is present."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.isoformat() + "Z"
            return value.isoformat()
        s = str(value)
        if s and len(s) >= 19:
            is_datetime = (s[4] == '-' and s[7] == '-' and s[10] == 'T'
                        and s[13] == ':' and s[16] == ':')
            has_tz = s.endswith("Z") or "+" in s[-6:] or "-" in s[-6:]
            if is_datetime and not has_tz:
                return s + "Z"
        return s

    def _get_last_sync(self, source_name: str) -> Optional[datetime]:
        """Get the last sync time for a source"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT last_sync FROM sync_log WHERE source = %s ORDER BY last_sync DESC LIMIT 1",
                (source_name,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            self._put_conn(conn)

    def _update_sync_log(self, source_name: str, count: int):
        """Update the sync log for a source"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sync_log (source, last_sync, records_imported) VALUES (%s, %s, %s)",
                (source_name, datetime.now(timezone.utc), count),
            )
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def log_submission(self, source_name: str, records_count: int):
        """Log an HTTP data submission to the sync log"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sync_log (source, last_sync, records_imported) VALUES (%s, %s, %s)",
                (source_name, datetime.now(timezone.utc), records_count),
            )
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def cleanup_expired_auth_tokens(self) -> int:
        """Delete session tokens whose expiry has passed."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM auth_tokens WHERE expires_at < %s",
                (datetime.now(timezone.utc),),
            )
            count = cur.rowcount
            self._commit(conn)
            return count
        finally:
            self._put_conn(conn)

    def cleanup_expired_login_tokens(self) -> int:
        """Delete pre-created user records whose one-time login token has expired."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM users WHERE login_token_expires_at < %s",
                (datetime.now(timezone.utc),),
            )
            count = cur.rowcount
            self._commit(conn)
            return count
        finally:
            self._put_conn(conn)

    def cleanup_orphaned_ephemeral_users(self) -> int:
        """Delete ephemeral (guest) users whose session tokens have all expired."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            now = datetime.now(timezone.utc)
            # Tokens must be removed before users to satisfy the FK constraint
            # (auth_tokens.user_id -> users.id), which was not enforced in SQLite.
            cur.execute(
                """
                DELETE FROM auth_tokens WHERE user_id IN (
                    SELECT u.id FROM users u
                    LEFT JOIN auth_tokens t ON t.user_id = u.id
                    WHERE u.is_ephemeral = 1
                    GROUP BY u.id
                    HAVING MAX(t.expires_at) IS NULL
                        OR MAX(t.expires_at) < %s
                )
                """,
                (now,),
            )
            cur.execute(
                """
                DELETE FROM users WHERE id IN (
                    SELECT u.id FROM users u
                    LEFT JOIN auth_tokens t ON t.user_id = u.id
                    WHERE u.is_ephemeral = 1
                    GROUP BY u.id
                    HAVING MAX(t.expires_at) IS NULL
                        OR MAX(t.expires_at) < %s
                )
                """,
                (now,),
            )
            count = cur.rowcount
            # Clean up any orphaned auth_tokens
            cur.execute(
                "DELETE FROM auth_tokens WHERE user_id NOT IN (SELECT id FROM users)"
            )
            self._commit(conn)
            return count
        finally:
            self._put_conn(conn)

    def cleanup_old_sync_log(self, retention_days: int) -> int:
        """Delete sync_log rows older than *retention_days*."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            cur.execute(
                """
                DELETE FROM sync_log
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM sync_log
                        WHERE last_sync >= %s
                        UNION
                        SELECT MAX(id) FROM sync_log GROUP BY source
                    )
                    sub
                )
                AND last_sync < %s
                """,
                (cutoff, cutoff),
            )
            count = cur.rowcount
            self._commit(conn)
            return count
        finally:
            self._put_conn(conn)

    def get_all_sources(self) -> List[Dict]:
        """Get all unique data sources from sync_log and remoteid tables."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                "SELECT source, MAX(last_sync) as last_sync, "
                "SUM(records_imported) as total_records "
                "FROM sync_log GROUP BY source ORDER BY source"
            )
            sync_rows = cur.fetchall()

            cur.execute(
                "SELECT source, MAX(timestamp) as last_ts "
                "FROM remoteid WHERE source IS NOT NULL "
                "GROUP BY source ORDER BY source"
            )
            data_rows = cur.fetchall()

            def _parse_ts(val):
                if isinstance(val, datetime):
                    return val
                if isinstance(val, str):
                    try:
                        return datetime.fromisoformat(val)
                    except (ValueError, TypeError):
                        return None
                return None

            data_lookup = {}
            for row in data_rows:
                data_lookup[row["source"]] = _parse_ts(row["last_ts"])

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
        finally:
            self._put_conn(conn)

    def _get_drones_query(
        self, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Return latest position per session in the time window."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """
                SELECT
                    uas_id,
                    NULLIF(computed_session_id, '') as computed_session_id,
                    max_ts as timestamp,
                    min_ts as session_start,
                    latitude, longitude, altitude, height, height_type, max_height,
                    operator_id,
                    operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM latest_positions
                WHERE max_ts BETWEEN %s AND %s
                ORDER BY uas_id, computed_session_id
            """,
                (start_time, end_time),
            )
            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_live_drones(self, stale_minutes: int) -> List[Dict]:
        """Return all sessions with a position newer than *stale_minutes*."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
            cur.execute(
                """
                SELECT
                    uas_id,
                    NULLIF(computed_session_id, '') as computed_session_id,
                    max_ts as timestamp,
                    min_ts as session_start,
                    latitude, longitude, altitude, height, height_type, max_height,
                    operator_id,
                    operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM latest_positions
                WHERE max_ts > %s
                ORDER BY uas_id, computed_session_id
            """,
                (cutoff,),
            )
            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_drones(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get list of unique drones seen in time window with latest positions"""
        return self._get_drones_query(start_time, end_time)

    def get_sessions_for_uas(
        self, uas_id: str, limit: int = 10, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        """Get all sessions for a UAS ID with pagination, ignoring time constraints."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cur.execute(
                "SELECT COUNT(*) FROM latest_positions WHERE uas_id = %s",
                (uas_id,),
            )
            total = cur.fetchone()[0]

            cur.execute(
                """
                SELECT
                    uas_id,
                    NULLIF(computed_session_id, '') as computed_session_id,
                    max_ts as timestamp,
                    min_ts as session_start,
                    latitude, longitude, altitude, height, height_type, max_height,
                    operator_id, operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM latest_positions
                WHERE uas_id = %s
                ORDER BY max_ts DESC
                LIMIT %s OFFSET %s
                """,
                (uas_id, limit, offset),
            )
            sessions = [self._sanitize_record(dict(row)) for row in cur.fetchall()]
            return sessions, total
        finally:
            self._put_conn(conn)

    def get_drones_incremental(
        self,
        start_time: datetime,
        end_time: datetime,
        known_timestamps: Dict[str, str],
    ) -> List[Dict]:
        """Get drones that have newer data than the client's known timestamps."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

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
                            "(uas_id = %s AND computed_session_id = %s AND max_ts > %s)"
                        )
                        params.extend([uas_id, session_id, ts])
                    else:
                        conditions.append(
                            "(uas_id = %s AND computed_session_id = '' AND max_ts > %s)"
                        )
                        params.extend([uas_id, ts])
                else:
                    conditions.append(
                        "(uas_id = %s AND computed_session_id = '' AND max_ts > %s)"
                    )
                    params.extend([key, ts])

            results = []

            # Known sessions — query latest_positions directly
            if conditions:
                where_clause = " OR ".join(conditions)
                cur.execute(
                    f"""
                    SELECT
                        uas_id,
                        NULLIF(computed_session_id, '') as computed_session_id,
                        max_ts as timestamp, min_ts as session_start,
                        latitude, longitude, altitude, height, height_type, max_height,
                        operator_id,
                        operator_latitude, operator_longitude, source,
                        collector_latitude, collector_longitude
                    FROM latest_positions
                    WHERE ({where_clause})
                    ORDER BY uas_id, computed_session_id
                """,
                    params,
                )
                results.extend(cur.fetchall())

            # New sessions (not in known_timestamps).
            cur.execute(
                """
                SELECT
                    uas_id,
                    NULLIF(computed_session_id, '') as computed_session_id,
                    max_ts as timestamp, min_ts as session_start,
                    latitude, longitude, altitude, height, height_type, max_height,
                    operator_id,
                    operator_latitude, operator_longitude, source,
                    collector_latitude, collector_longitude
                FROM latest_positions
                WHERE max_ts BETWEEN %s AND %s
                  AND max_ts > %s
                ORDER BY uas_id, computed_session_id
            """,
                (start_time, end_time, oldest_known),
            )
            for row in cur.fetchall():
                sid = row['computed_session_id'] or 'unknown'
                key = f"{row['uas_id']}:{sid}"
                if key not in known_uas_sessions:
                    results.append(row)

            return [self._sanitize_record(dict(row)) for row in results]
        finally:
            self._put_conn(conn)

    def get_positions(
        self,
        start_time: datetime,
        end_time: datetime,
        uas_id: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict]:
        """Get positions within time window"""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            if uas_id:
                cur.execute(
                    """
                    SELECT * FROM remoteid
                    WHERE uas_id = %s AND timestamp BETWEEN %s AND %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """,
                    (uas_id, start_time, end_time, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM remoteid
                    WHERE timestamp BETWEEN %s AND %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """,
                    (start_time, end_time, limit),
                )

            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_track(
        self, uas_id: str, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Get track (ordered positions) for a specific drone with session info"""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cur.execute(
                """
                SELECT latitude, longitude, altitude, height, height_type, timestamp,
                       operator_id, operator_latitude, operator_longitude,
                       computed_session_id
                FROM remoteid
                WHERE uas_id = %s AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp ASC
            """,
                (uas_id, start_time, end_time),
            )

            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_track_session_positions(
        self, uas_id: str, session_id: str
    ) -> List[Dict]:
        """Get positions for a specific session using indexed lookup."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cur.execute(
                """
                SELECT latitude, longitude, altitude, height, height_type, timestamp,
                       operator_id, operator_latitude, operator_longitude,
                       computed_session_id, collector_latitude, collector_longitude,
                       source
                FROM remoteid
                WHERE uas_id = %s AND computed_session_id = %s
                ORDER BY timestamp ASC
            """,
                (uas_id, session_id),
            )

            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_track_sessions(
        self, uas_id: str, start_time: datetime, end_time: datetime
    ) -> List[Dict]:
        """Get track grouped by session"""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cur.execute(
                """
                SELECT latitude, longitude, altitude, height, height_type, timestamp,
                       operator_id, operator_latitude, operator_longitude,
                       computed_session_id, collector_latitude, collector_longitude,
                       source
                FROM remoteid
                WHERE uas_id = %s AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp ASC
            """,
                (uas_id, start_time, end_time),
            )

            positions = [self._sanitize_record(dict(row)) for row in cur.fetchall()]

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

            result = list(sessions.values())
            result.sort(key=lambda s: s['positions'][0]['timestamp'] if s['positions'] else datetime.min)

            return result
        finally:
            self._put_conn(conn)

    def get_operators(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get latest operator positions for drones in time window"""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            cur.execute(
                """
                SELECT r1.uas_id, r1.operator_id, r1.operator_latitude,
                       r1.operator_longitude, r1.timestamp
                FROM remoteid r1
                INNER JOIN (
                    SELECT uas_id, MAX(timestamp) as max_ts
                    FROM remoteid
                    WHERE timestamp BETWEEN %s AND %s
                    AND operator_latitude IS NOT NULL
                    AND operator_latitude != 0
                    GROUP BY uas_id
                ) r2 ON r1.uas_id = r2.uas_id AND r1.timestamp = r2.max_ts
                ORDER BY r1.uas_id
            """,
                (start_time, end_time),
            )

            return [self._sanitize_record(dict(row)) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_bounds(self, start_time: datetime, end_time: datetime) -> Optional[Tuple]:
        """Get bounding box of all positions in time window"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude)
                FROM remoteid
                WHERE timestamp BETWEEN %s AND %s
            """,
                (start_time, end_time),
            )

            row = cur.fetchone()
            if row and row[0] is not None:
                return row
            return None
        finally:
            self._put_conn(conn)

    def insert_remoteid_records(
        self,
        source: str,
        records: List[Dict],
        session_gap_threshold: int = 600,
        source_tz: Optional[str] = None,
        collector_lat: Optional[float] = None,
        collector_lon: Optional[float] = None,
    ) -> Tuple[int, List[Dict], Optional[datetime]]:
        # pylint: disable=too-many-locals,too-many-positional-arguments,too-many-branches
        """Insert multiple records into remoteid table with session detection.

        Uses INSERT ... ON CONFLICT DO NOTHING with a UNIQUE index on
        (uas_id, source, timestamp) to skip duplicates without per-record
        SELECT checks. Including source means two different collectors that
        observe the same drone packet at the same timestamp each keep their
        own row (enabling per-collector attribution).
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
        try:
            cur = conn.cursor()

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

            # Use execute_values for efficient batch insert. RETURNING id
            # counts exactly the rows actually inserted (ON CONFLICT rows are
            # excluded), avoiding a full-table COUNT before/after.
            inserted_rows = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO remoteid
                (source, timestamp, mac_address, uas_id, session_id,
                 latitude, longitude, altitude, height, height_type,
                 operator_id, operator_latitude, operator_longitude,
                 computed_session_id, session_detected_at,
                 collector_latitude, collector_longitude)
                VALUES %s
                ON CONFLICT (uas_id, source, timestamp) DO NOTHING
                RETURNING id
                """,
                rows,
                page_size=1000,
                fetch=True,
            )
            self._commit(conn)

            inserted = len(inserted_rows)

            # Update materialized latest_positions for affected UAS IDs
            if inserted > 0:
                self.rebuild_latest_positions(affected_uas_ids)

            return inserted, errors, most_recent
        finally:
            self._put_conn(conn)

    def get_most_recent_timestamp(
        self, source: Optional[str] = None
    ) -> Optional[datetime]:
        """Get the most recent timestamp in the database."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if source:
                cur.execute(
                    "SELECT MAX(timestamp) FROM remoteid WHERE source = %s",
                    (source,),
                )
            else:
                cur.execute("SELECT MAX(timestamp) FROM remoteid")

            row = cur.fetchone()
            if row and row[0]:
                return row[0]
            return None
        finally:
            self._put_conn(conn)

    def get_stats(self, start_time: datetime, end_time: datetime) -> Dict:
        """Get aggregate statistics for the given time window."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    COUNT(DISTINCT uas_id),
                    COUNT(DISTINCT CASE WHEN computed_session_id IS NOT NULL THEN computed_session_id END),
                    COUNT(*)
                FROM remoteid WHERE timestamp BETWEEN %s AND %s
                """,
                (start_time, end_time),
            )
            row = cur.fetchone()
            total_drones = row[0] or 0
            total_sessions = row[1] or 0
            total_positions = row[2] or 0

            cur.execute(
                "SELECT COUNT(*) FROM geozone_events WHERE exited_at IS NULL"
            )
            active_alerts = cur.fetchone()[0] or 0

            cur.execute(
                "SELECT COUNT(*) FROM geozone_events WHERE entered_at BETWEEN %s AND %s",
                (start_time, end_time),
            )
            total_alerts = cur.fetchone()[0] or 0

            return {
            "total_drones": total_drones,
            "total_sessions": total_sessions,
            "total_positions": total_positions,
            "active_alerts": active_alerts,
            "total_alerts": total_alerts,
            }
        finally:
            self._put_conn(conn)

    def get_drones_for_alert_check(
        self, since: Optional[datetime] = None
    ) -> List[str]:
        """Get distinct UAS IDs with positions since *since* (for alert evaluation)."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if since:
                cur.execute(
                    "SELECT DISTINCT uas_id FROM remoteid WHERE timestamp >= %s",
                    (since,),
                )
            else:
                cur.execute("SELECT DISTINCT uas_id FROM remoteid")
            return [row[0] for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_positions_for_alert_check(
        self, uas_id: str, since: Optional[datetime] = None
    ) -> List[Dict]:
        """Get positions for a UAS since *since* (for alert evaluation)."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if since:
                cur.execute(
                    """SELECT latitude, longitude, timestamp
                       FROM remoteid
                       WHERE uas_id = %s AND timestamp >= %s
                       ORDER BY timestamp ASC""",
                    (uas_id, since),
                )
            else:
                cur.execute(
                    """SELECT latitude, longitude, timestamp
                       FROM remoteid
                       WHERE uas_id = %s
                       ORDER BY timestamp ASC""",
                    (uas_id,),
                )
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    # --- Session tracking helpers ---

    def get_latest_session_id(self, uas_id: str) -> Optional[str]:
        """Get the most recent ``computed_session_id`` for a UAS, or ``None``."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT computed_session_id FROM remoteid
                   WHERE uas_id = %s AND computed_session_id IS NOT NULL
                   ORDER BY timestamp DESC LIMIT 1""",
                (uas_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            self._put_conn(conn)

    def get_all_current_sessions(self) -> Dict[str, str]:
        """Return the latest ``computed_session_id`` for every UAS that has one."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT r.uas_id, r.computed_session_id
                   FROM remoteid r
                   INNER JOIN (
                       SELECT uas_id, MAX(timestamp) AS max_ts
                       FROM remoteid WHERE computed_session_id IS NOT NULL
                       GROUP BY uas_id
                   ) latest ON r.uas_id = latest.uas_id AND r.timestamp = latest.max_ts
                   WHERE r.computed_session_id IS NOT NULL"""
            )
            return {row[0]: row[1] for row in cur.fetchall()}
        finally:
            self._put_conn(conn)

    # --- Alert dedup helpers ---

    def claim_alert(
        self,
        alert_type: str,
        dedup_key: str,
        uas_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Atomically claim the right to fire an alert for *dedup_key*.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` against the unique
        ``(alert_type, dedup_key)`` constraint on ``sent_alerts``.
        """
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO sent_alerts
                    (alert_type, dedup_key, uas_id, session_id, sent_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (alert_type, dedup_key) DO NOTHING
                """,
                (alert_type, dedup_key, uas_id, session_id, datetime.now(timezone.utc)),
            )
            self._commit(conn)
            return cur.rowcount == 1
        finally:
            self._put_conn(conn)

    # --- Geozone event methods ---

    def get_active_geozone_events(self) -> List[Dict]:
        """Get all active (not yet exited) geozone events."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """
                SELECT * FROM geozone_events
                WHERE exited_at IS NULL
                ORDER BY entered_at DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def get_geozone_events_for_uas(self, uas_id: str) -> List[Dict]:
        """Get all events for a specific UAS, active first."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """
                SELECT * FROM geozone_events
                WHERE uas_id = %s
                ORDER BY exited_at IS NULL DESC, entered_at DESC
                """,
                (uas_id,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def enter_geozone(
        self, uas_id: str, geozone_name: str, timestamp: datetime
    ) -> Tuple[int, bool]:
        """Create a new geozone entry event if no active event exists."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO geozone_events (uas_id, geozone_name, entered_at, last_seen_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (uas_id, geozone_name, timestamp, timestamp),
            )
            created = cur.rowcount == 1
            cur.execute(
                "SELECT id FROM geozone_events "
                "WHERE uas_id = %s AND geozone_name = %s AND exited_at IS NULL",
                (uas_id, geozone_name),
            )
            event_id = cur.fetchone()[0]
            self._commit(conn)
            return event_id, created
        finally:
            self._put_conn(conn)

    def update_geozone_last_seen(self, event_id: int, timestamp: datetime):
        """Update last_seen_at for an active event."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE geozone_events SET last_seen_at = %s WHERE id = %s",
                (timestamp, event_id),
            )
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def exit_geozone(self, event_id: int, timestamp: datetime, reason: str = "left") -> int:
        """Mark a geozone event as exited."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE geozone_events SET exited_at = %s, exited_reason = %s "
                "WHERE id = %s AND exited_at IS NULL",
                (timestamp, reason, event_id),
            )
            self._commit(conn)
            return cur.rowcount
        finally:
            self._put_conn(conn)

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
        """Get geozone event history with filtering and pagination."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            conditions = []
            params = []

            if uas_id:
                conditions.append("uas_id = %s")
                params.append(uas_id)
            if geozone_name:
                conditions.append("geozone_name = %s")
                params.append(geozone_name)
            if from_date:
                conditions.append("entered_at >= %s")
                params.append(from_date)
            if to_date:
                conditions.append("entered_at <= %s")
                params.append(to_date)

            where = " AND ".join(conditions) if conditions else "1=1"

            # Get total count
            cur.execute(
                f"SELECT COUNT(*) FROM geozone_events WHERE {where}", params
            )
            total = cur.fetchone()[0]

            # Get paginated results
            query_params = params + [limit, offset]
            cur.execute(
                f"""
                SELECT * FROM geozone_events
                WHERE {where}
                ORDER BY entered_at DESC
                LIMIT %s OFFSET %s
                """,
                query_params,
            )
            events = [dict(row) for row in cur.fetchall()]

            return events, total
        finally:
            self._put_conn(conn)

    def check_stale_geozone_events(
        self, stale_timeout: int, reference_time: datetime
    ) -> int:
        """Mark events stale (timed out) where last_seen_at is older than timeout."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE geozone_events
                SET exited_at = last_seen_at, exited_reason = 'timeout'
                WHERE exited_at IS NULL
                AND last_seen_at < %s
                """,
                (reference_time - timedelta(seconds=stale_timeout),),
            )
            self._commit(conn)
            return cur.rowcount
        finally:
            self._put_conn(conn)

    def get_live_positions(self, since: datetime) -> List[Dict]:
        """Get the most recent position for each drone that has been updated since *since*."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """
                SELECT DISTINCT ON (uas_id) uas_id, latitude, longitude, max_ts
                FROM latest_positions
                WHERE latitude IS NOT NULL
                  AND longitude IS NOT NULL
                  AND max_ts >= %s
                ORDER BY uas_id, max_ts DESC
                """,
                (since,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    def update_collector_position(self, name: str, lat: float, lon: float):
        """Insert or replace a collector's current position"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO collector_positions (name, latitude, longitude, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    updated_at = NOW()
                """,
                (name, lat, lon),
            )
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def get_collector_positions(self) -> List[Dict]:
        """Get all current collector positions"""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                "SELECT name, latitude, longitude, updated_at FROM collector_positions ORDER BY name"
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            self._put_conn(conn)

    # --- Auth methods ---

    def create_user(  # pylint: disable=too-many-positional-arguments
        self, name: str, email: str, role_name: str,
        login_token: str, login_token_expires_at: datetime
    ) -> dict:
        """Create a pre-created user with a login token."""
        token_hash = hashlib.sha256(login_token.encode()).hexdigest()
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """
                INSERT INTO users (name, email, role_name, is_ephemeral, is_active,
                                   login_token_hash, login_token_expires_at, auth_method)
                VALUES (%s, %s, %s, 0, 1, %s, %s, 'login_link')
                RETURNING *
                """,
                (name, email, role_name, token_hash, login_token_expires_at),
            )
            self._commit(conn)
            return dict(cur.fetchone())
        finally:
            self._put_conn(conn)

    def create_ephemeral_user(self) -> Tuple[str, int]:
        """Create an ephemeral visitor user and an auth token."""
        name = f"Guest-{_secrets.token_hex(4)}"
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO users (name, role_name, is_ephemeral, is_active, auth_method)
                   VALUES (%s, 'guest', 1, 1, 'ephemeral')
                   RETURNING id""",
                (name,),
            )
            user_id = cur.fetchone()[0]
            session_token = _secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(session_token.encode()).hexdigest()
            expires_at = datetime.now(timezone.utc) + timedelta(days=90)
            cur.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user_id, token_hash, expires_at),
            )
            self._commit(conn)
            return session_token, user_id
        finally:
            self._put_conn(conn)

    def exchange_login_token(self, login_token: str) -> Optional[Tuple[str, dict]]:
        """Exchange a one-time login token for a session token."""
        token_hash = hashlib.sha256(login_token.encode()).hexdigest()
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                "SELECT * FROM users WHERE login_token_hash = %s AND login_token_expires_at > %s AND is_active = 1",
                (token_hash, datetime.now(timezone.utc)),
            )
            user = cur.fetchone()
            if not user:
                return None

            # Clear the one-time login token
            cur.execute(
                "UPDATE users SET login_token_hash = NULL, login_token_expires_at = NULL WHERE id = %s",
                (user["id"],),
            )

            # Create session token
            session_token = _secrets.token_urlsafe(32)
            session_hash = hashlib.sha256(session_token.encode()).hexdigest()
            expires_at = datetime.now(timezone.utc) + timedelta(days=90)
            cur.execute(
                "INSERT INTO auth_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user["id"], session_hash, expires_at),
            )
            self._commit(conn)

            return session_token, dict(user)
        finally:
            self._put_conn(conn)

    def get_user_by_auth_token(self, token: str) -> Optional[dict]:
        """Look up a user by their session auth token."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                """SELECT u.* FROM users u JOIN auth_tokens t ON u.id = t.user_id
                   WHERE t.token_hash = %s AND t.expires_at > %s AND u.is_active = 1""",
                (token_hash, datetime.now(timezone.utc)),
            )
            user = cur.fetchone()
            return dict(user) if user else None
        finally:
            self._put_conn(conn)

    def revoke_token(self, token: str):
        """Revoke (delete) an auth token."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_tokens WHERE token_hash = %s", (token_hash,))
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def revoke_all_user_tokens(self, user_id: int):
        """Revoke all auth tokens for a given user."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_tokens WHERE user_id = %s", (user_id,))
            self._commit(conn)
        finally:
            self._put_conn(conn)

    def upgrade_ephemeral_user(self, ephemeral_user_id: int, target_user_id: int) -> bool:
        """Merge a pre-created user into an ephemeral user record."""
        conn = self._get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                "SELECT name, email, role_name FROM users WHERE id = %s AND is_active = 1",
                (target_user_id,),
            )
            target = cur.fetchone()
            if not target:
                return False

            cur.execute(
                "UPDATE users SET name=%s, email=%s, role_name=%s, auth_method='upgraded' WHERE id=%s",
                (target["name"], target["email"], target["role_name"], ephemeral_user_id),
            )
            cur.execute("DELETE FROM auth_tokens WHERE user_id = %s", (target_user_id,))
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (target_user_id,))
            self._commit(conn)
            return True
        finally:
            self._put_conn(conn)
