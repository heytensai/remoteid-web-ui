"""Tests for session_detect.py - standalone session detection"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from session_detect import (
    get_uas_list,
    get_positions_for_uas,
    detect_sessions,
    update_session_ids,
    analyze_sessions,
    process_database,
)


@pytest.fixture
def populated_db():
    """Create a temp DB with known records for session detection tests."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("""
        CREATE TABLE remoteid(
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
            operator_longitude REAL,
            computed_session_id TEXT,
            session_detected_at DATETIME,
            collector_latitude REAL,
            collector_longitude REAL
        )
    """)
    now = datetime.now(timezone.utc)
    records = [
        (now - timedelta(hours=2), "drone-001", 37.0, -122.0),
        (now - timedelta(hours=1, minutes=55), "drone-001", 37.1, -122.1),
        (now - timedelta(minutes=30), "drone-001", 37.2, -122.2),
        (now - timedelta(minutes=25), "drone-001", 37.3, -122.3),
        (now - timedelta(hours=1), "drone-002", 38.0, -123.0),
    ]
    for i, (ts, uas, lat, lon) in enumerate(records):
        conn.execute(
            "INSERT INTO remoteid (source, timestamp, uas_id, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
            ("test", ts, uas, lat, lon),
        )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def empty_db():
    """Create an empty temp DB with no records."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("""
        CREATE TABLE remoteid(
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
            operator_longitude REAL,
            computed_session_id TEXT,
            session_detected_at DATETIME,
            collector_latitude REAL,
            collector_longitude REAL
        )
    """)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


class TestGetUasList:
    def test_get_uas_list(self, populated_db):
        uas_list = get_uas_list(populated_db)
        assert len(uas_list) == 2
        assert "drone-001" in uas_list
        assert "drone-002" in uas_list

    def test_get_uas_list_since(self, populated_db):
        now = datetime.now(timezone.utc)
        recent = get_uas_list(populated_db, since=now - timedelta(hours=3))
        assert "drone-001" in recent
        assert "drone-002" in recent
        far_future = get_uas_list(populated_db, since=now + timedelta(hours=1))
        assert far_future == []

    def test_get_uas_list_empty(self, empty_db):
        assert get_uas_list(empty_db) == []


class TestGetPositionsForUas:
    def test_get_positions(self, populated_db):
        positions = get_positions_for_uas(populated_db, "drone-001")
        assert len(positions) == 4
        for pos_id, ts in positions:
            assert isinstance(pos_id, int)
            assert isinstance(ts, datetime)

    def test_get_positions_nonexistent(self, populated_db):
        assert get_positions_for_uas(populated_db, "nonexistent") == []


class TestDetectSessions:
    def test_empty_positions(self):
        assert detect_sessions([], 600) == []

    def test_single_position(self):
        now = datetime.now(timezone.utc)
        positions = [(1, now)]
        sessions = detect_sessions(positions, 600)
        assert len(sessions) == 1
        assert sessions[0][0] == 1
        assert sessions[0][1].startswith("session_")

    def test_single_session(self):
        now = datetime.now(timezone.utc)
        positions = [(1, now), (2, now + timedelta(seconds=10))]
        sessions = detect_sessions(positions, 600)
        assert len(sessions) == 2
        assert sessions[0][1] == sessions[1][1]

    def test_gap_detects_new_session(self):
        now = datetime.now(timezone.utc)
        positions = [
            (1, now),
            (2, now + timedelta(seconds=10)),
            (3, now + timedelta(seconds=700)),
            (4, now + timedelta(seconds=710)),
        ]
        sessions = detect_sessions(positions, 600)
        assert len(sessions) == 4
        assert sessions[0][1] == sessions[1][1]
        assert sessions[2][1] == sessions[3][1]
        assert sessions[0][1] != sessions[2][1]

    def test_exact_boundary_gap(self):
        """Gap exactly equal to threshold does NOT create new session."""
        now = datetime.now(timezone.utc)
        positions = [(1, now), (2, now + timedelta(seconds=600))]
        sessions = detect_sessions(positions, 600)
        assert len(sessions) == 2
        assert sessions[0][1] == sessions[1][1]


class TestUpdateSessionIds:
    def test_update(self, populated_db):
        update_session_ids(populated_db, [("session_test_123", datetime.now(), 1)])
        import sqlite3
        conn = sqlite3.connect(populated_db, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = conn.execute("SELECT computed_session_id FROM remoteid WHERE id = 1")
        val = cursor.fetchone()[0]
        conn.close()
        assert val == "session_test_123"


class TestAnalyzeSessions:
    def test_analyze_sessions_all(self, populated_db):
        """Process DB with sessions, then analyze."""
        process_database(populated_db, 600)
        result = analyze_sessions(populated_db)
        assert result["total_count"] >= 2

    def test_analyze_sessions_for_uas(self, populated_db):
        process_database(populated_db, 600)
        result = analyze_sessions(populated_db, "drone-001")
        assert result["total_count"] >= 1
        for session in result["sessions"]:
            assert "session_id" in session or "computed_session_id" in session
            assert "duration_seconds" in session

    def test_analyze_sessions_empty(self, empty_db):
        result = analyze_sessions(empty_db)
        assert result["total_count"] == 0
        assert result["sessions"] == []


class TestProcessDatabase:
    def test_process_database(self, populated_db):
        result, uas_list = process_database(populated_db, 600)
        assert "UAS" in result
        assert "sessions" in result
        assert isinstance(uas_list, list)

    def test_process_database_dry_run(self, populated_db):
        """Dry run doesn't modify the DB."""
        result, uas_list = process_database(populated_db, 600, dry_run=True)
        assert "dry" not in result or "dry" not in result.lower()
        assert isinstance(uas_list, list)
        import sqlite3
        conn = sqlite3.connect(populated_db, detect_types=sqlite3.PARSE_DECLTYPES)
        cursor = conn.execute("SELECT computed_session_id FROM remoteid LIMIT 1")
        val = cursor.fetchone()[0]
        conn.close()
        assert val is None

    def test_process_database_force(self, populated_db):
        result, uas_list = process_database(populated_db, 600, force=True)
        assert "UAS" in result
        assert isinstance(uas_list, list)

    def test_process_database_since(self, populated_db):
        now = datetime.now(timezone.utc)
        result, uas_list = process_database(populated_db, 600, since=now - timedelta(minutes=20))
        assert "UAS" in result
        assert isinstance(uas_list, list)

    def test_process_database_not_found(self):
        result, uas_list = process_database("/nonexistent/db.sqlite", 600)
        assert result == "database not found"
        assert uas_list == []

    def test_process_database_empty(self, empty_db):
        result, uas_list = process_database(empty_db, 600)
        assert "0 UAS" in result
        assert isinstance(uas_list, list)
