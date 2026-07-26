"""Global instrument connection strings — read/upsert per instrument name."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from logic.db.connection import open_conn


def list_connections(db_path: Path) -> dict[str, str]:
    """Return {instrument_name: connection_string} for all stored instruments."""
    with open_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT instrument_name, connection_string FROM instrument_connections;"
        ).fetchall()
    return {str(r["instrument_name"]): str(r["connection_string"]) for r in rows}


def upsert_connection(
    db_path: Path,
    instrument_name: str,
    connection_string: str,
    updated_by: str,
) -> None:
    """Insert or replace the connection string for one instrument."""
    now = datetime.now().isoformat()
    with open_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO instrument_connections
                (instrument_name, connection_string, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(instrument_name) DO UPDATE SET
                connection_string = excluded.connection_string,
                updated_at        = excluded.updated_at,
                updated_by        = excluded.updated_by;
            """,
            (instrument_name.strip(), connection_string.strip(), now, updated_by.strip()),
        )
        conn.commit()
