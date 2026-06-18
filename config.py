"""Configuration loader for web interface"""

import logging
import os

from dataclasses import dataclass, field
from typing import List, Optional
import yaml

logger = logging.getLogger(__name__)


@dataclass
class MapConfig:
    """Map display configuration"""

    center_lat: Optional[float] = None
    center_lon: Optional[float] = None
    default_zoom: Optional[int] = None
    tile_provider: str = "osm"

    def __init__(self, data: dict = None):
        if data:
            self.center_lat = data.get("center_lat")
            self.center_lon = data.get("center_lon")
            self.default_zoom = data.get("default_zoom")
            self.tile_provider = data.get("tile_provider", "osm")


@dataclass
class WaypointConfig:
    """Custom waypoint displayed on the map"""

    name: str
    lat: float
    lon: float
    icon: str = "fa-map-pin"
    color: str = "#007bff"
    description: str = ""
    enabled: bool = True
    category: str = ""


@dataclass
class CollectorConfig:
    """Collector configuration - can be remote (ssh) or local"""

    name: str
    remote_db_path: str
    host: Optional[str] = None  # If None, treat as local file


@dataclass
class WebConfig:  # pylint: disable=too-many-instance-attributes
    """Web interface configuration"""

    host: str = "0.0.0.0"
    port: int = 5000
    database_path: str = "./web.db"
    sync_interval: int = 30
    default_hours: int = 24
    max_positions_per_query: int = 5000
    map: MapConfig = field(default_factory=MapConfig)
    collectors: List[CollectorConfig] = field(default_factory=list)
    waypoints: List[WaypointConfig] = field(default_factory=list)
    api_keys: dict = field(default_factory=dict)
    url_prefix: str = ""
    drone_aliases: dict = field(default_factory=dict)
    use_metric: bool = True

    def __init__(self, yaml_file: str):
        self.config_path = yaml_file

        try:
            with open(yaml_file, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as e:
            raise ValueError(
                f"Failed to parse config file {yaml_file}: {e}"
            ) from e

        if not isinstance(data, dict):
            raise ValueError(
                f"Config file {yaml_file} must contain a top-level mapping, "
                f"got {type(data).__name__}"
            )

        web_data = data.get("web_interface", {})

        self.host = web_data.get("host", "0.0.0.0")
        self.port = web_data.get("port", 5000)
        self.database_path = web_data.get("database_path", "./web.db")
        self.sync_interval = web_data.get("sync_interval", 30)
        self.default_hours = web_data.get("default_hours", 24)
        self.max_positions_per_query = web_data.get("max_positions_per_query", 5000)
        self.url_prefix = web_data.get("url_prefix", "")

        # Map configuration
        map_data = web_data.get("map") or {}
        self.map = MapConfig(map_data)

        # Collector configuration
        self.collectors = []
        for collector_data in web_data.get("collectors") or []:
            self.collectors.append(
                CollectorConfig(
                    name=collector_data["name"],
                    remote_db_path=collector_data["remote_db_path"],
                    host=collector_data.get("host"),  # Optional - None for local
                )
            )

        # Waypoint configuration
        self.waypoints = []
        for wp_data in web_data.get("waypoints") or []:
            self.waypoints.append(
                WaypointConfig(
                    name=wp_data["name"],
                    lat=wp_data["lat"],
                    lon=wp_data["lon"],
                    icon=wp_data.get("icon", "fa-map-pin"),
                    color=wp_data.get("color", "#007bff"),
                    description=wp_data.get("description", ""),
                    enabled=wp_data.get("enabled", True),
                    category=wp_data.get("category", ""),
                )
            )

        # API key configuration: api_key -> source name
        self.api_keys = web_data.get("api_keys") or {}

        # Drone aliases: uas_id -> friendly name
        self.drone_aliases = web_data.get("drone_aliases") or {}

        # Units preference: true for metric, false for imperial
        self.use_metric = web_data.get("use_metric", True)

        self._validate()

    def _validate(self):
        """Validate configuration values, raising ValueError on invalid input."""
        errors = []

        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            errors.append(f"port must be an integer between 1 and 65535, got {self.port!r}")

        if self.sync_interval is not None and self.sync_interval <= 0:
            errors.append(f"sync_interval must be positive, got {self.sync_interval}")

        if self.default_hours is not None and self.default_hours <= 0:
            errors.append(f"default_hours must be positive, got {self.default_hours}")

        if self.max_positions_per_query is not None and self.max_positions_per_query <= 0:
            errors.append(f"max_positions_per_query must be positive, got {self.max_positions_per_query}")

        if self.map.center_lat is not None:
            if not isinstance(self.map.center_lat, (int, float)):
                errors.append(f"map.center_lat must be a number, got {self.map.center_lat!r}")
            elif not -90 <= self.map.center_lat <= 90:
                errors.append(f"map.center_lat must be between -90 and 90, got {self.map.center_lat}")

        if self.map.center_lon is not None:
            if not isinstance(self.map.center_lon, (int, float)):
                errors.append(f"map.center_lon must be a number, got {self.map.center_lon!r}")
            elif not -180 <= self.map.center_lon <= 180:
                errors.append(f"map.center_lon must be between -180 and 180, got {self.map.center_lon}")

        if self.map.default_zoom is not None:
            if not isinstance(self.map.default_zoom, int):
                errors.append(f"map.default_zoom must be an integer, got {self.map.default_zoom!r}")
            elif not 1 <= self.map.default_zoom <= 20:
                errors.append(f"map.default_zoom must be between 1 and 20, got {self.map.default_zoom}")

        for i, wp in enumerate(self.waypoints):
            prefix = f"waypoints[{i}] ({wp.name!r})"
            if not wp.name or not isinstance(wp.name, str):
                errors.append(f"{prefix}.name must be a non-empty string")
            if not isinstance(wp.lat, (int, float)):
                errors.append(f"{prefix}.lat must be a number, got {wp.lat!r}")
            elif not -90 <= wp.lat <= 90:
                errors.append(f"{prefix}.lat must be between -90 and 90, got {wp.lat}")
            if not isinstance(wp.lon, (int, float)):
                errors.append(f"{prefix}.lon must be a number, got {wp.lon!r}")
            elif not -180 <= wp.lon <= 180:
                errors.append(f"{prefix}.lon must be between -180 and 180, got {wp.lon}")

        for collector in self.collectors:
            if collector.host is None:
                db_dir = os.path.dirname(collector.remote_db_path)
                if db_dir and not os.path.isdir(db_dir):
                    errors.append(
                        f"collector '{collector.name}': remote_db_path parent directory "
                        f"does not exist: {db_dir}"
                    )

        db_dir = os.path.dirname(self.database_path)
        if db_dir and not os.path.isdir(db_dir):
            errors.append(
                f"database_path parent directory does not exist: {db_dir}"
            )

        if errors:
            raise ValueError(
                "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "host": self.host,
            "port": self.port,
            "database_path": self.database_path,
            "sync_interval": self.sync_interval,
            "default_hours": self.default_hours,
            "max_positions_per_query": self.max_positions_per_query,
            "map": {
                "center_lat": self.map.center_lat,
                "center_lon": self.map.center_lon,
                "default_zoom": self.map.default_zoom,
                "tile_provider": self.map.tile_provider,
            },
            "collectors": [
                {"name": c.name, "host": c.host, "remote_db_path": c.remote_db_path}
                for c in self.collectors
            ],
            "waypoints": [
                {
                    "name": w.name,
                    "lat": w.lat,
                    "lon": w.lon,
                    "icon": w.icon,
                    "color": w.color,
                    "description": w.description,
                    "enabled": w.enabled,
                    "category": w.category,
                }
                for w in self.waypoints
            ],
            "use_metric": self.use_metric,
        }

    def reload_hot_config(self):
        """Re-read config file and update hot-reloadable fields (drone_aliases, waypoints)"""
        try:
            with open(self.config_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            web_data = data.get("web_interface", {})

            # Reload drone aliases
            new_aliases = web_data.get("drone_aliases") or {}
            if new_aliases != self.drone_aliases:
                self.drone_aliases = new_aliases
                logger.info("Reloaded drone_aliases from %s", self.config_path)

            # Reload waypoints
            new_waypoints = []
            for wp_data in web_data.get("waypoints") or []:
                new_waypoints.append(
                    WaypointConfig(
                        name=wp_data["name"],
                        lat=wp_data["lat"],
                        lon=wp_data["lon"],
                        icon=wp_data.get("icon", "fa-map-pin"),
                        color=wp_data.get("color", "#007bff"),
                        description=wp_data.get("description", ""),
                        enabled=wp_data.get("enabled", True),
                        category=wp_data.get("category", ""),
                    )
                )
            old_dict = {w.name: w for w in self.waypoints}
            new_dict = {w.name: w for w in new_waypoints}
            if old_dict != new_dict:
                self.waypoints = new_waypoints
                logger.info("Reloaded waypoints from %s", self.config_path)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to reload hot config from %s", self.config_path)
