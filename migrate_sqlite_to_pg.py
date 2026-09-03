"""One-shot migration: SQLite → PostgreSQL

Reads the legacy SQLite database and writes all data into a fresh
PostgreSQL database.  Tables are created with PG-native types; indexes
are recreated after the data copy.

Usage:
    python migrate_sqlite_to_pg.py \\
        --sqlite data/web.db \\
        --pg-url postgresql://remoteid:pass@localhost:5432/remoteid

The script is idempotent — running it twice will not create duplicate
rows thanks to ON CONFLICT DO NOTHING.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

BATCH_SIZE = 5000

# Tables to skip (PG creates its own _schema_version at version 8, and
# sqlite_sequence is a SQLite-internal autoincrement bookkeeping table).
SKIP_TABLES = {"_schema_version", "sqlite_sequence"}

# Tables whose id is a SERIAL/autoincrement column — their sequences must be
# advanced past the copied max(id), otherwise the next app INSERT would try
# id=1 and collide with migrated rows.
SERIAL_TABLES = [
    "remoteid",
    "sync_log",
    "session_tracking",
    "geozone_events",
    "users",
    "auth_tokens",
    "sent_alerts",
]

# ── PostgreSQL DDL (matches database.py) ────────────────────────────────────

PG_TABLES = {
    "remoteid": """
        CREATE TABLE IF NOT EXISTS remoteid(
            id SERIAL PRIMARY KEY,
            source TEXT,
            timestamp TIMESTAMPTZ,
            mac_address TEXT,
            uas_id TEXT,
            session_id TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            altitude DOUBLE PRECISION,
            height DOUBLE PRECISION,
            height_type TEXT,
            operator_id TEXT,
            operator_latitude DOUBLE PRECISION,
            operator_longitude DOUBLE PRECISION,
            computed_session_id TEXT,
            session_detected_at TIMESTAMPTZ,
            collector_latitude DOUBLE PRECISION,
            collector_longitude DOUBLE PRECISION
        )
    """,
    "sync_log": """
        CREATE TABLE IF NOT EXISTS sync_log(
            id SERIAL PRIMARY KEY,
            source TEXT,
            last_sync TIMESTAMPTZ,
            records_imported INTEGER
        )
    """,
    "session_tracking": """
        CREATE TABLE IF NOT EXISTS session_tracking(
            id SERIAL PRIMARY KEY,
            uas_id TEXT UNIQUE,
            last_seen TIMESTAMPTZ,
            current_session_id TEXT,
            updated_at TIMESTAMPTZ
        )
    """,
    "geozone_events": """
        CREATE TABLE IF NOT EXISTS geozone_events(
            id SERIAL PRIMARY KEY,
            uas_id TEXT NOT NULL,
            geozone_name TEXT NOT NULL,
            entered_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            exited_at TIMESTAMPTZ,
            exited_reason TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """,
    "collector_positions": """
        CREATE TABLE IF NOT EXISTS collector_positions(
            name TEXT PRIMARY KEY,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """,
    "users": """
        CREATE TABLE IF NOT EXISTS users(
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            role_name TEXT NOT NULL DEFAULT 'guest',
            is_ephemeral INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            login_token_hash TEXT UNIQUE,
            login_token_expires_at TIMESTAMPTZ,
            auth_method TEXT NOT NULL DEFAULT 'ephemeral',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """,
    "auth_tokens": """
        CREATE TABLE IF NOT EXISTS auth_tokens(
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """,
    "latest_positions": """
        CREATE TABLE IF NOT EXISTS latest_positions(
            uas_id TEXT NOT NULL,
            computed_session_id TEXT NOT NULL DEFAULT '',
            max_ts TIMESTAMPTZ NOT NULL,
            min_ts TIMESTAMPTZ NOT NULL,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            altitude DOUBLE PRECISION,
            height DOUBLE PRECISION,
            height_type TEXT,
            max_height DOUBLE PRECISION,
            operator_id TEXT,
            operator_latitude DOUBLE PRECISION,
            operator_longitude DOUBLE PRECISION,
            source TEXT,
            collector_latitude DOUBLE PRECISION,
            collector_longitude DOUBLE PRECISION,
            PRIMARY KEY (uas_id, computed_session_id)
        )
    """,
    "sent_alerts": """
        CREATE TABLE IF NOT EXISTS sent_alerts(
            id SERIAL PRIMARY KEY,
            alert_type TEXT NOT NULL,
            dedup_key TEXT NOT NULL,
            uas_id TEXT,
            session_id TEXT,
            sent_at TIMESTAMPTZ NOT NULL,
            UNIQUE (alert_type, dedup_key)
        )
    """,
}

PG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_uas_time ON remoteid(uas_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_source ON remoteid(source)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_uas_time_unique ON remoteid(uas_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON remoteid(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_computed_session ON remoteid(computed_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_geozone_events_active ON geozone_events(uas_id, geozone_name, exited_at)",
    "CREATE INDEX IF NOT EXISTS idx_geozone_events_stale ON geozone_events(exited_at, last_seen_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_geozone_events_active_unique "
    "ON geozone_events(uas_id, geozone_name) WHERE exited_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sync_log_source ON sync_log(source, last_sync)",
    "CREATE INDEX IF NOT EXISTS idx_lp_max_ts ON latest_positions(max_ts)",
]

PG_SCHEMA_VERSION = 8


# ── Helpers ─────────────────────────────────────────────────────────────────

def sqlite_tables(sqlite_path: str) -> list[str]:
    """Return user-created table names from the SQLite database."""
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def sqlite_columns(sqlite_conn, table: str) -> list[str]:
    """Return column names for a SQLite table via PRAGMA table_info."""
    cur = sqlite_conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def sqlite_row_count(sqlite_conn, table: str) -> int:
    """Return total row count for a SQLite table."""
    return sqlite_conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]


def sqlite_timestamp_columns(sqlite_conn, table: str) -> set:
    """Return the columns whose declared SQLite type is a timestamp type.

    Uses the declared type from ``PRAGMA table_info`` rather than guessing
    from the column name, so names like ``last_seen``/``max_ts`` are covered.
    """
    cur = sqlite_conn.execute(f"PRAGMA table_info({table})")
    out = set()
    for row in cur.fetchall():
        col_name, col_type = row[1], row[2].upper()
        if any(kw in col_type for kw in ("DATETIME", "TIMESTAMP", "DATE")):
            out.add(col_name)
    return out


def convert_timestamp(val):
    """Convert an SQLite datetime value to an aware UTC datetime.

    SQLite stores timestamps as strings like '2024-01-15 12:34:56' with no
    timezone.  They are treated as UTC so they round-trip correctly through a
    TIMESTAMPTZ column regardless of the server's session timezone.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(str(val), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # If it's already ISO-ish, let psycopg2 handle it
    return val


def adapt_row(row: tuple, columns: list, timestamp_cols: set) -> tuple:
    """Adapt a SQLite row for PostgreSQL insertion.

    Converts the timestamp columns (by declared type) to aware UTC datetimes.
    """
    return tuple(
        convert_timestamp(val) if col in timestamp_cols else val
        for val, col in zip(row, columns)
    )


# ── Main ────────────────────────────────────────────────────────────────────

def main():  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """CLI entry point — parse args and run the migration."""
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="Path to SQLite .db file")
    parser.add_argument("--pg-url", required=True, help="PostgreSQL connection URL")
    args = parser.parse_args()

    # ── Open SQLite (read-only) ──────────────────────────────────────────
    print(f"[sqlite] Opening {args.sqlite} (read-only)...")
    sqlite_conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    sqlite_conn.row_factory = sqlite3.Row

    tables = sqlite_tables(args.sqlite)
    tables = [t for t in tables if t not in SKIP_TABLES]
    print(f"[sqlite] Found {len(tables)} tables: {', '.join(tables)}")

    # ── Connect to PostgreSQL ────────────────────────────────────────────
    print(f"[pg] Connecting to {args.pg_url}...")
    pg_conn = psycopg2.connect(args.pg_url)
    pg_conn.autocommit = True
    pg_cur = pg_conn.cursor()

    # ── Create tables ────────────────────────────────────────────────────
    unknown = [t for t in tables if t not in PG_TABLES]
    if unknown:
        print(f"[pg] ERROR: no DDL defined for tables: {', '.join(unknown)}")
        print("[pg] Refusing to proceed — add them to PG_TABLES or SKIP_TABLES.")
        sys.exit(1)

    print("[pg] Creating tables...")
    # Iterate in PG_TABLES definition order (not alphabetical) so that tables
    # with foreign keys (auth_tokens -> users) are created after their parents.
    for table, ddl in PG_TABLES.items():
        if table not in tables:
            continue
        pg_cur.execute(ddl)
        print(f"  ✓ {table}")

    # ── Copy data ────────────────────────────────────────────────────────
    total_rows = 0
    copied_rows = 0
    skipped_tables = []

    # Copy data in PG_TABLES definition order (not alphabetical) so that rows
    # with foreign keys (auth_tokens -> users) are copied after their parents.
    copy_order = [t for t in PG_TABLES if t in tables]
    for table in copy_order:
        row_count = sqlite_row_count(sqlite_conn, table)
        total_rows += row_count
        if row_count == 0:
            print(f"  {table}: 0 rows (skipped)")
            skipped_tables.append(table)
            continue

        columns = sqlite_columns(sqlite_conn, table)
        timestamp_cols = sqlite_timestamp_columns(sqlite_conn, table)

        # Build INSERT statement. execute_values replaces the single %s with
        # the expanded VALUES (...) rows.
        col_list = ", ".join(columns)
        insert_sql = f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT DO NOTHING"

        print(f"  {table}: {row_count} rows...", end=" ", flush=True)

        cur = sqlite_conn.execute(f"SELECT * FROM [{table}]")
        batch = []
        batch_copied = 0

        while True:
            row = cur.fetchone()
            if row is None:
                break
            adapted = adapt_row(row, columns, timestamp_cols)
            batch.append(adapted)
            if len(batch) >= BATCH_SIZE:
                psycopg2.extras.execute_values(pg_cur, insert_sql, batch, page_size=BATCH_SIZE)
                batch_copied += len(batch)
                batch = []

        if batch:
            psycopg2.extras.execute_values(pg_cur, insert_sql, batch, page_size=BATCH_SIZE)
            batch_copied += len(batch)

        copied_rows += batch_copied
        print(f"✓ ({batch_copied} copied)")

    # ── Create indexes ───────────────────────────────────────────────────
    print("[pg] Creating indexes...")
    pg_conn.autocommit = True
    for idx_sql in PG_INDEXES:
        pg_cur.execute(idx_sql)
    print(f"  ✓ {len(PG_INDEXES)} indexes created")

    # ── Reset serial sequences ───────────────────────────────────────────
    print("[pg] Resetting serial sequences...")
    for table in SERIAL_TABLES:
        if table not in tables:
            continue
        pg_cur.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)",
            (table,),
        )
    print(f"  ✓ {len(SERIAL_TABLES)} sequences reset")

    # ── Set schema version ───────────────────────────────────────────────
    pg_cur.execute(
        "CREATE TABLE IF NOT EXISTS _schema_version(version INTEGER NOT NULL UNIQUE)"
    )
    pg_cur.execute(
        "INSERT INTO _schema_version (version) VALUES (%s) "
        "ON CONFLICT (version) DO NOTHING",
        (PG_SCHEMA_VERSION,),
    )
    print(f"[pg] Schema version set to {PG_SCHEMA_VERSION}")

    # ── Verify row counts ────────────────────────────────────────────────
    print("\n[verify] Row count comparison:")
    all_match = True
    for table in tables:
        if table in skipped_tables:
            continue
        pg_count_cur = pg_conn.cursor()
        pg_count_cur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = pg_count_cur.fetchone()[0]
        sg_count = sqlite_row_count(sqlite_conn, table)
        match = "✓" if pg_count == sg_count else "✗ MISMATCH"
        if pg_count != sg_count:
            all_match = False
        print(f"  {table}: SQLite={sg_count}  PG={pg_count}  {match}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print("Migration complete!")
    print(f"  Tables migrated: {len(tables) - len(skipped_tables)}")
    print(f"  Total rows copied: {copied_rows}")
    print(f"  Indexes created: {len(PG_INDEXES)}")
    if all_match:
        print("  All row counts match ✓")
    else:
        print("  ⚠ Some row counts differ — review above")

    sqlite_conn.close()
    pg_conn.close()
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
