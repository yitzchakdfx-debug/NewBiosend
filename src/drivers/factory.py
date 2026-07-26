"""Hardware driver selection helpers."""

from __future__ import annotations

from config import HARDWARE_BACKEND

from drivers.base_driver import BaseDriver
from drivers.mock_hardware import MockHardware


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
    backend = HARDWARE_BACKEND.strip().lower()
    if backend in {"prodigit", "visa", "usb"}:
        from drivers.prodigit_visa import ProdigitVisaDriver

        return ProdigitVisaDriver(
            timeout_ms=timeout_ms,
            probe_identity=probe_identity,
            reconnect_on_error=reconnect_on_error,
        )
    return MockHardware()
