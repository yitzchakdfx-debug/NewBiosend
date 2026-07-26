"""Test-version catalog CRUD."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from logic.db.connection import open_conn


def add_test_version(db_path: Path, test_name: str, uut_type: str,
                     version_name: str, content: str, created_by: str,
                     connection_params: str = "") -> int:
    name = test_name.strip()
    ver = version_name.strip()
    if not name or not ver:
        raise ValueError("test_name and version_name are required.")
    now = datetime.now().isoformat()
    with open_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO test_versions "
            "    (test_name, uut_type, version_name, test_content, "
            "     connection_params, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            (name, uut_type.strip(), ver, content,
             connection_params.strip(), now, created_by.strip()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_test_versions(db_path: Path) -> list[dict[str, str | int]]:
    with open_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, test_name, uut_type, version_name, "
            "       connection_params, created_at, created_by "
            "FROM test_versions "
            "ORDER BY created_at DESC, test_name COLLATE NOCASE ASC, "
            "         version_name COLLATE NOCASE DESC;"
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "test_name": row["test_name"],
                "uut_type": row["uut_type"],
                "version_name": row["version_name"],
                "connection_params": str(row["connection_params"] or ""),
                "created_at": row["created_at"],
                "created_by": row["created_by"],
            }
            for row in rows
        ]


def get_test_version(db_path: Path, version_id: int) -> dict | None:
    with open_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, test_name, uut_type, version_name, test_content, "
            "       connection_params, created_at, created_by "
            "FROM test_versions WHERE id = ?;",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "test_name": row["test_name"],
            "uut_type": row["uut_type"],
            "version_name": row["version_name"],
            "test_content": row["test_content"],
            "connection_params": str(row["connection_params"] or ""),
            "created_at": row["created_at"],
            "created_by": row["created_by"],
        }


def delete_test_version(db_path: Path, version_id: int) -> None:
    with open_conn(db_path) as conn:
        conn.execute("DELETE FROM test_versions WHERE id = ?;", (version_id,))
        conn.commit()


def version_exists(db_path: Path, test_name: str, version_name: str) -> bool:
    with open_conn(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM test_versions WHERE test_name = ? AND version_name = ?;",
            (test_name.strip(), version_name.strip()),
        ).fetchone()
        return row is not None
