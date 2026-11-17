"""Append-only Fernet-encrypted JSON log lines for traceability (daily files)."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import LOG_ENCRYPTION_PASSWORD

_SALT = b"ATE-LOG-SALT-v2"
#: The salt used up to 1.0.0-RC2 (2026-11-17), kept only to read and convert
#: logs written before it was replaced (`migrate_legacy_logs`, `_read_file`).
_LEGACY_SALT = bytes([68, 70, 88, 45, 65, 84, 69, 45, 83, 65, 76, 84, 45, 118, 49])
_ITERATIONS = 200_000
_KEY_LENGTH = 32
#: Written to the logs folder once every log there uses the current salt.
_MIGRATED_MARKER = ".log_key_v2"
_LEGACY_BACKUP_DIR = "legacy_backup"

_logger_singleton: SecureLogger | None = None


def _derive_fernet(password: str, salt: bytes = _SALT) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=salt,
        iterations=_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


def migrate_legacy_logs(logs_dir: Path, password: str) -> int:
    """Re-encrypt log lines written with the legacy salt; returns lines converted.

    Runs once per logs folder (a marker file records it), at GUI start before
    anything is logged: the GUI is the only writer and runs as a single
    instance, so no line can be appended while a file is rewritten. Each
    converted file is first copied to ``legacy_backup/`` and then replaced
    atomically. Lines already on the current salt are kept as they are, and a
    line neither key can read is kept unchanged rather than dropped.
    """
    logs_dir = Path(logs_dir)
    marker = logs_dir / _MIGRATED_MARKER
    if marker.exists() or not logs_dir.is_dir():
        return 0
    current = _derive_fernet(password)
    legacy = _derive_fernet(password, _LEGACY_SALT)
    converted = 0
    for path in sorted(logs_dir.glob("sys_*.dat")):
        out: list[bytes] = []
        changed = 0
        for raw in path.read_bytes().splitlines():
            token = raw.strip()
            if not token:
                continue
            try:
                current.decrypt(token)
                out.append(token)
                continue
            except InvalidToken:
                pass
            try:
                out.append(current.encrypt(legacy.decrypt(token)))
                changed += 1
            except InvalidToken:
                out.append(token)
        if not changed:
            continue
        backup = logs_dir / _LEGACY_BACKUP_DIR / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        tmp = path.with_suffix(".dat.tmp")
        tmp.write_bytes(b"".join(line + b"\n" for line in out))
        tmp.replace(path)
        converted += changed
    marker.write_text(
        f"Logs use the current key since {datetime.now():%Y-%m-%d %H:%M:%S}; "
        f"{converted} line(s) converted.\n",
        encoding="utf-8",
    )
    return converted


class SecureLogger:
    """Encrypt one JSON record per line under ``sys_YYYYMMDD.dat``; append-only."""

    def __init__(self, logs_dir: Path, password: str) -> None:
        self._dir = Path(logs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fernet = _derive_fernet(password)
        self._lock = Lock()
        self._current_date_key: str | None = None
        self._current_path: Path | None = None

    def _path_for_now(self) -> Path:
        key = datetime.now().strftime("%Y%m%d")
        if key != self._current_date_key or self._current_path is None:
            self._current_date_key = key
            self._current_path = self._dir / f"sys_{key}.dat"
        return self._current_path

    def log(self, category: str, payload: Any) -> None:
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "payload": payload,
        }
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        token = self._fernet.encrypt(line.encode("utf-8"))
        path = self._path_for_now()
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as fh:
                fh.write(token + b"\n")

    def log_system_event(
        self, username: str, action: str, details: str = ""
    ) -> None:
        payload = (
            f"[System] User: {username} | Action: {action} | Details: {details}"
        )
        self.log("system", payload)

    def day_file_path(self, day: date) -> Path:
        return self._dir / f"sys_{day.strftime('%Y%m%d')}.dat"

    def read_day(self, day: date, password: str) -> list[dict[str, Any]]:
        """Decrypt all records for a local calendar day from ``sys_YYYYMMDD.dat``."""
        path = self.day_file_path(day)
        if not path.is_file():
            return []
        return self._read_file(path, _derive_fernet(password), _derive_fernet(password, _LEGACY_SALT))

    @staticmethod
    def _read_file(path: Path, fernet: Fernet, legacy: Fernet | None = None) -> list[dict[str, Any]]:
        """Decrypt every readable line. `legacy` also opens lines on the old salt,
        e.g. a .dat file copied from a station that has not been updated."""
        out: list[dict[str, Any]] = []
        with path.open("rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                for key in (fernet, legacy):
                    if key is None:
                        continue
                    try:
                        out.append(json.loads(key.decrypt(raw).decode("utf-8")))
                        break
                    except (InvalidToken, json.JSONDecodeError):
                        continue
        return out

    def read_file(self, file_path: Path, password: str) -> list[dict[str, Any]]:
        """Decrypt all records from an arbitrary ``.dat`` file."""
        return self._read_file(
            Path(file_path), _derive_fernet(password), _derive_fernet(password, _LEGACY_SALT)
        )

    def read_all(self, password: str) -> list[dict[str, Any]]:
        """Decrypt every daily file under the logger directory (newest date last)."""
        if not self._dir.is_dir():
            return []
        current, legacy = _derive_fernet(password), _derive_fernet(password, _LEGACY_SALT)
        out: list[dict[str, Any]] = []
        for p in sorted(self._dir.glob("sys_*.dat")):
            out.extend(self._read_file(p, current, legacy))
        return out


def get_secure_logger() -> SecureLogger:
    """Process-wide singleton for encrypted system logs."""
    global _logger_singleton
    if _logger_singleton is None:
        from paths import user_data_path
        path = user_data_path("logs")
        _logger_singleton = SecureLogger(path, LOG_ENCRYPTION_PASSWORD)
    return _logger_singleton
