"""One-time import from a collector SQLite database into the web database

Usage:
    python import_db.py --web-db ./data/web.db --source ./data/collector.db --name "Field-Node"

This replaces the old background sync mechanism. Run on-demand whenever
you want to pull data from a collector's SQLite database into the web UI.
"""

import argparse
import logging
import sys

from database import WebDatabase

logger = logging.getLogger(__name__)


def main():
    """CLI entry point: parse args and import from a collector database."""
    parser = argparse.ArgumentParser(
        description="Import data from a collector SQLite database"
    )
    parser.add_argument(
        "--web-db", required=True, help="Path to web interface database"
    )
    parser.add_argument(
        "--source", required=True, help="Path to source collector database"
    )
    parser.add_argument(
        "--name", required=True, help="Source name for imported records"
    )
    parser.add_argument(
        "--gap-threshold",
        type=int,
        default=600,
        help="Session gap threshold in seconds (default: 600)",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone (e.g. America/Denver) for naive timestamps in source DB",
    )
    parser.add_argument(
        "--collector-lat",
        type=float,
        default=None,
        help="Collector latitude to stamp on imported records",
    )
    parser.add_argument(
        "--collector-lon",
        type=float,
        default=None,
        help="Collector longitude to stamp on imported records",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    db = WebDatabase(args.web_db)
    count = db.import_from_collector(
        args.source, args.name, args.gap_threshold, args.timezone,
        args.collector_lat, args.collector_lon,
    )
    logger.info("Imported %d records from %s into %s", count, args.name, args.web_db)
    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
