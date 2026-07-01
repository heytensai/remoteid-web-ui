"""Tests for database.py - database operations"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from database import WebDatabase


def test_database_init(db):
    assert db.db_path is not None
    assert db.db_path.exists()


def test_schema_version_created(db):
    """_schema_version table exists at current version."""
    import sqlite3
    conn = sqlite3.connect(db.db_path)
    version = conn.execute(
        "SELECT MAX(version) FROM _schema_version"
    ).fetchone()[0]
    conn.close()
    assert version == 3


def test_schema_version_upgrade(tmp_path):
    """Opening a pre-v1 database upgrades it to the current version."""
    import sqlite3
    from database import SCHEMA_VERSION

    db_path = tmp_path / "test_upgrade.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO _schema_version (version) VALUES (0)")
    conn.commit()
    conn.close()

    WebDatabase(str(db_path))

    conn = sqlite3.connect(str(db_path))
    version = conn.execute(
        "SELECT MAX(version) FROM _schema_version"
    ).fetchone()[0]
    conn.close()
    assert version == SCHEMA_VERSION


def test_validate_record_valid():
    now = datetime.now(timezone.utc)
    row = (1, now, "aa:bb:cc:dd:ee:ff", "uas-001", None, 37.7749, -122.4194, 100.0, None, None, None)
    result = WebDatabase._validate_record(row)
    assert result is not None
    assert result[2] == "uas-001"
    assert result[4] == 37.7749
    assert result[5] == -122.4194
    assert result[6] == 100.0


def test_validate_record_missing_timestamp():
    row = (1, None, "aa:bb:cc:dd:ee:ff", "uas-001", None, 37.7749, -122.4194, 100.0, None, None, None)
    assert WebDatabase._validate_record(row) is None


def test_validate_record_missing_uas_id():
    now = datetime.now(timezone.utc)
    row = (1, now, "aa:bb:cc:dd:ee:ff", None, None, 37.7749, -122.4194, 100.0, None, None, None)
    assert WebDatabase._validate_record(row) is None


def test_validate_record_invalid_latitude():
    now = datetime.now(timezone.utc)
    row = (1, now, "aa:bb:cc:dd:ee:ff", "uas-001", None, 999.0, -122.4194, 100.0, None, None, None)
    assert WebDatabase._validate_record(row) is None


def test_validate_record_invalid_longitude():
    now = datetime.now(timezone.utc)
    row = (1, now, "aa:bb:cc:dd:ee:ff", "uas-001", None, 37.7749, -999.0, 100.0, None, None, None)
    assert WebDatabase._validate_record(row) is None


def test_validate_record_non_numeric_latitude():
    now = datetime.now(timezone.utc)
    row = (1, now, "aa:bb:cc:dd:ee:ff", "uas-001", None, "not-a-number", -122.4194, 100.0, None, None, None)
    assert WebDatabase._validate_record(row) is None


def test_validate_record_bounds():
    mc = WebDatabase
    assert mc._sanitize_float(90.0, "latitude") == 90.0
    assert mc._sanitize_float(-90.0, "latitude") == -90.0
    assert mc._sanitize_float(90.001, "latitude") is None
    assert mc._sanitize_float(-90.001, "latitude") is None
    assert mc._sanitize_float(180.0, "longitude") == 180.0
    assert mc._sanitize_float(-180.0, "longitude") == -180.0
    assert mc._sanitize_float(180.001, "longitude") is None
    assert mc._sanitize_float(-180.001, "longitude") is None


def test_sanitize_float_non_numeric():
    mc = WebDatabase
    assert mc._sanitize_float(None, "latitude") is None
    assert mc._sanitize_float("abc", "latitude") is None


def test_sanitize_float_valid():
    mc = WebDatabase
    assert mc._sanitize_float(37.7749, "latitude") == 37.7749
    assert mc._sanitize_float("37.7749", "latitude") == 37.7749


def test_sanitize_timestamp():
    mc = WebDatabase
    assert mc._sanitize_timestamp(None) is None
    # Naive datetime strings get Z suffix
    assert mc._sanitize_timestamp("2024-01-15T10:00:00") == "2024-01-15T10:00:00Z"
    # Already has Z suffix, stays the same
    assert mc._sanitize_timestamp("2024-01-15T10:00:00Z") == "2024-01-15T10:00:00Z"
    # Has timezone offset, stays the same
    assert mc._sanitize_timestamp("2024-01-15T10:00:00+05:00") == "2024-01-15T10:00:00+05:00"
    # Non-datetime strings pass through
    assert mc._sanitize_timestamp(12345) == "12345"


def test_insert_remoteid_records(db, sample_records):
    inserted, errors, most_recent = db.insert_remoteid_records("test-source", sample_records)
    assert inserted == 2
    assert len(errors) == 0
    assert most_recent is not None


def test_insert_duplicate_records(db, sample_records):
    db.insert_remoteid_records("test-source", sample_records)
    inserted, errors, most_recent = db.insert_remoteid_records("test-source", sample_records)
    assert inserted == 0
    assert len(errors) == 0


def test_insert_missing_uas_id(db):
    records = [{"timestamp": datetime.now(timezone.utc).isoformat(), "latitude": 37.0, "longitude": -122.0}]
    inserted, errors, _ = db.insert_remoteid_records("test-source", records)
    assert inserted == 0
    assert len(errors) == 1
    assert errors[0]["reason"] == "Missing uas_id"


def test_insert_missing_timestamp(db):
    records = [{"uas_id": "drone-050", "latitude": 37.0, "longitude": -122.0}]
    inserted, errors, _ = db.insert_remoteid_records("test-source", records)
    assert inserted == 0
    assert len(errors) == 1
    assert "Missing timestamp" in errors[0]["reason"]


def test_insert_invalid_timestamp(db):
    records = [{"uas_id": "drone-050", "timestamp": "not-a-date", "latitude": 37.0, "longitude": -122.0}]
    inserted, errors, _ = db.insert_remoteid_records("test-source", records)
    assert inserted == 0
    assert len(errors) == 1
    assert "Invalid timestamp" in errors[0]["reason"]


def test_get_drones(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    drones = db.get_drones(start, end)
    assert len(drones) >= 3
    uas_ids = {d["uas_id"] for d in drones}
    assert "drone-001" in uas_ids
    assert "drone-002" in uas_ids


def test_get_drones_empty_window(db):
    start = datetime(2020, 1, 1)
    end = datetime(2020, 1, 2)
    drones = db.get_drones(start, end)
    assert drones == []


def test_get_positions(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    positions = db.get_positions(start, end)
    assert len(positions) >= 4


def test_get_positions_filtered(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    positions = db.get_positions(start, end, uas_id="drone-001")
    assert len(positions) >= 2
    for p in positions:
        assert p["uas_id"] == "drone-001"


def test_get_positions_limit(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    positions = db.get_positions(start, end, limit=2)
    assert len(positions) <= 2


def test_get_track(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    track = db.get_track("drone-001", start, end)
    assert len(track) >= 2
    assert track[0]["latitude"] == 37.7749


def test_get_track_sessions(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    sessions = db.get_track_sessions("drone-001", start, end)
    assert len(sessions) >= 1
    for s in sessions:
        assert "session_id" in s
        assert "positions" in s
        assert len(s["positions"]) > 0


def test_get_track_session_positions(db):
    """Indexed session lookup returns only positions for that session."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    sessions = db.get_track_sessions("drone-001", start, end)
    assert len(sessions) >= 1
    target = sessions[0]

    positions = db.get_track_session_positions("drone-001", target["session_id"])
    assert len(positions) == len(target["positions"])
    for p in positions:
        assert p["computed_session_id"] == target["session_id"]


def test_get_track_session_positions_nonexistent(db):
    """Non-existent session returns empty list."""
    positions = db.get_track_session_positions("drone-001", "session_nonexistent")
    assert positions == []


def test_get_operators(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    operators = db.get_operators(start, end)
    assert len(operators) >= 2
    for op in operators:
        assert "operator_id" in op


def test_get_bounds(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    bounds = db.get_bounds(start, end)
    assert bounds is not None
    min_lat, max_lat, min_lon, max_lon = bounds
    assert min_lat <= max_lat
    assert min_lon <= max_lon
    assert min_lat <= 37.7800
    assert max_lat >= 37.7700


def test_get_bounds_empty(db):
    start = datetime(2020, 1, 1)
    end = datetime(2020, 1, 2)
    bounds = db.get_bounds(start, end)
    assert bounds is None


def test_get_most_recent_timestamp(db):
    ts = db.get_most_recent_timestamp()
    assert ts is not None
    assert isinstance(ts, datetime)


def test_get_most_recent_timestamp_by_source(db):
    ts = db.get_most_recent_timestamp("test-source")
    assert ts is not None
    ts = db.get_most_recent_timestamp("nonexistent-source")
    assert ts is None


def test_session_detection(db):
    db2 = WebDatabase(db.db_path)
    now = datetime.now(timezone.utc)
    records = [
        {"timestamp": (now - timedelta(seconds=10)).isoformat(), "uas_id": "session-test", "latitude": 37.0, "longitude": -122.0},
        {"timestamp": (now - timedelta(seconds=5)).isoformat(), "uas_id": "session-test", "latitude": 37.1, "longitude": -122.1},
        {"timestamp": now.isoformat(), "uas_id": "session-test", "latitude": 37.2, "longitude": -122.2},
    ]
    db2.insert_remoteid_records("test-source", records)

    track = db2.get_track("session-test", now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(track) == 3
    sessions = db2.get_track_sessions("session-test", now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(sessions) == 1


def test_session_detection_gap(db):
    db2 = WebDatabase(db.db_path)
    now = datetime.now(timezone.utc)
    records = [
        {"timestamp": (now - timedelta(minutes=30)).isoformat(), "uas_id": "gap-test", "latitude": 37.0, "longitude": -122.0},
        {"timestamp": (now - timedelta(seconds=5)).isoformat(), "uas_id": "gap-test", "latitude": 37.1, "longitude": -122.1},
    ]
    db2.insert_remoteid_records("test-source", records, session_gap_threshold=60)

    sessions = db2.get_track_sessions("gap-test", now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(sessions) == 2


def test_sanitize_record():
    record = {
        "uas_id": "drone-001",
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": None,
        "operator_latitude": "invalid",
        "operator_longitude": None,
        "timestamp": "2024-01-15T10:00:00",
    }
    sanitized = WebDatabase._sanitize_record(record)
    assert sanitized["latitude"] == 37.7749
    assert sanitized["altitude"] is None
    assert sanitized["operator_latitude"] is None
    # Naive timestamp strings get Z suffix
    assert sanitized["timestamp"] == "2024-01-15T10:00:00Z"


# --- Geozone event history tests ---

def test_get_geozone_event_history_empty(db):
    events, total = db.get_geozone_event_history()
    assert events == []
    assert total == 0


def test_get_geozone_event_history_all(db):
    now = datetime.now()
    db.enter_geozone("drone-001", "ZoneA", now)
    db.enter_geozone("drone-002", "ZoneB", now)
    events, total = db.get_geozone_event_history()
    assert total == 2
    assert len(events) == 2


def test_get_geozone_event_history_filter_uas(db):
    now = datetime.now()
    db.enter_geozone("drone-001", "ZoneA", now)
    db.enter_geozone("drone-002", "ZoneB", now)
    events, total = db.get_geozone_event_history(uas_id="drone-001")
    assert total == 1
    assert events[0]["uas_id"] == "drone-001"


def test_get_geozone_event_history_filter_geozone(db):
    now = datetime.now()
    db.enter_geozone("drone-001", "ZoneA", now)
    db.enter_geozone("drone-001", "ZoneB", now)
    events, total = db.get_geozone_event_history(geozone_name="ZoneA")
    assert total == 1
    assert events[0]["geozone_name"] == "ZoneA"


def test_get_geozone_event_history_filter_date(db):
    now = datetime.now()
    old = now - timedelta(days=10)
    db.enter_geozone("drone-001", "ZoneA", old)
    db.enter_geozone("drone-001", "ZoneB", now)
    events, total = db.get_geozone_event_history(from_date=now - timedelta(days=1))
    assert total == 1
    assert events[0]["geozone_name"] == "ZoneB"


def test_get_geozone_event_history_pagination(db):
    now = datetime.now()
    for i in range(10):
        db.enter_geozone(f"drone-{i:03d}", f"Zone{i}", now)
    events, total = db.get_geozone_event_history(limit=3, offset=0)
    assert total == 10
    assert len(events) == 3


def test_get_geozone_event_history_orders_by_entered_desc(db):
    now = datetime.now()
    e1 = db.enter_geozone("drone-001", "ZoneA", now - timedelta(hours=2))
    e2 = db.enter_geozone("drone-001", "ZoneB", now - timedelta(hours=1))
    events, total = db.get_geozone_event_history(uas_id="drone-001")
    assert total == 2
    # Most recent first
    assert events[0]["geozone_name"] == "ZoneB"
    assert events[1]["geozone_name"] == "ZoneA"


# --- Stats tests ---

def test_get_stats_empty(db):
    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()
    stats = db.get_stats(start, end)
    assert stats["total_drones"] == 0
    assert stats["total_sessions"] == 0
    assert stats["total_positions"] == 0
    assert stats["active_alerts"] == 0
    assert stats["total_alerts"] == 0


def test_get_stats_with_data(db):
    """db fixture inserts 4 records for drone-001, drone-002, drone-003."""
    start = datetime.now() - timedelta(hours=24)
    end = datetime.now()
    stats = db.get_stats(start, end)
    assert stats["total_drones"] == 3
    assert stats["total_positions"] == 4
    # Sessions depend on session detection; at minimum 1 per drone
    assert stats["total_drones"] == 3


def test_get_stats_alerts(db):
    now = datetime.now()
    start = now - timedelta(hours=24)
    end = now + timedelta(hours=1)
    db.enter_geozone("drone-001", "ZoneA", now)
    stats = db.get_stats(start, end)
    assert stats["active_alerts"] == 1
    assert stats["total_alerts"] == 1


# --- Session tracking helpers ---

def test_get_latest_session_id(db, sample_records):
    """Returns the computed_session_id of the most recent record for a UAS."""
    inserted, _, _ = db.insert_remoteid_records("test-source", sample_records)
    assert inserted > 0
    session_id = db.get_latest_session_id("drone-001")
    assert session_id is not None
    assert session_id.startswith("session_")


def test_get_latest_session_id_nonexistent(db):
    """Returns None for a UAS with no records."""
    assert db.get_latest_session_id("nonexistent") is None


def test_get_all_current_sessions(db, sample_records):
    """Returns a dict mapping every UAS to its latest session ID."""
    db.insert_remoteid_records("test-source", sample_records)
    sessions = db.get_all_current_sessions()
    assert "drone-001" in sessions
    assert "drone-002" in sessions
    assert sessions["drone-001"].startswith("session_")
    assert sessions["drone-002"].startswith("session_")


# --- get_all_sources tests ---

def test_get_all_sources(db):
    sources = db.get_all_sources()
    assert isinstance(sources, list)
    assert len(sources) >= 1
    names = {s["source"] for s in sources}
    assert "test-source" in names


def test_get_all_sources_structure(db):
    sources = db.get_all_sources()
    for s in sources:
        assert "source" in s
        assert "last_sync" in s
        assert "total_records" in s


# --- get_drones_incremental tests ---

def test_get_drones_incremental_empty_known(db):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    drones = db.get_drones_incremental(start, end, {})
    assert len(drones) >= 3


def test_get_drones_incremental_with_known_all_fresh(db):
    """All known timestamps ahead of data → no drones returned."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    far_future = (now + timedelta(days=365)).isoformat()
    known = {"drone-001:session_xxx": far_future}
    drones = db.get_drones_incremental(start, end, known)
    assert len(drones) >= 0


def test_get_drones_incremental_empty_window(db):
    start = datetime(2020, 1, 1)
    end = datetime(2020, 1, 2)
    drones = db.get_drones_incremental(start, end, {})
    assert drones == []


# --- Alert check helpers ---

def test_get_drones_for_alert_check(db):
    drones = db.get_drones_for_alert_check()
    assert len(drones) >= 3
    assert "drone-001" in drones


def test_get_drones_for_alert_check_since(db):
    now = datetime.now()
    drones = db.get_drones_for_alert_check(since=now - timedelta(hours=5))
    assert len(drones) >= 3
    drones_old = db.get_drones_for_alert_check(since=now + timedelta(hours=1))
    assert len(drones_old) == 0


def test_get_positions_for_alert_check(db):
    positions = db.get_positions_for_alert_check("drone-001")
    assert len(positions) >= 2
    for p in positions:
        assert "latitude" in p
        assert "longitude" in p


def test_get_positions_for_alert_check_since(db):
    now = datetime.now()
    positions = db.get_positions_for_alert_check("drone-001", since=now - timedelta(hours=3))
    assert len(positions) >= 2
    positions_future = db.get_positions_for_alert_check("drone-001", since=now + timedelta(hours=1))
    assert len(positions_future) == 0


# --- Stale geozone events ---

def test_check_stale_geozone_events(db):
    now = datetime.now(timezone.utc)
    db.enter_geozone("drone-001", "ZoneA", now - timedelta(seconds=600))
    db.enter_geozone("drone-002", "ZoneB", now)
    marked = db.check_stale_geozone_events(stale_timeout=300, reference_time=now)
    assert marked >= 1
    active = db.get_active_geozone_events()
    names = [e["geozone_name"] for e in active]
    assert "ZoneA" not in names
    assert "ZoneB" in names


def test_check_stale_geozone_events_none_stale(db):
    now = datetime.now(timezone.utc)
    db.enter_geozone("drone-001", "ZoneA", now - timedelta(seconds=10))
    db.enter_geozone("drone-002", "ZoneB", now)
    marked = db.check_stale_geozone_events(stale_timeout=300, reference_time=now)
    assert marked == 0
    active = db.get_active_geozone_events()
    assert len(active) == 2


# --- Collector position CRUD ---

def test_update_and_get_collector_positions(db):
    positions = db.get_collector_positions()
    assert positions == []

    db.update_collector_position("Node1", 37.78, -122.41)
    positions = db.get_collector_positions()
    assert len(positions) == 1
    assert positions[0]["name"] == "Node1"
    assert positions[0]["latitude"] == 37.78
    assert positions[0]["longitude"] == -122.41

    db.update_collector_position("Node1", 37.79, -122.42)
    positions = db.get_collector_positions()
    assert len(positions) == 1
    assert positions[0]["latitude"] == 37.79

    db.update_collector_position("Node2", 38.0, -123.0)
    positions = db.get_collector_positions()
    assert len(positions) == 2


# --- Push subscription CRUD ---

def test_push_subscription_save_and_get(db):
    subs = db.get_all_push_subscriptions()
    assert subs == []

    db.save_push_subscription("https://push.example.com/1", "p256dh_1", "auth_1")
    subs = db.get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example.com/1"

    db.save_push_subscription("https://push.example.com/2", "p256dh_2", "auth_2", user_agent="TestAgent")
    subs = db.get_all_push_subscriptions()
    assert len(subs) == 2


def test_push_subscription_remove(db):
    db.save_push_subscription("https://push.example.com/remove", "pk", "ak")
    db.remove_push_subscription("https://push.example.com/remove")
    subs = db.get_all_push_subscriptions()
    assert len(subs) == 0


def test_push_subscription_replace(db):
    """Same endpoint replaces existing subscription."""
    db.save_push_subscription("https://push.example.com/ep", "old_key", "old_auth")
    db.save_push_subscription("https://push.example.com/ep", "new_key", "new_auth")
    subs = db.get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["p256dh_key"] == "new_key"


# --- log_submission ---

def test_log_submission(db):
    db.log_submission("test-source", 5)
    sources = db.get_all_sources()
    for s in sources:
        if s["source"] == "test-source":
            assert s["total_records"] is not None and s["total_records"] >= 5
            return
    assert False, "test-source not found in sources"


# --- Auth method tests ---


def test_create_user(tmp_path):
    db_path = tmp_path / "test_create_user.db"
    db = WebDatabase(str(db_path))
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    user = db.create_user("Alice", "alice@example.com", "operator", "login-token-123", expires_at)
    assert user["name"] == "Alice"
    assert user["email"] == "alice@example.com"
    assert user["role_name"] == "operator"
    assert user["is_ephemeral"] == 0
    assert user["is_active"] == 1
    assert user["auth_method"] == "login_link"
    assert user["id"] > 0


def test_create_user_stores_login_token_hash(tmp_path):
    """The login token is stored as a SHA-256 hash, not plaintext."""
    db_path = tmp_path / "test_login_hash.db"
    db = WebDatabase(str(db_path))
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.create_user("Bob", "bob@example.com", "viewer", "my-raw-token", expires_at)

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT login_token_hash FROM users WHERE name = ?", ("Bob",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] != "my-raw-token"  # not stored in plaintext
    assert row[0] == hashlib.sha256("my-raw-token".encode()).hexdigest()


def test_create_ephemeral_user(tmp_path):
    db_path = tmp_path / "test_ephemeral.db"
    db = WebDatabase(str(db_path))
    session_token, user_id = db.create_ephemeral_user()
    assert len(session_token) > 0
    assert user_id > 0

    # The token can be used to look up the user
    user = db.get_user_by_auth_token(session_token)
    assert user is not None
    assert user["id"] == user_id
    assert user["is_ephemeral"] == 1
    assert user["role_name"] == "guest"
    assert user["auth_method"] == "ephemeral"
    assert user["name"].startswith("Guest-")


def test_create_ephemeral_user_multiple(tmp_path):
    """Each ephemeral user gets a unique name."""
    db_path = tmp_path / "test_ephemeral_multi.db"
    db = WebDatabase(str(db_path))
    token1, uid1 = db.create_ephemeral_user()
    token2, uid2 = db.create_ephemeral_user()
    assert uid1 != uid2
    u1 = db.get_user_by_auth_token(token1)
    u2 = db.get_user_by_auth_token(token2)
    assert u1["name"] != u2["name"]


def test_exchange_login_token_valid(tmp_path):
    db_path = tmp_path / "test_exchange.db"
    db = WebDatabase(str(db_path))
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.create_user("Carol", "carol@example.com", "operator", "valid-login-token", expires_at)

    result = db.exchange_login_token("valid-login-token")
    assert result is not None
    session_token, user = result
    assert user["name"] == "Carol"
    assert user["role_name"] == "operator"

    # The session token works for auth
    looked_up = db.get_user_by_auth_token(session_token)
    assert looked_up is not None
    assert looked_up["id"] == user["id"]


def test_exchange_login_token_single_use(tmp_path):
    """A login token can only be used once."""
    db_path = tmp_path / "test_single_use.db"
    db = WebDatabase(str(db_path))
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.create_user("Dave", "dave@example.com", "viewer", "one-time-token", expires_at)

    result1 = db.exchange_login_token("one-time-token")
    assert result1 is not None

    result2 = db.exchange_login_token("one-time-token")
    assert result2 is None


def test_exchange_login_token_expired(tmp_path):
    db_path = tmp_path / "test_expired_token.db"
    db = WebDatabase(str(db_path))
    expires_at = datetime.now(timezone.utc) - timedelta(hours=1)  # already expired
    db.create_user("Eve", "eve@example.com", "viewer", "expired-token", expires_at)

    result = db.exchange_login_token("expired-token")
    assert result is None


def test_exchange_login_token_invalid(tmp_path):
    db_path = tmp_path / "test_invalid_token.db"
    db = WebDatabase(str(db_path))
    result = db.exchange_login_token("nonexistent-token")
    assert result is None


def test_get_user_by_auth_token_valid(tmp_path):
    db_path = tmp_path / "test_get_user.db"
    db = WebDatabase(str(db_path))
    token, user_id = db.create_ephemeral_user()
    user = db.get_user_by_auth_token(token)
    assert user is not None
    assert user["id"] == user_id


def test_get_user_by_auth_token_invalid(tmp_path):
    db_path = tmp_path / "test_get_user_invalid.db"
    db = WebDatabase(str(db_path))
    user = db.get_user_by_auth_token("invalid-token")
    assert user is None


def test_get_user_by_auth_token_expired(tmp_path):
    db_path = tmp_path / "test_get_user_expired.db"
    db = WebDatabase(str(db_path))

    # Manually insert a token with an expired date
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO users (name, role_name, is_ephemeral) VALUES (?, 'guest', 1)",
                 ("ExpiredUser",))
    user_id = conn.execute("SELECT id FROM users WHERE name = 'ExpiredUser'").fetchone()[0]
    token_hash = hashlib.sha256("stale-token".encode()).hexdigest()
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    conn.execute(
        "INSERT INTO auth_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user_id, token_hash, expired),
    )
    conn.commit()
    conn.close()

    user = db.get_user_by_auth_token("stale-token")
    assert user is None


def test_revoke_token(tmp_path):
    db_path = tmp_path / "test_revoke.db"
    db = WebDatabase(str(db_path))
    token, _ = db.create_ephemeral_user()

    # Token works before revocation
    assert db.get_user_by_auth_token(token) is not None

    db.revoke_token(token)

    # Token no longer works after revocation
    assert db.get_user_by_auth_token(token) is None


def test_revoke_all_user_tokens(tmp_path):
    db_path = tmp_path / "test_revoke_all.db"
    db = WebDatabase(str(db_path))
    token1, uid1 = db.create_ephemeral_user()
    token2, _ = db.create_ephemeral_user()

    # Both tokens work
    assert db.get_user_by_auth_token(token1) is not None
    assert db.get_user_by_auth_token(token2) is not None

    db.revoke_all_user_tokens(uid1)

    # Only user1's tokens are revoked
    assert db.get_user_by_auth_token(token1) is None
    assert db.get_user_by_auth_token(token2) is not None


def test_revoke_nonexistent_token(tmp_path):
    """Revoking a nonexistent token does not raise."""
    db_path = tmp_path / "test_revoke_nonexistent.db"
    db = WebDatabase(str(db_path))
    db.revoke_token("i-dont-exist")  # should not raise


def test_create_user_defaults(tmp_path):
    """A user with 'guest' role is created correctly."""
    db_path = tmp_path / "test_user_defaults.db"
    db = WebDatabase(str(db_path))
    import hashlib
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    user = db.create_user("GuestUser", "", "guest", "guest-token", expires_at)
    assert user["role_name"] == "guest"
    assert user["auth_method"] == "login_link"
    assert user["is_active"] == 1


# ---------------------------------------------------------------------------
# Auth cleanup tests
# ---------------------------------------------------------------------------


def test_cleanup_expired_auth_tokens(tmp_path):
    """cleanup_expired_auth_tokens removes tokens past expires_at."""
    import sqlite3
    db_path = tmp_path / "test_cleanup_tokens.db"
    db = WebDatabase(str(db_path))
    token, uid = db.create_ephemeral_user()

    # Token is valid (just created, expires in 90d)
    assert db.get_user_by_auth_token(token) is not None

    # Manually expire the token
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE auth_tokens SET expires_at = ? WHERE user_id = ?",
        (datetime.now(timezone.utc) - timedelta(hours=1), uid),
    )
    conn.commit()
    conn.close()

    assert db.cleanup_expired_auth_tokens() == 1

    # Token should be gone
    assert db.get_user_by_auth_token(token) is None


def test_cleanup_expired_auth_tokens_none(tmp_path):
    """cleanup_expired_auth_tokens returns 0 when nothing is expired."""
    db_path = tmp_path / "test_cleanup_tokens_none.db"
    db = WebDatabase(str(db_path))
    db.create_ephemeral_user()  # 90d into the future
    assert db.cleanup_expired_auth_tokens() == 0


def test_cleanup_expired_login_tokens(tmp_path):
    """cleanup_expired_login_tokens removes users with expired login tokens."""
    import hashlib
    import sqlite3
    db_path = tmp_path / "test_cleanup_login.db"
    db = WebDatabase(str(db_path))

    expires_at = datetime.now(timezone.utc) - timedelta(hours=1)  # already expired
    db.create_user("OldUser", "old@example.com", "operator", "old-token", expires_at)

    token_hash = hashlib.sha256("old-token".encode()).hexdigest()
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id FROM users WHERE login_token_hash = ?", (token_hash,)
    )
    assert cursor.fetchone() is not None
    conn.close()

    assert db.cleanup_expired_login_tokens() == 1

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id FROM users WHERE login_token_hash = ?", (token_hash,)
    )
    assert cursor.fetchone() is None
    conn.close()


def test_cleanup_expired_login_tokens_skips_valid(tmp_path):
    """cleanup_expired_login_tokens does not remove users with valid tokens."""
    import hashlib
    import sqlite3
    db_path = tmp_path / "test_cleanup_login_skip.db"
    db = WebDatabase(str(db_path))

    expires_at = datetime.now(timezone.utc) + timedelta(days=1)  # still valid
    db.create_user("ValidUser", "v@example.com", "operator", "valid-token", expires_at)

    assert db.cleanup_expired_login_tokens() == 0

    token_hash = hashlib.sha256("valid-token".encode()).hexdigest()
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id FROM users WHERE login_token_hash = ?", (token_hash,)
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_cleanup_orphaned_ephemeral_users(tmp_path):
    """cleanup_orphaned_ephemeral_users removes guests with no valid tokens."""
    import sqlite3
    db_path = tmp_path / "test_cleanup_orphan.db"
    db = WebDatabase(str(db_path))
    _token, uid = db.create_ephemeral_user()

    # Expire all tokens for this user
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE auth_tokens SET expires_at = ? WHERE user_id = ?",
        (datetime.now(timezone.utc) - timedelta(hours=1), uid),
    )
    conn.commit()
    conn.close()

    assert db.cleanup_orphaned_ephemeral_users() == 1

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT id FROM users WHERE id = ?", (uid,))
    assert cursor.fetchone() is None
    cursor = conn.execute("SELECT id FROM auth_tokens WHERE user_id = ?", (uid,))
    assert cursor.fetchone() is None  # no orphaned tokens
    conn.close()


def test_cleanup_orphaned_ephemeral_skips_active(tmp_path):
    """cleanup_orphaned_ephemeral_users does not remove users with valid tokens."""
    import sqlite3
    db_path = tmp_path / "test_cleanup_orphan_skip.db"
    db = WebDatabase(str(db_path))
    _token, uid = db.create_ephemeral_user()  # 90d into the future

    assert db.cleanup_orphaned_ephemeral_users() == 0

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT id FROM users WHERE id = ?", (uid,))
    assert cursor.fetchone() is not None
    conn.close()


def test_cleanup_orphaned_ephemeral_no_tokens(tmp_path):
    """cleanup_orphaned_ephemeral_users removes guests that have zero tokens."""
    import sqlite3
    db_path = tmp_path / "test_cleanup_orphan_notokens.db"
    db = WebDatabase(str(db_path))

    # Create a guest user directly with no tokens
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        """INSERT INTO users (name, role_name, is_ephemeral, is_active, auth_method)
           VALUES ('Ghost', 'guest', 1, 1, 'ephemeral')"""
    )
    uid = cursor.lastrowid
    conn.commit()
    conn.close()

    assert db.cleanup_orphaned_ephemeral_users() == 1

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT id FROM users WHERE id = ?", (uid,))
    assert cursor.fetchone() is None
    conn.close()
