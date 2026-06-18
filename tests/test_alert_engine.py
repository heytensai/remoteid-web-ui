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
