"""Configuration loader for web interface"""

import logging
import os

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import yaml

VALID_EVENTS = {"geozone_enter", "geozone_exit", "new_session", "unrecognized_drone", "drone_proximity"}
VALID_NOTIFIER_TYPES = {"discord", "ntfy", "teams"}

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


FEET_PER_METER = 3.28084
M_PER_DEG_LAT = 111320  # meters per degree latitude at equator


@dataclass
class WaypointConfig: # pylint: disable=too-many-instance-attributes
    """Custom waypoint displayed on the map"""

    name: str
    lat: float
    lon: float
    type: str = "point"
    icon: str = "fa-map-pin"
    color: str = "#007bff"
    description: str = ""
    enabled: bool = True
    category: str = ""
    radius: float = 0.0  # meters, for type "circle"
    width: float = 0.0   # meters, for type "rectangle"
    height: float = 0.0  # meters, for type "rectangle"
    fill_opacity: float = 0.1
    alert_enabled: bool = False


@dataclass
class CollectorConfig:
    """A position-reporting entity on the map.

    type: "mobile" — reports position via API (requires api_key)
          "fixed"  — static position from config (requires lat, lon)

    timezone: IANA timezone name (e.g. "America/Denver") for incoming
              timestamps. If set, naive timestamps are converted from
              this timezone to UTC before storing.
    """

    name: str
    api_key: str = ""
    color: str = "#e67e22"
    type: str = "mobile"
    lat: Optional[float] = None
    lon: Optional[float] = None
    timezone: Optional[str] = None


@dataclass
class RoleConfig:
    """A named role with a list of permissions."""

    name: str
    permissions: List[str]


@dataclass
class NotificationTargetConfig:
    """A configured notification target (discord, ntfy, teams, etc.).

    Each target specifies its type, which events trigger it, and any
    type-specific configuration (e.g. webhook URL for discord/ntfy).
    """

    name: str
    type: str  # "discord", "ntfy", or "teams"
    events: List[str]  # subset of ["geozone_enter", "geozone_exit", "new_session", "unrecognized_drone"]
    enabled: bool = True
    webhook_url: str = ""
    token: str = ""  # ntfy Bearer auth token (mutually exclusive with username/password)
    username: str = ""  # ntfy Basic auth username
    password: str = ""  # ntfy Basic auth password


VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class SessionDetectionConfig:
    """Background session detection configuration"""

    enabled: bool = False
    interval: int = 600  # seconds between runs
    gap_threshold: int = 600  # seconds gap to trigger new session
    log_level: str = "INFO"

    def __init__(self, data: dict = None):
        if data:
            self.enabled = data.get("enabled", False)
            self.interval = data.get("interval", 600)
            self.gap_threshold = data.get("gap_threshold", 600)
            self.log_level = data.get("log_level", "INFO").upper()


@dataclass
class AlertsConfig:
    """Alerting configuration"""

    stale_timeout: int = 300  # seconds without position before marking as left
    skip_known_drones: bool = False  # skip alerts for drones with aliases
    cooldown: dict = None  # per-event cooldowns in seconds
    proximity_distance: float = 100.0  # meters — drone proximity threshold (converted at init)

    def __init__(self, data: dict = None, use_metric: bool = True):
        if data:
            self.stale_timeout = data.get("stale_timeout", 300)
            self.skip_known_drones = data.get("skip_known_drones", False)
            self.cooldown = data.get("cooldown") or {}
            raw = data.get("proximity_distance", 100.0)
            if not use_metric:
                raw /= FEET_PER_METER
            self.proximity_distance = raw
        else:
            self.cooldown = {}
            self.proximity_distance = 100.0


@dataclass
class MaintenanceConfig:
    """Background maintenance task configuration.

    Runs periodic cleanup of stale auth data (expired tokens, orphaned users).
    """

    enabled: bool = False
    interval: int = 3600  # seconds between runs (default 1 hour)
    delete_expired_tokens: bool = True
    delete_expired_login_tokens: bool = True
    delete_orphaned_ephemeral_users: bool = True

    def __init__(self, data: dict = None):
        if data:
            self.enabled = data.get("enabled", False)
            self.interval = data.get("interval", 3600)
            self.delete_expired_tokens = data.get("delete_expired_tokens", True)
            self.delete_expired_login_tokens = data.get("delete_expired_login_tokens", True)
            self.delete_orphaned_ephemeral_users = data.get("delete_orphaned_ephemeral_users", True)


@dataclass
class WebConfig:  # pylint: disable=too-many-instance-attributes
    """Web interface configuration"""

    host: str = "0.0.0.0"
    port: int = 5000
    database_path: str = "./web.db"
    default_hours: int = 24
    max_positions_per_query: int = 5000
    map: MapConfig = field(default_factory=MapConfig)
    waypoints: List[WaypointConfig] = field(default_factory=list)
    api_keys: dict = field(default_factory=dict)
    url_prefix: str = ""
    drone_aliases: dict = field(default_factory=dict)
    manufacturer_prefixes: dict = field(default_factory=dict)
    use_metric: bool = True
    session_detection: SessionDetectionConfig = field(default_factory=SessionDetectionConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    collectors: List[CollectorConfig] = field(default_factory=list)
    position_stale_minutes: int = 30
    roles: Dict[str, RoleConfig] = field(default_factory=dict)
    server_url: str = ""
    notifications: List[NotificationTargetConfig] = field(default_factory=list)

    def __init__(self, yaml_file: str):
        self.config_path = yaml_file
        web_data = self._load_raw_config(yaml_file)
        self._parse_config(web_data)
        self._validate()

    @staticmethod
    def _load_raw_config(yaml_file: str) -> dict:
        """Load and return raw web_interface config from YAML file."""
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

        return data.get("web_interface", {})

    def _parse_config(self, web_data: dict) -> None:
        """Parse configuration values from raw web_interface dict."""
        self.host = web_data.get("host", "0.0.0.0")
        self.port = web_data.get("port", 5000)
        self.database_path = web_data.get("database_path", "./web.db")
        self.default_hours = web_data.get("default_hours", 24)
        self.max_positions_per_query = web_data.get("max_positions_per_query", 5000)
        self.url_prefix = web_data.get("url_prefix", "")

        # Map configuration
        map_data = web_data.get("map") or {}
        self.map = MapConfig(map_data)

        # Units preference: true for metric (meters), false for imperial (feet)
        self.use_metric = web_data.get("use_metric", True)

        # Waypoint & geozone configuration
        self.waypoints = self._parse_waypoints(web_data.get("waypoints") or [])

        # API key configuration: api_key -> source name
        self.api_keys = web_data.get("api_keys") or {}

        # Drone aliases: uas_id -> friendly name
        self.drone_aliases = web_data.get("drone_aliases") or {}

        # Manufacturer prefixes: serial prefix -> manufacturer name
        self.manufacturer_prefixes = web_data.get("manufacturer_prefixes") or {}

        # Session detection configuration
        sd_data = web_data.get("session_detection") or {}
        self.session_detection = SessionDetectionConfig(sd_data)

        # Maintenance configuration
        maint_data = web_data.get("maintenance") or {}
        self.maintenance = MaintenanceConfig(maint_data)

        # Alerts configuration
        alerts_data = web_data.get("alerts") or {}
        self.alerts = AlertsConfig(alerts_data, use_metric=self.use_metric)

        # Roles configuration
        self.roles = self._parse_roles(web_data.get("roles") or {})

        # Collector configuration
        self.position_stale_minutes = web_data.get("position_stale_minutes", 30)
        self.collectors = self._parse_collectors(web_data.get("collectors") or [])
        self.collectors_by_key = {c.api_key: c.name for c in self.collectors if c.api_key}

        # Notification targets
        self.server_url = web_data.get("server_url", "")
        self.notifications = self._parse_notifications(web_data.get("notifications") or [])

    def _parse_roles(self, roles_data: dict) -> Dict[str, RoleConfig]:
        """Parse role configuration from raw data."""
        roles = {}
        for name, data in roles_data.items():
            perms = data.get("permissions", []) if isinstance(data, dict) else []
            roles[name] = RoleConfig(name=name, permissions=perms)
        return roles

    def get_role_permissions(self, role_name: str) -> List[str]:
        """Return the permission list for a role, or empty list if not found."""
        role = self.roles.get(role_name)
        return role.permissions if role else []

    def _parse_waypoints(self, waypoints_data: list) -> list:
        """Parse waypoint configuration from raw data."""
        waypoints = []
        for wp_data in waypoints_data:
            wp_type = wp_data.get("type", "point")
            fill_opacity = wp_data.get("fill_opacity", 0.1)
            if wp_type == "circle":
                radius = wp_data.get("radius", 0)
                if not self.use_metric:
                    radius /= FEET_PER_METER
            else:
                radius = 0.0
            if wp_type == "rectangle":
                width = wp_data.get("width", 0)
                height = wp_data.get("height", 0)
                if not self.use_metric:
                    width /= FEET_PER_METER
                    height /= FEET_PER_METER
            else:
                width = 0.0
                height = 0.0
            waypoints.append(
                WaypointConfig(
                    name=wp_data["name"],
                    lat=wp_data["lat"],
                    lon=wp_data["lon"],
                    type=wp_type,
                    icon=wp_data.get("icon", "fa-map-pin"),
                    color=wp_data.get("color", "#007bff"),
                    description=wp_data.get("description", ""),
                    enabled=wp_data.get("enabled", True),
                    category=wp_data.get("category", ""),
                    radius=radius,
                    width=width,
                    height=height,
                    fill_opacity=fill_opacity,
                    alert_enabled=wp_data.get("alert_enabled", False),
                )
            )
        return waypoints

    def _parse_collectors(self, collectors_data: list) -> list:
        """Parse collector configuration from raw data."""
        collectors = []
        for c_data in collectors_data:
            c_type = c_data.get("type", "mobile")
            collectors.append(CollectorConfig(
                name=c_data["name"],
                api_key=c_data.get("api_key", ""),
                color=c_data.get("color", "#e67e22"),
                type=c_type,
                lat=c_data.get("lat"),
                lon=c_data.get("lon"),
                timezone=c_data.get("timezone") or None,
            ))
        return collectors

    def _parse_notifications(self, data: list) -> list:
        """Parse notification target configuration from raw data."""
        targets = []
        for nt in data:
            events = nt.get("events", [])
            unknown = [e for e in events if e not in VALID_EVENTS]
            if unknown:
                logger.warning(
                    "Notification target %r: unknown event(s): %s",
                    nt.get("name"), unknown,
                )
                events = [e for e in events if e in VALID_EVENTS]
            targets.append(NotificationTargetConfig(
                name=nt["name"],
                type=nt.get("type", ""),
                events=events,
                enabled=nt.get("enabled", True),
                webhook_url=nt.get("webhook_url", ""),
                token=nt.get("token", ""),
                username=nt.get("username", ""),
                password=nt.get("password", ""),
            ))
        return targets

    def _validate(self): # pylint: disable=too-many-branches
        """Validate configuration values, raising ValueError on invalid input."""
        errors = []

        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            errors.append(f"port must be an integer between 1 and 65535, got {self.port!r}")

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
            if wp.type not in ("point", "circle", "rectangle"):
                errors.append(f"{prefix}.type must be 'point', 'circle', or 'rectangle', got {wp.type!r}")
            if wp.type == "circle" and wp.radius <= 0:
                errors.append(f"{prefix}.radius must be > 0 for type 'circle', got {wp.radius}")
            if wp.type == "rectangle":
                if wp.width <= 0:
                    errors.append(f"{prefix}.width must be > 0 for type 'rectangle', got {wp.width}")
                if wp.height <= 0:
                    errors.append(f"{prefix}.height must be > 0 for type 'rectangle', got {wp.height}")

        # Alerts validation
        if self.alerts.stale_timeout is not None and self.alerts.stale_timeout <= 0:
            errors.append(f"alerts.stale_timeout must be positive, got {self.alerts.stale_timeout}")

        # Maintenance validation
        maint = self.maintenance
        if maint.interval is not None and maint.interval <= 0:
            errors.append(f"maintenance.interval must be positive, got {maint.interval}")

        # Session detection validation
        sd = self.session_detection
        if sd.interval is not None and sd.interval <= 0:
            errors.append(f"session_detection.interval must be positive, got {sd.interval}")
        if sd.gap_threshold is not None and sd.gap_threshold <= 0:
            errors.append(f"session_detection.gap_threshold must be positive, got {sd.gap_threshold}")
        if sd.log_level not in VALID_LOG_LEVELS:
            errors.append(
                f"session_detection.log_level must be one of {VALID_LOG_LEVELS}, "
                f"got {sd.log_level!r}"
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
            "default_hours": self.default_hours,
            "max_positions_per_query": self.max_positions_per_query,
            "map": {
                "center_lat": self.map.center_lat,
                "center_lon": self.map.center_lon,
                "default_zoom": self.map.default_zoom,
                "tile_provider": self.map.tile_provider,
            },
            "waypoints": [
                {
                    "name": w.name,
                    "lat": w.lat,
                    "lon": w.lon,
                    "type": w.type,
                    "icon": w.icon,
                    "color": w.color,
                    "description": w.description,
                    "enabled": w.enabled,
                    "category": w.category,
                    "radius": w.radius,
                    "width": w.width,
                    "height": w.height,
                    "fill_opacity": w.fill_opacity,
                    "alert_enabled": w.alert_enabled,
                }
                for w in self.waypoints
            ],
            "use_metric": self.use_metric,
            "alerts": {
                "stale_timeout": self.alerts.stale_timeout,
                "skip_known_drones": self.alerts.skip_known_drones,
                "proximity_distance": self.alerts.proximity_distance,
            },
            "collectors": [
                {
                    "name": c.name,
                    "color": c.color,
                    "type": c.type,
                    "lat": c.lat,
                    "lon": c.lon,
                }
                for c in self.collectors
            ],
            "position_stale_minutes": self.position_stale_minutes,
            "server_url": self.server_url,
            "notifications": [
                {
                    "name": n.name,
                    "type": n.type,
                    "events": n.events,
                    "webhook_url": n.webhook_url,
                }
                for n in self.notifications
            ],
            "roles": {
                name: {"name": r.name, "permissions": r.permissions}
                for name, r in self.roles.items()
            },
            "m_per_deg_lat": M_PER_DEG_LAT,
        }

    def reload_hot_config(self):
        """Re-read config file and return a new WebConfig with updated hot-reloadable fields.

        Returns the new :class:`WebConfig` if any hot-reloadable field changed,
        or ``None`` if nothing changed or the file could not be read.

        .. note::

            The original object is **never** mutated — thread-safe for readers
            holding a reference to the previous snapshot.
        """
        try:
            new_config = WebConfig(self.config_path)
        except (FileNotFoundError, PermissionError, OSError, ValueError, KeyError):
            logger.exception("Failed to reload hot config from %s", self.config_path)
            return None

        # Detect changes and log them (side-effect-free comparison only)
        changed = False

        if new_config.drone_aliases != self.drone_aliases:
            logger.info("Reloaded drone_aliases from %s", self.config_path)
            changed = True

        if new_config.api_keys != self.api_keys:
            logger.info("Reloaded api_keys from %s", self.config_path)
            changed = True

        if new_config.manufacturer_prefixes != self.manufacturer_prefixes:
            logger.info("Reloaded manufacturer_prefixes from %s", self.config_path)
            changed = True

        old_wp = {w.name: w for w in self.waypoints}
        new_wp = {w.name: w for w in new_config.waypoints}
        if old_wp != new_wp:
            logger.info("Reloaded waypoints from %s", self.config_path)
            changed = True

        if (new_config.session_detection.enabled != self.session_detection.enabled
                or new_config.session_detection.interval != self.session_detection.interval
                or new_config.session_detection.gap_threshold != self.session_detection.gap_threshold
                or new_config.session_detection.log_level != self.session_detection.log_level):
            logger.info(
                "Reloaded session_detection from %s (enabled=%s, interval=%s, gap=%s, log_level=%s)",
                self.config_path, new_config.session_detection.enabled,
                new_config.session_detection.interval,
                new_config.session_detection.gap_threshold,
                new_config.session_detection.log_level,
            )
            changed = True

        if (new_config.maintenance.enabled != self.maintenance.enabled
                or new_config.maintenance.interval != self.maintenance.interval
                or new_config.maintenance.delete_expired_tokens != self.maintenance.delete_expired_tokens
                or new_config.maintenance.delete_expired_login_tokens != self.maintenance.delete_expired_login_tokens
                or new_config.maintenance.delete_orphaned_ephemeral_users
                != self.maintenance.delete_orphaned_ephemeral_users):
            logger.info(
                "Reloaded maintenance from %s (enabled=%s, interval=%s, "
                "delete_expired_tokens=%s, delete_expired_login_tokens=%s, "
                "delete_orphaned_ephemeral_users=%s)",
                self.config_path, new_config.maintenance.enabled,
                new_config.maintenance.interval,
                new_config.maintenance.delete_expired_tokens,
                new_config.maintenance.delete_expired_login_tokens,
                new_config.maintenance.delete_orphaned_ephemeral_users,
            )
            changed = True

        if (new_config.alerts.stale_timeout != self.alerts.stale_timeout
                or new_config.alerts.skip_known_drones != self.alerts.skip_known_drones
                or new_config.alerts.proximity_distance != self.alerts.proximity_distance):
            logger.info(
                "Reloaded alerts from %s (stale_timeout=%s, skip_known_drones=%s, "
                "proximity_distance=%s)",
                self.config_path, new_config.alerts.stale_timeout,
                new_config.alerts.skip_known_drones,
                new_config.alerts.proximity_distance,
            )
            changed = True

        if new_config.position_stale_minutes != self.position_stale_minutes:
            logger.info("Reloaded position_stale_minutes from %s", self.config_path)
            changed = True

        if new_config.roles != self.roles:
            logger.info("Reloaded roles from %s", self.config_path)
            changed = True

        old_col = {c.name: c for c in self.collectors}
        new_col = {c.name: c for c in new_config.collectors}
        if old_col != new_col:
            logger.info("Reloaded collectors from %s", self.config_path)
            changed = True

        if new_config.server_url != self.server_url:
            logger.info("Reloaded server_url from %s (was %r, now %r)",
                        self.config_path, self.server_url, new_config.server_url)
            changed = True

        def _nt_key(n):
            return (n.type, n.events, n.enabled, n.webhook_url,
                    n.token, n.username, n.password)
        old_nt = {n.name: _nt_key(n) for n in self.notifications}
        new_nt = {n.name: _nt_key(n) for n in new_config.notifications}
        if old_nt != new_nt:
            logger.info("Reloaded notifications from %s", self.config_path)
            changed = True

        if not changed:
            return None

        return new_config
