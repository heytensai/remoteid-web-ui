"""Flask web interface for Remote ID visualization"""
# pylint: disable=too-many-lines

import argparse
import csv
import io
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, make_response, request, render_template, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf

from config import WebConfig, M_PER_DEG_LAT
from database import WebDatabase
from session_detect import process_database as redetect_sessions
from session_scheduler import SessionScheduler
from alert_engine import AlertEngine
from push_service import PushService, _ensure_vapid_keys

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances (initialized in _init_app)
CONFIG: WebConfig = None
DATABASE: WebDatabase = None
SESSION_SCHEDULER: Optional[SessionScheduler] = None
ALERT_ENGINE: Optional[AlertEngine] = None
PUSH_SERVICE: Optional[PushService] = None
PUSH_VAPID_PUBLIC_KEY: Optional[str] = None

# Thread-safe config snapshot — swapped atomically on hot reload.
# Readers should always call get_config() instead of accessing CONFIG directly.
_config_lock = threading.Lock()
_config_snapshot: Optional[WebConfig] = None # pylint: disable=invalid-name


def _get_config() -> WebConfig:
    """Return the current immutable config snapshot (thread-safe)."""
    return _config_snapshot

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError(
            "FLASK_SECRET_KEY environment variable must be set in production. "
            "Generate one with: python -c 'import os; print(os.urandom(24).hex())'"
        )
    app.secret_key = os.urandom(24).hex()
    logger.warning(
        "Using ephemeral FLASK_SECRET_KEY — CSRF tokens will be invalidated "
        "on restart. Set FLASK_SECRET_KEY env var for a persistent key."
    )
csrf = CSRFProtect(app)
logging.getLogger("flask_wtf.csrf").setLevel(logging.WARNING)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB limit
app.config["WTF_CSRF_TIME_LIMIT"] = None  # No time limit on CSRF tokens (session-scoped only)

# Session cookie security settings
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # Set to True if serving over HTTPS
)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=None,  # per-route only
    storage_uri='memory://',
)


@app.errorhandler(413)
def request_entity_too_large(error):  # pylint: disable=unused-argument
    """Return JSON error for payload exceeding MAX_CONTENT_LENGTH."""
    return jsonify({"success": False, "error": "Payload too large"}), 413


@app.errorhandler(429)
def rate_limit_exceeded(error):  # pylint: disable=unused-argument
    """Return JSON error when rate limit is exceeded."""
    return jsonify({"success": False, "error": "Rate limit exceeded"}), 429


@app.errorhandler(404)
def not_found(error):  # pylint: disable=unused-argument
    """Return JSON error for unknown routes."""
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):  # pylint: disable=unused-argument
    """Return JSON error for unhandled server exceptions."""
    return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(400)
def bad_request(error):  # pylint: disable=unused-argument
    """Log and return JSON for 400 errors (CSRF, malformed requests, etc.)."""
    desc = getattr(error, 'description', str(error))
    safe_headers = {
        k: v if k.lower() not in ('cookie', 'authorization') else '[REDACTED]'
        for k, v in request.headers
    }
    logger.warning(
        "400 Bad Request: %s %s — %s | Headers: %s | Body: %s",
        request.method, request.path,
        desc,
        safe_headers,
        request.get_data(as_text=True)[:500],
    )
    return jsonify({"error": desc}), 400


_CSP = (
    "default-src 'self';"
    " manifest-src 'self';"
    " script-src 'self' https://unpkg.com https://cdn.jsdelivr.net;"
    " style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com;"
    " font-src 'self' https://cdnjs.cloudflare.com;"
    " img-src 'self' https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com data:;"
    " connect-src 'self';"
    " worker-src 'self';"
)


@app.after_request
def add_security_headers(response):
    """Add CSP and security headers to every response."""
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# CORS intentionally not set globally — API is same-origin via the Flask server.
# Only /api/submit allows cross-origin (uses Bearer token auth).


@app.route("/")
def index():
    """Main page"""
    return render_template("index.html", url_prefix=_get_config().url_prefix)


@app.route("/manifest.json")
def pwa_manifest():
    """PWA web app manifest"""
    manifest = _build_manifest()
    resp = jsonify(manifest)
    resp.headers["Content-Type"] = "application/json"
    return resp


def _build_manifest():
    """Build the PWA manifest dict"""
    return {
        "name": "Drone Tracker",
        "short_name": "Drones",
        "description": "Real-time Remote ID drone tracking and visualization",
        "start_url": _get_config().url_prefix + "/",
        "display": "standalone",
        "background_color": "#1a1a2e",
        "theme_color": "#2c3e50",
        "icons": [
            {
                "src": _get_config().url_prefix + "/icons/icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": _get_config().url_prefix + "/icons/icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
        ],
    }


BASE_DIR = Path(__file__).parent


@app.route("/icons/<path:filename>")
def pwa_icon(filename):
    """Serve PWA icons via Flask route"""
    icons_dir = (BASE_DIR / "static" / "icons").resolve()
    requested = (icons_dir / filename).resolve()
    if not str(requested).startswith(str(icons_dir)):
        return "Not found", 404
    if not requested.exists() or not requested.is_file():
        return "Not found", 404
    body = requested.read_bytes()
    resp = make_response(body)
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/sw.js")
def service_worker():
    """PWA service worker"""
    sw_path = BASE_DIR / "static" / "sw.js"
    body = sw_path.read_text()
    prefix = _get_config().url_prefix
    body = body.replace("__URL_PREFIX__", prefix)
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = _get_config().url_prefix + "/"
    return resp


@app.route("/api/config")
def api_config():
    """Get map configuration"""
    cfg = _get_config()
    return jsonify(
        {
            "map": {
                "center_lat": cfg.map.center_lat,
                "center_lon": cfg.map.center_lon,
                "default_zoom": cfg.map.default_zoom,
                "tile_provider": cfg.map.tile_provider,
            },
            "default_hours": cfg.default_hours,
            "drone_aliases": cfg.drone_aliases,
            "manufacturer_prefixes": cfg.manufacturer_prefixes,
            "waypoints": cfg.to_dict().get("waypoints", []),
            "use_metric": cfg.use_metric,
            "stale_timeout": cfg.alerts.stale_timeout,
            "collectors": cfg.to_dict().get("collectors", []),
            "position_stale_minutes": cfg.position_stale_minutes,
            "m_per_deg_lat": M_PER_DEG_LAT,
            "vapid_public_key": PUSH_VAPID_PUBLIC_KEY,
            "csrf_token": generate_csrf(),
        }
    )


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    """Store a push notification subscription."""
    if PUSH_SERVICE is None:
        return jsonify({"success": False, "error": "Push not configured"}), 503
    try:
        data = request.get_json(force=True)
        PUSH_SERVICE.subscribe(
            data["endpoint"],
            data["keys"]["p256dh"],
            data["keys"]["auth"],
            request.headers.get("User-Agent"),
        )
        return jsonify({"success": True})
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid subscription data"}), 400


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    """Remove a push notification subscription."""
    if PUSH_SERVICE is None:
        return jsonify({"success": False, "error": "Push not configured"}), 503
    try:
        data = request.get_json(force=True)
        PUSH_SERVICE.unsubscribe(data["endpoint"])
        return jsonify({"success": True})
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid request"}), 400


def _format_utc_ts(dt):
    """Format a datetime as an ISO 8601 UTC string with Z suffix.

    Naive datetimes are assumed to be UTC (legacy data stored
    without timezone info but representing UTC).
    """
    if dt is None:
        return "Never"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@app.route("/api/sources")
def get_sources():
    """Get status of all data sources (API submitters and collectors)"""
    sources = []
    for source_info in DATABASE.get_all_sources():
        name = source_info["source"]
        last_data = DATABASE.get_most_recent_timestamp(source=name)
        last_sync = source_info["last_sync"]
        is_collector = name in {c.name for c in _get_config().collectors}
        sources.append({
            "name": name,
            "last_sync": _format_utc_ts(last_sync),
            "last_data": _format_utc_ts(last_data),
            "type": "collector" if is_collector else "api",
        })

    return jsonify({"sources": sources})


@app.route("/api/drones")
def get_drones():
    """Get list of unique drones in time window"""
    try:
        start, end = _parse_time_range(request.args)
        drones = DATABASE.get_drones(start, end)
        return jsonify({"drones": drones})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting drones")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/drones/incremental", methods=["POST"])
def get_drones_incremental():
    """Get drones with newer data than client's known timestamps"""
    try:
        start, end = _parse_time_range(request.args)
        data = request.get_json() or {}
        known_timestamps = data.get("known_timestamps", {})
        drones = DATABASE.get_drones_incremental(start, end, known_timestamps)
        return jsonify({"drones": drones})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting incremental drones")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/refresh", methods=["POST"])
def get_refresh():
    """Consolidated refresh: returns drones, alerts, stats, and sources in one call."""
    try:
        start, end = _parse_time_range(request.args)
        data = request.get_json() or {}
        known_timestamps = data.get("known_timestamps", {})

        drones = DATABASE.get_drones_incremental(start, end, known_timestamps)

        try:
            active = DATABASE.get_active_geozone_events()
        except sqlite3.Error:
            logger.exception("Error getting alerts in refresh")
            active = []
        uas_ids = list(set(e["uas_id"] for e in active))

        try:
            stats = DATABASE.get_stats(start, end)
        except sqlite3.Error:
            logger.exception("Error getting stats in refresh")
            stats = {}

        try:
            sources = []
            collector_names = {c.name for c in _get_config().collectors}
            for source_info in DATABASE.get_all_sources():
                name = source_info["source"]
                last_data = DATABASE.get_most_recent_timestamp(source=name)
                last_sync = source_info["last_sync"]
                sources.append({
                    "name": name,
                    "last_sync": _format_utc_ts(last_sync),
                    "last_data": _format_utc_ts(last_data),
                    "type": "collector" if name in collector_names else "api",
                })
        except sqlite3.Error:
            logger.exception("Error getting sources in refresh")
            sources = []

        return jsonify({
            "drones": drones,
            "alerts": {"active": active, "uas_ids": uas_ids, "count": len(active)},
            "stats": stats,
            "sources": sources,
        })
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error in refresh endpoint")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/positions")
def get_positions():
    """Get positions in time window"""
    try:
        start, end = _parse_time_range(request.args)
        uas_id = request.args.get("uas_id")
        limit = min(
            int(request.args.get("limit", _get_config().max_positions_per_query)),
            _get_config().max_positions_per_query,
        )

        positions = DATABASE.get_positions(start, end, uas_id, limit)
        return jsonify({"positions": positions})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting positions")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/tracks/<uas_id>")
def get_track(uas_id):
    """Get track for specific drone

    Returns positions grouped by session if session grouping is enabled.
    Query params:
        start: start time
        end: end time
        sessions: if "true", return tracks grouped by session (default: false)
        session_id: optional session id to filter to a single session (requires sessions=true)
    """
    try:
        start, end = _parse_time_range(request.args)
        group_by_session = request.args.get("sessions", "false").lower() == "true"
        session_id = request.args.get("session_id")

        if group_by_session:
            if session_id:
                positions = DATABASE.get_track_session_positions(uas_id, session_id)
                return jsonify({
                    "uas_id": uas_id,
                    "sessions": [{"session_id": session_id, "positions": positions}],
                })
            sessions = DATABASE.get_track_sessions(uas_id, start, end)
            return jsonify({"uas_id": uas_id, "sessions": sessions})
        track = DATABASE.get_track(uas_id, start, end)
        return jsonify({"uas_id": uas_id, "track": track})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting track")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/tracks/batch", methods=["POST"])
def get_tracks_batch():
    """Batch fetch tracks for multiple sessions in one request.

    Request body: {"sessions": [{"uas_id": "...", "session_id": "..."}, ...]}
    Response: {"tracks": {"uas_id:session_id": {"uas_id": "...", "session_id": "...", "positions": [...]}, ...}}
    """
    try:
        data = request.get_json(force=True)
        session_list = data.get("sessions", []) if isinstance(data, dict) else []

        if not isinstance(session_list, list):
            return jsonify({"error": "sessions must be an array"}), 400

        results = {}
        for entry in session_list:
            uas_id = entry.get("uas_id", "")
            session_id = entry.get("session_id", "")
            if not uas_id or not session_id:
                continue
            key = f"{uas_id}:{session_id}"
            positions = DATABASE.get_track_session_positions(uas_id, session_id)
            results[key] = {
                "uas_id": uas_id,
                "session_id": session_id,
                "positions": positions,
            }

        return jsonify({"tracks": results})
    except sqlite3.Error:
        logger.exception("Error in batch track fetch")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/operators")
def get_operators():
    """Get operator positions"""
    try:
        start, end = _parse_time_range(request.args)
        operators = DATABASE.get_operators(start, end)
        return jsonify({"operators": operators})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting operators")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/bounds")
def get_bounds():
    """Get bounding box of all positions in time window"""
    try:
        start, end = _parse_time_range(request.args)
        bounds = DATABASE.get_bounds(start, end)

        if bounds:
            return jsonify(
                {
                    "bounds": {
                        "min_lat": bounds[0],
                        "max_lat": bounds[1],
                        "min_lon": bounds[2],
                        "max_lon": bounds[3],
                    }
                }
            )
        return jsonify({"bounds": None})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting bounds")
        return jsonify({"error": "Internal server error"}), 500


def _safe_csv_val(val):
    """Sanitize a CSV cell value to prevent formula injection."""
    s = str(val) if val is not None else ""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\n", "\r"):
        return "'" + s
    return s


def _export_csv(positions, filename):
    """Generate CSV export from position data"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "latitude", "longitude",
        "altitude_m", "altitude_ft",
        "operator_id", "session_id"
    ])

    for pos in positions:
        alt_m = pos.get("altitude")
        alt_ft = round(alt_m * 3.28084, 1) if alt_m is not None else ""
        writer.writerow([
            pos.get("timestamp", ""),
            pos.get("latitude", ""),
            pos.get("longitude", ""),
            alt_m if alt_m is not None else "",
            alt_ft,
            _safe_csv_val(pos.get("operator_id", "")),
            _safe_csv_val(pos.get("computed_session_id", "")),
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )


def _export_gpx(positions, filename):
    """Generate GPX export from position data"""
    ns = "http://www.topografix.com/GPX/1/1"
    ET.register_namespace("", ns)

    gpx = ET.Element(f"{{{ns}}}gpx", version="1.1", creator="RemoteID Web UI")
    trk = ET.SubElement(gpx, f"{{{ns}}}trk")
    name = ET.SubElement(trk, f"{{{ns}}}name")
    name.text = filename
    trkseg = ET.SubElement(trk, f"{{{ns}}}trkseg")

    for pos in positions:
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            trkpt = ET.SubElement(trkseg, f"{{{ns}}}trkpt", lat=str(lat), lon=str(lon))
            ts = pos.get("timestamp")
            if ts:
                time_elem = ET.SubElement(trkpt, f"{{{ns}}}time")
                time_elem.text = str(ts)
            alt = pos.get("altitude")
            if alt is not None:
                ele = ET.SubElement(trkpt, f"{{{ns}}}ele")
                ele.text = str(alt)

    ET.indent(gpx)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(gpx, encoding="unicode")

    return Response(
        xml_str,
        mimetype="application/gpx+xml",
        headers={"Content-Disposition": f"attachment; filename={filename}.gpx"},
    )


def _export_kml(positions, filename):
    """Generate KML export from position data"""
    ns = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", ns)

    kml = ET.Element(f"{{{ns}}}kml")
    doc = ET.SubElement(kml, f"{{{ns}}}Document")
    doc_name = ET.SubElement(doc, f"{{{ns}}}name")
    doc_name.text = filename

    # Track as LineString
    pm = ET.SubElement(doc, f"{{{ns}}}Placemark")
    pm_name = ET.SubElement(pm, f"{{{ns}}}name")
    pm_name.text = f"{filename} Track"
    line_string = ET.SubElement(pm, f"{{{ns}}}LineString")
    alt_mode = ET.SubElement(line_string, f"{{{ns}}}altitudeMode")
    alt_mode.text = "absolute"
    coords = ET.SubElement(line_string, f"{{{ns}}}coordinates")

    coord_parts = []
    for pos in positions:
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            alt = pos.get("altitude") or 0
            coord_parts.append(f"{lon},{lat},{alt}")
    coords.text = "\n" + "\n".join(coord_parts) + "\n"

    # Individual position waypoints
    for i, pos in enumerate(positions):
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            ts = pos.get("timestamp", "")
            alt = pos.get("altitude") or 0
            wp = ET.SubElement(doc, f"{{{ns}}}Placemark")
            wp_name = ET.SubElement(wp, f"{{{ns}}}name")
            wp_name.text = f"Point {i + 1}"
            desc = ET.SubElement(wp, f"{{{ns}}}description")
            desc.text = f"Time: {ts}"
            point = ET.SubElement(wp, f"{{{ns}}}Point")
            pt_coords = ET.SubElement(point, f"{{{ns}}}coordinates")
            pt_coords.text = f"{lon},{lat},{alt}"

    ET.indent(kml)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(kml, encoding="unicode")

    return Response(
        xml_str,
        mimetype="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f"attachment; filename={filename}.kml"},
    )


@app.route("/api/export/<fmt>/<uas_id>")
def export_data(fmt, uas_id):
    """Export track data as CSV, GPX, or KML

    Query params:
        start: start time
        end: end time
        session_id: optional session id to filter to a single session
    """
    try:
        start, end = _parse_time_range(request.args)
        session_id = request.args.get("session_id")

        if session_id:
            sessions = DATABASE.get_track_sessions(uas_id, start, end)
            sessions = [s for s in sessions if s["session_id"] == session_id]
            positions = sessions[0]["positions"] if sessions else []
        else:
            positions = DATABASE.get_track(uas_id, start, end)

        safe_id = uas_id.replace("/", "_").replace("\\", "_")
        if session_id:
            short = session_id.replace("session_", "")
            filename = f"{safe_id}_{short}"
        else:
            filename = safe_id

        if fmt == "csv":
            return _export_csv(positions, filename)
        if fmt == "gpx":
            return _export_gpx(positions, filename)
        if fmt == "kml":
            return _export_kml(positions, filename)
        return jsonify({"error": f"Unsupported format: {fmt}"}), 400
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error exporting data for %s", uas_id)
        return jsonify({"error": "Internal server error"}), 500


def _get_api_key_source():
    """Extract source name from Authorization header.
    Checks api_keys first, then collectors_by_key.
    Returns source name or None if invalid/missing.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    api_key = auth_header[7:]  # Remove "Bearer " prefix
    source = _get_config().api_keys.get(api_key)
    if source:
        return source
    return _get_config().collectors_by_key.get(api_key)


@app.route("/api/submit", methods=["POST"])
@csrf.exempt
@limiter.limit("30/minute")
def submit_data():
    """Submit remote ID data from remote nodes.

    Requires Authorization: Bearer <api_key> header.
    Accepts JSON array of events.
    """
    # Validate API key
    source = _get_api_key_source()
    if source is None:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # Parse JSON payload
    try:
        data = request.get_json()
        if not isinstance(data, list):
            return jsonify({"success": False, "error": "Expected JSON array"}), 400
    except ValueError:
        return jsonify({"success": False, "error": "Invalid JSON"}), 400

    if not data:
        return jsonify(
            {"success": True, "inserted": 0, "errors": [], "last_timestamp": None}
        )

    try:
        # Look up collector-specific timezone and fixed position for this source
        source_tz = None
        collector_lat = None
        collector_lon = None
        for c in _get_config().collectors:
            if c.name == source:
                if c.timezone:
                    source_tz = c.timezone
                if c.lat is not None and c.lon is not None:
                    collector_lat = c.lat
                    collector_lon = c.lon
                break
        # Insert records
        inserted, errors, _ = DATABASE.insert_remoteid_records(
            source, data, source_tz=source_tz,
            collector_lat=collector_lat, collector_lon=collector_lon,
        )

        # Log the submission to sync_log
        DATABASE.log_submission(source, inserted)

        # Check submitted positions against alert-enabled geozones
        if ALERT_ENGINE:
            by_uas: dict = {}
            for record in data:
                uid = record.get("uas_id")
                if uid:
                    by_uas.setdefault(uid, []).append(record)
            for uid, positions in by_uas.items():
                ALERT_ENGINE.evaluate(uid, positions)

        # Get the most recent timestamp for this source after insert
        last_timestamp = DATABASE.get_most_recent_timestamp(source)

        # Format timestamp as ISO string
        last_ts_str = last_timestamp.isoformat() if hasattr(last_timestamp, 'isoformat') else last_timestamp

        return jsonify(
            {
                "success": True,
                "inserted": inserted,
                "errors": errors,
                "last_timestamp": last_ts_str,
            }
        )
    except (sqlite3.Error, ValueError, TypeError):
        logger.exception("Error submitting data from %s", source)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@app.route("/api/submit/ping", methods=["GET"])
@csrf.exempt
@limiter.limit("30/minute")
def submit_ping():
    """Heartbeat endpoint for API key submitters and collectors.

    Requires Authorization: Bearer <api_key> header.
    Logs a check-in to sync_log so the sources status panel
    shows the source as recently connected.

    For collectors: optional lat= & lon= query params update
    the collector's position on the map.
    """
    source = _get_api_key_source()
    if source is None:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        DATABASE.log_submission(source, 0)
        lat = request.args.get("lat")
        lon = request.args.get("lon")
        if lat is not None and lon is not None:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                if -90 <= lat_f <= 90 and -180 <= lon_f <= 180:
                    DATABASE.update_collector_position(source, lat_f, lon_f)
            except (ValueError, TypeError):
                pass
        logger.info("Heartbeat from %s", source)
        return jsonify({"success": True, "source": source})
    except sqlite3.Error:
        logger.exception("Error logging heartbeat from %s", source)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@app.route("/api/collectors")
def get_collectors():
    """Get current positions and status for all configured collectors"""
    positions = DATABASE.get_collector_positions()
    pos_by_name = {p["name"]: p for p in positions}
    sources = DATABASE.get_all_sources()
    last_sync_by_name = {s["source"]: s["last_sync"] for s in sources}
    stale_seconds = _get_config().position_stale_minutes * 60
    now = datetime.now(timezone.utc)
    result = []
    for c in _get_config().collectors:
        last_sync = last_sync_by_name.get(c.name)
        updated = None
        is_stale = True
        if last_sync and isinstance(last_sync, datetime):
            if last_sync.tzinfo is None:
                local_tz = datetime.now(timezone.utc).astimezone().tzinfo
                last_sync = last_sync.replace(tzinfo=local_tz).astimezone(timezone.utc)
            updated = last_sync
            if (now - last_sync).total_seconds() <= stale_seconds:
                is_stale = False
        if c.type == "fixed":
            result.append({
                "name": c.name,
                "color": c.color,
                "type": "fixed",
                "latitude": c.lat,
                "longitude": c.lon,
                "updated_at": updated,
                "stale": is_stale,
            })
        else:
            pos = pos_by_name.get(c.name, {})
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            result.append({
                "name": c.name,
                "color": c.color,
                "type": "mobile",
                "latitude": lat,
                "longitude": lon,
                "updated_at": updated,
                "stale": is_stale,
            })
    return jsonify(result)


@app.route("/api/sessions/redetect", methods=["POST"])
@csrf.exempt
@limiter.limit("10/minute")
def redetect():
    """Force full session re-detection for all UAS.

    Requires Authorization: Bearer <api_key> header.
    Useful after bulk timestamp corrections or data migrations.
    """
    source = _get_api_key_source()
    if source is None:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    try:
        redetect_sessions(
            _get_config().database_path,
            _get_config().session_detection.gap_threshold,
            dry_run=False,
            force=True,
        )
        logger.info("Full session re-detection triggered by %s", source)
        return jsonify({"success": True})
    except sqlite3.Error:
        logger.exception("Session re-detection failed")
        return jsonify({"success": False, "error": "Re-detection failed"}), 500


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """Get active geozone alert events."""
    active = DATABASE.get_active_geozone_events()
    uas_ids = list(set(e["uas_id"] for e in active))
    return jsonify({
        "active": active,
        "uas_ids": uas_ids,
        "count": len(active),
    })


@app.route("/api/alerts/history", methods=["GET"])
def get_alert_history():
    """Get geozone event history with optional filtering."""
    try:
        uas_id = request.args.get("uas_id") or None
        geozone_name = request.args.get("geozone_name") or None
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))

        from_str = request.args.get("from")
        to_str = request.args.get("to")
        from_date = None
        to_date = None
        if from_str:
            from_date = datetime.fromisoformat(from_str.replace("Z", "+00:00"))
        if to_str:
            to_date = datetime.fromisoformat(to_str.replace("Z", "+00:00"))

        events, total = DATABASE.get_geozone_event_history(
            uas_id=uas_id,
            geozone_name=geozone_name,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
        )

        # Format datetime fields to ISO strings for JSON
        for event in events:
            for key in ("entered_at", "last_seen_at", "exited_at", "created_at"):
                val = event.get(key)
                if val and hasattr(val, "isoformat"):
                    event[key] = val.isoformat()

        return jsonify({
            "events": events,
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting alert history")
        return jsonify({"error": "Internal server error"}), 500


def _export_alert_csv(events):
    """Generate CSV export from geozone event data"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "uas_id", "geozone_name", "entered_at", "last_seen_at",
        "exited_at", "exited_reason", "duration_seconds"
    ])

    for ev in events:
        entered = ev.get("entered_at")
        last_seen = ev.get("last_seen_at")
        exited = ev.get("exited_at")
        duration = None
        if entered:
            end = exited or datetime.now(timezone.utc)
            if hasattr(entered, "timestamp"):
                duration = int(end.timestamp() - entered.timestamp())

        def fmt_dt(val):
            """Format a datetime value for CSV output."""
            if val and hasattr(val, "isoformat"):
                return val.isoformat()
            return str(val) if val else ""

        writer.writerow([
            _safe_csv_val(ev.get("uas_id", "")),
            _safe_csv_val(ev.get("geozone_name", "")),
            fmt_dt(entered),
            fmt_dt(last_seen),
            fmt_dt(exited),
            _safe_csv_val(ev.get("exited_reason", "")),
            duration if duration is not None else "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=alert_events.csv"},
    )


@app.route("/api/alerts/export/csv", methods=["GET"])
def export_alert_csv():
    """Export geozone alert history as CSV."""
    try:
        uas_id = request.args.get("uas_id") or None
        geozone_name = request.args.get("geozone_name") or None

        from_str = request.args.get("from")
        to_str = request.args.get("to")
        from_date = None
        to_date = None
        if from_str:
            from_date = datetime.fromisoformat(from_str.replace("Z", "+00:00"))
        if to_str:
            to_date = datetime.fromisoformat(to_str.replace("Z", "+00:00"))

        events, _ = DATABASE.get_geozone_event_history(
            uas_id=uas_id,
            geozone_name=geozone_name,
            from_date=from_date,
            to_date=to_date,
            limit=1000000,
            offset=0,
        )

        return _export_alert_csv(events)
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error exporting alert CSV")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get aggregate statistics for the current time window."""
    try:
        start, end = _parse_time_range(request.args)
        stats = DATABASE.get_stats(start, end)
        return jsonify(stats)
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting stats")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/last-timestamp", methods=["GET"])
def get_last_timestamp():
    """Get the most recent timestamp in the database.

    If Authorization header is provided, returns max timestamp for that source.
    Otherwise returns max timestamp across all sources.
    """
    try:
        source = _get_api_key_source()

        # Get most recent timestamp
        last_timestamp = DATABASE.get_most_recent_timestamp(source)

        # Format as ISO string
        last_ts_str = last_timestamp.isoformat() if hasattr(last_timestamp, 'isoformat') else last_timestamp

        return jsonify({"last_timestamp": last_ts_str})
    except (sqlite3.Error, AttributeError, TypeError):
        logger.exception("Error getting last timestamp")
        return jsonify({"success": False, "error": "Internal server error"}), 500


def _to_naive_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to naive UTC, pass through naive unchanged."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_time_range(args):
    """Parse start/end time from request args.

    Returns naive UTC datetimes for consistency with the database.
    """
    end_time = datetime.now(timezone.utc).replace(tzinfo=None)

    # Parse end time
    end_str = args.get("end")
    if end_str:
        end_time = _to_naive_utc(
            datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        )

    # Parse start time
    start_str = args.get("start")
    if start_str:
        start_time = _to_naive_utc(
            datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        )
    else:
        # Default to default_hours before end
        start_time = end_time - timedelta(hours=_get_config().default_hours)

    return start_time, end_time


def _watch_config():
    """Background thread: periodically reload hot-reloadable config fields."""
    logger.info(
        "Config file watcher started for %s",
        os.path.abspath(_get_config().config_path),
    )
    while True:
        time.sleep(10)
        try:
            _hot_reload_config()
        except (FileNotFoundError, PermissionError, OSError, ValueError, KeyError):
            logger.exception("Error reloading hot config")


def _hot_reload_config():
    """Try to reload config from file and atomically swap the snapshot."""
    global CONFIG  # noqa: PLW0603  # pylint: disable=global-statement
    new_config = CONFIG.reload_hot_config()
    if new_config is not None:
        with _config_lock:
            CONFIG = new_config
            global _config_snapshot  # noqa: PLW0603  # pylint: disable=global-statement
            _config_snapshot = new_config


def _init_app(config_path: str):
    """Initialize application components. Returns Flask app ready to serve.

    Creates configuration, database, session scheduler, alert engine,
    and push notification service objects but does **not** start any
    background threads.  Call :func:`start_background_services` when
    ready to start them (gunicorn master via ``when_ready``, or
    ``main()`` for dev server).
    """
    # pylint: disable=global-statement
    global CONFIG, DATABASE, SESSION_SCHEDULER, ALERT_ENGINE, PUSH_SERVICE, PUSH_VAPID_PUBLIC_KEY

    logger.info("Loading configuration from %s", config_path)
    CONFIG = WebConfig(config_path)

    logger.info("Initializing database at %s", CONFIG.database_path)
    DATABASE = WebDatabase(CONFIG.database_path)

    ALERT_ENGINE = AlertEngine(DATABASE, CONFIG)

    # Initialize push notification service
    # Keys are stored alongside the database so they survive container rebuilds
    logger.info("Initializing push notification service (database_path=%s)", CONFIG.database_path)
    vapid_private, vapid_public = _ensure_vapid_keys(CONFIG.database_path)
    PUSH_VAPID_PUBLIC_KEY = vapid_public
    logger.info("PUSH_VAPID_PUBLIC_KEY=%s", vapid_public[:20] + "..." if vapid_public else "None")
    if vapid_private and vapid_public:
        PUSH_SERVICE = PushService(DATABASE, vapid_private, vapid_public)
        ALERT_ENGINE.on_new_alert = _on_new_alert
        ALERT_ENGINE.on_new_session = _on_new_session
        logger.info("Push notification service initialized")
    else:
        PUSH_SERVICE = None
        logger.info("Push notification service not available")

    SESSION_SCHEDULER = SessionScheduler(CONFIG, CONFIG.database_path, alert_engine=ALERT_ENGINE)

    global _config_snapshot  # noqa: PLW0603  # pylint: disable=global-statement
    _config_snapshot = CONFIG

    return app


def _on_new_alert(uas_id: str, geozone_name: str):
    """Callback fired when a new geozone alert is triggered. Sends push notification."""
    if PUSH_SERVICE is None:
        return
    name = CONFIG.drone_aliases.get(uas_id, uas_id)
    PUSH_SERVICE.notify_all(
        "Geozone Alert",
        f"{name} entered {geozone_name}",
        data={"uas_id": uas_id, "geozone": geozone_name},
    )


def _on_new_session(uas_id: str, session_id: str, first_position: Optional[Dict] = None):
    """Callback fired when a new drone session/flight is detected. Sends push notification."""
    if PUSH_SERVICE is None:
        return
    name = CONFIG.drone_aliases.get(uas_id, uas_id)
    body = f"{name} — new flight detected"
    if first_position and first_position.get("altitude") is not None:
        body += f" • Alt: {first_position['altitude']:.0f}m"
    PUSH_SERVICE.notify_all(
        "New Drone",
        body,
        data={"uas_id": uas_id, "session_id": session_id, "type": "new_session"},
    )


def start_background_services():
    """Start DB-bound background threads (session detection, config watcher).

    Called once from the gunicorn master process (via ``when_ready``) so
    only one instance of each runs, or from ``main()`` for the dev server.
    """
    if SESSION_SCHEDULER:

        SESSION_SCHEDULER.start()

    start_config_watcher()


def start_config_watcher():
    """Start a background thread to watch for config file changes."""
    watcher = threading.Thread(target=_watch_config, daemon=True)
    watcher.start()
    logger.info(
        "Config file watcher started for %s",
        os.path.abspath(_get_config().config_path),
    )


def main():
    """Main entry point for development server"""
    parser = argparse.ArgumentParser(description="Remote ID Web Interface")
    parser.add_argument(
        "--config", required=True, help="Path to configuration YAML file"
    )
    args = parser.parse_args()

    _init_app(args.config)
    start_background_services()

    try:
        logger.info("Starting web server on %s:%d", _get_config().host, _get_config().port)
        if _get_config().url_prefix:
            logger.info("URL prefix: %s", _get_config().url_prefix)
        else:
            logger.info("URL prefix: (none)")
        app.run(host=_get_config().host, port=_get_config().port, debug=False, threaded=True)
    finally:
        if SESSION_SCHEDULER:
            SESSION_SCHEDULER.stop()


if __name__ == "__main__":
    main()
