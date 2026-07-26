"""Environment-variable configuration loader (single source of truth).

Loads a `.env` once, as early as possible, from (in order):
  1. next to the executable / src dir (install root),
  2. the parent of the install root (repo root in dev: src/..),
  3. whatever python-dotenv's find_dotenv() locates.

Degrades gracefully if python-dotenv is not installed (real env vars / defaults).
"""
from __future__ import annotations

import os
from pathlib import Path

from paths import _install_root  # pure-stdlib module, no import cycle

_loaded = False


def load_env_once() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from dotenv import load_dotenv, find_dotenv
    except ImportError:
        return
    for path in (_install_root() / ".env", _install_root().parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
            return
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)


load_env_once()


def get_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val is not None else default


def get_required_str(key: str) -> str:
    val = os.getenv(key)
    if val is None or not val.strip():
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            "Set it in .env or in the process environment."
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
