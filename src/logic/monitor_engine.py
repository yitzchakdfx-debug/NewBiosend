"""Background monitor loop — polls real hardware or simulates readings."""

from __future__ import annotations

import random
import threading

from PySide6.QtCore import QThread, Signal

# How often to retry the hardware connection while disconnected. Long enough
# that a failed attempt's timeout does not stall the poll loop, short enough that
# replugging a cable restores readings within a few seconds.
_MONITOR_RECONNECT_INTERVAL_MS = 3000
# Connect timeout for the monitor. Shorter than a test run's: this is a
# background readout and must never block pause/resume for seconds.
_MONITOR_CONNECT_TIMEOUT_MS = 1200


class MonitorThread(QThread):
    """Emits voltage/current readings on a fixed interval.

    When simulate=True (mock mode): generates synthetic random values.
    When simulate=False (real mode): polls the load driver via measvoltage /
    meascurrent. Pause/resume disconnect and reconnect the driver so the test
    thread can have exclusive VISA access during a run.

    In real mode the connection is self-healing: a read failure discards the
    driver and the loop reconnects every `_MONITOR_RECONNECT_INTERVAL_MS`, so
    unplugging and replugging the instrument recovers without a restart. There is
    no MockHardware fallback — inventing readings for a disconnected instrument
    is worse than showing zeros.
    """

    values_updated = Signal(dict)
    #: Emitted when polling recovers after a disconnection.
    connection_restored = Signal()

    def __init__(
        self,
        parent: object | None = None,
        *,
        interval_ms: int = 500,
        simulate: bool = True,
    ) -> None:
        super().__init__(parent)
        self._interval_ms = max(1, interval_ms)
        self._simulate = simulate
        self._stop_requested = False
        self._paused = False
        self._driver = None
        self._driver_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public control API (called from the main thread)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Stop polling and release the hardware connection."""
        self._paused = True
        with self._driver_lock:
            if self._driver is not None:
                try:
                    self._driver.disconnect()
                except Exception:
                    pass
                self._driver = None

    def resume(self) -> None:
        """Reconnect (real mode) and restart polling.

        A failed connect is not fatal: polling resumes regardless and `run()`
        keeps retrying, so the panel recovers by itself once the instrument is
        back. This is why returning to the main screen with the cable still out
        no longer leaves the monitor permanently dead.
        """
        if not self._simulate:
            self._connect_driver()
        self._paused = False

    def stop(self) -> None:
        self._stop_requested = True
        self.wait()

    # ------------------------------------------------------------------
    # QThread internals
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self._simulate:
            self._connect_driver()

        since_retry_ms = 0
        while not self._stop_requested:
            if self._paused:
                self.msleep(100)
                continue

            # Reconnect on a schedule while disconnected, so plugging the cable
            # back in restores live readings on its own — no restart, no
            # re-entering the screen. Throttled: a connect attempt against an
            # absent instrument costs its timeout, and doing that every poll
            # would stall the loop and make the UI feel slow.
            if not self._simulate and self._driver is None:
                if since_retry_ms >= _MONITOR_RECONNECT_INTERVAL_MS:
                    since_retry_ms = 0
                    if self._connect_driver():
                        self.connection_restored.emit()
                else:
                    since_retry_ms += self._interval_ms

            v, a = self._read_values()
            self.values_updated.emit({"V": v, "A": a})
            self.msleep(self._interval_ms)

        with self._driver_lock:
            if self._driver is not None:
                try:
                    self._driver.disconnect()
                except Exception:
                    pass
                self._driver = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _connect_driver(self) -> bool:
        """Open a driver for polling. True on success.

        Deliberately no MockHardware fallback. That fallback used to be
        permanent: unplug the instrument, and the live monitor silently became a
        simulator for the rest of the session, so replugging the cable never
        restored real readings and the panel kept showing invented values. On
        failure the driver stays None and `run()` retries on its own schedule.
        """
        from drivers.factory import create_driver

        # Short timeout, no identity probe: this is a background readout, so it
        # must not block the UI thread's pause/resume for seconds at a time.
        drv = create_driver(
            timeout_ms=_MONITOR_CONNECT_TIMEOUT_MS,
            probe_identity=False,
            reconnect_on_error=False,
        )
        try:
            drv.connect()
        except Exception:
            try:
                drv.disconnect()
            except Exception:
                pass
            with self._driver_lock:
                self._driver = None
            return False

        with self._driver_lock:
            self._driver = drv
        return True

    def _read_values(self) -> tuple[float, float]:
        if self._simulate:
            v = round(12.0 + random.uniform(-0.15, 0.15), 2)
            a = round(0.5 + random.uniform(-0.05, 0.05), 2)
            return v, a

        with self._driver_lock:
            drv = self._driver
        if drv is None:
            return 0.0, 0.0
        try:
            if hasattr(drv, "activate_slot"):
                drv.activate_slot(1)  # type: ignore[attr-defined]
            v = round(drv.execute_command("measvoltage", []), 2)
            a = round(drv.execute_command("meascurrent", []), 2)
            return v, a
        except Exception:
            # Drop the dead driver so `run()` reconnects instead of polling a
            # broken handle forever. Without this the panel sat at 0.00 V for the
            # rest of the session even after the cable was plugged back in.
            self._discard_driver()
            return 0.0, 0.0

    def _discard_driver(self) -> None:
        with self._driver_lock:
            drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.disconnect()
            except Exception:
                pass
