"""SQLite persistence façade — thin wrapper over ``logic/db/`` sub-modules.

All callers import this module.  The sub-modules under ``logic/db/`` hold the
actual SQL; this class keeps the public API stable across that refactor.
"""

from __future__ import annotations

from pathlib import Path

from config import DEFAULT_ADMIN_EMPLOYEE_ID, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME
from logic.db import audit, connections, schema, test_runs, test_versions, users
from logic.models import TestRunRecord, normalize_role
from paths import user_data_path


class DatabaseManager:
    """Public API for all database operations.

    Constructing this class the first time in a process triggers the full
    schema bootstrap (CREATE TABLE IF NOT EXISTS, migrations, admin seed).
    Subsequent constructions skip the bootstrap via a class-level flag.
    """

    _PBKDF2_ITERATIONS = 200_000
    _schema_ready: bool = False

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or user_data_path("database.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        if not DatabaseManager._schema_ready:
            schema.create_tables(
                self._db_path,
                DEFAULT_ADMIN_USERNAME,
                DEFAULT_ADMIN_PASSWORD,
                DEFAULT_ADMIN_EMPLOYEE_ID,
                self._PBKDF2_ITERATIONS,
            )
            DatabaseManager._schema_ready = True

    # --- Auth helpers kept inline so AuthManager callers are unaffected ------

    def _validate_role(self, role: str) -> str:
        return normalize_role(role)

    # --- User management -----------------------------------------------------

    def verify_login(self, username: str, password: str) -> dict | None:
        return users.verify_login(self._db_path, username, password,
                                  self._PBKDF2_ITERATIONS)

    def create_user(self, username: str, password: str, role: str,
                    employee_id: str) -> None:
        users.create_user(self._db_path, username, password, role, employee_id,
                          self._PBKDF2_ITERATIONS)

    def delete_user(self, username: str) -> None:
        users.delete_user(self._db_path, username)

    def update_user(self, username: str, *, role: str | None = None,
                    employee_id: str | None = None,
                    password: str | None = None) -> None:
        users.update_user(self._db_path, username, role=role,
                          employee_id=employee_id, password=password,
                          iterations=self._PBKDF2_ITERATIONS)

    def update_role(self, username: str, role: str) -> None:
        users.update_user(self._db_path, username, role=role,
                          iterations=self._PBKDF2_ITERATIONS)

    def change_password(self, username: str, new_password: str) -> None:
        users.change_password(self._db_path, username, new_password,
                              self._PBKDF2_ITERATIONS)

    def list_users(self) -> list[dict[str, str]]:
        return users.list_users(self._db_path)

    # --- Test versions -------------------------------------------------------

    def add_test_version(self, test_name: str, uut_type: str, version_name: str,
                         content: str, created_by: str,
                         connection_params: str = "") -> int:
        return test_versions.add_test_version(
            self._db_path, test_name, uut_type, version_name,
            content, created_by, connection_params,
        )

    def list_test_versions(self) -> list[dict[str, str | int]]:
        return test_versions.list_test_versions(self._db_path)

    def get_test_version(self, version_id: int) -> dict | None:
        return test_versions.get_test_version(self._db_path, version_id)

    def delete_test_version(self, version_id: int) -> None:
        test_versions.delete_test_version(self._db_path, version_id)

    def version_exists(self, test_name: str, version_name: str) -> bool:
        return test_versions.version_exists(self._db_path, test_name, version_name)

    # --- Test runs -----------------------------------------------------------

    def save_run(self, record: TestRunRecord) -> int:
        return test_runs.save_run(self._db_path, record)

    # --- Instrument connections -----------------------------------------------

    def list_instrument_connections(self) -> dict[str, str]:
        return connections.list_connections(self._db_path)

    def upsert_instrument_connection(
        self, instrument_name: str, connection_string: str, updated_by: str
    ) -> None:
        connections.upsert_connection(
            self._db_path, instrument_name, connection_string, updated_by
        )

    # --- Audit trail ---------------------------------------------------------

    def log_audit_action(self, action: str, *, username: str = "",
                         employee_id: str = "", details: str = "") -> None:
        audit.log_audit_action(self._db_path, action, username=username,
                                employee_id=employee_id, details=details)

    def get_audit_logs(self, *, limit: int = 1000) -> list[dict[str, str]]:
        return audit.get_audit_logs(self._db_path, limit=limit)
