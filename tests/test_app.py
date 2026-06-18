"""Tests for app.py - Flask API endpoints"""

import json
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app import _parse_time_range


class TestIndex:
    def test_get_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")


class TestApiConfig:
    def test_get_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "map" in data
        assert "default_hours" in data
        assert "sync_enabled" in data
        assert "drone_aliases" in data
        assert "waypoints" in data
        assert "use_metric" in data
        assert "csrf_token" in data
        assert data["map"]["center_lat"] == 37.7749
        assert data["drone_aliases"]["drone-001"] == "Alpha"
        assert data["waypoints"] == []


class TestApiDrones:
    def test_get_drones(self, client, db):
        resp = client.get("/api/drones")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drones" in data
        assert len(data["drones"]) >= 3

    def test_get_drones_with_time(self, client, db):
        now = datetime.now()
        params = urlencode({
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(days=1)).isoformat(),
        })
        resp = client.get(f"/api/drones?{params}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["drones"]) >= 3

    def test_get_drones_empty(self, client):
        resp = client.get("/api/drones?start=2020-01-01T00:00:00&end=2020-01-02T00:00:00")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["drones"] == []

    def test_get_drones_invalid_params(self, client):
        resp = client.get("/api/drones?start=not-a-date")
        assert resp.status_code == 500


class TestApiPositions:
    def test_get_positions(self, client, db):
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "positions" in data
        assert len(data["positions"]) >= 4

    def test_get_positions_filtered(self, client, db):
        now = datetime.now()
        params = urlencode({
            "uas_id": "drone-001",
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(days=1)).isoformat(),
        })
        resp = client.get(f"/api/positions?{params}")
        assert resp.status_code == 200
        data = resp.get_json()
        for p in data["positions"]:
            assert p["uas_id"] == "drone-001"

    def test_get_positions_empty(self, client):
        resp = client.get("/api/positions?start=2020-01-01T00:00:00&end=2020-01-02T00:00:00")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["positions"] == []


class TestApiTracks:
    def test_get_track(self, client, db):
        resp = client.get("/api/tracks/drone-001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["uas_id"] == "drone-001"
        assert "track" in data or "sessions" in data

    def test_get_track_sessions(self, client, db):
        resp = client.get("/api/tracks/drone-001?sessions=true")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sessions" in data

    def test_get_track_nonexistent(self, client):
        resp = client.get("/api/tracks/nonexistent-drone")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "track" in data or "sessions" in data


class TestApiOperators:
    def test_get_operators(self, client, db):
        resp = client.get("/api/operators")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "operators" in data
        assert len(data["operators"]) >= 2

    def test_get_operators_empty(self, client):
        resp = client.get("/api/operators?start=2020-01-01T00:00:00&end=2020-01-02T00:00:00")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["operators"] == []


class TestApiBounds:
    def test_get_bounds(self, client, db):
        resp = client.get("/api/bounds")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bounds"] is not None
        assert "min_lat" in data["bounds"]
        assert "max_lat" in data["bounds"]
        assert data["bounds"]["min_lat"] <= data["bounds"]["max_lat"]

    def test_get_bounds_empty(self, client):
        resp = client.get("/api/bounds?start=2020-01-01T00:00:00&end=2020-01-02T00:00:00")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bounds"] is None


class TestApiSync:
    def test_get_sync_status(self, client):
        resp = client.get("/api/sync/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "enabled" in data

    def test_get_collectors_status(self, client):
        resp = client.get("/api/sync/collectors")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "collectors" in data

    def test_trigger_sync_no_collectors(self, client):
        resp = client.post("/api/sync")
        assert resp.status_code == 400

    def test_sync_status_post_no_collectors(self, client):
        resp = client.post(
            "/api/sync/status",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestApiSubmit:
    def test_submit_without_auth(self, client):
        resp = client.post(
            "/api/submit",
            data=json.dumps([{"uas_id": "test", "timestamp": datetime.now().isoformat(), "latitude": 37.0, "longitude": -122.0}]),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_submit_invalid_json(self, client):
        resp = client.post(
            "/api/submit",
            data=json.dumps({"not": "an array"}),
            content_type="application/json",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Expected JSON array" in data["error"]

    def test_submit_valid(self, client):
        records = [
            {
                "uas_id": "submit-test",
                "timestamp": datetime.now().isoformat(),
                "latitude": 38.0,
                "longitude": -123.0,
                "altitude": 200,
            }
        ]
        resp = client.post(
            "/api/submit",
            data=json.dumps(records),
            content_type="application/json",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["inserted"] == 1

    def test_submit_empty(self, client):
        resp = client.post(
            "/api/submit",
            data=json.dumps([]),
            content_type="application/json",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["inserted"] == 0

    def test_submit_invalid_api_key(self, client):
        resp = client.post(
            "/api/submit",
            data=json.dumps([]),
            content_type="application/json",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


class TestApiLastTimestamp:
    def test_last_timestamp(self, client, db):
        resp = client.get("/api/last-timestamp")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "last_timestamp" in data

    def test_last_timestamp_with_auth(self, client, db):
        resp = client.get(
            "/api/last-timestamp",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "last_timestamp" in data


class TestParseTimeRange:
    """Regression tests for _parse_time_range timezone handling"""

    def test_naive_datetime(self):
        """Naive input → naive output"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00",
            "end": "2024-06-01T12:00:00",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert end - start == timedelta(hours=2)

    def test_z_suffix(self):
        """Z → converted to naive UTC"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00Z",
            "end": "2024-06-01T12:00:00Z",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert start.hour == 10
        assert end.hour == 12

    def test_positive_offset(self):
        """+05:00 → normalized to naive UTC (hour shifts back by 5)"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00+05:00",
            "end": "2024-06-01T12:00:00+05:00",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert start.hour == 5   # 10:00 +05:00 = 05:00 UTC
        assert end.hour == 7     # 12:00 +05:00 = 07:00 UTC

    def test_utc_offset(self):
        """+00:00 → naive UTC, same wall clock"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00+00:00",
            "end": "2024-06-01T12:00:00+00:00",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert start.hour == 10
        assert end.hour == 12

    def test_negative_offset(self):
        """-05:00 → normalized to naive UTC (hour shifts forward by 5)"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00-05:00",
            "end": "2024-06-01T12:00:00-05:00",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert start.hour == 15  # 10:00 -05:00 = 15:00 UTC
        assert end.hour == 17    # 12:00 -05:00 = 17:00 UTC

    def test_mixed_offsets(self):
        """Different offsets → both normalized to naive UTC"""
        start, end = _parse_time_range({
            "start": "2024-06-01T10:00:00+05:30",
            "end": "2024-06-01T12:00:00Z",
        })
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert start.hour == 4   # 10:00 +05:30 = 04:30 UTC
        assert end.hour == 12    # 12:00 Z = 12:00 UTC

    def test_default_start_naive(self):
        """Start defaults to end - 24h when omitted (naive input)"""
        start, end = _parse_time_range({
            "end": "2024-06-01T12:00:00",
        })
        assert end.tzinfo is None
        assert end - start == timedelta(hours=24)

    def test_default_start_aware(self):
        """Start defaults from aware end without TypeError (regression test)"""
        start, end = _parse_time_range({
            "end": "2024-06-01T12:00:00Z",
        })
        assert end.tzinfo is None
        assert end - start == timedelta(hours=24)

    def test_default_end_and_start(self):
        """No args at all: both naive, end - start == 24h"""
        start, end = _parse_time_range({})
        assert start.tzinfo is None
        assert end.tzinfo is None
        assert end - start == timedelta(hours=24)


class TestCSRF:
    def test_csrf_on_post(self, client):
        with client.application.app_context():
            from flask_wtf.csrf import generate_csrf
            token = generate_csrf()

        resp = client.post(
            "/api/sync",
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code in (200, 400)

    def test_csrf_present_in_config(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        assert "csrf_token" in data
        assert data["csrf_token"] is not None
