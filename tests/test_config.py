"""Tests for config.py - configuration loading"""

import os
import tempfile

import pytest
import yaml

from config import WebConfig, MapConfig, CollectorConfig, WaypointConfig


def test_map_config_defaults():
    mc = MapConfig({})
    assert mc.tile_provider == "osm"
    assert mc.center_lat is None
    assert mc.center_lon is None
    assert mc.default_zoom is None


def test_map_config_with_data():
    data = {
        "center_lat": 40.0,
        "center_lon": -74.0,
        "default_zoom": 15,
        "tile_provider": "carto-dark",
    }
    mc = MapConfig(data)
    assert mc.center_lat == 40.0
    assert mc.center_lon == -74.0
    assert mc.default_zoom == 15
    assert mc.tile_provider == "carto-dark"


def test_web_config_defaults():
    config_data = {"web_interface": {}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name
    try:
        cfg = WebConfig(path)
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 5000
        assert cfg.database_path == "./web.db"
        assert cfg.sync_interval == 30
        assert cfg.default_hours == 24
        assert cfg.max_positions_per_query == 5000
        assert cfg.use_metric is True
        assert cfg.url_prefix == ""
        assert cfg.map.tile_provider == "osm"
        assert cfg.collectors == []
        assert cfg.api_keys == {}
        assert cfg.drone_aliases == {}
    finally:
        os.unlink(path)


def test_web_config_full():
    with tempfile.TemporaryDirectory() as td:
        local_db_dir = os.path.join(td, "collector_data")
        os.makedirs(local_db_dir, exist_ok=True)

        config_data = {
            "web_interface": {
                "host": "0.0.0.0",
                "port": 8080,
                "database_path": os.path.join(td, "web.db"),
                "sync_interval": 60,
                "default_hours": 12,
                "max_positions_per_query": 1000,
                "use_metric": False,
                "url_prefix": "/rid",
                "map": {
                    "center_lat": 51.5,
                    "center_lon": -0.12,
                    "default_zoom": 12,
                    "tile_provider": "carto-light",
                },
                "collectors": [
                    {"name": "c1", "remote_db_path": os.path.join(local_db_dir, "c1.db")},
                    {"name": "c2", "remote_db_path": "/remote/c2.db", "host": "10.0.0.1"},
                ],
                "api_keys": {"key1": "source1"},
                "drone_aliases": {"abc": "Drone-ABC"},
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            path = f.name
        try:
            cfg = WebConfig(path)
            assert cfg.host == "0.0.0.0"
            assert cfg.port == 8080
            assert cfg.database_path == os.path.join(td, "web.db")
            assert cfg.sync_interval == 60
            assert cfg.default_hours == 12
            assert cfg.max_positions_per_query == 1000
            assert cfg.use_metric is False
            assert cfg.url_prefix == "/rid"
            assert cfg.map.center_lat == 51.5
            assert cfg.map.tile_provider == "carto-light"
            assert len(cfg.collectors) == 2
            assert cfg.collectors[0].name == "c1"
            assert cfg.collectors[0].host is None
            assert cfg.collectors[1].host == "10.0.0.1"
            assert cfg.api_keys == {"key1": "source1"}
            assert cfg.drone_aliases == {"abc": "Drone-ABC"}
        finally:
            os.unlink(path)


def test_web_config_missing_file():
    with pytest.raises(FileNotFoundError):
        WebConfig("/nonexistent/config.yaml")


def test_collector_config():
    cc = CollectorConfig(name="test", remote_db_path="/path/to/db")
    assert cc.name == "test"
    assert cc.remote_db_path == "/path/to/db"
    assert cc.host is None


def test_collector_config_remote():
    cc = CollectorConfig(name="remote", remote_db_path="/path", host="10.0.0.1")
    assert cc.host == "10.0.0.1"


def test_waypoint_config_defaults():
    wp = WaypointConfig(name="Test", lat=40.0, lon=-74.0)
    assert wp.name == "Test"
    assert wp.lat == 40.0
    assert wp.lon == -74.0
    assert wp.type == "point"
    assert wp.icon == "fa-map-pin"
    assert wp.color == "#007bff"
    assert wp.description == ""
    assert wp.enabled is True
    assert wp.category == ""
    assert wp.radius == 0.0
    assert wp.width == 0.0
    assert wp.height == 0.0
    assert wp.fill_opacity == 0.1


def test_waypoint_config_all_fields():
    wp = WaypointConfig(
        name="Launch",
        lat=37.0,
        lon=-122.0,
        type="circle",
        icon="fa-rocket",
        color="#e74c3c",
        description="Launch pad",
        enabled=False,
        category="ops",
        radius=200.0,
        fill_opacity=0.15,
    )
    assert wp.type == "circle"
    assert wp.icon == "fa-rocket"
    assert wp.color == "#e74c3c"
    assert wp.description == "Launch pad"
    assert wp.enabled is False
    assert wp.category == "ops"
    assert wp.radius == 200.0
    assert wp.fill_opacity == 0.15


def test_waypoints_parsing():
    with tempfile.TemporaryDirectory() as td:
        config_data = {
            "web_interface": {
                "database_path": os.path.join(td, "web.db"),
                "waypoints": [
                    {
                        "name": "WP1",
                        "lat": 37.0,
                        "lon": -122.0,
                    },
                    {
                        "name": "WP2",
                        "lat": 38.0,
                        "lon": -123.0,
                        "icon": "fa-flag",
                        "color": "#00ff00",
                        "description": "A waypoint",
                        "enabled": False,
                        "category": "test",
                    },
                    {
                        "name": "Circle1",
                        "lat": 39.0,
                        "lon": -124.0,
                        "type": "circle",
                        "radius": 200,
                        "color": "#e67e22",
                        "fill_opacity": 0.15,
                        "description": "Flight zone",
                    },
                    {
                        "name": "Rect1",
                        "lat": 40.0,
                        "lon": -125.0,
                        "type": "rectangle",
                        "width": 100,
                        "height": 50,
                        "color": "#27ae60",
                    },
                ],
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            path = f.name
        try:
            cfg = WebConfig(path)
            assert len(cfg.waypoints) == 4
            # Point WP1
            assert cfg.waypoints[0].name == "WP1"
            assert cfg.waypoints[0].type == "point"
            assert cfg.waypoints[0].radius == 0.0
            assert cfg.waypoints[0].width == 0.0
            assert cfg.waypoints[0].height == 0.0
            # Point WP2
            assert cfg.waypoints[1].name == "WP2"
            assert cfg.waypoints[1].icon == "fa-flag"
            assert cfg.waypoints[1].color == "#00ff00"
            assert cfg.waypoints[1].enabled is False
            assert cfg.waypoints[1].category == "test"
            # Circle1
            assert cfg.waypoints[2].name == "Circle1"
            assert cfg.waypoints[2].type == "circle"
            assert cfg.waypoints[2].radius == 200.0
            assert cfg.waypoints[2].fill_opacity == 0.15
            assert cfg.waypoints[2].color == "#e67e22"
            assert cfg.waypoints[2].description == "Flight zone"
            # Rect1
            assert cfg.waypoints[3].name == "Rect1"
            assert cfg.waypoints[3].type == "rectangle"
            assert cfg.waypoints[3].width == 100.0
            assert cfg.waypoints[3].height == 50.0
            assert cfg.waypoints[3].fill_opacity == 0.1
            assert cfg.waypoints[3].color == "#27ae60"
        finally:
            os.unlink(path)


def test_to_dict(sample_config_yaml):
    config_path, _ = sample_config_yaml
    cfg = WebConfig(config_path)
    d = cfg.to_dict()
    assert d["host"] == "127.0.0.1"
    assert d["port"] == 5001
    assert d["map"]["center_lat"] == 37.7749
    assert d["use_metric"] is True
    assert len(d["collectors"]) == 0
    assert d["waypoints"] == []


def test_to_dict_with_waypoints():
    with tempfile.TemporaryDirectory() as td:
        config_data = {
            "web_interface": {
                "database_path": os.path.join(td, "web.db"),
                "waypoints": [
                    {
                        "name": "WP1",
                        "lat": 37.0,
                        "lon": -122.0,
                        "icon": "fa-flag",
                        "color": "#ff0000",
                        "description": "Test",
                        "enabled": True,
                        "category": "cat",
                    },
                    {
                        "name": "Circle1",
                        "lat": 38.0,
                        "lon": -123.0,
                        "type": "circle",
                        "radius": 150,
                        "color": "#e67e22",
                        "fill_opacity": 0.2,
                    },
                ],
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            path = f.name
        try:
            cfg = WebConfig(path)
            d = cfg.to_dict()
            assert len(d["waypoints"]) == 2
            # Point WP1
            wp = d["waypoints"][0]
            assert wp["name"] == "WP1"
            assert wp["lat"] == 37.0
            assert wp["lon"] == -122.0
            assert wp["type"] == "point"
            assert wp["icon"] == "fa-flag"
            assert wp["color"] == "#ff0000"
            assert wp["description"] == "Test"
            assert wp["enabled"] is True
            assert wp["category"] == "cat"
            assert wp["radius"] == 0.0
            assert wp["width"] == 0.0
            assert wp["height"] == 0.0
            assert wp["fill_opacity"] == 0.1
            # Circle1
            wp2 = d["waypoints"][1]
            assert wp2["name"] == "Circle1"
            assert wp2["type"] == "circle"
            assert wp2["radius"] == 150.0
            assert wp2["fill_opacity"] == 0.2
            assert wp2["width"] == 0.0
            assert wp2["height"] == 0.0
        finally:
            os.unlink(path)


def test_default_hours_from_config():
    config_data = {"web_interface": {"default_hours": 48}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name
    try:
        cfg = WebConfig(path)
        assert cfg.default_hours == 48
    finally:
        os.unlink(path)


def _write_config(data):
    """Write config data to a temp YAML and return the path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"web_interface": data}, f)
        return f.name


def _write_raw(content):
    """Write raw string content to a temp YAML and return the path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


class TestValidation:
    """Validation tests for WebConfig"""

    def test_port_out_of_range(self):
        path = _write_config({"port": 99999})
        with pytest.raises(ValueError, match="port.*between 1 and 65535"):
            WebConfig(path)

    def test_port_not_int(self):
        path = _write_config({"port": "eight"})
        with pytest.raises(ValueError, match="port.*between 1 and 65535"):
            WebConfig(path)

    def test_center_lat_out_of_range(self):
        path = _write_config({"map": {"center_lat": 100}})
        with pytest.raises(ValueError, match="center_lat.*-90 and 90"):
            WebConfig(path)

    def test_center_lon_out_of_range(self):
        path = _write_config({"map": {"center_lon": -200}})
        with pytest.raises(ValueError, match="center_lon.*-180 and 180"):
            WebConfig(path)

    def test_center_lat_not_a_number(self):
        path = _write_config({"map": {"center_lat": "abc"}})
        with pytest.raises(ValueError, match="center_lat.*must be a number"):
            WebConfig(path)

    def test_default_zoom_out_of_range(self):
        path = _write_config({"map": {"default_zoom": 0}})
        with pytest.raises(ValueError, match="default_zoom.*between 1 and 20"):
            WebConfig(path)

    def test_default_zoom_not_int(self):
        path = _write_config({"map": {"default_zoom": 5.5}})
        with pytest.raises(ValueError, match="default_zoom.*must be an integer"):
            WebConfig(path)

    def test_negative_sync_interval(self):
        path = _write_config({"sync_interval": -5})
        with pytest.raises(ValueError, match="sync_interval"):
            WebConfig(path)

    def test_negative_default_hours(self):
        path = _write_config({"default_hours": -1})
        with pytest.raises(ValueError, match="default_hours"):
            WebConfig(path)

    def test_invalid_collector_local_path(self):
        path = _write_config({
            "collectors": [{"name": "bad", "remote_db_path": "/nonexistent/dir/db.db"}],
        })
        with pytest.raises(ValueError, match="collector.*bad.*parent directory"):
            WebConfig(path)

    def test_remote_collector_skips_path_check(self):
        path = _write_config({
            "collectors": [{"name": "remote", "remote_db_path": "/doesnt/exist.db", "host": "10.0.0.1"}],
        })
        cfg = WebConfig(path)
        assert cfg.collectors[0].name == "remote"

    def test_invalid_database_path(self):
        path = _write_config({"database_path": "/nonexistent/subdir/web.db"})
        with pytest.raises(ValueError, match="database_path parent directory"):
            WebConfig(path)

    def test_waypoint_lat_out_of_range(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "W", "lat": 100, "lon": 0}],
        })
        with pytest.raises(ValueError, match="waypoints.*lat.*between -90 and 90"):
            WebConfig(path)

    def test_waypoint_lon_out_of_range(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "W", "lat": 0, "lon": -200}],
        })
        with pytest.raises(ValueError, match="waypoints.*lon.*between -180 and 180"):
            WebConfig(path)

    def test_waypoint_lat_not_a_number(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "W", "lat": "abc", "lon": 0}],
        })
        with pytest.raises(ValueError, match="waypoints.*lat.*must be a number"):
            WebConfig(path)

    def test_waypoint_empty_name(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "", "lat": 37, "lon": -122}],
        })
        with pytest.raises(ValueError, match="waypoints.*name.*must be a non-empty string"):
            WebConfig(path)

    def test_circle_missing_radius(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "C", "lat": 37, "lon": -122, "type": "circle", "radius": 0}],
        })
        with pytest.raises(ValueError, match="radius.*> 0"):
            WebConfig(path)

    def test_rectangle_missing_width(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "R", "lat": 37, "lon": -122, "type": "rectangle", "width": 0, "height": 50}],
        })
        with pytest.raises(ValueError, match="width.*> 0"):
            WebConfig(path)

    def test_rectangle_missing_height(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "R", "lat": 37, "lon": -122, "type": "rectangle", "width": 50, "height": 0}],
        })
        with pytest.raises(ValueError, match="height.*> 0"):
            WebConfig(path)

    def test_invalid_waypoint_type(self):
        path = _write_config({
            "database_path": "/tmp",
            "waypoints": [{"name": "W", "lat": 37, "lon": -122, "type": "polygon"}],
        })
        with pytest.raises(ValueError, match="type.*must be.*point.*circle.*rectangle"):
            WebConfig(path)

    def test_geozone_imperial_conversion(self):
        """Circle radius in feet should be converted to meters when use_metric=False"""
        path = _write_config({
            "use_metric": False,
            "database_path": "/tmp/test_geozone_imperial.db",
            "waypoints": [
                {
                    "name": "Zone",
                    "lat": 37.0,
                    "lon": -122.0,
                    "type": "circle",
                    "radius": 328,  # ~100 meters
                },
                {
                    "name": "Rect",
                    "lat": 38.0,
                    "lon": -123.0,
                    "type": "rectangle",
                    "width": 164,   # ~50 meters
                    "height": 82,   # ~25 meters
                },
            ],
        })
        try:
            cfg = WebConfig(path)
            assert cfg.use_metric is False
            # 328 feet ≈ 100 meters
            assert abs(cfg.waypoints[0].radius - 100.0) < 1.0
            # 164 feet ≈ 50 meters
            assert abs(cfg.waypoints[1].width - 50.0) < 0.5
            # 82 feet ≈ 25 meters
            assert abs(cfg.waypoints[1].height - 25.0) < 0.5
        finally:
            os.unlink(path)

    def test_no_errors_for_valid_config(self):
        """Sanity check: a reasonable config passes validation"""
        with tempfile.TemporaryDirectory() as td:
            config_data = {
                "port": 8080,
                "database_path": os.path.join(td, "web.db"),
                "map": {
                    "center_lat": 37.0,
                    "center_lon": -122.0,
                    "default_zoom": 10,
                },
            }
            path = _write_config(config_data)
            cfg = WebConfig(path)
            assert cfg.port == 8080
            os.unlink(path)


class TestYamlErrors:
    """Tests for YAML parsing error handling"""

    def test_tab_instead_of_spaces(self):
        path = _write_raw("web_interface:\n\tport: 5000\n")
        with pytest.raises(ValueError, match="Failed to parse config"):
            WebConfig(path)

    def test_top_level_list(self):
        path = _write_raw("- item1\n- item2\n")
        with pytest.raises(ValueError, match="top-level mapping"):
            WebConfig(path)

    def test_top_level_scalar(self):
        path = _write_raw("just a string\n")
        with pytest.raises(ValueError, match="top-level mapping"):
            WebConfig(path)

    def test_corrupt_yaml(self):
        path = _write_raw("web_interface:\n  port: [5000\n")
        with pytest.raises(ValueError, match="Failed to parse config"):
            WebConfig(path)
