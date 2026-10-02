"""Alert engine — evaluates drone positions against configured alert conditions.

Currently supports:
  - New flight detection (one of new_unknown / new_known / new_trusted,
    chosen by the drone's trust tier)
  - Geozone entry/exit (drone enters or exits an alert-enabled area)

Extensible to additional triggers (altitude, etc.) as check methods added
to ``evaluate()``.
"""

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Callable

from config import (
    DRONE_TRUST_KNOWN,
    DRONE_TRUST_TRUSTED,
    DRONE_TRUST_UNKNOWN,
    M_PER_DEG_LAT,
    WaypointConfig,
)

logger = logging.getLogger(__name__)

# New-flight alert events, one per drone trust tier. Every drone maps to
# exactly one of these, so a target subscribed to a tier sees exactly one
# alert per flight.
NEW_FLIGHT_EVENTS = ("new_unknown", "new_known", "new_trusted")
NEW_FLIGHT_EVENT_BY_TRUST = {
    DRONE_TRUST_UNKNOWN: "new_unknown",
    DRONE_TRUST_KNOWN: "new_known",
    DRONE_TRUST_TRUSTED: "new_trusted",
}


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


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in meters between two points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class AlertEngine:  # pylint: disable=too-many-instance-attributes
    """Evaluates drone positions against configured alert conditions.

    Callbacks (set externally):
      on_new_alert(uas_id, geozone_name, position=None)   — drone entered a geozone
      on_geozone_exit(uas_id, geozone_name, position=None) — drone left a geozone
      on_new_flight(uas_id, session_id, event_type, first_position) — a new flight
          started; ``event_type`` is one of ``new_unknown`` / ``new_known`` /
          ``new_trusted``, determined by the drone's trust tier
      on_drone_proximity(uas_id_a, name_a, uas_id_b, name_b, distance_m,
                         position_a=None, position_b=None) — two drones too close
    """

    def __init__(self, database, config):
        self._db = database
        self._config = config
        self._geozones: List[WaypointConfig] = []
        self._rebuild_geozone_list()
        # Session tracking
        self._known_sessions: Dict[str, str] = {}
        # (event, uas_id) → monotonic timestamp of last fire
        self._new_flight_cooldown: Dict[tuple, float] = {}
        self._geozone_alert_cooldown: Dict[str, float] = {}
        self._drone_proximity_cooldown: Dict[str, float] = {}  # "uas_a:uas_b" → monotonic
        self._load_known_sessions()
        # Callbacks
        self.on_new_alert: Optional[Callable] = None
        self.on_geozone_exit: Optional[Callable] = None
        self.on_new_flight: Optional[Callable] = None
        self.on_drone_proximity: Optional[Callable] = None

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
        start = time.monotonic()
        logger.info("Loading known sessions for alert engine")
        try:
            self._known_sessions = self._db.get_all_current_sessions()
            logger.info(
                "Loaded %d existing sessions in %.2fs",
                len(self._known_sessions), time.monotonic() - start,
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
        :meth:`evaluate` will not re-fire ``on_new_flight`` for sessions
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
        for store in (self._new_flight_cooldown, self._geozone_alert_cooldown,
                      self._drone_proximity_cooldown):
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
        """Fire exactly one tier-appropriate new-flight callback per session.

        Queries the latest ``computed_session_id`` from the database and
        compares it against the internally tracked value for this UAS.
        A difference means either a first flight or a new flight after a gap.
        The in-memory ``_known_sessions`` map is only a fast path — the
        authoritative dedup is an atomic ``INSERT OR IGNORE`` claim on the
        shared ``sent_alerts`` table, so a ``(uas_id, session_id)`` event
        fires exactly once even when multiple gunicorn workers each hold
        their own forked copy of this engine.

        The drone's trust tier (``drone_trust_level()``) picks exactly one of
        ``new_unknown`` / ``new_known`` / ``new_trusted``, so a notification
        target subscribed to a tier receives exactly one alert per new flight.
        """
        session_id = self._db.get_latest_session_id(uas_id)
        if session_id is None:
            return
        if self._known_sessions.get(uas_id) == session_id:
            return
        now = time.monotonic()
        first_pos = positions[0] if positions else None

        event_type = NEW_FLIGHT_EVENT_BY_TRUST[self._config.drone_trust_level(uas_id)]
        cooldown = self._config.alerts.cooldown.get(event_type, 300)

        # Cooldowns are tracked per (event, drone) rather than per drone so a
        # tier change mid-window still alerts: promoting a drone from unknown
        # to trusted (or demoting it) is a meaningful event in its own right
        # and must not be swallowed by the cooldown of the previous tier.
        cooldown_key = (event_type, uas_id)
        last_fired = self._new_flight_cooldown.get(cooldown_key)
        if last_fired is not None and (now - last_fired) < cooldown:
            logger.debug(
                "Skipping duplicate %s alert for %s: %s (cooldown %ds)",
                event_type, uas_id, session_id, cooldown,
            )
            self._known_sessions[uas_id] = session_id
            return

        # Cross-process authoritative dedup: the first process to claim
        # (uas_id, session_id) is the only one that fires, even when each
        # gunicorn worker has its own copy of this engine. The claim is keyed
        # on the tier-specific event name, so a drone promoted to trusted
        # mid-session can still raise its own new_trusted alert.
        if not self._db.claim_alert(
            event_type, f"{uas_id}:{session_id}",
            uas_id=uas_id, session_id=session_id,
        ):
            logger.debug(
                "Skipping %s alert for %s: %s (already fired)",
                event_type, uas_id, session_id,
            )
            self._known_sessions[uas_id] = session_id
            return

        self._new_flight_cooldown[cooldown_key] = now
        self._known_sessions[uas_id] = session_id
        logger.info(
            "New flight for %s (%s): session %s",
            uas_id, event_type, session_id,
        )
        self._fire(self.on_new_flight, uas_id, session_id, event_type, first_pos)

    # --- Geozone evaluation ---

    def _evaluate_geozones(self, uas_id: str, positions: List[Dict]):
        """Check positions against all alert-enabled geozones.

        Suppression is per trust tier: ``skip_known_drones`` silences aliased
        but untrusted drones and ``skip_trusted_drones`` silences ``trusted:
        true`` drones, independently. Unknown drones always alert.

        A suppressed drone is still evaluated for **exit** only. It never
        opens a geozone event, but if it was already inside when it was
        promoted into a silenced tier, its active event is closed (without a
        notification) so the drone is not left showing as inside until
        ``check_stale()`` times the event out.
        """
        if not self._geozones:
            return
        trust = self._config.drone_trust_level(uas_id)
        suppressed = (
            trust == DRONE_TRUST_TRUSTED and self._config.alerts.skip_trusted_drones
        ) or (
            trust == DRONE_TRUST_KNOWN and self._config.alerts.skip_known_drones
        )
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
                    if not suppressed:
                        self._handle_entry(uas_id, gz.name, ts, pos)
                else:
                    # Always process exits, even for suppressed drones, so an
                    # event opened before the tier change gets closed. The
                    # notification itself is suppressed.
                    self._handle_exit(uas_id, gz.name, ts, pos, notify=not suppressed)

    def _handle_entry(self, uas_id: str, geozone_name: str, timestamp: datetime,
                      position: Optional[Dict] = None):
        """Called when a position is inside a geozone. Creates or updates event."""
        # Atomic across processes: only the caller that actually creates the
        # new event row fires the notification, so concurrent gunicorn
        # workers evaluating the same packet cannot double-notify.
        event_id, created = self._db.enter_geozone(uas_id, geozone_name, timestamp)
        if not created:
            self._db.update_geozone_last_seen(event_id, timestamp)
            return
        logger.info(
            "ALERT: %s entered geozone '%s' at %s",
            uas_id, geozone_name, timestamp.isoformat(),
        )
        cooldown_key = f"geozone_enter:{uas_id}:{geozone_name}"
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
            self._fire(self.on_new_alert, uas_id, geozone_name, position)

    def _handle_exit(self, uas_id: str, geozone_name: str, timestamp: datetime,  # pylint: disable=too-many-positional-arguments
                     position: Optional[Dict] = None, notify: bool = True):
        """Called when a position is outside a geozone. Exits active event.

        ``notify=False`` still closes the event but skips the callback, which
        is what a tier-suppressed drone needs when it leaves a geozone it
        entered before being silenced.
        """
        events = self._db.get_geozone_events_for_uas(uas_id)
        active = [e for e in events if e["geozone_name"] == geozone_name and e["exited_at"] is None]
        if not active:
            return
        updated = self._db.exit_geozone(active[0]["id"], timestamp, "left")
        if updated == 0:
            # Another process already exited this event and fired the alert.
            return
        if not notify:
            logger.info(
                "%s left geozone '%s' at %s (event closed, notification "
                "suppressed for silenced trust tier)",
                uas_id, geozone_name, timestamp.isoformat(),
            )
            return
        logger.info(
            "ALERT: %s left geozone '%s' at %s",
            uas_id, geozone_name, timestamp.isoformat(),
        )
        cooldown_key = f"geozone_exit:{uas_id}:{geozone_name}"
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
            self._fire(self.on_geozone_exit, uas_id, geozone_name, position)

    # --- Drone proximity ---

    def _check_drone_proximity(self):
        """Check if any two live drones are within the configured proximity distance.

        Only considers drones whose latest position is within ``stale_timeout``
        seconds of now (i.e. live).  Each pair is checked once per cycle and
        throttled by a cooldown keyed on the sorted pair of UAS IDs.
        """
        distance_m = self._config.alerts.proximity_distance
        if not distance_m or distance_m <= 0:
            return

        stale_timeout = self._config.alerts.stale_timeout
        now = datetime.now(timezone.utc)
        since = now - timedelta(seconds=stale_timeout)
        live = self._db.get_live_positions(since)
        if len(live) < 2:
            return

        # Sort by uas_id so pair keys are deterministic
        live.sort(key=lambda r: r["uas_id"])

        for i, a in enumerate(live):
            for b in live[i + 1:]:
                dist = haversine_distance(a["latitude"], a["longitude"],
                                         b["latitude"], b["longitude"])
                if dist > distance_m:
                    continue

                pair_key = f"{a['uas_id']}|{b['uas_id']}"
                cd_key = f"drone_proximity:{pair_key}"
                cooldown = self._config.alerts.cooldown.get("drone_proximity", 300)
                mono = time.monotonic()
                last = self._drone_proximity_cooldown.get(cd_key)
                if last is not None and (mono - last) < cooldown:
                    logger.debug(
                        "Skipping drone_proximity alert for %s (cooldown %ds)",
                        pair_key, cooldown,
                    )
                    continue
                self._drone_proximity_cooldown[cd_key] = mono

                name_a = self._config.get_drone_name(a["uas_id"])
                name_b = self._config.get_drone_name(b["uas_id"])

                logger.info(
                    "ALERT: drone proximity %s (%s) ↔ %s (%s) at %.1fm",
                    a["uas_id"], name_a, b["uas_id"], name_b, dist,
                )
                self._fire(
                    self.on_drone_proximity,
                    a["uas_id"], name_a,
                    b["uas_id"], name_b,
                    dist,
                    a, b,
                )

    # --- Batch processing ---

    def evaluate_all(self, since: Optional[datetime] = None):
        """Evaluate all UAS with positions since *since* against all alert conditions.

        Used by the session scheduler for periodic background checking.
        """
        drones = self._db.get_drones_for_alert_check(since)
        for uas_id in drones:
            positions = self._db.get_positions_for_alert_check(uas_id, since)
            if positions:
                self.evaluate(uas_id, positions)
        # Proximity check runs once per cycle regardless of geozone state
        self._check_drone_proximity()

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
