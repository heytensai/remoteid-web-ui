"""Tests for config.py - configuration loading"""

import os
import tempfile

import pytest
import yaml

from config import WebConfig, MapConfig, CollectorConfig


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
    config_data = {
        "web_interface": {
            "host": "0.0.0.0",
            "port": 8080,
            "database_path": "/tmp/test.db",
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
                {"name": "c1", "remote_db_path": "/data/c1.db"},
                {"name": "c2", "remote_db_path": "/data/c2.db", "host": "10.0.0.1"},
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
        assert cfg.database_path == "/tmp/test.db"
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


def test_to_dict(sample_config_yaml):
    config_path, _ = sample_config_yaml
    cfg = WebConfig(config_path)
    d = cfg.to_dict()
    assert d["host"] == "127.0.0.1"
    assert d["port"] == 5001
    assert d["map"]["center_lat"] == 37.7749
    assert d["use_metric"] is True
    assert len(d["collectors"]) == 0


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
