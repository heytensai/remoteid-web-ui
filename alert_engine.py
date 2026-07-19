"""Alert engine — evaluates drone positions against configured alert conditions.

Currently supports:
  - New session detection (drone starts a new flight)
  - Geozone entry/exit (drone enters or exits an alert-enabled area)

Extensible to additional triggers (altitude, unknown drone, etc.) as
check methods added to ``evaluate()``.
"""

import logging
import math
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Callable

from config import WaypointConfig, M_PER_DEG_LAT

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
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(lat_rad)
    half_h = height_m / 2 / M_PER_DEG_LAT
    half_w = width_m / 2 / m_per_deg_lon
    return (
        center_lat - half_h <= lat <= center_lat + half_h
        and center_lon - half_w <= lon <= center_lon + half_w
    )


class AlertEngine:
    """Evaluates drone positions against configured alert conditions.

    Callbacks (set externally):
      on_new_alert(uas_id, geozone_name)   — drone entered a geozone
      on_geozone_exit(uas_id, geozone_name) — drone left a geozone
      on_new_session(uas_id, session_id, first_position) — drone started a new flight
      on_unrecognized_drone(uas_id, session_id, first_position) — unknown drone started a new flight
    """

    def __init__(self, database, config):
        self._db = database
        self._config = config
        self._geozones: List[WaypointConfig] = []
        self._rebuild_geozone_list()
        # Session tracking
        self._known_sessions: Dict[str, str] = {}
        self._session_alert_cooldown: Dict[str, float] = {}  # key → monotonic timestamp of last fire
        self._geozone_alert_cooldown: Dict[str, float] = {}
        self._unrecognized_drone_cooldown: Dict[str, float] = {}
        self._load_known_sessions()
        # Callbacks
        self.on_new_alert: Optional[Callable] = None
        self.on_geozone_exit: Optional[Callable] = None
        self.on_new_session: Optional[Callable] = None
        self.on_unrecognized_drone: Optional[Callable] = None

    # --- Config loading ---

    def _rebuild_geozone_list(self):
        """Rebuild the internal list of alert-enabled geozones from config."""
        self._geozones = [
            wp for wp in self._config.waypoints
            if wp.alert_enabled and wp.type in ("circle", "rectangle")
        ]
        logger.debug(
            "AlertEngine: %d alert-enabled geozones loaded", len(self._geozones)
        )

    def _load_known_sessions(self):
        """Pre-populate known sessions from DB to avoid false "new" notifications on startup."""
        try:
            self._known_sessions = self._db.get_all_current_sessions()
            logger.debug(
                "AlertEngine: loaded %d existing sessions", len(self._known_sessions)
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("AlertEngine: failed to load known sessions")

    def reload_config(self, config):
        """Hot-reload the config reference."""
        self._config = config
        self._rebuild_geozone_list()

    def sync_sessions(self):
        """Re-read all current session IDs from the database.

        Updates the internal session tracking so that subsequent calls to
        :meth:`evaluate` will not re-fire ``on_new_session`` for sessions
        that are already known.  Useful after session detection re-runs
        and assigns new session IDs.
        """
        try:
            current = self._db.get_all_current_sessions()
            for uas_id, session_id in current.items():
                old = self._known_sessions.get(uas_id)
                if old is not None and old != session_id:
                    logger.debug("Session changed for %s: %s -> %s", uas_id, old, session_id)
            self._known_sessions = current
            self._prune_cooldowns()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("AlertEngine: failed to sync sessions")

    def _prune_cooldowns(self):
        """Remove cooldown entries older than 2× the longest cooldown period.

        Prevents unbounded memory growth from stale keys.
        """
        now = time.monotonic()
        cooldowns = self._config.alerts.cooldown
        max_cd = max(cooldowns.values()) if cooldowns else 300
        cutoff = now - (max_cd * 2)
        for store in (self._session_alert_cooldown, self._geozone_alert_cooldown,
                      self._unrecognized_drone_cooldown):
            stale = [k for k, t in store.items() if t < cutoff]
            for k in stale:
                del store[k]
            if stale:
                logger.debug("Pruned %d stale cooldown entries", len(stale))

    # --- Public entry point ---

    def evaluate(self, uas_id: str, positions: List[Dict]):
        """Evaluate a drone position update against all alert conditions.

        Called after data is inserted (submit handler) or during background
        checks (session scheduler).  Positions are dicts with at least
        ``latitude``, ``longitude``, and ``timestamp`` keys (and optionally
        ``uas_id``, ``altitude``, etc.).
        """
        self._check_new_session(uas_id, positions)
        self._evaluate_geozones(uas_id, positions)

    # --- Session tracking ---

    def _check_new_session(self, uas_id: str, positions: List[Dict]):
        """Fire ``on_new_session`` when the drone's session ID changes.

        Queries the latest ``computed_session_id`` from the database and
        compares it against the internally tracked value for this UAS.
        A difference means either a first flight or a new flight after a gap.
        Uses a per-uas_id cooldown to prevent duplicate alerts when session
        detection regenerates IDs (the scheduler runs every ~30s, changing
        all session UUIDs).
        """
        session_id = self._db.get_latest_session_id(uas_id)
        if session_id is None:
            return
        if self._known_sessions.get(uas_id) == session_id:
            return
        now = time.monotonic()
        cooldown = self._config.alerts.cooldown.get("new_session", 300)
        last_fired = self._session_alert_cooldown.get(uas_id)
        if last_fired is not None and (now - last_fired) < cooldown:
            logger.debug(
                "Skipping duplicate new session alert for %s: %s (cooldown %ds)",
                uas_id, session_id, cooldown,
            )
            return
        self._known_sessions[uas_id] = session_id
        self._session_alert_cooldown[uas_id] = now
        first_pos = positions[0] if positions else None
        logger.info(
            "New session for %s: %s", uas_id, session_id,
        )
        self._fire(self.on_new_session, uas_id, session_id, first_pos)

        if uas_id not in self._config.drone_aliases:
            udr_cooldown = self._config.alerts.cooldown.get("unrecognized_drone", 300)
            last_fired = self._unrecognized_drone_cooldown.get(uas_id)
            if last_fired is not None and (now - last_fired) < udr_cooldown:
                logger.debug(
                    "Skipping unrecognized drone alert for %s (cooldown %ds)",
                    uas_id, udr_cooldown,
                )
            else:
                self._unrecognized_drone_cooldown[uas_id] = now
                self._fire(self.on_unrecognized_drone, uas_id, session_id, first_pos)

    # --- Geozone evaluation ---

    def _evaluate_geozones(self, uas_id: str, positions: List[Dict]):
        """Check positions against all alert-enabled geozones."""
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
            cooldown_key = f"{uas_id}:{geozone_name}"
            now = time.monotonic()
            cooldown = self._config.alerts.cooldown.get("geozone_enter", 300)
            last_fired = self._geozone_alert_cooldown.get(cooldown_key)
            if last_fired is not None and (now - last_fired) < cooldown:
                logger.debug(
                    "Skipping geozone_enter alert for %s/%s (cooldown %ds)",
                    uas_id, geozone_name, cooldown,
                )
            else:
                self._geozone_alert_cooldown[cooldown_key] = now
                self._fire(self.on_new_alert, uas_id, geozone_name)

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
            cooldown_key = f"{uas_id}:{geozone_name}"
            now = time.monotonic()
            cooldown = self._config.alerts.cooldown.get("geozone_exit", 300)
            last_fired = self._geozone_alert_cooldown.get(cooldown_key)
            if last_fired is not None and (now - last_fired) < cooldown:
                logger.debug(
                    "Skipping geozone_exit alert for %s/%s (cooldown %ds)",
                    uas_id, geozone_name, cooldown,
                )
            else:
                self._geozone_alert_cooldown[cooldown_key] = now
                self._fire(self.on_geozone_exit, uas_id, geozone_name)

    # --- Batch processing ---

    def evaluate_all(self, since: Optional[datetime] = None):
        """Evaluate all UAS with positions since *since* against all alert conditions.

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

    # --- Helpers ---

    @staticmethod
    def _fire(callback, *args):
        """Safely invoke an optional callback, logging but not propagating exceptions."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Alert callback failed")
