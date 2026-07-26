"""Qt-free mock hardware driver for ATE simulation.

Exposes a generic `execute_command(name, args) -> float` interface used by
the script-driven runner. Returns a measurement value for commands listed in
`MEASUREMENT_COMMANDS`; for side-effect commands (`setvoltage`, `relay`,
`getid`) the return value is ignored by the runner.
"""

from __future__ import annotations

import random
import time
from typing import ClassVar

from drivers.base_driver import BaseDriver, UnknownCommandError


class MockHardware(BaseDriver):
    """Simulates hardware instrument responses without any Qt dependencies."""

    MEASUREMENT_COMMANDS: ClassVar[frozenset[str]] = frozenset({
        "readchannel", "readinput", "checkpolarity",
        "measvoltage", "meascurrent", "measpower",
        "getmode", "getload",
        "getcurrent", "getvoltage", "getresistance", "getpower",
    })

    _CHANNEL_NOMINALS: ClassVar[dict[int, tuple[float, float, int]]] = {
        0: (5.0, 0.05, 3),    # 5 V rail, +/- ~50 mV jitter, 3 decimals
        1: (12.0, 0.10, 3),   # 12 V rail, +/- ~100 mV jitter
        2: (28.0, 0.20, 3),   # 28 V rail, +/- ~200 mV jitter
        3: (1.2, 0.30, 3),    # 1.2 A current, wider jitter
        10: (0.0, 0.05, 3),   # 0 V rail, +/- ~50 mV jitter
    }
    _OUT_OF_SPEC_PROB: ClassVar[float] = 0.25

    def __init__(self) -> None:
        self.connected = False
        self._rng = random.Random()
        self._mode = "CR"
        self._load_on = False
        self._set_current = 0.0
        self._set_voltage = 0.0
        self._set_resistance = 12.5
        self._set_power = 0.0

    def activate_slot(self, slot_index: int, *, load_serial_number: str = "") -> None:
        """No-op slot selection for the mock driver."""
        time.sleep(0.05)

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    @property
    def measurement_commands(self) -> frozenset[str]:
        return self.MEASUREMENT_COMMANDS

    def execute_command(self, command: str, args: list[str]) -> float:
        cmd = command.lower()

        if cmd == "readchannel":
            channel = int(args[0]) if args else 0
            return self._read_channel(channel)

        if cmd == "readinput":
            # Return nominal 24 V so the input-connection check always passes.
            time.sleep(self._rng.uniform(0.05, 0.15))
            return 24.0

        if cmd == "checkpolarity":
            # Spec §1.1.7.1 reads polarity as a signed voltage, so return volts
            # rather than a +/-1 flag: mock runs then exercise the same units and
            # noise-floor threshold the real driver does. Nominal 24 V matches
            # `readinput` above, so a mock polarity check passes.
            time.sleep(0.05)
            return 24.0

        if cmd == "setmode":
            self._mode = (args[0] if args else "CR").upper()
            return 0.0

        if cmd == "getmode":
            return {"CC": 1.0, "CV": 2.0, "CR": 3.0, "CP": 4.0}.get(self._mode, 3.0)

        if cmd == "loadon":
            self._load_on = True
            return 1.0

        if cmd == "loadoff":
            self._load_on = False
            return 0.0

        if cmd == "getload":
            return 1.0 if self._load_on else 0.0

        if cmd == "setcurrent":
            self._set_current = float(args[0]) if args else 0.0
            return self._set_current

        if cmd == "getcurrent":
            return self._set_current

        if cmd == "measvoltage":
            time.sleep(self._rng.uniform(0.05, 0.15))
            return round(24.0 + self._rng.uniform(-0.5, 0.5), 3)

        if cmd == "meascurrent":
            time.sleep(self._rng.uniform(0.05, 0.15))
            return round(self._set_current + self._rng.uniform(-0.05, 0.05), 3)

        if cmd == "measpower":
            time.sleep(self._rng.uniform(0.05, 0.15))
            v = 24.0 + self._rng.uniform(-0.5, 0.5)
            i = self._set_current + self._rng.uniform(-0.05, 0.05)
            return round(v * i, 2)

        if cmd == "setresistance":
            self._set_resistance = float(args[0]) if args else 0.0
            time.sleep(0.05)
            return self._set_resistance

        if cmd == "getresistance":
            return self._set_resistance

        if cmd == "setvoltage":
            self._set_voltage = float(args[0]) if args else 0.0
            return self._set_voltage

        if cmd == "getvoltage":
            return self._set_voltage

        if cmd == "setpower":
            self._set_power = float(args[0]) if args else 0.0
            return self._set_power

        if cmd == "getpower":
            return self._set_power

        if cmd in ("relay", "getid"):
            time.sleep(0.05)
            return 0.0

        if cmd == "setlogic":
            # args: [<line: int>, <on|off>]  — side-effect only, no measurement.
            time.sleep(0.05)
            return 0.0

        raise UnknownCommandError(f"Unknown hardware command: {command!r}")

    def _read_channel(self, channel: int) -> float:
        nominal, jitter, decimals = self._CHANNEL_NOMINALS.get(
            channel, (50.0, 35.0, 2)
        )
        time.sleep(self._rng.uniform(0.2, 0.6))
        outside = self._rng.random() < self._OUT_OF_SPEC_PROB
        spread = jitter * (3.0 if outside else 1.0)
        value = nominal + self._rng.uniform(-spread, spread)
        return round(value, decimals)
