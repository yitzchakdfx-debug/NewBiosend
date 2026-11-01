"""Where reports are archived: Admin setting (database), then `.env`, then default.

Resolved on every call, not cached: a folder change applies to the next report
without a restart. (The load address is cached for a safety reason — the exit
load-off must reach the instrument the run used — which does not apply here.)
"""

from __future__ import annotations

from pathlib import Path

from drivers.factory import RESULTS_DIR_KEY, XML_DIR_KEY
from paths import user_data_path


def _stored() -> dict[str, str]:
    """Admin overrides from the database, or {} if it cannot be read (never raises)."""
    try:
        from logic.database_manager import DatabaseManager

        return DatabaseManager().list_instrument_connections()
    except Exception:
        return {}


def local_results_dir() -> Path:
    """The station's own folder, also the fallback when a chosen one is unreachable."""
    return user_data_path("results")


def results_dir() -> Path:
    """Folder for the per-unit report folders (PDF + XML)."""
    stored = _stored().get(RESULTS_DIR_KEY, "").strip()
    return Path(stored) if stored else local_results_dir()


def mes_dir() -> Path | None:
    """The customer's MES (CAMSTAR) folder: Admin setting, else `XML_EXPORT_DIR`, else None.

    There is no local default: with no folder set the XML transfer service
    reports MES as unavailable and keeps the reports in Pending.
    """
    stored = _stored().get(XML_DIR_KEY, "").strip()
    if stored:
        return Path(stored)
    from config import XML_EXPORT_DIR

    return Path(XML_EXPORT_DIR.strip()) if XML_EXPORT_DIR.strip() else None

