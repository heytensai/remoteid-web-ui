"""Tests for session_detect.py - standalone session detection"""

from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from session_detect import (
    get_uas_list,
    get_positions_for_uas,
    detect_sessions,
    update_session_ids,
    analyze_sessions,
    process_database,
)
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def populated_db(pg_conn):
    """Create a PG connection with known records for session detection tests."""
    cur = pg_conn.cursor()
    # Ensure tables exist (conftest setup_test_db creates the DB but tables
    # are created by WebDatabase._init_db; create remoteid here for standalone use)
    cur.execute("""
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
    """)
    pg_conn.commit()
    cur.execute("TRUNCATE TABLE remoteid RESTART IDENTITY CASCADE")
    pg_conn.commit()

    now = datetime.now(timezone.utc)
    records = [
        (now - timedelta(hours=2), "drone-001", 37.0, -122.0),
        (now - timedelta(hours=1, minutes=55), "drone-001", 37.1, -122.1),
        (now - timedelta(minutes=30), "drone-001", 37.2, -122.2),
        (now - timedelta(minutes=25), "drone-001", 37.3, -122.3),
        (now - timedelta(hours=1), "drone-002", 38.0, -123.0),
    ]
    for ts, uas, lat, lon in records:
        cur.execute(
            "INSERT INTO remoteid (source, timestamp, uas_id, latitude, longitude) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("test", ts, uas, lat, lon),
        )
    pg_conn.commit()
    yield pg_conn
    # Cleanup
    cur.execute("TRUNCATE TABLE remoteid RESTART IDENTITY CASCADE")
    pg_conn.commit()


@pytest.fixture
def empty_db(pg_conn):
    """Create a PG connection with no records."""
    cur = pg_conn.cursor()
    cur.execute("""
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
    """)
    pg_conn.commit()
    cur.execute("TRUNCATE TABLE remoteid RESTART IDENTITY CASCADE")
    pg_conn.commit()
    yield pg_conn
    cur.execute("TRUNCATE TABLE remoteid RESTART IDENTITY CASCADE")
    pg_conn.commit()


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
        cur = populated_db.cursor()
        cur.execute("SELECT computed_session_id FROM remoteid WHERE id = 1")
        val = cur.fetchone()[0]
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
        cur = populated_db.cursor()
        cur.execute("SELECT computed_session_id FROM remoteid LIMIT 1")
        val = cur.fetchone()[0]
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
        """Connection to nonexistent PG database raises an error."""
        with pytest.raises(psycopg2.OperationalError):
            conn = psycopg2.connect("postgresql://invalid:invalid@nonexistent:5432/fake")
            conn.close()

    def test_process_database_empty(self, empty_db):
        result, uas_list = process_database(empty_db, 600)
        assert "0 UAS" in result
        assert isinstance(uas_list, list)
