"""Table creation and live migrations — run once per process via DatabaseManager."""

from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import sqlite3
from datetime import datetime

from logic.db.connection import open_conn
from logic.roles import ALL_ROLES

#: Column list of the `users` table, shared by the CREATE and the rebuild
#: migration so the two can never drift apart.
_USER_COLUMNS = (
    "id",
    "username",
    "password_hash",
    "salt",
    "role",
    "employee_id",
    "created_at",
    "updated_at",
)


def _role_check_sql() -> str:
    """Render the CHECK(role IN (...)) clause from the canonical role list."""
    for role in ALL_ROLES:
        # Roles come from a code-level enum, but this DDL is built by string
        # interpolation — refuse anything that could carry SQL syntax.
        if not re.fullmatch(r"[A-Za-z ]+", role):
            raise ValueError(f"role name is not safe to interpolate into DDL: {role!r}")
    values = ",".join(f"'{role}'" for role in ALL_ROLES)
    return f"CHECK(role IN ({values}))"


def _users_table_sql(table_name: str = "users") -> str:
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash BLOB NOT NULL,
            salt BLOB NOT NULL,
            role TEXT NOT NULL {_role_check_sql()},
            employee_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );"""


def create_tables(db_path, default_admin_username: str,
                  default_admin_password: str, default_admin_employee_id: str,
                  pbkdf2_iterations: int) -> None:
    from pathlib import Path
    path = Path(db_path)
    with open_conn(path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS test_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator TEXT,
            part_number TEXT,
            serial_number TEXT,
            overall_passed INTEGER,
            start_time TEXT,
            end_time TEXT
        );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            test_name TEXT,
            value REAL,
            min_val REAL,
            max_val REAL,
            unit TEXT,
            passed INTEGER,
            FOREIGN KEY(run_id) REFERENCES test_runs(id)
        );"""
        )
        conn.execute(_users_table_sql())
        _migrate_role_check(conn, path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS test_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL,
            uut_type TEXT NOT NULL,
            version_name TEXT NOT NULL,
            test_content TEXT NOT NULL,
            connection_params TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            UNIQUE(test_name, version_name)
        );"""
        )
        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(test_versions);").fetchall()
        }
        if "connection_params" not in existing_cols:
            conn.execute(
                "ALTER TABLE test_versions "
                "ADD COLUMN connection_params TEXT NOT NULL DEFAULT '';"
            )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            employee_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS instrument_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_name TEXT UNIQUE NOT NULL COLLATE NOCASE,
            connection_string TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        );"""
        )
        _seed_admin(conn, default_admin_username, default_admin_password,
                    default_admin_employee_id, pbkdf2_iterations)
        conn.commit()


def _current_role_check_values(conn: sqlite3.Connection) -> set[str] | None:
    """Roles allowed by the live `users` table's CHECK clause, or None if absent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users';"
    ).fetchone()
    if row is None or not row["sql"]:
        return None
    match = re.search(r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)\s*\)", row["sql"], re.IGNORECASE)
    if match is None:
        return None
    return {value.strip().strip("'\"") for value in match.group(1).split(",") if value.strip()}


def _migrate_role_check(conn: sqlite3.Connection, db_path) -> None:
    """Widen the `users.role` CHECK constraint to the current role list.

    `CREATE TABLE IF NOT EXISTS` never revisits an existing table, so a database
    created before a role was added keeps the old CHECK forever and rejects the
    new role with an IntegrityError. SQLite cannot ALTER a CHECK constraint, so
    the table is rebuilt. The file is backed up first — this is the one
    migration that drops and recreates a table holding credentials.
    """
    allowed = _current_role_check_values(conn)
    if allowed is None or allowed == set(ALL_ROLES):
        return  # no CHECK to widen, or already current

    backup = db_path.with_name(
        f"{db_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    conn.commit()
    shutil.copy2(db_path, backup)

    columns = ", ".join(_USER_COLUMNS)
    # Foreign keys must be toggled outside a transaction for the pragma to take
    # effect; no table references users, but the rename is still done safely.
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("BEGIN;")
        conn.execute(_users_table_sql("users_new"))
        conn.execute(f"INSERT INTO users_new ({columns}) SELECT {columns} FROM users;")
        conn.execute("DROP TABLE users;")
        conn.execute("ALTER TABLE users_new RENAME TO users;")
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")

    integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(
            f"users table rebuild left the database inconsistent ({integrity}); "
            f"a pre-migration backup is at {backup}"
        )


def _seed_admin(conn: sqlite3.Connection, username: str, password: str,
                employee_id: str, iterations: int) -> None:
    now = datetime.now().isoformat()
    salt = secrets.token_bytes(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    conn.execute(
        """
        INSERT OR IGNORE INTO users
        (username, password_hash, salt, role, employee_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (username, pw_hash, salt, "Admin", employee_id, now, now),
    )
