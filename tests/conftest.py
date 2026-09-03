"""Shared test fixtures and configuration"""

import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest
import yaml

import app as _app_module

from app import _init_app, limiter


SAMPLE_API_KEY = "test-api-key-123"

# PostgreSQL test database URL — set TEST_DATABASE_URL to override
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/remoteid_test",
)


@pytest.fixture(scope="session")
def pg_admin():
    """Connect to PostgreSQL as admin (postgres DB) to create/drop the test DB."""
    # Connect to the default 'postgres' database for admin operations
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def setup_test_db(pg_admin):
    """Create the test database if it doesn't exist, drop it on teardown."""
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[1]
    cur = pg_admin.cursor()
    # Terminate existing connections
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (db_name,),
    )
    # Drop and recreate
    cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
    cur.execute(f"CREATE DATABASE {db_name}")
    pg_admin.commit()
    yield
    # Teardown: drop the test DB
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (db_name,),
    )
    cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
    pg_admin.commit()


@pytest.fixture(scope="session")
def pg_conn(setup_test_db):
    """Session-scoped connection to the test database."""
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="function")
def _truncate_db(pg_conn):
    """TRUNCATE all tables between tests for isolation."""
    cur = pg_conn.cursor()
    tables = [
        "remoteid", "sync_log", "session_tracking", "geozone_events",
        "sent_alerts", "collector_positions", "users", "auth_tokens",
        "latest_positions", "_schema_version",
    ]
    for table in tables:
        # Skip tables that don't exist yet (fresh DB: the schema is created
        # lazily by the first WebDatabase initialization).
        cur.execute("SELECT to_regclass(%s)", (table,))
        if cur.fetchone()[0] is None:
            continue
        cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    pg_conn.commit()
    yield


@pytest.fixture
def sample_config_yaml(_truncate_db):
    """Create a temporary config YAML file for testing."""
    import tempfile
    config = {
        "web_interface": {
            "host": "127.0.0.1",
            "port": 5001,
            "database_url": TEST_DATABASE_URL,
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

    yield config_path

    os.unlink(config_path)


@pytest.fixture
def app(sample_config_yaml):
    """Create a Flask app instance for testing."""
    config_path = sample_config_yaml

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
    """Flask test client."""
    with app.test_client() as client:
        yield client


@pytest.fixture
def db(app):
    """Get the test database instance with some sample data."""
    db = _app_module.DATABASE
    now = datetime.now(timezone.utc)

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
    """Sample records for insertion tests."""
    now = datetime.now(timezone.utc)
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
