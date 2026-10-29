"""Serial number of the 3316G module on each load channel, for the reports.

The module cannot report its own serial over the link (probed 2026-10-29), so
the Admin types it in Connections. Resolved on every call — a change applies to
the next run without a restart: Admin setting (database), then `.env`
`LOAD_SERIALS`, then `LOAD-CH<n>`. `LOAD_SERIALS` also sets the channel count.
"""

from __future__ import annotations

from config import LOAD_SERIALS
from drivers.factory import load_serial_key


def _stored() -> dict[str, str]:
    """Admin overrides from the database, or {} if it cannot be read (never raises)."""
    try:
        from logic.database_manager import DatabaseManager

        return DatabaseManager().list_instrument_connections()
    except Exception:
        return {}


def env_serial(slot: int) -> str:
    """The `.env` value for a channel (1-based), else `LOAD-CH<n>`."""
    if 1 <= slot <= len(LOAD_SERIALS) and LOAD_SERIALS[slot - 1].strip():
        return LOAD_SERIALS[slot - 1].strip()
    return f"LOAD-CH{slot}"


def channel_count() -> int:
    return max(1, len(LOAD_SERIALS))


def load_serials() -> list[str]:
    """One serial per channel, CH1 first."""
    stored = _stored()
    return [
        stored.get(load_serial_key(slot), "").strip() or env_serial(slot)
        for slot in range(1, channel_count() + 1)
    ]


def serial_for(slot: int) -> str:
    serials = load_serials()
    return serials[slot - 1] if 1 <= slot <= len(serials) else f"LOAD-CH{slot}"
