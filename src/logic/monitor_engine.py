"""Background monitor loop — polls real hardware or simulates readings."""

from __future__ import annotations

import random
import threading

from PySide6.QtCore import QThread, Signal


class MonitorThread(QThread):
    """Emits voltage/current readings on a fixed interval.

    When simulate=True (mock mode): generates synthetic random values.
    When simulate=False (real mode): polls the load driver via measvoltage /
    meascurrent. Pause/resume disconnect and reconnect the driver so the test
    thread can have exclusive VISA access during a run.
    """

    values_updated = Signal(dict)

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
        """Reconnect (real mode) and restart polling."""
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

        while not self._stop_requested:
            if self._paused:
                self.msleep(100)
                continue

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

    def _connect_driver(self) -> None:
        from drivers.factory import create_driver
        from drivers.mock_hardware import MockHardware

        drv = create_driver()
        try:
            drv.connect()
        except Exception:
            drv = MockHardware()
            drv.connect()

        with self._driver_lock:
            self._driver = drv

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
            return 0.0, 0.0
