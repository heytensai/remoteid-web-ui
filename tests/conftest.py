"""Shared test fixtures and configuration"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
import yaml

import app as _app_module

from app import _init_app, limiter


SAMPLE_API_KEY = "test-api-key-123"


@pytest.fixture
def sample_config_yaml():
    """Create a temporary config YAML file for testing"""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    config = {
        "web_interface": {
            "host": "127.0.0.1",
            "port": 5001,
            "database_path": db_path,
            "default_hours": 24,
            "max_positions_per_query": 5000,
            "map": {
                "center_lat": 37.7749,
                "center_lon": -122.4194,
                "default_zoom": 11,
                "tile_provider": "osm",
            },
            "api_keys": {SAMPLE_API_KEY: "test-source"},
            "drone_aliases": {"drone-001": "Alpha", "drone-002": "Bravo"},
            "use_metric": True,
            "roles": {
                "operator": {
                    "permissions": [
                        "view_map", "view_drones", "view_tracks",
                        "view_operators", "view_waypoints", "view_sources",
                        "view_stats", "view_alert_history", "view_settings",
                        "use_replay", "export_data", "add_waypoint",
                        "edit_waypoint", "delete_waypoint", "add_alias",
                        "edit_alias", "delete_alias", "receive_notifications",
                        "manage_collectors",
                    ],
                },
                "viewer": {
                    "permissions": [
                        "view_map", "view_drones", "view_tracks",
                        "view_operators", "view_waypoints",
                        "view_alert_history", "use_replay",
                        "receive_notifications",
                    ],
                },
                "guest": {
                    "permissions": [
                        "view_map", "view_drones", "view_tracks",
                        "view_operators", "view_waypoints", "use_replay",
                    ],
                },
            },
        }
    }

    config_fd, config_path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(config_fd, "w") as f:
        yaml.dump(config, f)

    yield config_path, db_path

    os.unlink(config_path)
    os.unlink(db_path)


@pytest.fixture
def app(sample_config_yaml):
    """Create a Flask app instance for testing"""
    config_path, db_path = sample_config_yaml

    app = _init_app(config_path)
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SERVER_NAME"] = "localhost"
    limiter.enabled = False

    if _app_module.SESSION_SCHEDULER:
        _app_module.SESSION_SCHEDULER.stop()

    yield app

    if _app_module.SESSION_SCHEDULER:
        _app_module.SESSION_SCHEDULER.stop()


@pytest.fixture
def client(app):
    """Flask test client"""
    with app.test_client() as client:
        yield client


@pytest.fixture
def db(app):
    """Get the test database instance with some sample data"""
    db = _app_module.DATABASE
    now = datetime.now()

    records = [
        {
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "uas_id": "drone-001",
            "latitude": 37.7749,
            "longitude": -122.4194,
            "altitude": 100.0,
            "mac_address": "aa:bb:cc:dd:ee:01",
            "operator_id": "op-001",
            "operator_latitude": 37.7750,
            "operator_longitude": -122.4195,
        },
        {
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "uas_id": "drone-001",
            "latitude": 37.7755,
            "longitude": -122.4185,
            "altitude": 150.0,
            "mac_address": "aa:bb:cc:dd:ee:01",
            "operator_id": "op-001",
            "operator_latitude": 37.7750,
            "operator_longitude": -122.4195,
        },
        {
            "timestamp": (now - timedelta(hours=3)).isoformat(),
            "uas_id": "drone-002",
            "latitude": 37.7800,
            "longitude": -122.4100,
            "altitude": 200.0,
            "mac_address": "aa:bb:cc:dd:ee:02",
            "operator_id": "op-002",
            "operator_latitude": 37.7801,
            "operator_longitude": -122.4101,
        },
        {
            "timestamp": (now - timedelta(hours=4)).isoformat(),
            "uas_id": "drone-003",
            "latitude": 37.7700,
            "longitude": -122.4200,
            "altitude": None,
            "mac_address": "aa:bb:cc:dd:ee:03",
            "operator_id": None,
            "operator_latitude": None,
            "operator_longitude": None,
        },
    ]

    inserted, errors, _ = db.insert_remoteid_records("test-source", records)
    assert len(errors) == 0
    assert inserted == len(records)

    return db


@pytest.fixture
def sample_records():
    """Sample records for insertion tests"""
    now = datetime.now()
    return [
        {
            "timestamp": now.isoformat(),
            "uas_id": "drone-010",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "altitude": 300.0,
            "mac_address": "aa:bb:cc:dd:ee:10",
            "operator_id": "op-010",
        },
        {
            "timestamp": now.isoformat(),
            "uas_id": "drone-011",
            "latitude": 40.7129,
            "longitude": -74.0061,
            "altitude": 350.0,
            "mac_address": "aa:bb:cc:dd:ee:11",
            "operator_id": "op-011",
        },
    ]
