"""Audit-log append and query."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from logic.db.connection import open_conn


def log_audit_action(db_path: Path, action: str, *, username: str = "",
                     employee_id: str = "", details: str = "") -> None:
    text = action.strip()
    if not text:
        raise ValueError("Audit action is required.")
    ts = datetime.now().isoformat()
    with open_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_logs (timestamp, username, employee_id, action, details) "
            "VALUES (?, ?, ?, ?, ?);",
            (ts, username.strip(), employee_id.strip(), text, details.strip()),
        )
        conn.commit()
    try:
        from logic.secure_logger import get_secure_logger
        get_secure_logger().log_system_event(
            username=username.strip(),
            action=text,
            details=details.strip(),
        )
    except Exception:
        pass


def get_audit_logs(db_path: Path, *, limit: int = 1000) -> list[dict[str, str]]:
    cap = max(1, min(int(limit), 50_000))
    with open_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, timestamp, username, employee_id, action, details "
            "FROM audit_logs ORDER BY timestamp DESC LIMIT ?;",
            (cap,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "timestamp": str(row["timestamp"]),
            "username": str(row["username"] or ""),
            "employee_id": str(row["employee_id"] or ""),
            "action": str(row["action"] or ""),
            "details": str(row["details"] or ""),
        }
        for row in rows
    ]
