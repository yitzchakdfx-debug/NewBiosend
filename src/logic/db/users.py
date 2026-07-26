"""User CRUD and authentication queries."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from logic.db.connection import open_conn
from logic.models import normalize_role

_PBKDF2_ITERATIONS = 200_000


def _hash(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def verify_login(db_path: Path, username: str, password: str,
                 iterations: int = _PBKDF2_ITERATIONS) -> dict | None:
    with open_conn(db_path) as conn:
        row = conn.execute(
            "SELECT username, password_hash, salt, role, employee_id "
            "FROM users WHERE username = ?;",
            (username.strip(),),
        ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(_hash(password, row["salt"], iterations),
                                      row["password_hash"]):
            return None
        return {
            "name": row["username"],
            "username": row["username"],
            "role": row["role"],
            "employee_id": str(row["employee_id"] or ""),
        }


def _admin_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'Admin';"
    ).fetchone()
    return int(row["c"]) if row is not None else 0


def create_user(db_path: Path, username: str, password: str, role: str,
                employee_id: str, iterations: int = _PBKDF2_ITERATIONS) -> None:
    clean = username.strip()
    if not clean:
        raise ValueError("Username is required.")
    role_value = normalize_role(role)
    eid = employee_id.strip()
    now = datetime.now().isoformat()
    salt = secrets.token_bytes(16)
    pw_hash = _hash(password, salt, iterations)
    with open_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO users "
            "(username, password_hash, salt, role, employee_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            (clean, pw_hash, salt, role_value, eid, now, now),
        )
        conn.commit()


def delete_user(db_path: Path, username: str) -> None:
    clean = username.strip()
    with open_conn(db_path) as conn:
        row = conn.execute(
            "SELECT role FROM users WHERE username = ?;", (clean,)
        ).fetchone()
        if row is None:
            raise ValueError("User does not exist.")
        if row["role"] == "Admin" and _admin_count(conn) <= 1:
            raise ValueError("Cannot delete the last admin user.")
        conn.execute("DELETE FROM users WHERE username = ?;", (clean,))
        conn.commit()


def update_user(db_path: Path, username: str, *, role: str | None = None,
                employee_id: str | None = None, password: str | None = None,
                iterations: int = _PBKDF2_ITERATIONS) -> None:
    clean = username.strip()
    with open_conn(db_path) as conn:
        exists = conn.execute(
            "SELECT role FROM users WHERE username = ?;", (clean,)
        ).fetchone()
        if exists is None:
            raise ValueError("User does not exist.")
        if role is not None:
            role_value = normalize_role(role)
            if exists["role"] == "Admin" and role_value != "Admin" and _admin_count(conn) <= 1:
                raise ValueError("Cannot demote the last admin user.")
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE username = ?;",
                (role_value, datetime.now().isoformat(), clean),
            )
        if employee_id is not None:
            conn.execute(
                "UPDATE users SET employee_id = ?, updated_at = ? WHERE username = ?;",
                (employee_id.strip(), datetime.now().isoformat(), clean),
            )
        if password is not None:
            salt = secrets.token_bytes(16)
            pw_hash = _hash(password, salt, iterations)
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, updated_at = ? WHERE username = ?;",
                (pw_hash, salt, datetime.now().isoformat(), clean),
            )
        conn.commit()


def change_password(db_path: Path, username: str, new_password: str,
                    iterations: int = _PBKDF2_ITERATIONS) -> None:
    clean = username.strip()
    salt = secrets.token_bytes(16)
    pw_hash = _hash(new_password, salt, iterations)
    with open_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, updated_at = ? WHERE username = ?;",
            (pw_hash, salt, datetime.now().isoformat(), clean),
        )
        if cur.rowcount == 0:
            raise ValueError("User does not exist.")
        conn.commit()


def list_users(db_path: Path) -> list[dict[str, str]]:
    with open_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT username, role, employee_id FROM users "
            "ORDER BY role DESC, username COLLATE NOCASE ASC;"
        ).fetchall()
        return [
            {
                "username": row["username"],
                "role": row["role"],
                "employee_id": str(row["employee_id"] or ""),
            }
            for row in rows
        ]
