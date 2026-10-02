"""Tests for alert_engine.py - geozone alerting logic"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from config import WebConfig, AlertsConfig, DroneAlias
from database import WebDatabase
from alert_engine import point_in_circle, point_in_rectangle, AlertEngine

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/remoteid_test"


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
def engine_db(_truncate_db):
    """Create a DB connection for alert engine tests"""
    db = WebDatabase(TEST_DB_URL)
    yield db


@pytest.fixture
def engine_config_yaml():
    """Create a config with alert-enabled geozones"""
    config_data = {
        "web_interface": {
            "database_url": TEST_DB_URL,
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


def test_geozone_callback_receives_position(engine):
    """on_new_alert gets the position dict so altitude/height can be shown."""
    alert_engine, _, _ = engine
    now = datetime.now(timezone.utc)
    calls = []
    alert_engine.on_new_alert = lambda uas_id, gz, pos=None: calls.append((uas_id, gz, pos))

    alert_engine.evaluate("drone-001", [{
        "latitude": 37.78, "longitude": -122.42, "timestamp": now,
        "altitude": 300.0, "height": 200.0, "height_type": "agl",
    }])

    assert len(calls) == 1
    uas_id, gz, pos = calls[0]
    assert uas_id == "drone-001"
    assert gz == "TestCircle"
    assert pos["altitude"] == 300.0
    assert pos["height"] == 200.0
    assert pos["height_type"] == "agl"

    # Same entry in the other geozone does not re-fire on_new_alert
    assert len(calls) == 1


def test_geozone_exit_callback_receives_position(engine):
    """on_geozone_exit gets the position dict so altitude/height can be shown."""
    alert_engine, _, _ = engine
    now = datetime.now(timezone.utc)
    inside = {"latitude": 37.78, "longitude": -122.42, "timestamp": now}
    alert_engine.evaluate("drone-001", [inside])

    calls = []
    alert_engine.on_geozone_exit = lambda uas_id, gz, pos=None: calls.append((uas_id, gz, pos))
    alert_engine.evaluate("drone-001", [{
        "latitude": 38.0, "longitude": -122.0,
        "timestamp": now + timedelta(seconds=60),
        "altitude": 250.0, "height": 150.0, "height_type": "agl",
    }])

    assert len(calls) == 1
    _, gz, pos = calls[0]
    assert gz == "TestCircle"
    assert pos["altitude"] == 250.0
    assert pos["height"] == 150.0


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
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
    config.alerts.skip_known_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_tier_promotion_within_cooldown_still_alerts(engine):
    """A drone promoted to trusted mid-window still fires its new_trusted alert.

    Cooldowns are keyed per (event, drone), so the new_unknown cooldown does
    not swallow the subsequent new_trusted event.
    """
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "uas_id": "drone-x", "latitude": 37.78, "longitude": -122.42, "altitude": 100,
    }])
    calls = []
    alert_engine.on_new_flight = lambda u, _s, e, _f: calls.append(e)
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    assert calls == ["new_unknown"]

    # Promote to trusted, then a second flight inside the 300s cooldown window
    config.drone_aliases["drone-x"] = DroneAlias("X", trusted=True)
    db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-x", "latitude": 37.78, "longitude": -122.42, "altitude": 120,
    }])
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert calls == ["new_unknown", "new_trusted"]


def test_tier_demotion_within_cooldown_still_alerts(engine):
    """Demotion works the same way — a demoted drone fires its own tier event."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    config.drone_aliases["drone-x"] = DroneAlias("X", trusted=True)
    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "uas_id": "drone-x", "latitude": 37.78, "longitude": -122.42, "altitude": 100,
    }])
    calls = []
    alert_engine.on_new_flight = lambda u, _s, e, _f: calls.append(e)
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    assert calls == ["new_trusted"]

    config.drone_aliases["drone-x"] = DroneAlias("X")
    db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-x", "latitude": 37.78, "longitude": -122.42, "altitude": 120,
    }])
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert calls == ["new_trusted", "new_known"]


def test_same_tier_within_cooldown_is_still_suppressed(engine):
    """Two flights of the same tier inside the cooldown window fire once."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    config.drone_aliases["drone-x"] = DroneAlias("X")
    for ts in ((now - timedelta(hours=2)), now):
        db.insert_remoteid_records("test", [{
            "timestamp": ts.isoformat(),
            "uas_id": "drone-x", "latitude": 37.78, "longitude": -122.42, "altitude": 100,
        }])
    calls = []
    alert_engine.on_new_flight = lambda u, _s, e, _f: calls.append(e)

    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42,
                                       "timestamp": now - timedelta(hours=2)}])
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert calls == ["new_known"]


def test_silenced_tier_does_not_open_geozone_event(engine):
    """A suppressed drone never opens a geozone event."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)
    config.drone_aliases["drone-001"] = DroneAlias("Alpha", trusted=True)
    config.alerts.skip_trusted_drones = True

    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert len(db.get_active_geozone_events()) == 0


def test_silenced_tier_closes_geozone_event_opened_before_promotion(engine):
    """A drone promoted mid-geozone has its active event closed on exit, silently."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)
    exits = []
    alert_engine.on_geozone_exit = lambda u, gz, p: exits.append((u, gz))

    # Enters as a known (alerting) drone -> event opens
    config.drone_aliases["drone-x"] = DroneAlias("X")
    alert_engine.evaluate("drone-x", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    assert len(db.get_active_geozone_events()) == 1

    # Promoted to trusted with skip_trusted_drones, then flies out
    config.drone_aliases["drone-x"] = DroneAlias("X", trusted=True)
    config.alerts.skip_trusted_drones = True
    away = {"latitude": 0.0, "longitude": 0.0, "timestamp": now + timedelta(minutes=5)}
    alert_engine.evaluate("drone-x", [away])

    assert len(db.get_active_geozone_events()) == 0
    assert exits == []


@pytest.fixture
def overlapping_engine(engine_db):
    """AlertEngine with two co-located alert geozones."""
    config_data = {
        "web_interface": {
            "database_url": TEST_DB_URL,
            "waypoints": [
                {"name": "ZoneA", "lat": 37.78, "lon": -122.42,
                 "type": "circle", "radius": 200, "alert_enabled": True},
                {"name": "ZoneB", "lat": 37.78, "lon": -122.42,
                 "type": "circle", "radius": 200, "alert_enabled": True},
            ],
            "alerts": {"stale_timeout": 300},
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(config_data, f)
    try:
        # One config instance shared with the engine, so tests can mutate it
        config = WebConfig(path)
        yield AlertEngine(engine_db, config), engine_db, config
    finally:
        os.unlink(path)


def test_silenced_tier_exit_closes_every_active_event(overlapping_engine):
    """A silenced drone leaving closes both of its active geozone events."""
    alert_engine, db, config = overlapping_engine
    now = datetime.now(timezone.utc)
    exits = []
    alert_engine.on_geozone_exit = lambda u, gz, p: exits.append((u, gz))

    config.drone_aliases["drone-x"] = DroneAlias("X")
    inside = [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}]
    alert_engine.evaluate("drone-x", inside)
    assert len(db.get_active_geozone_events()) == 2

    config.drone_aliases["drone-x"] = DroneAlias("X", trusted=True)
    config.alerts.skip_trusted_drones = True
    away = [{"latitude": 0.0, "longitude": 0.0,
             "timestamp": now + timedelta(minutes=5)}]
    alert_engine.evaluate("drone-x", away)

    assert len(db.get_active_geozone_events()) == 0
    assert exits == []


def test_skip_known_drones_does_not_skip_trusted(engine):
    """skip_known_drones only silences untrusted aliases; trusted is independent"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = DroneAlias("Alpha", trusted=True)
    config.alerts.skip_known_drones = True
    config.alerts.skip_trusted_drones = False
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_skip_trusted_drones_skips_trusted(engine):
    """skip_trusted_drones silences trusted drones from geozone alerts"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = DroneAlias("Alpha", trusted=True)
    config.alerts.skip_trusted_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 0


def test_skip_trusted_drones_does_not_skip_known(engine):
    """skip_trusted_drones leaves aliased-but-untrusted drones alerting"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
    config.alerts.skip_trusted_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert len(events) == 1


def test_both_skip_flags_silence_both_aliased_tiers(engine):
    """With both flags set, only unknown drones geozone-alert"""
    alert_engine, db, config = engine
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
    config.drone_aliases["drone-002"] = DroneAlias("Bravo", trusted=True)
    config.alerts.skip_known_drones = True
    config.alerts.skip_trusted_drones = True
    now = datetime.now(timezone.utc)
    alert_engine.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    alert_engine.evaluate("drone-002", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    alert_engine.evaluate("unknown-drone", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    events = db.get_active_geozone_events()
    assert [e["uas_id"] for e in events] == ["unknown-drone"]


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
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
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

def test_new_known_callback_fired(engine):
    """on_new_flight fires with new_known when an aliased drone first appears."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    # Alias the drone so the tier maps to new_known
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")

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
    alert_engine.on_new_flight = lambda uas_id, session_id, event_type, first_pos: \
        calls.append((uas_id, session_id, event_type))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert len(calls) == 1
    assert calls[0][0] == "drone-001"
    assert calls[0][1].startswith("session_")
    assert calls[0][2] == "new_known"


def test_new_flight_event_per_trust_tier(engine):
    """Each trust tier maps to exactly one new-flight event type."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    config.drone_aliases["drone-known"] = DroneAlias("Alpha")
    config.drone_aliases["drone-trusted"] = DroneAlias("Bravo", trusted=True)

    for uas_id in ("drone-unknown", "drone-known", "drone-trusted"):
        db.insert_remoteid_records("test", [{
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "uas_id": uas_id,
            "latitude": 37.78,
            "longitude": -122.42,
            "altitude": 100,
        }])

    calls = []
    alert_engine.on_new_flight = lambda uas_id, _sid, event_type, _fp: calls.append((uas_id, event_type))

    for uas_id in ("drone-unknown", "drone-known", "drone-trusted"):
        alert_engine.evaluate(uas_id, [
            {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
        ])

    assert calls == [
        ("drone-unknown", "new_unknown"),
        ("drone-known", "new_known"),
        ("drone-trusted", "new_trusted"),
    ]


def test_trusted_drone_uses_new_trusted_alert(engine):
    """A trusted drone fires new_trusted, not new_known."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    config.drone_aliases["drone-001"] = DroneAlias("Alpha", trusted=True)

    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
    }])

    calls = []
    alert_engine.on_new_flight = lambda uas_id, _sid, event_type, _fp: calls.append((uas_id, event_type))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert calls == [("drone-001", "new_trusted")]


def test_new_flight_not_fired_for_known_session(engine):
    """on_new_flight does NOT fire when the session hasn't changed."""
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
    alert_engine.on_new_flight = lambda uas_id, session_id, event_type, first_pos: calls.append((uas_id, session_id))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert len(calls) == 0


def test_new_flight_fired_after_gap(engine):
    """on_new_flight fires when the session changes (new flight)."""
    alert_engine, db, config = engine
    now = datetime.now(timezone.utc)

    # Alias the drone so the tier maps to new_known
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")

    # Insert first flight
    db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=3)).isoformat(),
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
    alert_engine.on_new_flight = \
        lambda uas_id, session_id, event_type, first_pos: \
        calls.append((uas_id, session_id, event_type, first_pos))

    alert_engine.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now, "altitude": 200},
    ])

    assert len(calls) == 1
    assert calls[0][0] == "drone-001"
    assert calls[0][1] != old_session
    assert calls[0][2] == "new_known"
    assert calls[0][3] is not None
    assert calls[0][3].get("altitude") == 200


# --- Drone proximity tests ---


@pytest.fixture
def proximity_engine(_truncate_db):
    """Create an AlertEngine configured for proximity testing (no geozones)."""
    config_data = {
        "web_interface": {
            "database_url": TEST_DB_URL,
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
    config = WebConfig(path)
    db = WebDatabase(TEST_DB_URL)
    eng = AlertEngine(db, config)
    yield eng, db, config
    os.unlink(path)


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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append((uid_a, uid_b, dist))
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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_single_drone_no_alert(proximity_engine):
    """Only one live drone means no pair possible — no alert fires."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
    eng._check_drone_proximity()

    assert len(calls) == 0


def test_proximity_callback_receives_positions(proximity_engine):
    """on_drone_proximity receives position dicts for both drones."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append((pos_a, pos_b))
    eng._check_drone_proximity()

    assert len(calls) == 1
    pos_a, pos_b = calls[0]
    assert pos_a is not None
    assert pos_b is not None
    assert pos_a["uas_id"] == "drone-A"
    assert pos_b["uas_id"] == "drone-B"
    # get_live_positions now carries altitude/height/height_type
    assert "altitude" in pos_a
    assert "height" in pos_a
    assert "height_type" in pos_a


def test_proximity_cooldown_suppresses_duplicate(proximity_engine):
    """Same pair within cooldown window only fires once."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
    eng._check_drone_proximity()
    eng._check_drone_proximity()

    assert len(calls) == 1


def test_proximity_uses_aliases(proximity_engine):
    """Callback receives resolved alias names when configured."""
    eng, db, config = proximity_engine
    config.drone_aliases["drone-A"] = DroneAlias("Alpha")
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append((name_a, name_b))
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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append((uid_a, uid_b))
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
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
    eng.evaluate_all(since=now - timedelta(hours=1))

    assert len(calls) == 1


def test_proximity_does_not_run_via_evaluate(proximity_engine):
    """evaluate() per-drone does NOT trigger proximity (requires all drones)."""
    eng, db, config = proximity_engine
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append(1)
    eng.evaluate("drone-A", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert len(calls) == 0


def test_proximity_imperial_config():
    """Imperial config (feet) is converted to meters internally."""
    config_data = {
        "web_interface": {
            "database_url": TEST_DB_URL,
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
    config = WebConfig(path)
    db = WebDatabase(TEST_DB_URL)
    eng = AlertEngine(db, config)

    # 328 ft ≈ 100 m — drone-A and drone-B are ~55m apart, should trigger
    now = datetime.now(timezone.utc)
    _insert_position(db, "drone-A", 37.78, -122.42, now)
    _insert_position(db, "drone-B", 37.7805, -122.42, now)

    calls = []
    eng.on_drone_proximity = lambda uid_a, name_a, uid_b, name_b, dist, pos_a=None, pos_b=None: calls.append((uid_a, uid_b, dist))
    eng._check_drone_proximity()

    assert len(calls) == 1
    assert calls[0][2] < 110  # distance reported in meters

    os.unlink(path)


# --- Cross-process (multi-worker) dedup tests ---
#
# gunicorn forks a private copy of the AlertEngine into each worker, so the
# in-memory _known_sessions/cooldown dicts are per-process. These tests
# simulate that by running two engine instances over the SAME database and
# asserting the shared DB-backed dedup lets only one of them fire.

def _make_two_engines(engine_db, engine_config_yaml):
    config = WebConfig(engine_config_yaml)
    engine_a = AlertEngine(engine_db, config)
    engine_b = AlertEngine(engine_db, config)
    return engine_a, engine_b, config


def test_session_alert_fires_once_across_engines(engine_db, engine_config_yaml):
    """A known drone's session alert fires once even when two workers see it."""
    engine_a, engine_b, config = _make_two_engines(engine_db, engine_config_yaml)
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
    now = datetime.now(timezone.utc)

    engine_db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
        "altitude": 100,
    }])

    calls = []
    engine_a.on_new_flight = lambda uas_id, session_id, event_type, fp: calls.append(session_id)
    engine_b.on_new_flight = lambda uas_id, session_id, event_type, fp: calls.append(session_id)

    engine_a.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    engine_b.evaluate("drone-001", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert len(calls) == 1


def test_unknown_drone_fires_once_across_engines(engine_db, engine_config_yaml):
    """The reported bug: a new-flight alert fires once even across workers."""
    engine_a, engine_b, _ = _make_two_engines(engine_db, engine_config_yaml)
    now = datetime.now(timezone.utc)

    engine_db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "unknown-drone",
        "latitude": 37.78,
        "longitude": -122.42,
    }])

    calls = []
    engine_a.on_new_flight = lambda *args: calls.append(1)
    engine_b.on_new_flight = lambda *args: calls.append(1)

    engine_a.evaluate("unknown-drone", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])
    engine_b.evaluate("unknown-drone", [{"latitude": 37.78, "longitude": -122.42, "timestamp": now}])

    assert len(calls) == 1


def test_new_flight_still_fires_via_other_engine(engine_db, engine_config_yaml):
    """A genuinely new flight notifies even when handled by a different worker."""
    engine_a, engine_b, config = _make_two_engines(engine_db, engine_config_yaml)
    config.drone_aliases["drone-001"] = DroneAlias("Alpha")
    now = datetime.now(timezone.utc)

    # First flight, seen by engine A
    engine_db.insert_remoteid_records("test", [{
        "timestamp": (now - timedelta(hours=3)).isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
    }])
    old_session = engine_db.get_latest_session_id("drone-001")
    engine_a.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now - timedelta(hours=3)},
    ])

    # Second flight (gap > 600s), seen only by engine B
    engine_db.insert_remoteid_records("test", [{
        "timestamp": now.isoformat(),
        "uas_id": "drone-001",
        "latitude": 37.78,
        "longitude": -122.42,
    }])

    calls = []
    engine_b.on_new_flight = lambda uas_id, session_id, event_type, fp: calls.append(session_id)
    engine_b.evaluate("drone-001", [
        {"latitude": 37.78, "longitude": -122.42, "timestamp": now},
    ])

    assert len(calls) == 1
    assert calls[0] != old_session


def test_geozone_enter_fires_once_across_engines(engine_db, engine_config_yaml):
    """Concurrent workers evaluating an entry create one event and one alert."""
    engine_a, engine_b, _ = _make_two_engines(engine_db, engine_config_yaml)
    now = datetime.now(timezone.utc)
    pos = {"latitude": 37.78, "longitude": -122.42, "timestamp": now}

    calls = []
    engine_a.on_new_alert = lambda uas_id, gz, pos=None: calls.append((uas_id, gz))
    engine_b.on_new_alert = lambda uas_id, gz, pos=None: calls.append((uas_id, gz))

    engine_a.evaluate("drone-001", [pos])
    engine_b.evaluate("drone-001", [pos])

    assert len(calls) == 1
    events = engine_db.get_active_geozone_events()
    assert len(events) == 1


def test_geozone_exit_fires_once_across_engines(engine_db, engine_config_yaml):
    """Concurrent workers evaluating an exit fire the alert only once."""
    engine_a, engine_b, _ = _make_two_engines(engine_db, engine_config_yaml)
    now = datetime.now(timezone.utc)
    inside = {"latitude": 37.78, "longitude": -122.42, "timestamp": now}
    outside = {"latitude": 38.0, "longitude": -122.0, "timestamp": now + timedelta(seconds=60)}

    engine_a.evaluate("drone-001", [inside])

    calls = []
    engine_a.on_geozone_exit = lambda uas_id, gz, pos=None: calls.append((uas_id, gz))
    engine_b.on_geozone_exit = lambda uas_id, gz, pos=None: calls.append((uas_id, gz))

    engine_a.evaluate("drone-001", [outside])
    engine_b.evaluate("drone-001", [outside])

    assert len(calls) == 1
