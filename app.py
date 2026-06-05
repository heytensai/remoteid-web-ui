"""Flask web interface for Remote ID visualization"""

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from config import WebConfig
from database import WebDatabase
from sync import create_sync_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances
CONFIG: WebConfig = None
DATABASE: WebDatabase = None
SYNC_MANAGER = None  # type: Optional[SyncManager]

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes


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
    """Get track for specific drone"""
    try:
        start, end = _parse_time_range(request.args)
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
        SYNC_MANAGER.force_sync()
        return jsonify({"status": "sync triggered"})
    return jsonify({"status": "sync disabled - no collectors configured"}), 400


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
    except Exception:
        return jsonify({"success": False, "error": "Invalid JSON"}), 400

    if not data:
        return jsonify(
            {"success": True, "inserted": 0, "errors": [], "last_timestamp": None}
        )

    try:
        # Insert records
        inserted, errors, most_recent = DATABASE.insert_remoteid_records(source, data)

        # Get the most recent timestamp for this source after insert
        last_timestamp = DATABASE.get_most_recent_timestamp(source)

        # Format timestamp as ISO string
        last_ts_str = last_timestamp.isoformat() if last_timestamp else None

        return jsonify(
            {
                "success": True,
                "inserted": inserted,
                "errors": errors,
                "last_timestamp": last_ts_str,
            }
        )
    except Exception as e:
        logger.exception("Error submitting data from %s", source)
        return jsonify({"success": False, "error": str(e)}), 500


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
        last_ts_str = last_timestamp.isoformat() if last_timestamp else None

        return jsonify({"last_timestamp": last_ts_str})
    except Exception as e:
        logger.exception("Error getting last timestamp")
        return jsonify({"success": False, "error": str(e)}), 500


def _parse_time_range(args):
    """Parse start/end time from request args"""
    end_time = datetime.now()

    # Parse end time
    end_str = args.get("end")
    if end_str:
        end_time = datetime.fromisoformat(
            end_str.replace("Z", "+00:00").replace("+00:00", "")
        )

    # Parse start time
    start_str = args.get("start")
    if start_str:
        start_time = datetime.fromisoformat(
            start_str.replace("Z", "+00:00").replace("+00:00", "")
        )
    else:
        # Default to default_hours before end
        start_time = end_time - timedelta(hours=CONFIG.default_hours)

    return start_time, end_time


def main():
    """Main entry point"""
    # pylint: disable=global-statement
    global CONFIG, DATABASE, SYNC_MANAGER

    parser = argparse.ArgumentParser(description="Remote ID Web Interface")
    parser.add_argument(
        "--config", required=True, help="Path to configuration YAML file"
    )
    args = parser.parse_args()

    # Load configuration
    logger.info("Loading configuration from %s", args.config)
    CONFIG = WebConfig(args.config)

    # Initialize DATABASE
    logger.info("Initializing database at %s", CONFIG.database_path)
    DATABASE = WebDatabase(CONFIG.database_path)

    # Initialize sync manager
    SYNC_MANAGER = create_sync_manager(
        DATABASE, CONFIG.collectors, CONFIG.sync_interval
    )

    if SYNC_MANAGER:
        SYNC_MANAGER.start()

    try:
        logger.info("Starting web server on %s:%d", CONFIG.host, CONFIG.port)
        app.run(host=CONFIG.host, port=CONFIG.port, debug=False, threaded=True)
    finally:
        if SYNC_MANAGER:
            SYNC_MANAGER.stop()


if __name__ == "__main__":
    main()
