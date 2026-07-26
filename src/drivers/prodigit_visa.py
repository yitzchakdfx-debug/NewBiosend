"""Prodigit electronic-load driver over VISA/USB.

This driver is intentionally configurable because Prodigit installations can
vary by option card, firmware, and site wiring. The default command templates
come from ``config.py`` and can be overridden in ``.env`` without code changes.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from config import (
    PRODIGIT_QUERY_CURRENT,
    PRODIGIT_QUERY_CURRENT_SET,
    PRODIGIT_QUERY_ID,
    PRODIGIT_QUERY_INPUT_VOLT,
    PRODIGIT_QUERY_LOAD,
    PRODIGIT_QUERY_MODE,
    PRODIGIT_QUERY_POLARITY,
    PRODIGIT_QUERY_POWER,
    PRODIGIT_QUERY_POWER_SET,
    PRODIGIT_QUERY_RESISTANCE_SET,
    PRODIGIT_QUERY_VOLTAGE,
    PRODIGIT_QUERY_VOLTAGE_SET,
    PRODIGIT_SELECT_SLOT,
    PRODIGIT_SET_CURRENT,
    PRODIGIT_SET_LOAD,
    PRODIGIT_SET_MODE,
    PRODIGIT_SET_POWER,
    PRODIGIT_SET_RELAY,
    PRODIGIT_SET_RESISTANCE,
    PRODIGIT_SET_VOLTAGE,
    PRODIGIT_VISA_BACKEND,
    PRODIGIT_VISA_READ_TERM,
    PRODIGIT_VISA_RESOURCE,
    PRODIGIT_VISA_TIMEOUT_MS,
    PRODIGIT_VISA_WRITE_TERM,
)
from drivers.base_driver import BaseDriver, HardwareError, UnknownCommandError


# Seconds to wait after writing a CR resistance before reading it back. The
# load needs a moment to apply (and possibly re-range) a new value; reading too
# soon can return the previous setting and trip a false "mismatch".
_RESISTANCE_SETTLE_S = 0.2
# How many times to (re)write + verify the resistance before giving up.
_RESISTANCE_VERIFY_ATTEMPTS = 2

# If a VISA I/O op fails (e.g. the load drops the TCP socket mid-run), keep
# trying to reconnect and retry the op for this long before giving up and
# letting the error propagate (so the run aborts as before).
_RECONNECT_BUDGET_S = 5.0
# Pause between a failed op / reconnect attempt and the next retry.
_RECONNECT_RETRY_DELAY_S = 0.5

# Magnitude reported when a polarity query answers with a word ("positive",
# "reverse", ...) rather than a number. Only the sign is meaningful; the
# magnitude exists so `_read_polarity` can always hand the caller volts. Kept
# comfortably above the caller's noise-floor threshold so a keyword "positive"
# is not mistaken for a dead output.
_POLARITY_KEYWORD_NOMINAL_V = 24.0


def _format_template(template: str, **values: Any) -> str:
    if not template.strip():
        return ""
    return template.format(**values).strip()


def _parse_float(value: str, fallback: float | None = None) -> float:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        if fallback is not None:
            return fallback
        raise


@dataclass(slots=True)
class _VISAContext:
    resource: Any
    resource_name: str


class ProdigitVisaDriver(BaseDriver):
    """VISA-backed driver for a Prodigit load system."""

    MEASUREMENT_COMMANDS = frozenset({
        "readchannel", "readinput", "checkpolarity",
        "measvoltage", "meascurrent", "measpower",
        "getmode", "getload",
        "getcurrent", "getvoltage", "getresistance", "getpower",
    })

    def __init__(
        self,
        *,
        timeout_ms: float | None = None,
        probe_identity: bool = True,
    ) -> None:
        self._ctx: _VISAContext | None = None
        self._reconnecting = False
        self._active_slot = 1
        self._load_serial = ""
        self._timeout_ms = (
            int(timeout_ms)
            if timeout_ms
            else int(_parse_float(PRODIGIT_VISA_TIMEOUT_MS, 5000))
        )
        self._probe_identity = probe_identity
        self._resource_name = PRODIGIT_VISA_RESOURCE.strip()
        self._backend = PRODIGIT_VISA_BACKEND.strip() or None
        self._read_term = PRODIGIT_VISA_READ_TERM
        self._write_term = PRODIGIT_VISA_WRITE_TERM

    @property
    def measurement_commands(self) -> frozenset[str]:
        return self.MEASUREMENT_COMMANDS

    def connect(self) -> bool:
        try:
            import pyvisa
        except ImportError as exc:
            raise HardwareError(
                "pyvisa is required for the Prodigit USB/VISA driver. "
                "Install it and make sure the VISA runtime is available."
            ) from exc

        try:
            rm = (
                pyvisa.ResourceManager(self._backend)
                if self._backend
                else pyvisa.ResourceManager()
            )
            resource_name = self._resource_name or self._auto_discover(rm)
            if not resource_name:
                raise HardwareError(
                    "No VISA resource found for Prodigit. "
                    "Set PRODIGIT_VISA_RESOURCE in .env."
                )
            resource = rm.open_resource(resource_name, open_timeout=self._timeout_ms)
            resource.timeout = self._timeout_ms
            resource.read_termination = self._read_term
            resource.write_termination = self._write_term
            self._ctx = _VISAContext(resource=resource, resource_name=resource_name)
        except HardwareError:
            raise
        except Exception as exc:
            raise HardwareError(
                f"Failed to connect to Prodigit VISA device: {exc}"
            ) from exc

        if self._probe_identity:
            try:
                _ = self.execute_command("getid", [])
            except Exception:
                # A real unit should still be usable even if *IDN? is not supported.
                pass
        return True

    def disconnect(self) -> None:
        """Drop the load, then close the VISA resource.

        Closing the socket does not stop a 3316G drawing current — it keeps the
        last commanded state. So the load is switched off first: once the
        resource is closed there is no way left to command it, and the UUT would
        sit under full test load until someone noticed.

        Best-effort by design. A failure here must not prevent the close (that
        would leak the resource and block the next connect), and callers that
        care about confirmation use the engine's `_force_load_off`, which reports
        to the operator. `_reconnecting` is honoured so a reconnect probe's own
        teardown does not fight the reconnect it is part of.
        """
        if self._ctx is None:
            return
        try:
            if not self._reconnecting:
                try:
                    self._write(PRODIGIT_SET_LOAD, state="OFF")
                except Exception:
                    pass  # unreachable instrument — nothing more we can do here
            self._ctx.resource.close()
        finally:
            self._ctx = None

    def activate_slot(self, slot_index: int, *, load_serial_number: str = "") -> None:
        self._active_slot = max(1, int(slot_index))
        self._load_serial = load_serial_number.strip()
        template = PRODIGIT_SELECT_SLOT
        if template.strip():
            self._write(template)

    def execute_command(self, command: str, args: list[str]) -> float:
        cmd = command.lower().strip()
        if cmd == "getid":
            return 0.0 if not self._query_text(PRODIGIT_QUERY_ID) else 0.0

        if cmd == "readinput":
            return self._read_measurement(PRODIGIT_QUERY_INPUT_VOLT, args)

        if cmd == "checkpolarity":
            return self._read_polarity()

        if cmd == "readchannel":
            channel = int(args[0]) if args else 0
            return self._read_channel(channel)

        if cmd == "setmode":
            mode = (args[0] if args else "CR").upper()
            self._write(PRODIGIT_SET_MODE, mode=mode)
            return 0.0

        if cmd == "getmode":
            raw = self._query_text(PRODIGIT_QUERY_MODE).upper()
            return {"CC": 1.0, "CV": 2.0, "CR": 3.0, "CP": 4.0}.get(raw, 0.0)

        if cmd == "loadon":
            self._write(PRODIGIT_SET_LOAD, state="ON")
            return 1.0

        if cmd == "loadoff":
            self._write(PRODIGIT_SET_LOAD, state="OFF")
            return 0.0

        if cmd == "getload":
            raw = self._query_text(PRODIGIT_QUERY_LOAD).upper()
            return 1.0 if raw in {"1", "ON"} else 0.0

        if cmd == "setcurrent":
            value = args[0] if args else "0"
            self._write(PRODIGIT_SET_CURRENT, value=value)
            return _parse_float(value, 0.0)

        if cmd == "getcurrent":
            return _parse_float(self._query_text(PRODIGIT_QUERY_CURRENT_SET), 0.0)

        if cmd == "measvoltage":
            return _parse_float(self._query_text(PRODIGIT_QUERY_INPUT_VOLT), 0.0)

        if cmd == "meascurrent":
            return _parse_float(self._query_text(PRODIGIT_QUERY_CURRENT), 0.0)

        if cmd == "measpower":
            return _parse_float(self._query_text(PRODIGIT_QUERY_POWER), 0.0)

        if cmd == "setresistance":
            resistance = args[0] if args else "0"
            expected = _parse_float(resistance, 0.0)
            # Write, let the load settle, then read back to confirm. If the
            # device still reports the old value, re-write and check again
            # before failing — this absorbs the load's apply/re-range latency.
            actual: float | None = None
            for _ in range(_RESISTANCE_VERIFY_ATTEMPTS):
                self._write(PRODIGIT_SET_RESISTANCE, value=resistance)
                time.sleep(_RESISTANCE_SETTLE_S)
                try:
                    actual = _parse_float(self._query_text(PRODIGIT_QUERY_RESISTANCE_SET))
                except Exception:
                    # Device can't report the set value back — trust the write.
                    return expected
                if abs(actual - expected) / max(expected, 1e-6) <= 0.05:
                    return expected
            raise RuntimeError(
                f"Resistance mismatch: sent {expected}Ω, device reports {actual}Ω"
            )

        if cmd == "getresistance":
            return _parse_float(self._query_text(PRODIGIT_QUERY_RESISTANCE_SET), 0.0)

        if cmd == "setvoltage":
            voltage = args[0] if args else "0"
            self._write(PRODIGIT_SET_VOLTAGE, value=voltage)
            return _parse_float(voltage, 0.0)

        if cmd == "getvoltage":
            return _parse_float(self._query_text(PRODIGIT_QUERY_VOLTAGE_SET), 0.0)

        if cmd == "setpower":
            power = args[0] if args else "0"
            self._write(PRODIGIT_SET_POWER, value=power)
            return _parse_float(power, 0.0)

        if cmd == "getpower":
            return _parse_float(self._query_text(PRODIGIT_QUERY_POWER_SET), 0.0)

        if cmd == "relay":
            relay_id = args[0] if args else "1"
            state = args[1] if len(args) > 1 else "off"
            if PRODIGIT_SET_RELAY.strip():
                self._write(PRODIGIT_SET_RELAY, relay=relay_id, state=state)
            return 0.0

        raise UnknownCommandError(f"Unknown hardware command: {command!r}")

    def _require_resource(self) -> Any:
        if self._ctx is None:
            raise RuntimeError("Prodigit VISA driver is not connected.")
        return self._ctx.resource

    def _auto_discover(self, rm: Any) -> str:
        try:
            resources = rm.list_resources()
        except Exception:
            resources = ()
        for resource_name in resources:
            if "USB" not in str(resource_name).upper():
                continue
            try:
                resource = rm.open_resource(resource_name)
                resource.timeout = self._timeout_ms
                resource.read_termination = self._read_term
                resource.write_termination = self._write_term
                identity = resource.query(PRODIGIT_QUERY_ID).strip()
                resource.close()
                if identity:
                    return str(resource_name)
            except Exception:
                continue
        return next((str(item) for item in resources if "USB" in str(item).upper()), "")

    def _io_with_resilience(self, op: Any) -> Any:
        """Run a VISA I/O op; on failure, reconnect and retry within a budget.

        Absorbs transient comms drops (e.g. the load forcibly closing the TCP
        socket mid-run): the op is retried, attempting a reconnect between
        tries, for up to ``_RECONNECT_BUDGET_S``. If it still fails after that,
        the last error propagates so the run aborts as it did before.
        """
        deadline = time.monotonic() + _RECONNECT_BUDGET_S
        while True:
            try:
                return op()
            except Exception:
                # Don't recurse while a reconnect's own probe I/O is running,
                # and give up once the reconnect budget is exhausted.
                if self._reconnecting or time.monotonic() >= deadline:
                    raise
                self._attempt_reconnect()
                time.sleep(_RECONNECT_RETRY_DELAY_S)

    def _attempt_reconnect(self) -> None:
        """Best-effort close + reopen of the VISA resource (errors swallowed)."""
        self._reconnecting = True
        try:
            try:
                self.disconnect()
            except Exception:
                pass
            try:
                self.connect()
            except Exception:
                pass
        finally:
            self._reconnecting = False

    def _query_text(self, template: str, **values: Any) -> str:
        command = _format_template(
            template,
            slot=self._active_slot,
            load_serial=self._load_serial,
            **values,
        )
        if not command:
            return ""
        return self._io_with_resilience(
            lambda: self._require_resource().query(command).strip()
        )

    def _write(self, template: str, **values: Any) -> None:
        command = _format_template(
            template,
            slot=self._active_slot,
            load_serial=self._load_serial,
            **values,
        )
        if not command:
            return

        def op() -> None:
            resource = self._require_resource()
            for part in command.split(";"):
                part = part.strip()
                if part:
                    resource.write(part)

        self._io_with_resilience(op)

    def _read_measurement(self, template: str, args: list[str]) -> float:
        query = _format_template(
            template,
            slot=self._active_slot,
            load_serial=self._load_serial,
            channel=args[0] if args else "",
        )
        if not query:
            raise RuntimeError("No VISA query template configured for this measurement.")
        value = self._io_with_resilience(
            lambda: self._require_resource().query(query).strip()
        )
        return _parse_float(value)

    def _read_polarity(self) -> float:
        """Return the UUT output voltage, signed, for the polarity gate.

        Spec Rev.1.1 §1.1.7.1 defines this test as a *voltage readback* with the
        load off: a positive value passes, a negative value fails. The default
        `PRODIGIT_QUERY_POLARITY` is a voltage query, so the reading is returned
        in volts and the caller decides the verdict.

        This must **fail closed**. Every path that cannot produce a trustworthy
        reading raises instead of returning a value, because `_check_polarity`
        turns an exception into a FAIL. Returning an optimistic default here
        would let a reverse-polarity or dead UUT clear a safety gate.

        Sites whose load answers a true `MEAS:POL?` with words rather than a
        number are still supported: the keyword is mapped onto a nominal +/-
        voltage so callers always receive volts.
        """
        response = self._query_text(PRODIGIT_QUERY_POLARITY)
        lowered = response.strip().lower()
        if not lowered:
            raise HardwareError(
                "Polarity query "
                f"{PRODIGIT_QUERY_POLARITY!r} returned an empty response; "
                "cannot determine output polarity."
            )

        # Non-numeric replies from a dedicated polarity query. Mapped onto a
        # nominal magnitude purely so the caller has volts to record; the sign
        # is what carries the meaning.
        if lowered in {"1", "ok", "pass", "pos", "positive", "normal"}:
            return _POLARITY_KEYWORD_NOMINAL_V
        if lowered in {"0", "-1", "fail", "neg", "negative", "reverse"}:
            return -_POLARITY_KEYWORD_NOMINAL_V

        try:
            value = float(lowered)
        except ValueError:
            raise HardwareError(
                f"Polarity query {PRODIGIT_QUERY_POLARITY!r} returned "
                f"{response!r}, which is neither a voltage nor a known "
                "polarity keyword."
            ) from None
        if not math.isfinite(value):
            raise HardwareError(
                f"Polarity query {PRODIGIT_QUERY_POLARITY!r} returned "
                f"{response!r}, which is not a finite voltage."
            )
        return value

    def _read_channel(self, channel: int) -> float:
        if channel in {0, 2, 10}:
            return self._read_measurement(PRODIGIT_QUERY_VOLTAGE, [str(channel)])
        if channel in {1, 3}:
            return self._read_measurement(PRODIGIT_QUERY_CURRENT, [str(channel)])
        if channel == 4:
            return self._read_measurement(PRODIGIT_QUERY_POWER, [str(channel)])
        return self._read_measurement(PRODIGIT_QUERY_VOLTAGE, [str(channel)])
