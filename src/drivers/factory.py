"""Hardware driver selection helpers.

Station configuration has two layers. `.env` is the bootstrap default, shipped
next to the EXE; the database holds an optional Admin override set from the
Connections dialog. The override wins when present, so a deployed station is
configurable without editing files, while an untouched one behaves exactly as
its `.env` says.

The lookup lives here rather than in `config` because `config` cannot import the
database: `logic.database_manager` imports `config`, so the dependency would be
circular.

Both settings are resolved ONCE per process and cached. They must not change
under a running app: the 3300G accepts a single TCP connection, and the shutdown `loadoff` safety path builds a driver of
its own -- if that path reached a different instrument (or a simulator) than the
run used, it would report "Load off confirmed on exit." while a real load stayed
energized with a UUT attached. The Connections dialog tells the admin a change
applies on the next launch; caching is what makes that true.
"""

from __future__ import annotations

from config import (
    HARDWARE_BACKEND,
    PRODIGIT_VISA_RESOURCE,
    REAL_BACKENDS,
    SIMULATED_BACKENDS,
)

from drivers.base_driver import BaseDriver
from drivers.mock_hardware import MockHardware

#: Reserved `instrument_connections` keys for settings that are not instrument
#: addresses. Double-underscored so they cannot collide with a real instrument
#: name from `config.INSTRUMENTS`.
BACKEND_SETTING_KEY = "__hardware_backend__"

#: Row carrying the electronic load's VISA resource string. Reserved-named like
#: the key above: it previously used a human label ("Electronic Load (VISA)"),
#: which shares a key space with `config.INSTRUMENTS` -- an INSTRUMENTS entry of
#: the same name would alias the two, and the dialog's per-instrument loop would
#: overwrite the dedicated field's value.
LOAD_RESOURCE_KEY = "__load_visa_resource__"

#: Admin-chosen report folders (see `logic.report_paths`). Empty = default.
RESULTS_DIR_KEY = "__results_dir__"
XML_DIR_KEY = "__xml_dir__"


def _stored_settings() -> dict[str, str]:
    """Admin overrides from the database, or {} if it cannot be read.

    Never raises. This runs on paths that legitimately have no usable database
    yet -- a fresh install's channel scan, or the very first launch before the
    schema exists -- and a configuration lookup must never be the thing that
    stops the station from starting. Any failure falls back to `.env`.
    """
    try:
        from logic.database_manager import DatabaseManager

        return DatabaseManager().list_instrument_connections()
    except Exception:
        return {}


#: Backend resolved once per process. `None` until first asked.
#
# Deliberately cached, unlike the load address. The backend must be STABLE for
# the process lifetime, because two things assume it is: the 3300G accepts only one
# TCP connection, and the exit
# `loadoff` safety path builds a driver of its own. Re-resolving per call meant a
# mid-session switch to the simulator could hand the exit path a MockHardware --
# which reports "Load off confirmed on exit." while a real load stays energized
# with a UUT attached. The Connections dialog already promises the change applies
# on restart; this makes that promise true.
_backend_cache: str | None = None


def resolved_backend() -> str:
    """Effective backend name: database override if valid, else `.env`.

    Resolved on first call and cached for the process lifetime -- see
    `_backend_cache`. An invalid stored value is ignored rather than fatal: `.env`
    is validated at import and aborts startup on a typo, but a bad database row
    must not brick a station mid-shift, and the dialog that writes it only offers
    valid choices, so a bad value here means hand-edited data or an older build.
    """
    global _backend_cache
    if _backend_cache is None:
        stored = _stored_settings().get(BACKEND_SETTING_KEY, "").strip().lower()
        _backend_cache = (
            stored if stored in REAL_BACKENDS | SIMULATED_BACKENDS else HARDWARE_BACKEND
        )
    return _backend_cache


def resolve_station_config() -> None:
    """Fill both caches now, so nothing later can observe a mid-session change.

    Both resolvers cache on first read, which makes the *timing* of that first
    read matter: if it happened after an admin saved a new backend, the process
    would pick up the change it is supposed to defer to the next launch. Call this
    once while the main window is opening -- before the Connections dialog is
    reachable -- so the values are fixed for the process lifetime.
    """
    resolved_backend()
    resolved_visa_resource()


def reset_config_cache() -> None:
    """Forget both cached settings. For tests only -- the app never switches live."""
    global _backend_cache, _resource_cache
    _backend_cache = None
    _resource_cache = None


def is_simulated() -> bool:
    """Whether every component must use the simulator.

    The single answer for the whole app. Two call sites deciding this
    independently is what previously let an unrecognised value simulate the
    test runner while the (since removed) idle monitor dialled the real
    instrument.
    """
    return resolved_backend() in SIMULATED_BACKENDS


#: Load address resolved once per process, for the same reason as the backend.
_resource_cache: str | None = None


def resolved_visa_resource() -> str:
    """Electronic load's VISA resource: database override if set, else `.env`.

    Cached for the process lifetime alongside the backend -- the exit `loadoff`
    path must reach the instrument the run actually used, not whatever a
    mid-session edit points at. Empty means "auto-discover", which the Prodigit
    driver handles itself.
    """
    global _resource_cache
    if _resource_cache is None:
        stored = _stored_settings().get(LOAD_RESOURCE_KEY, "").strip()
        _resource_cache = stored or PRODIGIT_VISA_RESOURCE.strip()
    return _resource_cache


def create_driver(
    *,
    timeout_ms: float | None = None,
    probe_identity: bool = True,
    reconnect_on_error: bool = True,
) -> BaseDriver:
    """Build the configured hardware driver.

    `reconnect_on_error=False` disables the per-I/O reconnect-and-retry budget.
    Use it for the pre-test channel scan, where empty slots are expected and
    retrying each one would cost seconds; leave it on for test runs, which need
    to survive a transient comms drop mid-measurement.
    """
    if not is_simulated():
        from drivers.prodigit_visa import ProdigitVisaDriver

        return ProdigitVisaDriver(
            timeout_ms=timeout_ms,
            probe_identity=probe_identity,
            reconnect_on_error=reconnect_on_error,
            resource_name=resolved_visa_resource(),
        )
    return MockHardware()
