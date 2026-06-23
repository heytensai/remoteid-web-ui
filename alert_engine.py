"""Geozone alert engine — evaluates positions against alert-enabled geozones."""

import logging
import math
from datetime import datetime, timezone
from typing import List, Dict, Optional

from config import WaypointConfig

logger = logging.getLogger(__name__)


def point_in_circle(
    lat: float, lon: float,
    center_lat: float, center_lon: float,
    radius_m: float,
) -> bool:
    """Check if a point is within a circle defined by center and radius (meters).

    Uses the haversine formula for great-circle distance.
    """
    R = 6371000  # Earth radius in meters
    dlat = math.radians(lat - center_lat)
    dlon = math.radians(lon - center_lon)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(center_lat))
        * math.cos(math.radians(lat))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance <= radius_m


def point_in_rectangle( # pylint: disable=too-many-positional-arguments
    lat: float, lon: float,
    center_lat: float, center_lon: float,
    width_m: float, height_m: float,
) -> bool:
    """Check if a point is within a rectangle defined by center, width, and height.

    Converts meter dimensions to lat/lng offsets at the center latitude.
    """
    lat_rad = math.radians(center_lat)
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * math.cos(lat_rad)
    half_h = height_m / 2 / m_per_deg_lat
    half_w = width_m / 2 / m_per_deg_lon
    return (
        center_lat - half_h <= lat <= center_lat + half_h
        and center_lon - half_w <= lon <= center_lon + half_w
    )


class AlertEngine:
    """Evaluates drone positions against alert-enabled geozone waypoints."""

    def __init__(self, database, config):
        self._db = database
        self._config = config
        self._geozones: List[WaypointConfig] = []
        self._rebuild_geozone_list()

    def _rebuild_geozone_list(self):
        """Rebuild the internal list of alert-enabled geozones from config."""
        self._geozones = [
            wp for wp in self._config.waypoints
            if wp.alert_enabled and wp.type in ("circle", "rectangle")
        ]
        logger.debug(
            "AlertEngine: %d alert-enabled geozones loaded", len(self._geozones)
        )

    def reload_config(self, config):
        """Hot-reload the config reference."""
        self._config = config
        self._rebuild_geozone_list()

    def evaluate(self, uas_id: str, positions: List[Dict]):
        """Evaluate a list of positions for a UAS against all geozones.

        Positions should be dicts with 'latitude', 'longitude', and 'timestamp' keys.
        Timestamps can be datetime objects or ISO-format strings.
        """
        if not self._geozones:
            return

        if self._config.alerts.skip_known_drones and uas_id in self._config.drone_aliases:
            return

        for pos in positions:
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            ts = pos.get("timestamp")
            if lat is None or lon is None or ts is None:
                continue

            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            for gz in self._geozones:
                inside = False
                if gz.type == "circle":
                    inside = point_in_circle(lat, lon, gz.lat, gz.lon, gz.radius)
                elif gz.type == "rectangle":
                    inside = point_in_rectangle(
                        lat, lon, gz.lat, gz.lon, gz.width, gz.height
                    )

                if inside:
                    self._handle_entry(uas_id, gz.name, ts)
                else:
                    self._handle_exit(uas_id, gz.name, ts)

    def _handle_entry(self, uas_id: str, geozone_name: str, timestamp: datetime):
        """Called when a position is inside a geozone. Creates or updates event."""
        events = self._db.get_geozone_events_for_uas(uas_id)
        active = [e for e in events if e["geozone_name"] == geozone_name and e["exited_at"] is None]
        if active:
            self._db.update_geozone_last_seen(active[0]["id"], timestamp)
        else:
            self._db.enter_geozone(uas_id, geozone_name, timestamp)
            logger.info(
                "ALERT: %s entered geozone '%s' at %s",
                uas_id, geozone_name, timestamp.isoformat(),
            )

    def _handle_exit(self, uas_id: str, geozone_name: str, timestamp: datetime):
        """Called when a position is outside a geozone. Exits active event."""
        events = self._db.get_geozone_events_for_uas(uas_id)
        active = [e for e in events if e["geozone_name"] == geozone_name and e["exited_at"] is None]
        if active:
            self._db.exit_geozone(active[0]["id"], timestamp, "left")
            logger.info(
                "ALERT: %s left geozone '%s' at %s",
                uas_id, geozone_name, timestamp.isoformat(),
            )

    def evaluate_all(self, since: Optional[datetime] = None):
        """Evaluate all UAS with positions since *since* against geozones.

        Used by the session scheduler for periodic background checking.
        """
        if not self._geozones:
            return

        drones = self._db.get_drones_for_alert_check(since)
        for uas_id in drones:
            positions = self._db.get_positions_for_alert_check(uas_id, since)
            if positions:
                self.evaluate(uas_id, positions)

    def check_stale(self, reference_time: Optional[datetime] = None):
        """Mark events as timed out if last_seen_at is older than stale_timeout."""
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        timeout = self._config.alerts.stale_timeout
        count = self._db.check_stale_geozone_events(timeout, reference_time)
        if count:
            logger.info("AlertEngine: marked %d geozone event(s) as stale", count)
