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

_SALT = b"DFX-ATE-SALT-v1"
_ITERATIONS = 200_000
_KEY_LENGTH = 32

_logger_singleton: SecureLogger | None = None


def _derive_fernet(password: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=_SALT,
        iterations=_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


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
        other = _derive_fernet(password)
        path = self.day_file_path(day)
        if not path.is_file():
            return []
        return self._read_file(path, other)

    @staticmethod
    def _read_file(path: Path, fernet: Fernet) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with path.open("rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    plain = fernet.decrypt(raw).decode("utf-8")
                    out.append(json.loads(plain))
                except (InvalidToken, json.JSONDecodeError):
                    continue
        return out

    def read_file(self, file_path: Path, password: str) -> list[dict[str, Any]]:
        """Decrypt all records from an arbitrary ``.dat`` file."""
        fernet = _derive_fernet(password)
        return self._read_file(Path(file_path), fernet)

    def read_all(self, password: str) -> list[dict[str, Any]]:
        """Decrypt every daily file under the logger directory (newest date last)."""
        other = _derive_fernet(password)
        if not self._dir.is_dir():
            return []
        paths = sorted(self._dir.glob("sys_*.dat"))
        out: list[dict[str, Any]] = []
        for p in paths:
            out.extend(self._read_file(p, other))
        return out


def get_secure_logger() -> SecureLogger:
    """Process-wide singleton for encrypted system logs."""
    global _logger_singleton
    if _logger_singleton is None:
        from paths import user_data_path
        path = user_data_path("logs")
        _logger_singleton = SecureLogger(path, LOG_ENCRYPTION_PASSWORD)
    return _logger_singleton
