"""Database layer for web interface"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


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
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        """Initialize the database schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
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
                    operator_id TEXT,
                    operator_latitude REAL,
                    operator_longitude REAL
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

            # Create indexes
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_uas_time ON remoteid(uas_id, timestamp)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON remoteid(source)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON remoteid(timestamp)"
            )

            conn.commit()
        logger.debug("Database initialized at %s", self.db_path)

    @staticmethod
    def _validate_record(row: tuple) -> Optional[tuple]:
        """Validate and sanitize a record before import.

        Returns sanitized tuple or None if record is invalid.
        row: (id, timestamp, mac_address, uas_id, session_id, lat, lon, alt, op_id, op_lat, op_lon)
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
        )

    def import_from_collector(self, source_db_path: str, source_name: str) -> int:
        """Import new records from a collector's database"""
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
                source_db_path, detect_types=sqlite3.PARSE_DECLTYPES
            ) as src_conn:
                if last_sync:
                    cursor = src_conn.execute(
                        f"SELECT {columns} FROM remoteid WHERE timestamp > ? ORDER BY timestamp",
                        (last_sync,),
                    )
                else:
                    cursor = src_conn.execute(
                        f"SELECT {columns} FROM remoteid ORDER BY timestamp"
                    )

                # Import into web database using named parameters
                with sqlite3.connect(
                    self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
                ) as dest_conn:
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

                            dest_conn.execute(
                                """
                                INSERT INTO remoteid
                                (source, timestamp, mac_address, uas_id, session_id,
                                 latitude, longitude, altitude, operator_id,
                                 operator_latitude, operator_longitude)
                                VALUES (:source, :timestamp, :mac_address, :uas_id, :session_id,
                                        :latitude, :longitude, :altitude, :operator_id,
                                        :operator_latitude, :operator_longitude)
                            """,
                                {
                                    "source": source_name,
                                    "timestamp": validated[0],
                                    "mac_address": validated[1],
                                    "uas_id": validated[2],
                                    "session_id": validated[3],
                                    "latitude": validated[4],
                                    "longitude": validated[5],
                                    "altitude": validated[6],
                                    "operator_id": validated[7],
                                    "operator_latitude": validated[8],
                                    "operator_longitude": validated[9],
                                },
                            )
                            count += 1

                    dest_conn.commit()

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
            "operator_latitude",
            "operator_longitude",
        ]
        for key, value in record.items():
            if key in coord_keys:
                sanitized[key] = WebDatabase._sanitize_float(value, key)
            elif key == "timestamp":
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
            if key in ("longitude", "operator_longitude") and (fval < -180 or fval > 180):
                return None
            return fval
        except (TypeError, ValueError):
            logger.debug("Non-numeric value for %s, setting to None", key)
            return None

    @staticmethod
    def _sanitize_timestamp(value) -> Optional[str]:
        """Sanitize a timestamp value."""
        if value is None:
            return None
        return str(value)

    def _get_last_sync(self, source_name: str) -> Optional[datetime]:
        """Get the last sync time for a source"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
            cursor = conn.execute(
                "SELECT last_sync FROM sync_log WHERE source = ? ORDER BY last_sync DESC LIMIT 1",
                (source_name,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def _update_sync_log(self, source_name: str, count: int):
        """Update the sync log for a source"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
            conn.execute(
                "INSERT INTO sync_log (source, last_sync, records_imported) VALUES (?, ?, ?)",
                (source_name, datetime.now(), count),
            )
            conn.commit()

    def get_drones(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get list of unique drones seen in time window with latest positions"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
            conn.row_factory = sqlite3.Row

            # Get latest position for each drone in time window
            cursor = conn.execute(
                """
                SELECT r1.uas_id, r1.latitude, r1.longitude, r1.altitude,
                       r1.timestamp, r1.operator_id, r1.operator_latitude, r1.operator_longitude,
                       r1.source
                FROM remoteid r1
                INNER JOIN (
                    SELECT uas_id, MAX(timestamp) as max_ts
                    FROM remoteid
                    WHERE timestamp BETWEEN ? AND ?
                    GROUP BY uas_id
                ) r2 ON r1.uas_id = r2.uas_id AND r1.timestamp = r2.max_ts
                ORDER BY r1.uas_id
            """,
                (start_time, end_time),
            )

            return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_positions(
        self,
        start_time: datetime,
        end_time: datetime,
        uas_id: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict]:
        """Get positions within time window"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
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
        """Get track (ordered positions) for a specific drone"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT latitude, longitude, altitude, timestamp,
                       operator_id, operator_latitude, operator_longitude
                FROM remoteid
                WHERE uas_id = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp ASC
            """,
                (uas_id, start_time, end_time),
            )

            return [self._sanitize_record(dict(row)) for row in cursor.fetchall()]

    def get_operators(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Get latest operator positions for drones in time window"""
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
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
        with sqlite3.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
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
