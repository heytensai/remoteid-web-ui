"""Tests for config.py - configuration loading"""

import os
import tempfile

import pytest
import yaml

from config import (
    WebConfig, MapConfig, WaypointConfig, RoleConfig, MaintenanceConfig,
    NotificationTargetConfig, VALID_NOTIFIER_TYPES,
)


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
        assert cfg.default_hours == 24
        assert cfg.max_positions_per_query == 5000
        assert cfg.use_metric is True
        assert cfg.url_prefix == ""
        assert cfg.map.tile_provider == "osm"
        assert cfg.api_keys == {}
        assert cfg.drone_aliases == {}
    finally:
        os.unlink(path)


def test_web_config_full():
    with tempfile.TemporaryDirectory() as td:

        config_data = {
            "web_interface": {
                "host": "0.0.0.0",
                "port": 8080,
                "database_path": os.path.join(td, "web.db"),
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
                "api_keys": {"key1": "source1"},
                "drone_aliases": {"abc": "Drone-ABC"},
                "alerts": {
                    "stale_timeout": 600,
                    "skip_known_drones": True,
                },
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
            assert cfg.default_hours == 12
            assert cfg.max_positions_per_query == 1000
            assert cfg.use_metric is False
            assert cfg.url_prefix == "/rid"
            assert cfg.map.center_lat == 51.5
            assert cfg.map.tile_provider == "carto-light"
            assert cfg.api_keys == {"key1": "source1"}
            assert cfg.drone_aliases == {"abc": "Drone-ABC"}
            assert cfg.alerts.stale_timeout == 600
            assert cfg.alerts.skip_known_drones is True
        finally:
            os.unlink(path)


def test_web_config_missing_file():
    with pytest.raises(FileNotFoundError):
        WebConfig("/nonexistent/config.yaml")


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
            # Default alert_enabled should be False
            assert wp2["alert_enabled"] is False
        finally:
            os.unlink(path)


def test_to_dict_with_alert_enabled():
    with tempfile.TemporaryDirectory() as td:
        config_data = {
            "web_interface": {
                "database_path": os.path.join(td, "web.db"),
                "waypoints": [
                    {
                        "name": "AlertCircle",
                        "lat": 38.0,
                        "lon": -123.0,
                        "type": "circle",
                        "radius": 100,
                        "alert_enabled": True,
                    },
                ],
                "alerts": {
                    "stale_timeout": 600,
                    "skip_known_drones": True,
                },
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            path = f.name
        try:
            cfg = WebConfig(path)
            d = cfg.to_dict()
            assert d["waypoints"][0]["alert_enabled"] is True
            assert d["alerts"]["stale_timeout"] == 600
            assert d["alerts"]["skip_known_drones"] is True
            assert cfg.alerts.stale_timeout == 600
            assert cfg.alerts.skip_known_drones is True
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

    def test_negative_default_hours(self):
        path = _write_config({"default_hours": -1})
        with pytest.raises(ValueError, match="default_hours"):
            WebConfig(path)

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

    def test_stale_timeout_negative(self):
        path = _write_config({
            "database_path": "/tmp",
            "alerts": {"stale_timeout": -1},
        })
        with pytest.raises(ValueError, match="stale_timeout.*positive"):
            WebConfig(path)

    def test_stale_timeout_zero(self):
        path = _write_config({
            "database_path": "/tmp",
            "alerts": {"stale_timeout": 0},
        })
        with pytest.raises(ValueError, match="stale_timeout.*positive"):
            WebConfig(path)


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


class TestApiKeysHotReload:
    """Tests for api_keys hot reload in reload_hot_config"""

    def test_api_keys_reloadable(self, sample_config_yaml):
        """Changing api_keys in the YAML is picked up by reload_hot_config"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)
        assert cfg.api_keys == {"test-api-key-123": "test-source"}

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["api_keys"] = {
            "new-key": "new-source",
            "another-key": "another-source",
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        cfg = cfg.reload_hot_config() or cfg
        assert cfg.api_keys == {
            "new-key": "new-source",
            "another-key": "another-source",
        }

    def test_api_keys_reload_removes_keys(self, sample_config_yaml):
        """Removing keys from YAML is reflected after reload"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)
        assert cfg.api_keys == {"test-api-key-123": "test-source"}

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["api_keys"] = {}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        cfg = cfg.reload_hot_config() or cfg
        assert cfg.api_keys == {}

    def test_api_keys_hot_reload_logs_change(self, sample_config_yaml, caplog):
        """reload_hot_config logs when api_keys change"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["api_keys"] = {"new-key": "new-source"}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        with caplog.at_level("INFO"):
            cfg.reload_hot_config()

        assert any("Reloaded api_keys" in msg for msg in caplog.messages)

    def test_api_keys_no_log_when_unchanged(self, sample_config_yaml, caplog):
        """reload_hot_config does not log when api_keys haven't changed"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)

        with caplog.at_level("INFO"):
            cfg.reload_hot_config()

        assert not any("Reloaded api_keys" in msg for msg in caplog.messages)


# --- Role configuration tests ---


def test_role_config_dataclass():
    role = RoleConfig(name="operator", permissions=["view_map", "view_drones", "*"])
    assert role.name == "operator"
    assert role.permissions == ["view_map", "view_drones", "*"]


def test_role_config_empty_permissions():
    role = RoleConfig(name="guest", permissions=[])
    assert role.permissions == []


def test_parse_roles_empty():
    config_data = {"web_interface": {"database_path": "/tmp/test_empty_roles.db"}}
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.roles == {}
    finally:
        os.unlink(path)


def test_parse_roles_with_data():
    config_data = {
        "database_path": "/tmp/test_roles.db",
        "roles": {
            "operator": {
                "permissions": ["view_map", "view_drones", "view_tracks"],
            },
            "viewer": {
                "permissions": ["view_map", "view_drones"],
            },
        },
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert "operator" in cfg.roles
        assert cfg.roles["operator"].name == "operator"
        assert cfg.roles["operator"].permissions == ["view_map", "view_drones", "view_tracks"]
        assert "viewer" in cfg.roles
        assert cfg.roles["viewer"].permissions == ["view_map", "view_drones"]
    finally:
        os.unlink(path)


def test_parse_roles_with_wildcard():
    """A role with '*' permission gets full access."""
    config_data = {
        "database_path": "/tmp/test_wildcard.db",
        "roles": {
            "admin": {"permissions": ["*"]},
        },
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.roles["admin"].permissions == ["*"]
    finally:
        os.unlink(path)


def test_get_role_permissions_found():
    config_data = {
        "database_path": "/tmp/test_get_perm.db",
        "roles": {
            "viewer": {"permissions": ["view_map"]},
        },
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.get_role_permissions("viewer") == ["view_map"]
    finally:
        os.unlink(path)


def test_get_role_permissions_not_found():
    config_data = {
        "web_interface": {
            "database_path": "/tmp/test_get_perm_missing.db",
            "roles": {
                "viewer": {"permissions": ["view_map"]},
            },
        }
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.get_role_permissions("nonexistent") == []
    finally:
        os.unlink(path)


def test_roles_in_to_dict():
    config_data = {
        "database_path": "/tmp/test_roles_dict.db",
        "roles": {
            "operator": {"permissions": ["view_map", "view_drones"]},
        },
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        d = cfg.to_dict()
        assert "roles" in d
        assert d["roles"]["operator"]["name"] == "operator"
        assert d["roles"]["operator"]["permissions"] == ["view_map", "view_drones"]
    finally:
        os.unlink(path)


def test_roles_empty_in_to_dict():
    """When no roles configured, to_dict still includes empty 'roles' key."""
    config_data = {
        "web_interface": {
            "database_path": "/tmp/test_empty_roles_dict.db",
        }
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        d = cfg.to_dict()
        assert "roles" in d
        assert d["roles"] == {}
    finally:
        os.unlink(path)


class TestRolesHotReload:
    """Tests for roles hot reload in reload_hot_config"""

    def test_roles_reloadable(self, sample_config_yaml):
        """Changing roles in the YAML is picked up by reload_hot_config"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)
        assert "custom" not in cfg.roles

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["roles"] = {
            "custom": {"permissions": ["view_map"]},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        cfg = cfg.reload_hot_config() or cfg
        assert "custom" in cfg.roles
        assert cfg.roles["custom"].permissions == ["view_map"]

    def test_roles_hot_reload_logs_change(self, sample_config_yaml, caplog):
        """reload_hot_config logs when roles change"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["roles"] = {
            "custom": {"permissions": ["view_map"]},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        with caplog.at_level("INFO"):
            cfg.reload_hot_config()

        assert any("Reloaded roles" in msg for msg in caplog.messages)

    def test_roles_no_log_when_unchanged(self, sample_config_yaml, caplog):
        """reload_hot_config does not log when roles haven't changed"""
        config_path, _ = sample_config_yaml

        cfg = WebConfig(config_path)

        with caplog.at_level("INFO"):
            cfg.reload_hot_config()

        assert not any("Reloaded roles" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# MaintenanceConfig
# ---------------------------------------------------------------------------


def test_maintenance_config_defaults():
    """MaintenanceConfig uses all-default values when no data is given."""
    mc = MaintenanceConfig()
    assert mc.enabled is False
    assert mc.interval == 3600
    assert mc.delete_expired_tokens is True
    assert mc.delete_expired_login_tokens is True
    assert mc.delete_orphaned_ephemeral_users is True


def test_maintenance_config_with_data():
    """MaintenanceConfig parses provided values correctly."""
    mc = MaintenanceConfig({
        "enabled": True,
        "interval": 7200,
        "delete_expired_tokens": False,
        "delete_expired_login_tokens": True,
        "delete_orphaned_ephemeral_users": False,
    })
    assert mc.enabled is True
    assert mc.interval == 7200
    assert mc.delete_expired_tokens is False
    assert mc.delete_expired_login_tokens is True
    assert mc.delete_orphaned_ephemeral_users is False


def test_maintenance_config_from_yaml():
    """MaintenanceConfig is parsed from the web_config YAML."""
    config_data = {
        "web_interface": {
            "database_path": "/tmp",
            "maintenance": {
                "enabled": True,
                "interval": 1800,
                "delete_expired_login_tokens": False,
            },
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name
    try:
        cfg = WebConfig(path)
        assert cfg.maintenance.enabled is True
        assert cfg.maintenance.interval == 1800
        assert cfg.maintenance.delete_expired_tokens is True  # default
        assert cfg.maintenance.delete_expired_login_tokens is False
        assert cfg.maintenance.delete_orphaned_ephemeral_users is True  # default
    finally:
        os.unlink(path)


def test_maintenance_config_empty_yaml():
    """No maintenance block in YAML gives all-default values."""
    config_data = {"web_interface": {"database_path": "/tmp"}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name
    try:
        cfg = WebConfig(path)
        assert cfg.maintenance.enabled is False
        assert cfg.maintenance.interval == 3600
        assert cfg.maintenance.delete_expired_tokens is True
    finally:
        os.unlink(path)


def test_maintenance_hot_reload_logs_change(sample_config_yaml, caplog):
    """reload_hot_config logs when maintenance config changes."""
    config_path, _ = sample_config_yaml
    cfg = WebConfig(config_path)

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["web_interface"]["maintenance"] = {"enabled": True, "interval": 7200}
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    with caplog.at_level("INFO"):
        cfg.reload_hot_config()

    assert any("Reloaded maintenance" in msg for msg in caplog.messages)


def test_maintenance_no_log_when_unchanged(sample_config_yaml, caplog):
    """reload_hot_config does not log when maintenance hasn't changed."""
    config_path, _ = sample_config_yaml
    cfg = WebConfig(config_path)

    with caplog.at_level("INFO"):
        cfg.reload_hot_config()

    assert not any("Reloaded maintenance" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Notification target config
# ---------------------------------------------------------------------------


def test_valid_notifier_types_includes_ntfy():
    assert "ntfy" in VALID_NOTIFIER_TYPES
    assert "discord" in VALID_NOTIFIER_TYPES
    assert "push" in VALID_NOTIFIER_TYPES
    assert "teams" in VALID_NOTIFIER_TYPES


def test_notification_target_config_defaults():
    nt = NotificationTargetConfig(name="Test", type="push", events=["alert"])
    assert nt.enabled is True
    assert nt.webhook_url == ""
    assert nt.token == ""
    assert nt.username == ""
    assert nt.password == ""


def test_notification_target_config_disabled():
    nt = NotificationTargetConfig(
        name="Disabled", type="discord", events=["alert"], enabled=False,
    )
    assert nt.enabled is False


def test_notification_target_config_with_token():
    nt = NotificationTargetConfig(
        name="ntfy-test", type="ntfy", events=["alert"],
        webhook_url="https://ntfy.sh/topic", token="tk_abc123",
    )
    assert nt.token == "tk_abc123"
    assert nt.webhook_url == "https://ntfy.sh/topic"
    assert nt.username == ""


def test_notification_target_config_with_basic_auth():
    nt = NotificationTargetConfig(
        name="ntfy-basic", type="ntfy", events=["alert"],
        webhook_url="https://ntfy.example.com/t", username="admin", password="secret",
    )
    assert nt.username == "admin"
    assert nt.password == "secret"
    assert nt.token == ""


def test_parse_notifications_ntfy():
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {
                "name": "Phone Alerts",
                "type": "ntfy",
                "webhook_url": "https://ntfy.sh/mytopic",
                "token": "tk_secret",
                "events": ["alert", "new_session"],
            },
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert len(cfg.notifications) == 1
        nt = cfg.notifications[0]
        assert nt.name == "Phone Alerts"
        assert nt.type == "ntfy"
        assert nt.webhook_url == "https://ntfy.sh/mytopic"
        assert nt.token == "tk_secret"
        assert nt.events == ["alert", "new_session"]
    finally:
        os.unlink(path)


def test_parse_notifications_ntfy_no_token():
    """ntfy target without token is valid (public topic)."""
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {
                "name": "Public ntfy",
                "type": "ntfy",
                "webhook_url": "https://ntfy.sh/public-topic",
                "events": ["alert"],
            },
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.notifications[0].token == ""
        assert cfg.notifications[0].username == ""
    finally:
        os.unlink(path)


def test_parse_notifications_ntfy_basic_auth():
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {
                "name": "Self-Hosted",
                "type": "ntfy",
                "webhook_url": "https://ntfy.example.com/alerts",
                "username": "myuser",
                "password": "mypassword",
                "events": ["alert"],
            },
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        nt = cfg.notifications[0]
        assert nt.username == "myuser"
        assert nt.password == "mypassword"
        assert nt.token == ""
    finally:
        os.unlink(path)


def test_parse_notifications_disabled():
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {
                "name": "Paused",
                "type": "discord",
                "webhook_url": "https://discord.com/api/webhooks/...",
                "enabled": False,
                "events": ["alert"],
            },
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.notifications[0].enabled is False
    finally:
        os.unlink(path)


def test_parse_notifications_enabled_defaults_true():
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {"name": "Active", "type": "push", "events": ["alert"]},
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert cfg.notifications[0].enabled is True
    finally:
        os.unlink(path)


def test_parse_notifications_multiple_types():
    config_data = {
        "database_path": "/tmp",
        "notifications": [
            {"name": "Push", "type": "push", "events": ["alert"]},
            {"name": "Discord", "type": "discord", "webhook_url": "https://discord.com/api/webhooks/...", "events": ["new_session"]},
            {"name": "ntfy", "type": "ntfy", "webhook_url": "https://ntfy.sh/t", "token": "tk_x", "events": ["alert", "new_session"]},
        ],
    }
    path = _write_config(config_data)
    try:
        cfg = WebConfig(path)
        assert len(cfg.notifications) == 3
        types = {nt.type for nt in cfg.notifications}
        assert types == {"push", "discord", "ntfy"}
    finally:
        os.unlink(path)


def test_notification_hot_reload_detects_token_change(sample_config_yaml):
    config_path, _ = sample_config_yaml
    cfg = WebConfig(config_path)

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["web_interface"]["notifications"] = [
        {
            "name": "ntfy",
            "type": "ntfy",
            "webhook_url": "https://ntfy.sh/t",
            "token": "tk_old",
            "events": ["alert"],
        },
    ]
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    cfg = cfg.reload_hot_config() or cfg
    assert cfg.notifications[0].token == "tk_old"

    # Change the token
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["web_interface"]["notifications"][0]["token"] = "tk_new"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    new_cfg = cfg.reload_hot_config()
    assert new_cfg is not None
    assert new_cfg.notifications[0].token == "tk_new"
