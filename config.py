"""Configuration loader for web interface"""

from dataclasses import dataclass, field
from typing import List, Optional
import yaml


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
    api_keys: dict = field(default_factory=dict)
    url_prefix: str = ""
    drone_aliases: dict = field(default_factory=dict)
    use_metric: bool = True

    def __init__(self, yaml_file: str):
        with open(yaml_file, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

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

        # API key configuration: api_key -> source name
        self.api_keys = web_data.get("api_keys") or {}

        # Drone aliases: uas_id -> friendly name
        self.drone_aliases = web_data.get("drone_aliases") or {}

        # Units preference: true for metric, false for imperial
        self.use_metric = web_data.get("use_metric", True)

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
            "use_metric": self.use_metric,
        }
