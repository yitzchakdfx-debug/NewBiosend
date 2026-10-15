"""Environment-variable configuration loader (single source of truth).

Loads a `.env` once, as early as possible, from (in order):
  1. next to the executable / src dir (install root),
  2. the parent of the install root (repo root in dev: src/..),
  3. whatever python-dotenv's find_dotenv() locates.

python-dotenv is a required dependency (see requirements.txt), not an optional
extra: without it `.env` cannot be read at all. If the import fails we do NOT
raise here — this module is imported for its side effect at import time, and
callers that only read optional values (e.g. version.py) must keep working. The
failure is recorded and reported by `get_required_str`, the only place where a
missing value is actually fatal.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from paths import _install_root  # pure-stdlib module, no import cycle

_loaded = False
# Set when python-dotenv is missing, so a "required variable" error can name the
# real cause instead of blaming a .env that was never opened.
_dotenv_import_failed = False
# The .env actually loaded, or None. Lets the error message say which file was
# read rather than telling the reader to go check "the" .env.
_dotenv_path: Path | None = None


def load_env_once() -> None:
    global _loaded, _dotenv_import_failed, _dotenv_path
    if _loaded:
        return
    _loaded = True
    try:
        from dotenv import load_dotenv, find_dotenv
    except ImportError:
        _dotenv_import_failed = True
        return
    for path in (_install_root() / ".env", _install_root().parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
            _dotenv_path = path
            return
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)
        _dotenv_path = Path(found)


load_env_once()


def get_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val is not None else default


def get_required_str(key: str) -> str:
    val = os.getenv(key)
    if val is None or not val.strip():
        if _dotenv_import_failed:
            # The variable may well be present in .env — we never read it. Point
            # at the dependency, not at the config file.
            raise RuntimeError(
                "Cannot read configuration: the 'python-dotenv' package is not "
                f"installed in the running interpreter ({sys.executable}), so "
                ".env was never loaded. This usually means the project venv is "
                "not active. Run the app with venv\\Scripts\\python.exe, or "
                "install dependencies with 'pip install -r requirements.txt'. "
                f"(While resolving required variable {key}.)"
            )
        where = f"in {_dotenv_path}" if _dotenv_path else "in .env"
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Set it {where} or in the process environment."
        )
    return val


def get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def get_list(key: str, default: list[str] | None = None) -> list[str]:
    val = os.getenv(key)
    if not val:
        return list(default or [])
    return [item.strip() for item in val.split(",") if item.strip()]
