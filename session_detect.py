"""Session detection and assignment for UAS tracks.

This script analyzes existing remoteid data and assigns session IDs based on
time gaps between consecutive messages from the same UAS. A new session is
started when there's a gap larger than the configured threshold.

Usage:
    python session_detect.py --db data/web.db --gap 600

The default gap threshold is 600 seconds (10 minutes).
"""

import argparse
import uuid
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# Default gap threshold in seconds (10 minutes)
DEFAULT_GAP_THRESHOLD = 600


def _adapt_datetime(dt: datetime) -> str:
    """Adapt datetime to ISO format string for SQLite"""
    return dt.isoformat()


def _convert_datetime(s: bytes) -> datetime:
    """Convert ISO format string from SQLite to datetime"""
    return datetime.fromisoformat(s.decode())


# Register adapters for datetime handling
sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("DATETIME", _convert_datetime)


def get_uas_list(db_path: str, since: Optional[datetime] = None) -> List[str]:
    """Get list of all unique UAS IDs in the database.

    When *since* is provided, only UAS with at least one position
    at or after that timestamp are returned.
    """
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5) as conn:
        if since is not None:
            cursor = conn.execute(
                "SELECT DISTINCT uas_id FROM remoteid WHERE uas_id IS NOT NULL AND timestamp >= ?",
                (since,),
            )
        else:
            cursor = conn.execute(
                "SELECT DISTINCT uas_id FROM remoteid WHERE uas_id IS NOT NULL"
            )
        return [row[0] for row in cursor.fetchall()]


def get_positions_for_uas(db_path: str, uas_id: str) -> List[Tuple[int, datetime]]:
    """Get all positions for a UAS ordered by timestamp

    Returns list of (id, timestamp) tuples
    """
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5) as conn:
        cursor = conn.execute(
            "SELECT id, timestamp FROM remoteid WHERE uas_id = ? ORDER BY timestamp",
            (uas_id,)
        )
        return cursor.fetchall()


def detect_sessions(positions: List[Tuple[int, datetime]], gap_threshold: int) -> List[Tuple[int, str]]:
    """Detect sessions based on time gaps

    Args:
        positions: List of (id, timestamp) tuples
        gap_threshold: Gap threshold in seconds

    Returns:
        List of (id, session_id) tuples
    """
    if not positions:
        return []

    sessions = []
    session_id = f"session_{uuid.uuid4().hex[:12]}"

    for i, (pos_id, timestamp) in enumerate(positions):
        if i == 0:
            sessions.append((pos_id, session_id))
            continue

        prev_timestamp = positions[i - 1][1]
        gap = (timestamp - prev_timestamp).total_seconds()

        if gap > gap_threshold:
            # Start a new session
            session_id = f"session_{uuid.uuid4().hex[:12]}"
            logger.debug("New session detected at %s (gap: %.1fs)", timestamp, gap)

        sessions.append((pos_id, session_id))

    return sessions


def update_session_ids(db_path: str, updates: List[Tuple[str, datetime, int]]):
    """Update computed_session_id for a batch of records

    Args:
        updates: List of (session_id, detected_at, id) tuples
    """
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5) as conn:
        conn.executemany(
            """UPDATE remoteid
               SET computed_session_id = ?,
                   session_detected_at = ?
               WHERE id = ?""",
            updates
        )
        conn.commit()


def analyze_sessions(db_path: str, uas_id: Optional[str] = None) -> dict:
    """Analyze sessions for a UAS or all UAS

    Returns dictionary with session statistics
    """
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=5) as conn:
        conn.row_factory = sqlite3.Row

        if uas_id:
            cursor = conn.execute(
                """SELECT computed_session_id,
                          COUNT(*) as count,
                          MIN(timestamp) as start_time,
                          MAX(timestamp) as end_time
                   FROM remoteid
                   WHERE uas_id = ? AND computed_session_id IS NOT NULL
                   GROUP BY computed_session_id
                   ORDER BY start_time""",
                (uas_id,)
            )
        else:
            cursor = conn.execute(
                """SELECT uas_id,
                          computed_session_id,
                          COUNT(*) as count,
                          MIN(timestamp) as start_time,
                          MAX(timestamp) as end_time
                   FROM remoteid
                   WHERE computed_session_id IS NOT NULL
                   GROUP BY uas_id, computed_session_id
                   ORDER BY uas_id, start_time"""
            )

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            # Parse datetime strings if needed
            start_time = row_dict.get('start_time')
            end_time = row_dict.get('end_time')

            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))

            if start_time and end_time:
                duration = (end_time - start_time).total_seconds()
                row_dict['duration_seconds'] = duration
                row_dict['start_time'] = start_time
                row_dict['end_time'] = end_time
            results.append(row_dict)

        return {"sessions": results, "total_count": len(results)}


def process_database(
    db_path: str,
    gap_threshold: int,
    dry_run: bool = False,
    since: Optional[datetime] = None,
    force: bool = False,
):
    """Process the database and assign session IDs.

    When *since* is provided, only UAS with positions at or after that
    timestamp are processed (all historic positions for those UAS are
    still scanned so session boundary detection stays correct). UAS that
    have had no activity since *since* are skipped entirely.

    When *force* is True, all UAS are processed regardless of *since*.
    """
    if force:
        since = None

    db_path = Path(db_path)

    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        return "database not found", []

    logger.debug("Processing database: %s", db_path)
    logger.debug("Gap threshold: %i seconds", gap_threshold)
    if since is not None:
        logger.debug("Incremental mode: only UAS with activity since %s", since)

    # Get relevant UAS IDs (filtered by activity window when since is set)
    uas_list = get_uas_list(str(db_path), since=since)
    logger.debug("Found %i unique UAS IDs", len(uas_list))

    total_sessions = 0
    total_records = 0

    for uas_id in uas_list:
        positions = get_positions_for_uas(str(db_path), uas_id)

        if len(positions) < 2:
            # Single record - assign an opaque session ID
            if positions and not dry_run:
                session_id = f"session_{uuid.uuid4().hex[:12]}"
                update_session_ids(str(db_path), [(session_id, datetime.now(), positions[0][0])])
            continue

        # Detect sessions
        sessions = detect_sessions(positions, gap_threshold)

        if sessions:
            session_count = len(set(s[1] for s in sessions))
            total_sessions += session_count
            total_records += len(sessions)

            if not dry_run:
                # Batch update
                updates = [
                    (session_id, datetime.now(), pos_id)
                    for pos_id, session_id in sessions
                ]
                update_session_ids(str(db_path), updates)

    logger.debug(
        "Session detection: %i UAS, %i records, %i sessions detected",
        len(uas_list), total_records, total_sessions,
    )

    if dry_run:
        logger.debug("(Dry run - no changes made)")

    return f"{len(uas_list)} UAS, {total_records} records, {total_sessions} sessions detected", uas_list


def main():
    """Main entry point for session detection CLI."""
    parser = argparse.ArgumentParser(
        description="Detect and assign session IDs to UAS tracks based on time gaps"
    )
    parser.add_argument(
        "--db",
        default="./data/web.db",
        help="Path to SQLite database (default: ./data/web.db)"
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=DEFAULT_GAP_THRESHOLD,
        help=f"Gap threshold in seconds (default: {DEFAULT_GAP_THRESHOLD})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force full scan of all UAS, bypassing incremental mode"
    )
    parser.add_argument(
        "--analyze",
        metavar="UAS_ID",
        help="Analyze sessions for a specific UAS ID"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    if args.analyze:
        # Just analyze and show sessions
        stats = analyze_sessions(args.db, args.analyze)
        print(f"\nSession analysis for UAS: {args.analyze}")
        print(f"Total sessions: {stats['total_count']}")
        print("-" * 80)
        for session in stats["sessions"]:
            duration = session.get('duration_seconds', 0)
            print(f"Session: {session['computed_session_id']}")
            print(f"  Records: {session['count']}")
            print(f"  Start: {session['start_time']}")
            print(f"  End: {session['end_time']}")
            print(f"  Duration: {duration/60:.1f} minutes")
            print()
    else:
        # Process the database
        process_database(args.db, args.gap, args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
