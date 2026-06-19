"""Flask web interface for Remote ID visualization"""

import argparse
import csv
import io
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.sax.saxutils import escape

from flask import Flask, jsonify, request, render_template, Response
from flask_cors import cross_origin
from flask_wtf.csrf import CSRFProtect, generate_csrf

from config import WebConfig
from database import WebDatabase
from session_scheduler import SessionScheduler
from sync import create_sync_manager
from alert_engine import AlertEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances
CONFIG: WebConfig = None
DATABASE: WebDatabase = None
SYNC_MANAGER = None  # type: Optional[SyncManager]
SESSION_SCHEDULER: Optional[SessionScheduler] = None
ALERT_ENGINE: Optional[AlertEngine] = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
csrf = CSRFProtect(app)
# CORS intentionally not set globally — API is same-origin via the Flask server.
# Only /api/submit allows cross-origin (uses Bearer token auth).


@app.route("/")
def index():
    """Main page"""
    return render_template("index.html", url_prefix=CONFIG.url_prefix)


@app.route("/api/config")
def get_config():
    """Get map configuration"""
    return jsonify(
        {
            "map": {
                "center_lat": CONFIG.map.center_lat,
                "center_lon": CONFIG.map.center_lon,
                "default_zoom": CONFIG.map.default_zoom,
                "tile_provider": CONFIG.map.tile_provider,
            },
            "default_hours": CONFIG.default_hours,
            "sync_enabled": SYNC_MANAGER is not None,
            "drone_aliases": CONFIG.drone_aliases,
            "manufacturer_prefixes": CONFIG.manufacturer_prefixes,
            "waypoints": CONFIG.to_dict().get("waypoints", []),
            "use_metric": CONFIG.use_metric,
            "stale_timeout": CONFIG.alerts.stale_timeout,
            "csrf_token": generate_csrf(),
        }
    )


@app.route("/api/sync/status", methods=["GET"])
def get_sync_status():
    """Get sync thread status"""
    if SYNC_MANAGER:
        return jsonify({"enabled": True})
    return jsonify({"enabled": False})


@app.route("/api/sync/status", methods=["POST"])
def set_sync_status():
    """Enable or disable sync thread"""
    data = request.get_json()
    enabled = data.get("enabled", True)

    if SYNC_MANAGER:
        if enabled:
            SYNC_MANAGER.start()
        else:
            SYNC_MANAGER.stop()
        return jsonify({"status": "ok", "enabled": enabled})
    return jsonify({"status": "disabled", "enabled": False}), 400


@app.route("/api/sync/collectors")
def get_collectors_status():
    """Get status of all sync collectors"""
    if SYNC_MANAGER:
        collectors_status = []
        for collector in SYNC_MANAGER.collectors:
            last_sync = SYNC_MANAGER.get_last_sync(collector.name)
            collectors_status.append(
                {
                    "name": collector.name,
                    "host": collector.host,
                    "path": collector.remote_db_path,
                    "last_sync": (
                        last_sync.strftime("%Y-%m-%d %H:%M") if last_sync else "Never"
                    ),
                }
            )
        return jsonify({"collectors": collectors_status})
    return jsonify({"collectors": []})


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


@app.route("/api/positions")
def get_positions():
    """Get positions in time window"""
    try:
        start, end = _parse_time_range(request.args)
        uas_id = request.args.get("uas_id")
        limit = min(
            int(request.args.get("limit", CONFIG.max_positions_per_query)),
            CONFIG.max_positions_per_query,
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
            sessions = DATABASE.get_track_sessions(uas_id, start, end)
            if session_id:
                sessions = [s for s in sessions if s["session_id"] == session_id]
            return jsonify({"uas_id": uas_id, "sessions": sessions})
        track = DATABASE.get_track(uas_id, start, end)
        return jsonify({"uas_id": uas_id, "track": track})
    except (ValueError, TypeError, sqlite3.Error):
        logger.exception("Error getting track")
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


@app.route("/api/sync", methods=["POST"])
def trigger_sync():
    """Manually trigger sync from collectors"""
    if SYNC_MANAGER:
        failures = SYNC_MANAGER.force_sync()
        if failures:
            return jsonify({"status": "sync completed with errors", "failed": failures}), 500
        return jsonify({"status": "sync completed", "failed": 0})
    return jsonify({"status": "sync disabled - no collectors configured"}), 400


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
            pos.get("operator_id", "") or "",
            pos.get("computed_session_id", "") or "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )


def _export_gpx(positions, filename):
    """Generate GPX export from position data"""
    gpx = '<?xml version="1.0" encoding="UTF-8"?>\n'
    gpx += '<gpx version="1.1" creator="RemoteID Web UI" xmlns="http://www.topografix.com/GPX/1/1">\n'
    gpx += f'  <trk>\n    <name>{escape(filename)}</name>\n    <trkseg>\n'

    for pos in positions:
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            gpx += f'      <trkpt lat="{lat}" lon="{lon}">\n'
            ts = pos.get("timestamp")
            if ts:
                gpx += f'        <time>{ts}</time>\n'
            alt = pos.get("altitude")
            if alt is not None:
                gpx += f'        <ele>{alt}</ele>\n'
            gpx += "      </trkpt>\n"

    gpx += "    </trkseg>\n  </trk>\n</gpx>\n"

    return Response(
        gpx,
        mimetype="application/gpx+xml",
        headers={"Content-Disposition": f"attachment; filename={filename}.gpx"},
    )


def _export_kml(positions, filename):
    """Generate KML export from position data"""
    kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
    kml += f'  <Document>\n    <name>{escape(filename)}</name>\n'

    # Track as LineString
    kml += "    <Placemark>\n"
    kml += f'      <name>{escape(filename)} Track</name>\n'
    kml += '      <LineString>\n        <altitudeMode>absolute</altitudeMode>\n        <coordinates>\n'

    for pos in positions:
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            alt = pos.get("altitude") or 0
            kml += f"          {lon},{lat},{alt}\n"

    kml += "        </coordinates>\n      </LineString>\n    </Placemark>\n"

    # Individual position waypoints
    for i, pos in enumerate(positions):
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is not None and lon is not None:
            ts = pos.get("timestamp", "")
            alt = pos.get("altitude") or 0
            kml += "    <Placemark>\n"
            kml += f'      <name>Point {i + 1}</name>\n'
            kml += f'      <description>Time: {escape(str(ts))}</description>\n'
            kml += f'      <Point>\n        <coordinates>{lon},{lat},{alt}</coordinates>\n      </Point>\n'
            kml += "    </Placemark>\n"

    kml += "  </Document>\n</kml>\n"

    return Response(
        kml,
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
    Returns source name or None if invalid/missing.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    api_key = auth_header[7:]  # Remove "Bearer " prefix
    return CONFIG.api_keys.get(api_key)


@app.route("/api/submit", methods=["POST"])
@csrf.exempt
@cross_origin()
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
        # Insert records
        inserted, errors, _ = DATABASE.insert_remoteid_records(source, data)

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
    except (sqlite3.Error, ValueError, TypeError) as e:
        logger.exception("Error submitting data from %s", source)
        return jsonify({"success": False, "error": str(e)}), 500


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
    except (sqlite3.Error, AttributeError, TypeError) as e:
        logger.exception("Error getting last timestamp")
        return jsonify({"success": False, "error": str(e)}), 500


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
        start_time = end_time - timedelta(hours=CONFIG.default_hours)

    return start_time, end_time


def _watch_config():
    """Background thread: periodically reload hot-reloadable config fields."""
    logger.info(
        "Config file watcher started for %s",
        os.path.abspath(CONFIG.config_path),
    )
    while True:
        time.sleep(10)
        try:
            CONFIG.reload_hot_config()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Error reloading hot config")


def _init_app(config_path: str):
    """Initialize application components. Returns Flask app ready to serve.

    Creates configuration, database, sync manager, and session detector
    objects but does **not** start any background threads.  Call
    :func:`start_background_services` when ready to start them
    (gunicorn master via ``when_ready``, or ``main()`` for dev server).
    """
    # pylint: disable=global-statement
    global CONFIG, DATABASE, SYNC_MANAGER, SESSION_SCHEDULER, ALERT_ENGINE

    logger.info("Loading configuration from %s", config_path)
    CONFIG = WebConfig(config_path)

    logger.info("Initializing database at %s", CONFIG.database_path)
    DATABASE = WebDatabase(CONFIG.database_path)

    SYNC_MANAGER = create_sync_manager(
        DATABASE, CONFIG.collectors, CONFIG.sync_interval
    )

    ALERT_ENGINE = AlertEngine(DATABASE, CONFIG)

    SESSION_SCHEDULER = SessionScheduler(CONFIG, CONFIG.database_path, alert_engine=ALERT_ENGINE)

    return app


def start_background_services():
    """Start DB-bound background threads (sync, session detection, config watcher).

    Called once from the gunicorn master process (via ``when_ready``) so
    only one instance of each runs, or from ``main()`` for the dev server.
    """
    if SYNC_MANAGER:
        SYNC_MANAGER.start()
    if SESSION_SCHEDULER:

        SESSION_SCHEDULER.start()

    start_config_watcher()


def start_config_watcher():
    """Start a background thread to watch for config file changes."""
    watcher = threading.Thread(target=_watch_config, daemon=True)
    watcher.start()
    logger.info(
        "Config file watcher started for %s",
        os.path.abspath(CONFIG.config_path),
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
        logger.info("Starting web server on %s:%d", CONFIG.host, CONFIG.port)
        if CONFIG.url_prefix:
            logger.info("URL prefix: %s", CONFIG.url_prefix)
        else:
            logger.info("URL prefix: (none)")
        app.run(host=CONFIG.host, port=CONFIG.port, debug=False, threaded=True)
    finally:
        if SYNC_MANAGER:
            SYNC_MANAGER.stop()
        if SESSION_SCHEDULER:
            SESSION_SCHEDULER.stop()


if __name__ == "__main__":
    main()
