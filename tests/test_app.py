"""Tests for app.py - Flask API endpoints"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app import _parse_time_range


class TestServiceWorker:
    def test_sw_served(self, client):
        resp = client.get("/sw.js")
        assert resp.status_code == 200
        assert resp.content_type == "application/javascript"
        assert "Service-Worker-Allowed" in resp.headers

    def test_sw_url_prefix_replaced(self, client):
        resp = client.get("/sw.js")
        body = resp.get_data(as_text=True)
        assert "__URL_PREFIX__" not in body


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
        assert "drone_aliases" in data
        assert "manufacturer_prefixes" in data
        assert data["manufacturer_prefixes"] == {}
        assert "waypoints" in data
        assert "use_metric" in data
        assert "csrf_token" in data
        assert "stale_timeout" in data
        assert data["stale_timeout"] == 300
        assert data["map"]["center_lat"] == 37.7749
        assert data["drone_aliases"]["drone-001"] == "Alpha"
        assert data["waypoints"] == []
        assert data["m_per_deg_lat"] == 111320


class TestApiAlerts:
    def test_get_alerts_empty(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "active" in data
        assert "uas_ids" in data
        assert "count" in data
        assert data["active"] == []
        assert data["uas_ids"] == []
        assert data["count"] == 0

    def test_get_alerts_with_data(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "TestZone", now)
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["uas_ids"] == ["drone-001"]
        assert data["active"][0]["geozone_name"] == "TestZone"
        assert data["active"][0]["uas_id"] == "drone-001"


class TestApiAlertHistory:
    def test_get_history_empty(self, client):
        resp = client.get("/api/alerts/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["events"] == []
        assert data["total"] == 0
        assert data["limit"] == 100
        assert data["offset"] == 0

    def test_get_history_with_data(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        db.enter_geozone("drone-002", "ZoneB", now)
        resp = client.get("/api/alerts/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        assert len(data["events"]) == 2

    def test_get_history_filter_uas(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        db.enter_geozone("drone-002", "ZoneB", now)
        resp = client.get("/api/alerts/history?uas_id=drone-001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["events"][0]["uas_id"] == "drone-001"

    def test_get_history_filter_geozone(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        db.enter_geozone("drone-001", "ZoneB", now)
        resp = client.get("/api/alerts/history?geozone_name=ZoneA")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["events"][0]["geozone_name"] == "ZoneA"

    def test_get_history_pagination(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        for i in range(5):
            db.enter_geozone(f"drone-{i:03d}", f"Zone{i}", now)
        resp = client.get("/api/alerts/history?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 5
        assert len(data["events"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0


class TestApiStats:
    def test_get_stats(self, client, db):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_drones" in data
        assert "total_sessions" in data
        assert "total_positions" in data
        assert "active_alerts" in data
        assert "total_alerts" in data
        assert data["total_drones"] >= 0
        assert data["total_sessions"] >= 0
        assert data["total_positions"] >= 0

    def test_get_stats_with_alerts(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        db.enter_geozone("drone-001", "ZoneA", datetime.now())
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["active_alerts"] == 1
        assert data["total_alerts"] == 1


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

    def test_get_track_by_session_id(self, client, db):
        """Server-side session filtering returns only the requested session."""
        resp = client.get("/api/tracks/drone-001?sessions=true")
        all_sessions = resp.get_json()["sessions"]
        assert len(all_sessions) >= 1
        target_id = all_sessions[0]["session_id"]

        resp2 = client.get(f"/api/tracks/drone-001?sessions=true&session_id={target_id}")
        filtered = resp2.get_json()["sessions"]
        assert len(filtered) == 1
        assert filtered[0]["session_id"] == target_id

    def test_get_track_by_session_id_uses_indexed_lookup(self, client, db):
        """Session-specific track uses indexed query, not window scan."""
        now = datetime.now()
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()

        # Fetch all sessions first
        resp = client.get(f"/api/tracks/drone-001?sessions=true&start={start}&end={end}")
        all_sessions = resp.get_json()["sessions"]

        for session in all_sessions:
            sid = session["session_id"]
            resp2 = client.get(
                f"/api/tracks/drone-001"
                f"?sessions=true&session_id={sid}&start={start}&end={end}"
            )
            data = resp2.get_json()
            assert len(data["sessions"]) == 1
            assert data["sessions"][0]["session_id"] == sid
            # Positions should match exactly (not filtered from full window)
            assert len(data["sessions"][0]["positions"]) == len(session["positions"])

    def test_tracks_batch(self, client, db):
        """Batch endpoint returns multiple sessions in one request."""
        resp = client.get("/api/tracks/drone-001?sessions=true")
        all_sessions = resp.get_json()["sessions"]
        assert len(all_sessions) >= 1

        session_list = [
            {"uas_id": "drone-001", "session_id": s["session_id"]}
            for s in all_sessions[:2]
        ]

        resp2 = client.post(
            "/api/tracks/batch",
            data=json.dumps({"sessions": session_list}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp2.status_code == 200
        data = resp2.get_json()
        assert "tracks" in data
        for entry in session_list:
            key = f"{entry['uas_id']}:{entry['session_id']}"
            assert key in data["tracks"]
            assert len(data["tracks"][key]["positions"]) > 0

    def test_tracks_batch_empty(self, client):
        """Empty batch request returns empty tracks."""
        resp = client.post(
            "/api/tracks/batch",
            data=json.dumps({"sessions": []}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["tracks"] == {}

    def test_tracks_batch_invalid_body(self, client):
        """Invalid batch body returns 400."""
        resp = client.post(
            "/api/tracks/batch",
            data=json.dumps({"sessions": "not-an-array"}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400


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


class TestApiSources:
    def test_get_sources(self, client):
        resp = client.get("/api/sources")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sources" in data


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

    def test_submit_after_hot_reload_new_key(self, client, sample_config_yaml):
        """A newly added API key works after hot reload"""
        config_path, _ = sample_config_yaml

        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["api_keys"]["hot-reloaded-key"] = "hot-source"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        import app as _app_module
        res = _app_module.CONFIG.reload_hot_config()
        if res is not None:
            _app_module.CONFIG = res
            _app_module._config_snapshot = res

        resp = client.post(
            "/api/submit",
            data=json.dumps([]),
            content_type="application/json",
            headers={"Authorization": "Bearer hot-reloaded-key"},
        )
        assert resp.status_code == 200

    def test_submit_after_hot_reload_removed_key(self, client, sample_config_yaml):
        """A removed API key is rejected after hot reload"""
        config_path, _ = sample_config_yaml

        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["api_keys"] = {}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        import app as _app_module
        res = _app_module.CONFIG.reload_hot_config()
        if res is not None:
            _app_module.CONFIG = res
            _app_module._config_snapshot = res

        resp = client.post(
            "/api/submit",
            data=json.dumps([]),
            content_type="application/json",
            headers={"Authorization": "Bearer test-api-key-123"},
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


class TestExport:
    def test_export_csv(self, client, db):
        resp = client.get("/api/export/csv/drone-001")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert resp.headers["Content-Disposition"]
        assert "drone-001.csv" in resp.headers["Content-Disposition"]
        body = resp.get_data(as_text=True)
        assert "timestamp,latitude,longitude" in body
        assert "drone-001" not in body  # no uas_id column in CSV

    def test_export_csv_with_session(self, client, db):
        # First get a session id
        resp = client.get("/api/tracks/drone-001?sessions=true")
        sessions = resp.get_json()["sessions"]
        assert len(sessions) >= 1
        sid = sessions[0]["session_id"]

        resp = client.get(f"/api/export/csv/drone-001?session_id={sid}")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 data row
        # All rows should have the same session_id (last column)
        for line in lines[1:]:
            cols = line.split(",")
            # Last column is session_id - may be quoted
            assert sid.replace("session_", "") in line or sid in line

    def test_export_gpx(self, client, db):
        resp = client.get("/api/export/gpx/drone-001")
        assert resp.status_code == 200
        assert resp.mimetype == "application/gpx+xml"
        body = resp.get_data(as_text=True)
        assert "<gpx" in body
        assert "<trkpt" in body
        assert "drone-001" in body

    def test_export_kml(self, client, db):
        resp = client.get("/api/export/kml/drone-001")
        assert resp.status_code == 200
        assert resp.mimetype == "application/vnd.google-earth.kml+xml"
        body = resp.get_data(as_text=True)
        assert "<kml" in body
        assert "<coordinates>" in body
        assert "drone-001" in body

    def test_export_unsupported_format(self, client, db):
        resp = client.get("/api/export/pdf/drone-001")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Unsupported format" in data["error"]

    def test_export_nonexistent_drone(self, client):
        resp = client.get("/api/export/csv/nonexistent")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        assert len(lines) == 1  # header only, no data

    def test_export_gpx_structure(self, client, db):
        """Verify GPX contains correct XML structure with track points"""
        resp = client.get("/api/export/gpx/drone-001")
        body = resp.get_data(as_text=True)
        assert '<?xml version="1.0" encoding="UTF-8"?>' in body
        assert 'version="1.1"' in body
        assert "<trk>" in body
        assert "<trkseg>" in body
        assert "</trkseg>" in body
        assert "</trk>" in body
        assert "</gpx>" in body

    def test_export_kml_structure(self, client, db):
        """Verify KML contains correct XML structure with coordinates"""
        resp = client.get("/api/export/kml/drone-001")
        body = resp.get_data(as_text=True)
        assert '<?xml version="1.0" encoding="UTF-8"?>' in body
        assert '<kml xmlns="http://www.opengis.net/kml/2.2"' in body
        assert "<Document>" in body
        assert "<Placemark>" in body
        assert "<LineString>" in body
        assert "<coordinates>" in body
        assert "</coordinates>" in body
        assert "</LineString>" in body
        assert "</Document>" in body
        assert "</kml>" in body


class TestAlertExport:
    def test_export_alert_csv_empty(self, client):
        resp = client.get("/api/alerts/export/csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert "alert_events.csv" in resp.headers["Content-Disposition"]
        body = resp.get_data(as_text=True)
        assert "uas_id,geozone_name" in body
        lines = body.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_export_alert_csv_with_data(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        events = db.get_active_geozone_events()
        assert len(events) == 1
        db.update_geozone_last_seen(events[0]["id"], now + timedelta(seconds=30))
        resp = client.get("/api/alerts/export/csv")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        assert len(lines) == 2  # header + 1 data row
        assert "drone-001" in lines[1]
        assert "ZoneA" in lines[1]

    def test_export_alert_csv_with_exited(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        events = db.get_active_geozone_events()
        assert len(events) == 1
        db.exit_geozone(events[0]["id"], now + timedelta(minutes=5), "left")
        resp = client.get("/api/alerts/export/csv")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        assert len(lines) == 2
        assert "left" in lines[1]
        # duration should be ~300 seconds
        parts = lines[1].split(",")
        duration_str = parts[6]  # duration_seconds column
        assert duration_str and int(float(duration_str)) >= 295

    def test_export_alert_csv_filter_uas(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        now = datetime.now()
        db.enter_geozone("drone-001", "ZoneA", now)
        db.enter_geozone("drone-002", "ZoneB", now)
        resp = client.get("/api/alerts/export/csv?uas_id=drone-001")
        body = resp.get_data(as_text=True)
        lines = body.strip().split("\n")
        assert len(lines) == 2
        assert "drone-001" in lines[1]
        assert "drone-002" not in body


class TestApiDronesIncremental:
    def test_incremental_drones_all(self, client, db):
        """Without known_timestamps, returns all drones."""
        resp = client.post(
            "/api/drones/incremental",
            data=json.dumps({"known_timestamps": {}}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drones" in data
        assert len(data["drones"]) >= 3

    def test_incremental_drones_with_known(self, client, db):
        """With known timestamps, returns only changed drones."""
        known = {}
        now = datetime.now()
        resp = client.get("/api/drones")
        for d in resp.get_json()["drones"]:
            sid = d.get("computed_session_id", "unknown")
            key = f"{d['uas_id']}:{sid}"
            known[key] = (now + timedelta(days=1)).isoformat()

        resp = client.post(
            "/api/drones/incremental",
            data=json.dumps({"known_timestamps": known}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drones" in data

    def test_incremental_drones_empty_window(self, client):
        resp = client.post(
            "/api/drones/incremental?start=2020-01-01T00:00:00&end=2020-01-02T00:00:00",
            data=json.dumps({"known_timestamps": {}}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["drones"] == []


class TestApiRefresh:
    def test_refresh_endpoint(self, client, db):
        resp = client.post(
            "/api/refresh",
            data=json.dumps({"known_timestamps": {}}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drones" in data
        assert "alerts" in data
        assert "stats" in data
        assert "sources" in data
        assert len(data["drones"]) >= 3
        assert data["alerts"]["count"] >= 0

    def test_refresh_with_known_timestamps(self, client, db):
        known = {}
        resp = client.get("/api/drones")
        for d in resp.get_json()["drones"]:
            sid = d.get("computed_session_id", "unknown")
            key = f"{d['uas_id']}:{sid}"
            known[key] = (datetime.now() + timedelta(days=1)).isoformat()

        resp = client.post(
            "/api/refresh",
            data=json.dumps({"known_timestamps": known}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "drones" in data
        assert "alerts" in data
        assert "stats" in data

    def test_refresh_invalid_params(self, client):
        resp = client.post(
            "/api/refresh?start=not-a-date",
            data=json.dumps({}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 500


class TestApiCollectors:
    def _operator_token(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("OpUser", "op@example.com", "operator", "op-login", expires)
        resp = client.post("/api/auth/login",
            data=json.dumps({"login_token": "op-login"}),
            content_type="application/json",
        )
        return resp.get_json()["token"]

    def test_collectors_no_config(self, client, app):
        """With no collectors in config, returns empty list."""
        token = self._operator_token(client, app)
        resp = client.get("/api/collectors", headers={"X-Auth-Token": token})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []

    def test_collectors_with_fixed(self, client, app, sample_config_yaml):
        """A fixed collector appears in the response."""
        token = self._operator_token(client, app)
        config_path, _ = sample_config_yaml
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["collectors"] = [
            {"name": "Node1", "api_key": "key1", "color": "#ff0000",
             "type": "fixed", "lat": 37.78, "lon": -122.41},
        ]
        data["web_interface"]["api_keys"]["key1"] = "Node1"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        import app as _app_module
        res = _app_module.CONFIG.reload_hot_config()
        if res is not None:
            _app_module.CONFIG = res
            _app_module._config_snapshot = res

        resp = client.get("/api/collectors", headers={"X-Auth-Token": token})
        assert resp.status_code == 200
        result = resp.get_json()
        assert len(result) == 1
        assert result[0]["name"] == "Node1"
        assert result[0]["type"] == "fixed"
        assert result[0]["latitude"] == 37.78
        assert result[0]["stale"] is True

    def test_collectors_with_mobile(self, client, app, sample_config_yaml):
        """A mobile collector appears with lat/lon from DB."""
        config_path, db_path = sample_config_yaml
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["collectors"] = [
            {"name": "Mobile1", "api_key": "key2", "color": "#00ff00",
             "type": "mobile", "lat": None, "lon": None},
        ]
        data["web_interface"]["api_keys"]["key2"] = "Mobile1"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        import app as _app_module
        res = _app_module.CONFIG.reload_hot_config()
        if res is not None:
            _app_module.CONFIG = res
            _app_module._config_snapshot = res

        # Update collector position via DB and log submission to avoid stale
        _app_module.DATABASE.update_collector_position("Mobile1", 37.77, -122.40)
        _app_module.DATABASE.log_submission("Mobile1", 0)

        token = self._operator_token(client, app)
        resp = client.get("/api/collectors", headers={"X-Auth-Token": token})
        assert resp.status_code == 200
        result = resp.get_json()
        assert len(result) == 1
        assert result[0]["name"] == "Mobile1"
        assert result[0]["type"] == "mobile"
        assert result[0]["latitude"] == 37.77
        assert result[0]["longitude"] == -122.40
        assert result[0]["stale"] is False


class TestApiPing:
    def test_ping_without_auth(self, client):
        resp = client.get("/api/submit/ping")
        assert resp.status_code == 401

    def test_ping_with_auth(self, client):
        resp = client.get(
            "/api/submit/ping",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["source"] == "test-source"

    def test_ping_with_position(self, client):
        resp = client.get(
            "/api/submit/ping?lat=37.78&lon=-122.41",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Verify collector position was stored
        import app as _app_module
        positions = _app_module.DATABASE.get_collector_positions()
        pos_by_name = {p["name"]: p for p in positions}
        assert pos_by_name.get("test-source") is not None, \
            "Collector position should have been stored in DB"

    def test_ping_invalid_position(self, client):
        """Invalid lat/lon values are silently ignored."""
        resp = client.get(
            "/api/submit/ping?lat=abc&lon=def",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200


class TestApiRedetect:
    def test_redetect_without_auth(self, client):
        resp = client.post("/api/sessions/redetect")
        assert resp.status_code == 401

    def test_redetect_with_auth(self, client):
        resp = client.post(
            "/api/sessions/redetect",
            headers={"Authorization": "Bearer test-api-key-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True


class TestPwa:
    def test_manifest_json(self, client):
        resp = client.get("/manifest.json")
        assert resp.status_code == 200
        assert resp.content_type == "application/json"
        data = resp.get_json()
        assert data["name"] == "Drone Tracker"
        assert data["short_name"] == "Drones"
        assert "icons" in data
        assert len(data["icons"]) == 2

    def test_manifest_start_url(self, client):
        resp = client.get("/manifest.json")
        data = resp.get_json()
        assert data["start_url"] == "/"

    def test_pwa_icon_valid(self, client):
        resp = client.get("/icons/icon-192x192.png")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"
        assert resp.headers["Cache-Control"] == "public, max-age=86400"

    def test_pwa_icon_not_found(self, client):
        resp = client.get("/icons/nonexistent.png")
        assert resp.status_code == 404

    def test_pwa_icon_path_traversal(self, client):
        """Path traversal attempts are rejected."""
        resp = client.get("/icons/../../../etc/passwd")
        assert resp.status_code == 404


class TestApiPush:
    def test_push_subscribe_invalid(self, client):
        """Missing fields return 400."""
        resp = client.post(
            "/api/push/subscribe",
            data=json.dumps({"endpoint": "https://example.com"}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Invalid subscription data" in data.get("error", "")

    def test_push_subscribe_valid(self, client):
        resp = client.post(
            "/api/push/subscribe",
            data=json.dumps({
                "endpoint": "https://push.example.com/abc",
                "keys": {"p256dh": "key123", "auth": "auth456"},
            }),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_push_unsubscribe_invalid(self, client):
        resp = client.post(
            "/api/push/unsubscribe",
            data=json.dumps({}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 400

    def test_push_unsubscribe_valid(self, client):
        """Subscribe then unsubscribe."""
        client.post(
            "/api/push/subscribe",
            data=json.dumps({
                "endpoint": "https://push.example.com/remove-me",
                "keys": {"p256dh": "k1", "auth": "a1"},
            }),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        resp = client.post(
            "/api/push/unsubscribe",
            data=json.dumps({"endpoint": "https://push.example.com/remove-me"}),
            content_type="application/json",
            headers={"X-CSRFToken": "test"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True


class TestCSRF:
    def test_csrf_present_in_config(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        assert "csrf_token" in data
        assert data["csrf_token"] is not None


class TestAuth:
    """Tests for authentication endpoints"""

    def _get_session_token(self, client):
        """Helper: create an ephemeral user and return the session token."""
        resp = client.post("/api/auth/anon")
        assert resp.status_code == 200
        data = resp.get_json()
        return data["token"]

    def test_anon_creates_ephemeral_user(self, client):
        resp = client.post("/api/auth/anon")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert len(data["token"]) > 0
        assert "user" in data
        assert data["user"]["role"] == "guest"
        assert data["user"]["name"].startswith("Guest-")

    def test_anon_user_has_auth_method_ephemeral(self, client, app):
        import app as _app_module
        token = self._get_session_token(client)
        user = _app_module.DATABASE.get_user_by_auth_token(token)
        assert user is not None
        assert user["auth_method"] == "ephemeral"

    def test_auth_me_unauthenticated(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is False

    def test_auth_me_authenticated(self, client):
        token = self._get_session_token(client)
        resp = client.get("/api/auth/me", headers={"X-Auth-Token": token})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is True
        assert "user" in data
        assert data["user"]["role"] == "guest"
        assert data["user"]["is_ephemeral"] is True
        assert "permissions" in data

    def test_auth_me_with_invalid_token(self, client):
        resp = client.get("/api/auth/me", headers={"X-Auth-Token": "invalid-token"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is False

    def test_auth_login_valid_token(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("TestUser", "test@example.com", "operator", "my-login-token", expires_at)

        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "my-login-token"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert data["user"]["name"] == "TestUser"
        assert data["user"]["role"] == "operator"
        assert data["user"]["email"] == "test@example.com"

        # Session token works
        me = client.get("/api/auth/me", headers={"X-Auth-Token": data["token"]})
        assert me.get_json()["authenticated"] is True

    def test_auth_login_invalid_token(self, client):
        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "nonexistent"}),
            content_type="application/json",
        )
        assert resp.status_code == 401
        data = resp.get_json()
        assert "error" in data

    def test_auth_login_expired_token(self, client, app):
        import app as _app_module
        db = _app_module.DATABASE
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.create_user("ExpiredUser", "expired@example.com", "viewer", "expired-login", expires_at)

        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "expired-login"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_auth_login_missing_token(self, client):
        resp = client.post(
            "/api/auth/login",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_auth_logout_revokes_token(self, client):
        token = self._get_session_token(client)

        # Token works before logout
        me_before = client.get("/api/auth/me", headers={"X-Auth-Token": token})
        assert me_before.get_json()["authenticated"] is True

        resp = client.post(
            "/api/auth/logout",
            data=json.dumps({"token": token}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Token no longer works after logout
        me_after = client.get("/api/auth/me", headers={"X-Auth-Token": token})
        assert me_after.get_json()["authenticated"] is False

    def test_auth_logout_from_header(self, client):
        """Logout also works when token is in X-Auth-Token header."""
        token = self._get_session_token(client)

        resp = client.post(
            "/api/auth/logout",
            headers={"X-Auth-Token": token},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # Token revoked
        me = client.get("/api/auth/me", headers={"X-Auth-Token": token})
        assert me.get_json()["authenticated"] is False

    def test_auth_logout_without_token(self, client):
        """Logout without a token still returns success."""
        resp = client.post(
            "/api/auth/logout",
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_config_includes_auth_block(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "auth" in data
        assert "authenticated" in data["auth"]
        assert data["auth"]["authenticated"] is False
        assert "permissions" in data["auth"]

    def test_config_auth_block_authenticated(self, client):
        token = self._get_session_token(client)
        resp = client.get("/api/config", headers={"X-Auth-Token": token})
        data = resp.get_json()
        assert data["auth"]["authenticated"] is True

    def test_auth_me_returns_permissions(self, client):
        token = self._get_session_token(client)
        resp = client.get("/api/auth/me", headers={"X-Auth-Token": token})
        data = resp.get_json()
        assert "permissions" in data
        # Guest role has read-only permissions
        assert data["permissions"] == [
            "view_map", "view_drones", "view_tracks",
            "view_operators", "view_waypoints", "use_replay",
        ]

    def test_login_single_use(self, client, app):
        """A login token can only be used once."""
        import app as _app_module
        db = _app_module.DATABASE
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("SingleUse", "single@example.com", "viewer", "single-use-token", expires_at)

        resp1 = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "single-use-token"}),
            content_type="application/json",
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "single-use-token"}),
            content_type="application/json",
        )
        assert resp2.status_code == 401

    def test_login_upgrades_ephemeral_in_place(self, client, app):
        """Ephemeral user upgraded in-place when login link is used in same session."""
        import app as _app_module
        db = _app_module.DATABASE

        # Create an ephemeral session first
        anon = client.post("/api/auth/anon")
        ephemeral_token = anon.get_json()["token"]

        # Create a pre-created user
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("Alice", "alice@example.com", "operator", "alice-login", expires_at)

        # Login with the ephemeral session's X-Auth-Token
        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "alice-login"}),
            content_type="application/json",
            headers={"X-Auth-Token": ephemeral_token},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["token"] == ephemeral_token
        assert data["user"]["name"] == "Alice"
        assert data["user"]["email"] == "alice@example.com"
        assert data["user"]["role"] == "operator"

        # Verify the upgraded session works
        me = client.get("/api/auth/me", headers={"X-Auth-Token": ephemeral_token})
        me_data = me.get_json()
        assert me_data["authenticated"] is True
        assert me_data["user"]["name"] == "Alice"
        assert me_data["user"]["role"] == "operator"

        # Verify the pre-created user was deactivated (login token gone)
        reuse = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "alice-login"}),
            content_type="application/json",
        )
        assert reuse.status_code == 401

    def test_login_does_not_upgrade_without_existing_session(self, client, app):
        """Normal login (no X-Auth-Token) creates a fresh session as before."""
        import app as _app_module
        db = _app_module.DATABASE

        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("Bob", "bob@example.com", "viewer", "bob-login", expires_at)

        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "bob-login"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["token"] != ""  # fresh session token
        assert data["user"]["name"] == "Bob"

    def test_login_does_not_upgrade_non_ephemeral_session(self, client, app):
        """An already-logged-in (non-ephemeral) session is not upgraded."""
        import app as _app_module
        db = _app_module.DATABASE

        # Create a real user and login first
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.create_user("Carol", "carol@example.com", "viewer", "carol-login-1", expires_at)

        resp1 = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "carol-login-1"}),
            content_type="application/json",
        )
        existing_token = resp1.get_json()["token"]

        # Create another user and login while holding the first session
        db.create_user("Dave", "dave@example.com", "operator", "dave-login", expires_at)

        resp2 = client.post(
            "/api/auth/login",
            data=json.dumps({"login_token": "dave-login"}),
            content_type="application/json",
            headers={"X-Auth-Token": existing_token},
        )
        assert resp2.status_code == 200
        data = resp2.get_json()
        # Token should be a new session token (Carol's session is not ephemeral)
        assert data["token"] != existing_token
        assert data["user"]["name"] == "Dave"
        assert data["user"]["role"] == "operator"
