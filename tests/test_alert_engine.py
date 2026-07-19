"""Tests for alert_engine.py - geozone alerting logic"""

import math
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from config import WebConfig, AlertsConfig
from database import WebDatabase
from alert_engine import point_in_circle, point_in_rectangle, AlertEngine


# --- Geometry tests ---

def test_point_in_circle_center():
    assert point_in_circle(37.0, -122.0, 37.0, -122.0, 100)


def test_point_in_circle_inside():
    assert point_in_circle(37.001, -122.0, 37.0, -122.0, 200)


def test_point_in_circle_outside():
    assert not point_in_circle(38.0, -122.0, 37.0, -122.0, 100)


def test_point_in_circle_exact_boundary():
    # ~111m per degree at equator, so 100m radius ~= 0.0009 degrees
    assert point_in_circle(37.0, -122.0009, 37.0, -122.0, 100)


def test_point_in_rectangle_center():
    assert point_in_rectangle(37.0, -122.0, 37.0, -122.0, 200, 100)


def test_point_in_rectangle_inside():
    assert point_in_rectangle(37.0004, -122.0008, 37.0, -122.0, 200, 100)


def test_point_in_rectangle_outside():
    assert not point_in_rectangle(38.0, -122.0, 37.0, -122.0, 200, 100)


def test_point_in_rectangle_boundary():
    # 50m height / 2 = 25m offset, ~111320 m/deg, so ~0.000225 deg
    assert point_in_rectangle(37.00022, -122.0, 37.0, -122.0, 200, 50)


# --- AlertsConfig tests ---

def test_alerts_config_defaults():
    ac = AlertsConfig()
    assert ac.stale_timeout == 300
    assert ac.skip_known_drones is False


def test_alerts_config_from_dict():
    ac = AlertsConfig({"stale_timeout": 600})
    assert ac.stale_timeout == 600
    assert ac.skip_known_drones is False


def test_alerts_config_empty_dict():
    ac = AlertsConfig({})
    assert ac.stale_timeout == 300
    assert ac.skip_known_drones is False


def test_alerts_config_skip_known():
    ac = AlertsConfig({"stale_timeout": 600, "skip_known_drones": True})
    assert ac.skip_known_drones is True


def test_alerts_config_proximity_distance_metric():
    ac = AlertsConfig({"proximity_distance": 200}, use_metric=True)
    assert ac.proximity_distance == 200


def test_alerts_config_proximity_distance_imperial():
    """Feet value is converted to meters when use_metric=False."""
    ac = AlertsConfig({"proximity_distance": 328}, use_metric=False)
    assert abs(ac.proximity_distance - 100.0) < 1.0


def test_alerts_config_proximity_distance_default():
    ac = AlertsConfig()
    assert ac.proximity_distance == 100.0


# --- AlertEngine tests ---

@pytest.fixture
def engine_db():
    """Create a temp DB for alert engine tests"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = WebDatabase(path)
    yield db
    os.unlink(path)


@pytest.fixture
def engine_config_yaml():
    """Create a config with alert-enabled geozones"""
    config_data = {
        "web_interface": {
            "database_path": "/tmp/test.db",
            "waypoints": [
                {
                    "name": "TestCircle",
                    "lat": 37.78,
                    "lon": -122.42,
                    "type": "circle",
                    "radius": 200,
                    "alert_enabled": True,
                },
                {
                    "name": "TestRect",
                    "lat": 37.77,
                    "lon": -122.41,
                    "type": "rectangle",
                    "width": 100,
                    "height": 60,
                    "alert_enabled": True,
                },
                {
                    "name": "DisabledZone",
                    "lat": 37.79,
                    "lon": -122.43,
                    "type": "circle",
                    "radius": 100,
                    "alert_enabled": False,
                },
                {
                    "name": "PointOnly",
                    "lat": 37.76,
                    "lon": -122.40,
                    "type": "point",
                    "alert_enabled": True,
                },
            ],
            "alerts": {
                "stale_timeout": 300,
            },
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(config_data, f)
    yield path
    os.unlink(path)


@pytest.fixture
def engine(engine_db, engine_config_yaml):
    """Create an AlertEngine with the test config and DB"""
    config = WebConfig(engine_config_yaml)
    engine = AlertEngine(engine_db, config)
    return engine, engine_db, config


def test_engine_loads_alert_enabled_geozones(engine):
    alert_engine, _, _ = engine
    assert len(alert_engine._geozones) == 2
    names = {g.name for g in alert_engine._geozones}
    assert names == {"TestCircle", "TestRect"}


def test_engine_evaluate_inside_circle(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    positions = [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ]
    alert_engine.evaluate("drone-001", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 1
    assert events[0]["uas_id"] == "drone-001"
    assert events[0]["geozone_name"] == "TestCircle"
    assert events[0]["exited_at"] is None


def test_engine_evaluate_inside_rectangle(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    positions = [
        {"latitude": 37.77, "longitude": -122.41, "timestamp": now},
    ]
    alert_engine.evaluate("drone-002", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 1
    assert events[0]["uas_id"] == "drone-002"
    assert events[0]["geozone_name"] == "TestRect"


def test_engine_evaluate_outside(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    positions = [
        {"latitude": 38.0, "longitude": -122.0, "timestamp": now},
    ]
    alert_engine.evaluate("drone-003", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_engine_evaluate_disabled_geozone(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    # DisabledZone has alert_enabled=false, so no alert
    positions = [
        {"latitude": 37.79, "longitude": -122.43, "timestamp": now},
    ]
    alert_engine.evaluate("drone-004", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_engine_evaluate_point_type(engine):
    """Point type waypoints should not trigger geozone alerts"""
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    positions = [
        {"latitude": 37.76, "longitude": -122.40, "timestamp": now},
    ]
    alert_engine.evaluate("drone-005", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_engine_updates_last_seen(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    pos1 = {"latitude": 37.78, "longitude": -122.42, "timestamp": now}
    pos2 = {"latitude": 37.78, "longitude": -122.42, "timestamp": now + timedelta(seconds=10)}
    alert_engine.evaluate("drone-001", [pos1])
    alert_engine.evaluate("drone-001", [pos2])
    events = db.get_active_geozone_events()
    assert len(events) == 1
    # Cast to string for comparison if needed
    assert str(events[0]["last_seen_at"]) >= str(events[0]["entered_at"])


def test_engine_exits_on_outside(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    inside = {"latitude": 37.78, "longitude": -122.42, "timestamp": now}
    outside = {"latitude": 38.0, "longitude": -122.0, "timestamp": now + timedelta(seconds=60)}
    alert_engine.evaluate("drone-001", [inside])
    alert_engine.evaluate("drone-001", [outside])
    events = db.get_active_geozone_events()
    assert len(events) == 0
    all_events = db.get_geozone_events_for_uas("drone-001")
    assert len(all_events) == 1
    assert all_events[0]["exited_at"] is not None
    assert all_events[0]["exited_reason"] == "left"


def test_engine_multiple_drones(engine):
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    alert_engine.evaluate("drone-002", [{"latitude": 37.77, "longitude": -122.41, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 2


def test_engine_evaluate_incremental(engine):
    """Check that evaluate_all with since only checks recent positions"""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)
    # Add position inside geozone
    db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
        "mac_address": "aa:bb:cc:dd:ee:01",
    }])
    # evaluate_all with since should pick it up
    alert_engine.evaluate_all(since=now - timedelta(hours=1))
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_check_stale(engine):
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=600)
    # Manually insert a stale event
    db.enter_geozone("drone-001", "TestCircle", old)
    alert_engine.check_stale(reference_time=now)
    events = db.get_active_geozone_events()
    assert len(events) == 0
    all_events = db.get_geozone_events_for_uas("drone-001")
    assert len(all_events) == 1
    assert all_events[0]["exited_reason"] == "timeout"


def test_check_stale_not_stale(engine):
    """Events within stale_timeout should not be marked stale"""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)
    recent = now - timedelta(seconds=60)
    db.enter_geozone("drone-001", "TestCircle", recent)
    alert_engine.check_stale(reference_time=now)
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_reload_config(engine):
    alert_engine, db, config = engine
    assert len(alert_engine._geozones) == 2
    # Simulate disabling alerts
    for wp in config.waypoints:
        wp.alert_enabled = False
    alert_engine.reload_config(config)
    assert len(alert_engine._geozones) == 0


def test_skip_known_drones_skips_aliased(engine):
    """Known (aliased) drones should be skipped when skip_known_drones is enabled"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = "Alpha"
    config.alerts.skip_known_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_skip_known_drones_allows_unknown(engine):
    """Unknown drones should still trigger alerts when skip_known_drones is enabled"""
    alert_engine, db, config = engine
    config.alerts.skip_known_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("unknown-drone", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_skip_known_drones_false_processes_all(engine):
    """When skip_known_drones is False, aliased drones still trigger alerts"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = "Alpha"
    config.alerts.skip_known_drones = False
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_evaluate_string_timestamp(engine):
    """Timestamps can be ISO format strings"""
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)
    positions = [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now.isoformat()},
    ]
    alert_engine.evaluate("drone-001", positions)
    events = db.get_active_geozone_events()
    assert len(events) == 1


# --- New session callback tests ---

def test_new_session_callback_fired(engine):
    """on_new_session fires when a drone first appears in the DB."""
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)

    # Insert a record so a session is created in the DB
    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
    }])

    # _known_sessions was loaded at init time, before the insert,
    # so "drone-001" is not tracked yet → callback should fire
    calls = []
    alert_engine.on_new_session = lambda uas_id, session_id, first_pos: calls.append((uas_id, session_id))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert len(calls) == 1
    assert calls[0][0] == "drone-001"
    assert calls[0][1].startswith("session_")


def test_new_session_not_fired_for_known(engine):
    """on_new_session does NOT fire when the session hasn't changed."""
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)

    db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
    }])

    # Manually track the current session so it's "known"
    session_id = db.get_latest_session_id("drone-001")
    alert_engine._known_sessions["drone-001"] = session_id

    calls = []
    alert_engine.on_new_session = lambda uas_id, session_id, first_pos: calls.append((uas_id, session_id))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert len(calls) == 0


def test_new_session_fired_after_gap(engine):
    """on_new_session fires when the session changes (new flight)."""
    alert_engine, db, _ = engine
    now = datetime.now(timezone.utc)

    # First flight — creates session_old
    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
    }])

    # Track the first session
    old_session = db.get_latest_session_id("drone-001")
    alert_engine._known_sessions["drone-001"] = old_session

    # Second flight — gap > 600s, creates a new session
    db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 200,
    }])

    calls = []
    alert_engine.on_new_session = lambda uas_id, session_id, first_pos: calls.append((uas_id, session_id, first_pos))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now, "altitude": 200},
    ])

    assert len(calls) == 1
    assert calls[0][0] == "drone-001"
    assert calls[0][1] != old_session
    assert calls[0][2] is not None
    assert calls[0][2].get("altitude") == 200


# --- Drone proximity tests ---


@pytest.fixture
def proximity_engine():
    """Create an AlertEngine configured for proximity testing (no geozones)."""
    config_data = {
        "web_interface": {
            "database_path": "/tmp/test.db",
            "alerts": {
                "stale_timeout": 300,
                "proximity_distance": 100,
                "cooldown": {
                    "drone_proximity": 300,
                },
            },
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(config_data, f)
    fd2, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd2)
    config = WebConfig(path)
    db = WebDatabase(db_path)
    eng = AlertEngine(db, config)
    yield eng, db, config
    os.unlink(path)
    os.unlink(db_path)


def _insert_position(db, uas_id, lat, lon, ts):
    """Insert a single position record for proximity tests."""
    db.insert_remoteid_records("test", [{
        "timestamp": ts.isoformat(),
        "uas_id": uas_id,
        "latitude": lat,
        "longitude": lon,
        "altitude": 100,
    }])


def test_proximity_two_drones_within_distance(proximity_engine):
    """Two live drones within proximity_distance fires the callback."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)  # ~55m north

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append((uid_a, uid_b, dist))
    eng._check_drone_proximity()

    assert len(calls) == 1
    assert "drone-A" in calls[0][:2]
    assert "drone-B" in calls[0][:2]
    assert calls[0][2] < 100


def test_proximity_two_drones_outside_distance(proximity_engine):
    """Two live drones beyond proximity_distance does NOT fire the callback."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 38.0, -122.0, now)  # ~25km away

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_ignores_stale_drones(proximity_engine):
    """Drones with positions older than stale_timeout are ignored."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    # drone-A is live, drone-B is stale (outside stale_timeout)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now - timedelta(seconds=600))

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_single_drone_no_alert(proximity_engine):
    """Only one live drone means no pair possible — no alert fires."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_cooldown_suppresses_duplicate(proximity_engine):
    """Same pair within cooldown window only fires once."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng._check_drone_proximity()
    eng._check_drone_proximity()

    assert len(calls) == 1


def test_proximity_uses_aliases(proximity_engine):
    """Callback receives resolved alias names when configured."""
    eng, db, config = proximity_engine
    config.drone_aliases["drone-A"] = "Alpha"
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append((name_a, name_b))
    eng._check_drone_proximity()

    assert len(calls) == 1
    names = set(calls[0])
    assert "Alpha" in names


def test_proximity_disabled_when_zero(proximity_engine):
    """proximity_distance of 0 disables the check entirely."""
    eng, db, config = proximity_engine
    config.alerts.proximity_distance = 0
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_three_drones_multiple_pairs(proximity_engine):
    """Three close drones should fire for each valid pair."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    # All three within ~55m of each other
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)
    _insert_position(db, "drone-C", 37.78, -122.4195, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append((uid_a, uid_b))
    eng._check_drone_proximity()

    assert len(calls) == 3
    pairs = {frozenset(c) for c in calls}
    assert pairs == {frozenset(("drone-A", "drone-B")),
                     frozenset(("drone-A", "drone-C")),
                     frozenset(("drone-B", "drone-C"))}


def test_proximity_runs_via_evaluate_all(proximity_engine):
    """evaluate_all triggers the proximity check (not evaluate)."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng.evaluate_all(since=now - timedelta(hours=1))

    assert len(calls) == 1


def test_proximity_does_not_run_via_evaluate(proximity_engine):
    """evaluate() per-drone does NOT trigger proximity (requires all drones)."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append(1)
    eng.evaluate("drone-A", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert len(calls) == 0


def test_proximity_imperial_config():
    """Imperial config (feet) is converted to meters internally."""
    config_data = {
        "web_interface": {
            "database_path": "/tmp/test.db",
            "use_metric": False,
            "alerts": {
                "stale_timeout": 300,
                "proximity_distance": 328,  # ~100m in feet
                "cooldown": {"drone_proximity": 300},
            },
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(config_data, f)
    fd2, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd2)
    config = WebConfig(path)
    db = WebDatabase(db_path)
    eng = AlertEngine(db, config)

    # 328 ft ≈ 100 m — drone-A and drone-B are ~55m apart, should trigger
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist: calls.append((uid_a, uid_b, dist))
    eng._check_drone_proximity()

    assert len(calls) == 1
    assert calls[0][2] < 110  # distance reported in meters

    os.unlink(path)
    os.unlink(db_path)
