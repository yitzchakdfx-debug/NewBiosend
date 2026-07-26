"""Filesystem path resolution that works both in dev and inside a PyInstaller bundle.

Two roots are exposed:

* :func:`resource_path` — read-only bundled files (icons, QSS, default scripts).
  When frozen with PyInstaller, these live under ``sys._MEIPASS``.
* :func:`user_data_path` — writable per-installation files (database, encrypted
  logs, generated reports, app.lock). When frozen, these live next to the
  ``DFX_Tester.exe`` so they persist across launches.

In dev mode both roots resolve to ``<repo>/src``, preserving the previous layout.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _bundle_root() -> Path:
    """Root that contains bundled read-only resources."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _install_root() -> Path:
    """Directory the EXE lives in (or src/ in dev) — used for writable data."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """Return a path to a bundled read-only resource."""
    return _bundle_root().joinpath(*parts)


def user_data_path(*parts: str) -> Path:
    """Return a path inside the writable per-installation data directory."""
    return _install_root().joinpath("data", *parts)


def user_tmp_path(*parts: str) -> Path:
    """Return a path inside the app's dedicated scratch directory (cleared on startup)."""
    return _install_root().joinpath("tmp", *parts)


_SEED_FILES = ("limits.json", "sequence.tst", "demo_system.tst")
_seeded = False


def ensure_user_data_seeded() -> None:
    """Copy bundled seed files into the user data dir if they aren't there yet."""
    global _seeded
    if _seeded:
        return
    target_dir = user_data_path()
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in _SEED_FILES:
        dest = target_dir / name
        if dest.exists():
            continue
        src = resource_path("data", name)
        if src.is_file():
            try:
                shutil.copyfile(src, dest)
            except OSError:
                pass
    _seeded = True
