"""Background batch-aware test sequencer (QThread).

Loads a `.tst` script via `ScriptManager`, walks each `TestStep` for every
`BatchUnit` in sequence, dispatches hardware commands through the driver,
applies inline limits, retries failing steps as configured, and aborts the
run when a `Critical` step fails.

Target-unique features preserved:
- `prompt_yesno_request` signal and `PromptYesNo` command dispatch
- `_has_promptyesno` detection for `is_measurement` classification
- `submit_yesno_answer` / `_yesno_event` handshake
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any

from PySide6.QtCore import QThread, Signal

from config import (
    INPUT_CONNECTED_TARGET_V,
    INPUT_CONNECTED_TOLERANCE_PCT,
    LOAD_RESISTANCE_50W_OHM,
    LOAD_RESISTANCE_100W_OHM,
    LOAD_RESISTANCE_300W_OHM,
)
from logic.load_serials import serial_for
from drivers.base_driver import BaseDriver, HardwareError
from drivers.factory import create_driver
from logic.database_manager import DatabaseManager
from logic.models import (
    BatchUnit,
    BatchUnitReport,
    TestResultPayload,
    TestRunRecord,
    TestStep,
)
from logic.script_manager import ScriptManager, ScriptParseError
from logic.secure_logger import get_secure_logger


def _as_float(raw: str, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


# Fraction of the configured input target below which a polarity readback is
# treated as "no output" rather than "correct polarity". See
# `TestRunnerThread._polarity_floor_v`.
_POLARITY_FLOOR_FRACTION = 0.1

# Upper bound recorded for the polarity check. Spec §1.1.7.1 sets no maximum —
# it is a wiring check — so this is only a display ceiling for the report's
# "max" column, matching the `Limits 0 1000` convention the .tst scripts use.
_POLARITY_MAX_REPORTED_V = 1000.0

# How long a run start keeps retrying `connect()` before aborting. Covers an
# operator plugging the instrument's cable back in, without leaving the UI stuck
# when the instrument is genuinely absent.
_CONNECT_RETRY_BUDGET_S = 8.0
# Pause between connect attempts. The load needs a moment after its link comes
# back, so a tight loop would only produce more failures.
_CONNECT_RETRY_DELAY_S = 1.0
# Slice length for the operator-prompt wait. Short enough that closing the app
# is not perceptibly delayed, long enough to cost nothing while idling.
_OPERATOR_PROMPT_POLL_S = 0.2

# Settling time between switching the load off and reading the polarity voltage.
# Spec §1.1.7.1 specifies 3 seconds for this test.
_POLARITY_SETTLE_MS = 3000
#: Public view of the settle time, for UI text describing the Polarity Check.
POLARITY_SETTLE_S = _POLARITY_SETTLE_MS / 1000

# How often a `Delay` samples current and power for the live-readings pane.
# Voltage is already read every poll when the delay is monitored; current and
# power are extra queries on the 3300G's single connection, so they are sampled
# at a gentler rate than the 300 ms monitoring cadence.
_LIVE_READING_INTERVAL_MS = 1000
# Gentler rates when several units share the 3300G's one connection (parallel
# run): four units polling every 300 ms plus a `CHAN n` before each query is
# tens of commands a second, and the load drops the link under that load.
# 500 ms is still continuous monitoring (120 checks a minute per unit).
_SHARED_POLL_S = 0.5
# After LOAD ON in a step that set a resistance, the channel's current is
# checked against Ohm's law (I = V / R). Settle first so the load has come up.
_LOAD_VERIFY_SETTLE_S = 0.3
_LOAD_VERIFY_TOL = 0.15        # +-15 % of the expected current
_LOAD_VERIFY_MIN_A = 0.03      # floor for small currents
_SHARED_LIVE_INTERVAL_S = 2.0
# After a failed live sample, wait this long before trying again (it used to
# stop sampling for the rest of the delay, leaving stale values on screen).
_LIVE_RETRY_S = 5.0
# Live V/A/W are written to the encrypted log at this rate, per unit, so a run
# can be checked afterwards for what each load actually drew.
_LIVE_LOG_INTERVAL_S = 5.0

# Expected time a step takes beyond its `Delay`s (instrument round-trips),
# used only to weight the Total progress bar. Rough is fine: next to the 1-15
# minute burn-in waits it only keeps the short steps from counting as zero.
_STEP_BASE_S = 2.0

#: Measurement commands whose result is a live V/A/W reading, mapped to the key
#: the live-readings pane displays it under.
_LIVE_READING_KEYS: dict[str, str] = {
    "measvoltage": "V",
    "meascurrent": "A",
    "measpower": "W",
}


class _MonitorOutOfRange(Exception):
    """Raised when continuous voltage monitoring during a `Delay` trips.

    Carries the offending measurement so the failing step can report it as a
    real value (instead of a blank "-") in the results table.
    """

    def __init__(self, value: float, lo: float, hi: float, elapsed_s: float) -> None:
        self.value = value
        self.lo = lo
        self.hi = hi
        self.elapsed_s = elapsed_s
        super().__init__(
            f"Voltage {value:.3f}V out of range "
            f"[{lo}-{hi}V] at {elapsed_s:.1f}s into delay"
        )


# Readings the instrument cannot have measured. The 3300G answered a voltage
# query with "9999" 47 s into a 100 W wait on the station — a no-valid-reading
# code, not 9999 V — and it was reported as the UUT's voltage. Anything at or
# above the sentinel, or a voltage beyond what a 24 V product and the 3316G can
# produce, is treated as an instrument fault, re-read, and never as the UUT.
_INVALID_READING_SENTINEL = 9990.0
_MAX_PLAUSIBLE_VOLTS = 100.0
_VOLTAGE_READS = frozenset({"measvoltage", "readinput", "checkpolarity"})
_VALIDATED_READS = _VOLTAGE_READS | {"meascurrent", "measpower"}
_INVALID_READING_RETRIES = 2
_INVALID_READING_RETRY_S = 0.2


class InvalidReadingError(Exception):
    """The instrument returned a value it cannot have measured (e.g. 9999)."""

    def __init__(self, command: str, value: float) -> None:
        self.command = command
        self.value = value
        super().__init__(
            f"instrument returned an invalid reading ({value:g}) for {command} - "
            "check the load's front panel"
        )


def _is_invalid_reading(command: str, value: float | None) -> bool:
    if value is None:
        return False
    if abs(value) >= _INVALID_READING_SENTINEL:
        return True
    return command in _VOLTAGE_READS and abs(value) > _MAX_PLAUSIBLE_VOLTS


class _MonitorReadFailed(Exception):
    """Raised when monitoring cannot read the voltage at all during a `Delay`.

    Distinct from :class:`_MonitorOutOfRange`, which means the UUT misbehaved:
    this means the *instrument* stopped answering. The driver has already spent
    its reconnect budget by the time this is raised, so the link is genuinely
    down rather than blipping.

    Carries no value — there is no measurement — so the failing step reports a
    blank reading, and the message names the underlying comms error to separate
    "UUT out of spec" from "lost the load" in the trace log.
    """

    def __init__(self, cause: BaseException, elapsed_s: float) -> None:
        self.cause = cause
        self.elapsed_s = elapsed_s
        super().__init__(
            f"Voltage monitoring lost the instrument at {elapsed_s:.1f}s into "
            f"delay (after reconnect attempts): {cause!s}"
        )


class TestRunnerThread(QThread):
    """Runs one script across one or more batch units, sequentially."""

    log_msg = Signal(str)
    test_result = Signal(str, dict)
    progress_total = Signal(int)
    progress_test = Signal(int)
    current_test = Signal(str)
    prompt_request = Signal(str)
    prompt_yesno_request = Signal(str)
    script_log = Signal(str)
    loop_started = Signal(int, int)
    current_unit_changed = Signal(str, int, int, str)  # serial, index, total, position_label
    unit_finished = Signal(dict)
    unit_alert = Signal(str)
    #: The electronic load could not be commanded off. Carries the reason.
    #: Safety-relevant: the UUT may still be dissipating full test power with
    #: nothing supervising it, so the UI must show this, not just log it.
    load_shutdown_failed = Signal(str)
    #: A live reading for the current unit: {"slot", "serial"} plus any of
    #: "V", "A", "W". Partial on purpose — a step that measures only current
    #: updates only that cell — so the receiver merges rather than replaces.
    live_reading = Signal(dict)
    #: "ok" / "reconnecting" / "down" for the load link (toolbar indicator).
    link_state = Signal(str)

    def __init__(
        self,
        script_path: Path,
        selected_names: set[str],
        *,
        loop_count: int = 1,
        stop_on_fail: bool = False,
        operator: str = "",
        tester_name: str = "",
        employee_id: str = "",
        uut_type: str = "",
        part_number: str = "",
        serial_number: str = "",
        batch_units: list[BatchUnit] | None = None,
        script_manager: ScriptManager | None = None,
        start_time: datetime | None = None,
        logical_script_name: str = "",
        driver: BaseDriver | None = None,
        hw_lock: Lock | None = None,
        skip_hw_lifecycle: bool = False,
        production_flow: bool = True,
        persist_runs: bool = True,
        polarity_check: bool = False,
    ) -> None:
        super().__init__()
        self._script_path = Path(script_path)
        self._logical_script_name = logical_script_name.strip() or self._script_path.stem
        self._selected_names = set(selected_names)
        self._loop_count = max(1, loop_count)
        # Spec Rev.1.1 §1.1.4.5: "the system shall proceed to the next test only
        # if the previous test has passed. If any test fails ... all subsequent
        # tests shall not be performed and shall be marked as N/A." That is not
        # optional in production, so the caller's flag can only ever *add*
        # stopping, never remove it. It used to be honoured as given, and the
        # "Stop on fail" checkbox ships unticked — meaning the shipped default
        # violated the clause for any step that was not marked `Critical`.
        #
        # This changes nothing for `biosense_test.tst`, where every step carrying
        # pass/fail limits is already Critical; it closes the hole for any
        # future script whose author omits the keyword. The checkbox survives as
        # a Maintenance-mode control, where a diagnostic run legitimately wants
        # to continue past a failing step.
        self._stop_on_fail = stop_on_fail or production_flow
        self._script_manager = script_manager or ScriptManager()
        self._hw: BaseDriver = driver or create_driver()
        self._hw_lock: Lock | None = hw_lock
        self._live_cache: dict[str, float] = {}
        self._live_logged_at = 0.0
        self._skip_hw_lifecycle: bool = skip_hw_lifecycle
        # A maintenance run is a diagnostic, not a production test: the
        # station-integrity gates (24 V presence, polarity) would abort it and
        # inject rows for checks the user did not ask for, and its results must
        # not enter the production record.
        self._production_flow: bool = production_flow
        self._persist_runs: bool = persist_runs
        # Maintenance-only: run the Polarity Check even though production_flow
        # is off (production always runs it as a gate). See test list groups.
        self._polarity_check: bool = polarity_check
        self._stop_requested = False
        self._link_down_logged = False
        #: Why the run could not connect to the load ("" if it did); shown to
        #: the operator instead of a generic "the run did not start".
        self.connect_error = ""
        # Set once the script is parsed; 0 until then so an early-abort report
        # simply omits the coverage line rather than claiming "0 / 0".
        self._steps_selected: int = 0
        self._steps_available: int = 0
        self._prompt_event: Event = Event()
        self._yesno_event: Event = Event()
        self._yesno_answer: bool = False
        self._pause_event: Event = Event()
        self._pause_event.set()  # set = running; clear = paused
        self._db = DatabaseManager()
        try:
            self._secure_log = get_secure_logger()
        except Exception:
            self._secure_log = None
        self.tester_name = tester_name.strip() or operator.strip()
        self.employee_id = employee_id.strip()
        self.uut_type = uut_type.strip()
        self._start_dt = start_time or datetime.now()
        self._operator = operator.strip() or self.tester_name
        self._part_number = part_number.strip()
        self._connected_target_v = _as_float(INPUT_CONNECTED_TARGET_V, 24.0)
        self._connected_tol_pct = _as_float(INPUT_CONNECTED_TOLERANCE_PCT, 5.0)
        # Fallback resistance per managed load band, keyed by the marker that
        # appears in the step name. Spec Rev 1.1: Low Load = 5.76 ohm (100 W),
        # Burn-In = 2.0 ohm (~288 W at 24 V). 50W is retained only so legacy
        # scripts that still name their steps "50W" keep a sane fallback.
        self._load_res_by_band: dict[str, float] = {
            "50w": _as_float(LOAD_RESISTANCE_50W_OHM, 12.5),
            "100w": _as_float(LOAD_RESISTANCE_100W_OHM, 5.76),
            "300w": _as_float(LOAD_RESISTANCE_300W_OHM, 2.0),
        }
        # Bands whose load resistance the *script* explicitly set during the
        # current unit's run. Used to make the .tst the source of truth and fall
        # back to the .env value only when the script did not set it (e.g. a
        # measurement step run without its Setup step). Reset per unit.
        self._tst_set_bands: set[str] = set()
        self._reports: list[BatchUnitReport] = []
        self._current_unit: BatchUnit | None = None
        self._current_step: TestStep | None = None
        # Why the last `_run_step` failed, for the results table ("at 0.3 s",
        # "no reading — instrument not responding (30.1 s)").
        self._fail_note = ""
        # Time-weighted Total progress for the current unit (see `_emit_total`).
        self._progress_total_s = 0.0
        self._progress_done_s = 0.0
        self._step_weight_s = 0.0
        self._progress_unit = (1, 1)  # (unit_index, total_units)
        self._current_test_name = ""
        if batch_units:
            self._batch_units = list(batch_units)
        else:
            load_serial = serial_for(1)
            self._batch_units = [
                BatchUnit(
                    serial_number=serial_number.strip(),
                    slot_index=1,
                    position_label="Position 1",
                    load_channel="CH1",
                    load_serial_number=load_serial,
                )
            ]

    def _emit_log(self, category: str, msg: str) -> None:
        self.log_msg.emit(msg)
        if self._secure_log is not None:
            try:
                self._secure_log.log(
                    "trace",
                    {
                        "category": category,
                        "message": msg,
                        "script": self._logical_script_name,
                        "serial_number": (
                            self._current_unit.serial_number
                            if self._current_unit
                            else ""
                        ),
                    },
                )
            except Exception:
                pass

    def _hw_execute(self, command: str, args: list[str]) -> float:
        """Execute a hardware command; re-read an impossible measurement.

        A V/I/P read that comes back as an instrument code (9999) or an
        impossible voltage is retried; if it persists, `InvalidReadingError` is
        raised so the step fails as an instrument fault rather than recording
        the code as the UUT's value.
        """
        value = self._hw_execute_raw(command, args)
        if command not in _VALIDATED_READS:
            return value
        for _ in range(_INVALID_READING_RETRIES):
            if not _is_invalid_reading(command, value):
                return value
            self._emit_log(
                "error",
                f"Invalid reading {value:g} for {command}; reading again.",
            )
            time.sleep(_INVALID_READING_RETRY_S)
            value = self._hw_execute_raw(command, args)
        if _is_invalid_reading(command, value):
            raise InvalidReadingError(command, value)
        return value

    def _hw_execute_raw(self, command: str, args: list[str]) -> float:
        """Execute a hardware command, re-selecting the slot atomically when shared."""
        unit = self._current_unit
        if self._hw_lock is not None and unit is not None:
            with self._hw_lock:
                self._hw.activate_slot(
                    unit.slot_index,
                    load_serial_number=unit.load_serial_number,
                )
                return self._hw.execute_command(command, args)
        return self._hw.execute_command(command, args)

    def _verify_load(self, ohms: float) -> None:
        """Check that the channel really draws what `ohms` gives (I = V / R).

        The instrument's own read-back cannot be trusted for this: on the
        station CH2 answered `CR:HIGH?` with the new 57.6 ohm while drawing the
        previous run's 20 W (28.8 ohm) — most likely a missed `CHAN n`, so the
        setting and its read-back both went to another channel. Measuring what
        the channel does catches that, a stuck value, an inactive level and a
        load that never switched on. On a mismatch the setup is sent once more;
        a second mismatch fails the step rather than testing at the wrong load.
        """
        unit = self._current_unit
        ch = f"CH{unit.slot_index}" if unit is not None else "The load"

        def check() -> str | None:
            """None if the channel draws ~V/R, else a description of what it does."""
            self.msleep(int(_LOAD_VERIFY_SETTLE_S * 1000))
            volts = self._hw_execute("measvoltage", [])
            amps = self._hw_execute("meascurrent", [])
            expected = volts / ohms
            if abs(amps - expected) <= max(_LOAD_VERIFY_TOL * expected, _LOAD_VERIFY_MIN_A):
                return None
            return (
                f"{ch} draws {volts * amps:.1f} W ({amps:.3f} A), expected "
                f"~{volts * expected:.1f} W ({expected:.3f} A at {ohms:g} ohm)"
            )

        # Measured twice before acting: V and I are two reads, and right after
        # LOAD ON at 100/300 W the voltage can still be moving between them.
        def check_twice() -> str | None:
            detail = check()
            return check() if detail is not None else None

        detail = check_twice()
        if detail is None:
            return
        self._emit_log("error", f"{detail} - sending the setup again.")
        self._hw_execute("setmode", ["CR"])
        self._hw_execute("setresistance", [f"{ohms:g}"])
        self._hw_execute("loadon", [])
        detail = check_twice()
        if detail is not None:
            raise HardwareError(f"load not as set - {detail}")

    def _on_hw_link_state(self, state: str) -> None:
        """Relay the driver's link state; say once in the log that the link is gone."""
        self.link_state.emit(state)
        if state == "down" and not self._link_down_logged:
            self._link_down_logged = True
            self._emit_log(
                "error",
                "Lost the connection to the electronic load - no reply after "
                "reconnect attempts. Commands fail until it is back.",
            )
        elif state == "ok":
            self._link_down_logged = False

    def _on_hw_reconnect(self, slot: int) -> None:
        self._emit_log(
            "error",
            f"Lost the connection to the electronic load - reconnected and "
            f"selected CH{slot} again.",
        )

    @staticmethod
    def step_delay_s(step: TestStep) -> float:
        """Total `Delay` time of a step in seconds (0 if it waits for nothing)."""
        total_ms = 0.0
        for cmd in step.commands:
            args = cmd.get("args") or []
            if str(cmd.get("cmd", "")).lower() == "delay" and args:
                try:
                    total_ms += float(args[0])
                except (TypeError, ValueError):
                    pass
        return total_ms / 1000

    def _emit_total(self, step_fraction: float = 0.0) -> None:
        """Emit the Total bar: expected time done so far over the unit's total.

        `step_fraction` is how far into the current step's weight we are, so the
        bar moves during a long `Delay` instead of jumping when it ends.
        """
        if not self._progress_total_s:
            return
        done = self._progress_done_s + step_fraction * self._step_weight_s
        fraction = min(1.0, done / self._progress_total_s)
        unit_index, total_units = self._progress_unit
        self.progress_total.emit(int(((unit_index - 1) + fraction) / max(1, total_units) * 100))

    def _emit_live(self, values: dict[str, float]) -> None:
        """Publish V/A/W readings for the current unit to the live-readings pane."""
        unit = self._current_unit
        if unit is None or not values:
            return
        self.live_reading.emit(
            {"slot": unit.slot_index, "serial": unit.serial_number, **values}
        )
        self._live_cache.update(values)
        now = time.monotonic()
        if self._secure_log is not None and now - self._live_logged_at >= _LIVE_LOG_INTERVAL_S:
            self._live_logged_at = now
            try:
                self._secure_log.log(
                    "live",
                    {"serial_number": unit.serial_number, "slot": unit.slot_index,
                     **self._live_cache},
                )
            except Exception:
                pass

    def _sample_live_readings(self, *, include_voltage: bool) -> bool:
        """Read current and power (and voltage if asked) for the live pane.

        Display only: these values never decide a verdict, so a failed read is
        swallowed rather than failing the step. Returns False on a failure so the
        caller can back off (`_LIVE_RETRY_S`) — the driver has already spent its
        reconnect budget by then, and retrying every second would stretch the
        delay.
        """
        commands = ["meascurrent", "measpower"]
        if include_voltage:
            commands.insert(0, "measvoltage")
        values: dict[str, float] = {}
        try:
            for command in commands:
                values[_LIVE_READING_KEYS[command]] = self._hw_execute(command, [])
        except Exception:
            self._emit_live(values)
            return False
        self._emit_live(values)
        return True

    def run(self) -> None:
        # Hooked here, not in __init__: recording an LED failure builds a runner
        # that must never touch the driver.
        if hasattr(self._hw, "on_reconnect"):
            self._hw.on_reconnect = self._on_hw_reconnect  # type: ignore[attr-defined]
        if hasattr(self._hw, "on_link_state"):
            self._hw.on_link_state = self._on_hw_link_state  # type: ignore[attr-defined]
        self.progress_total.emit(0)
        self.progress_test.emit(0)
        self.current_test.emit("")

        try:
            if not self._skip_hw_lifecycle:
                try:
                    if not self._hw.connect():
                        self._emit_log("error", "Hardware connect() returned False; aborting.")
                        self.connect_error = "The electronic load did not accept the connection."
                        self.link_state.emit("down")
                        return
                except HardwareError as exc:
                    # Retry before giving up: an unplugged/late-plugged LAN cable
                    # is the common case, and the instrument needs a moment
                    # after the link comes back. Bounded so a genuinely absent
                    # instrument fails fast instead of hanging the operator.
                    if not self._retry_connect(exc):
                        self.link_state.emit("down")
                        return
                if hasattr(self._hw, "on_link_state"):
                    self.link_state.emit("ok")

            steps = self._load_selected_steps()
            if steps is None:
                return

            total_units = len(self._batch_units)

            for unit_index, unit in enumerate(self._batch_units, start=1):
                if self._stop_requested:
                    self._emit_log("info", "Test execution aborted by user.")
                    break
                self._current_unit = unit
                self.current_unit_changed.emit(
                    unit.serial_number,
                    unit_index,
                    total_units,
                    unit.position_label,
                )
                self._emit_log(
                    "info",
                    f"=== Unit {unit_index}/{total_units}: {unit.serial_number}"
                    f" @ {unit.position_label} ===",
                )
                report = self._run_single_unit(unit, unit_index, total_units, steps)
                self._reports.append(report)
                self.unit_finished.emit(
                    {
                        "serial_number": unit.serial_number,
                        "slot_index": unit.slot_index,
                        "position_label": unit.position_label,
                        "passed": report.overall_result == "PASS",
                        "report_generated": report.should_generate_report,
                        "aborted": report.aborted_by_operator,
                        "link_lost": report.aborted_link_lost,
                    }
                )
        finally:
            if not self._skip_hw_lifecycle:
                try:
                    self._hw.disconnect()
                except Exception:
                    pass
            self.current_test.emit("")
            self.progress_test.emit(0)
            self._pause_event.set()

    def _load_selected_steps(self) -> list[TestStep] | None:
        """Parse the script and return the selected steps, or None (already logged).

        Shared by `run()` and `record_led_failure()` so a report built without a
        run covers exactly the steps a run would have.
        """
        try:
            doc = self._script_manager.load_document(self._script_path)
        except ScriptParseError as exc:
            self._emit_log("error", f"Script load failed at line {exc.line_no}: {exc.msg}")
            return None
        except (OSError, ValueError) as exc:
            self._emit_log("error", f"Script load failed: {exc}")
            return None

        self._script_metadata = doc.metadata
        steps = [s for s in doc.steps if s.name in self._selected_names]
        if not steps:
            self._emit_log("info", "No steps selected to run.")
            return None

        # Record how much of the script this run covers, so a report can
        # disclose a partial selection. Hidden setup/teardown steps are
        # excluded from both counts: they are machinery, not test criteria,
        # and counting them would make a full run read as "17 / 20".
        self._steps_available = sum(
            1 for s in doc.steps if not getattr(s, "hidden", False)
        )
        self._steps_selected = sum(
            1 for s in steps if not getattr(s, "hidden", False)
        )
        if self._steps_selected < self._steps_available:
            self._emit_log(
                "info",
                f"Partial run: {self._steps_selected} of "
                f"{self._steps_available} tests selected.",
            )
        return steps

    def record_led_failure(self) -> BatchUnitReport | None:
        """Record the first batch unit as failing the LED check, without a run.

        The operator answers the LED check in the pre-test dialog. A unit whose
        LED is confirmed OFF never enters the run — the operator swaps in a
        replacement instead — but spec §1.1.4.5 still requires a report for it.
        This builds the same report a run would have produced had the LED step
        failed: the LED row as FAIL and every later test N/A. It never touches
        the hardware, so call it on a thread that was not started. Returns None
        if the script cannot be loaded (already logged).
        """
        steps = self._load_selected_steps()
        if steps is None:
            return None
        unit = self._batch_units[0]
        self._current_unit = unit
        record = self._new_record(unit, 1)
        normal_steps = [s for s in steps if not getattr(s, "always", False)]

        led_step = next((s for s in normal_steps if self.is_led_step(s)), None)
        if led_step is not None:
            payload: TestResultPayload = {
                "value": 0.0,
                "min": led_step.min_val if led_step.min_val is not None else float("nan"),
                "max": led_step.max_val if led_step.max_val is not None else float("nan"),
                "unit": led_step.unit,
                "passed": False,
                "is_measurement": True,
            }
            self._emit_result(unit, led_step.name, {**payload, **self._report_fields(led_step)})
            record.results.append(
                {
                    "test_name": led_step.name,
                    "loop": 1,
                    **self._report_fields(led_step),
                    **dict(payload),
                }
            )
        self._emit_log(
            "error", f"{unit.serial_number}: LED indication not lit (recorded at setup)."
        )
        self._backfill_skipped(record, unit, normal_steps)

        record.overall_passed = False
        record.end_time = datetime.now()
        self._save_record(record)
        report = BatchUnitReport(
            unit=unit,
            record=record,
            tester_name=self.tester_name,
            employee_id=self.employee_id,
            uut_type=self.uut_type,
            test_program_name=self._logical_script_name,
            overall_result="FAIL",
            alert_message="LED indication not lit (recorded at pre-test setup).",
            steps_selected=self._steps_selected,
            steps_available=self._steps_available,
        )
        self._reports.append(report)
        return report

    def _new_record(self, unit: BatchUnit, batch_index: int) -> TestRunRecord:
        return TestRunRecord(
            operator=self._operator,
            part_number=self._part_number,
            serial_number=unit.serial_number,
            overall_passed=True,
            batch_label=self._logical_script_name,
            batch_index=batch_index,
            slot_index=unit.slot_index,
            position_label=unit.position_label,
            load_channel=unit.load_channel,
            load_serial_number=unit.load_serial_number,
            start_time=datetime.now(),
        )

    def _run_single_unit(
        self,
        unit: BatchUnit,
        unit_index: int,
        total_units: int,
        steps: list[TestStep],
    ) -> BatchUnitReport:
        """Run one unit, guaranteeing the load is commanded off afterwards.

        The `finally` is the safety contract: this method has several early
        returns (slot activation, input connection, polarity) plus the normal
        path, and an exception can escape any of them. A unit must never be left
        dissipating test power because of which branch happened to be taken, so
        the shutdown lives here rather than in each exit.
        """
        self._unit_link_mark = getattr(self._hw, "link_lost_count", 0)
        try:
            report = self._run_single_unit_inner(unit, unit_index, total_units, steps)
            if self._link_lost():
                report.aborted_link_lost = True
                self._emit_log(
                    "error",
                    f"{unit.serial_number}: communication with the load was lost - "
                    "not saved, no report.",
                )
            return report
        finally:
            self._force_load_off(unit)

    def _link_lost(self) -> bool:
        """Whether the load's link was lost (and not restored) during this unit."""
        return getattr(self._hw, "link_lost_count", 0) > getattr(self, "_unit_link_mark", 0)

    def _backfill_skipped(
        self,
        record: TestRunRecord,
        unit: BatchUnit,
        steps: list[TestStep],
        loop_number: int = 1,
    ) -> None:
        """Record every not-yet-executed step as an N/A row.

        Spec Rev.1.1 §1.1.4.5: when a run stops early, the tests that did not
        run "shall not be performed and shall be marked as N/A in the test
        report". The mid-run abort path did this, but the early returns (slot
        activation, input-connection check, up-front polarity) returned with only
        their own failure row.

        That left the report missing the four spec tests entirely, and it also
        broke the XML: `write_xml_report` decides whether a run can be mapped
        onto Appendix B by looking for `report_test` on the *result rows*, so a
        run with no rows was reported to the operator as "this script declares no
        'Report' directives" — blaming a script that was in fact correct. These
        rows carry `_report_fields(step)`, so the mapping is present and the XML
        renders the tests as N/A.
        """
        # Only this loop's rows: a step that ran in loop 1 still needs its N/A
        # row when loop 2 aborts before reaching it.
        executed = {
            r["test_name"] for r in record.results
            if int(r.get("loop", 1) or 1) == loop_number
        }
        self._backfill_polarity(record, unit, loop_number)
        for step in steps:
            if step.name in executed or getattr(step, "hidden", False):
                continue
            na_payload: TestResultPayload = {
                "value": 0.0,
                # The step's own limits, so the report can still state what the
                # test would have required (Expected Value). N/A rows never show
                # them in a Min/Max column: the table and PDF blank skipped rows.
                "min": step.min_val if step.min_val is not None else float("nan"),
                "max": step.max_val if step.max_val is not None else float("nan"),
                "unit": step.unit,
                "passed": False,
                "is_measurement": False,
            }
            self._emit_result(
                unit, step.name,
                {**na_payload, "skipped": True, "loop": loop_number, **self._report_fields(step)},
            )
            record.results.append(
                {
                    "test_name": step.name,
                    "loop": loop_number,
                    "skipped": True,
                    **self._report_fields(step),
                    **dict(na_payload),
                }
            )

    def _backfill_polarity(self, record: TestRunRecord, unit: BatchUnit, loop_number: int) -> None:
        """Add Polarity Check as N/A when a production run ended before it ran.

        Polarity is an engine gate, not a script step, so the step backfill
        above cannot see it: an LED fail (at setup or in the run) or a failed
        input check left the report with three tests instead of the spec's four
        (Req 10 / Appendix A-B: every test not performed is N/A).
        """
        if not self._production_flow:
            return
        if any(r.get("test_name") == "Polarity Check" for r in record.results):
            return
        payload: TestResultPayload = {
            "value": 0.0,
            "min": float("nan"),
            "max": float("nan"),
            "unit": "V",
            "passed": False,
            "is_measurement": False,
        }
        fields = {"report_test": "Polarity Check", "report_quantity": "Voltage"}
        self._emit_result(unit, "Polarity Check", {**payload, "skipped": True, "loop": loop_number, **fields})
        record.results.append(
            {"test_name": "Polarity Check", "loop": loop_number, "skipped": True, **fields, **dict(payload)}
        )

    def _run_single_unit_inner(
        self,
        unit: BatchUnit,
        unit_index: int,
        total_units: int,
        steps: list[TestStep],
    ) -> BatchUnitReport:
        record = self._new_record(unit, unit_index)
        # Each unit re-runs its Setup steps, so forget which bands the script set
        # for the previous unit before starting this one.
        self._tst_set_bands = set()

        try:
            if self._hw_lock is not None:
                with self._hw_lock:
                    self._hw.activate_slot(unit.slot_index, load_serial_number=unit.load_serial_number)
            else:
                self._hw.activate_slot(unit.slot_index, load_serial_number=unit.load_serial_number)  # type: ignore[attr-defined]
        except Exception as exc:
            self._emit_log(
                "error",
                f"{unit.serial_number}: failed to activate {unit.position_label}: {exc}",
            )
            record.overall_passed = False
            record.end_time = datetime.now()
            record.results.append(
                self._result_row("Slot Activation", False, 0.0, 0.0, 0.0, "")
            )
            self._backfill_skipped(record, unit, steps)
            self._save_record(record)
            return BatchUnitReport(
                unit=unit,
                record=record,
                tester_name=self.tester_name,
                employee_id=self.employee_id,
                uut_type=self.uut_type,
                test_program_name=self._logical_script_name,
                overall_result="FAIL",
                steps_selected=self._steps_selected,
                steps_available=self._steps_available,
            )

        if self._production_flow and not self._check_input_connection(record, unit):
            record.end_time = datetime.now()
            self._backfill_skipped(record, unit, steps)
            self._save_record(record)
            return BatchUnitReport(
                unit=unit,
                record=record,
                tester_name=self.tester_name,
                employee_id=self.employee_id,
                uut_type=self.uut_type,
                test_program_name=self._logical_script_name,
                overall_result="FAIL",
                steps_selected=self._steps_selected,
                steps_available=self._steps_available,
            )

        # Maintenance can run the Polarity Check on its own (it has no script
        # steps — the engine performs it). Production runs it as a gate below.
        if not self._production_flow and self._polarity_check:
            self._check_polarity(record, unit)

        # Steps marked `Always` are teardown steps (e.g. a final loadoff). They
        # are pulled out of the normal sequence and run unconditionally at the
        # end — even after a critical abort or a user stop — so the load is never
        # left energized after a failure.
        normal_steps = [s for s in steps if not getattr(s, "always", False)]
        teardown_steps = [s for s in steps if getattr(s, "always", False)]

        # The polarity gate does not run before the loop. Spec §1.1.4.2 makes the
        # manual LED check the first test — "the subsequent DC Load tests will
        # not be permitted to proceed" until the operator answers — and Appendix
        # A numbers LED as Test 1, Polarity as Test 2. So it runs inside the
        # loop, right after the LED step passes and before any load is applied.
        #
        # If the selected steps contain no LED check (a partial Maintenance
        # selection, or a product whose script has none), fall back to gating up
        # front: polarity must still be verified before a load is switched on.
        has_led_step = any(self.is_led_step(s) for s in normal_steps)
        polarity_pending = self._production_flow and has_led_step
        if self._production_flow and not has_led_step:
            if not self._check_polarity(record, unit):
                msg = (
                    f"Polarity check failed for {unit.serial_number}"
                    f" at {unit.position_label}. Skipping report for this unit."
                )
                self.unit_alert.emit(msg)
                record.end_time = datetime.now()
                self._backfill_skipped(record, unit, normal_steps)
                self._save_record(record)
                return BatchUnitReport(
                    unit=unit,
                    record=record,
                    tester_name=self.tester_name,
                    employee_id=self.employee_id,
                    uut_type=self.uut_type,
                    test_program_name=self._logical_script_name,
                    overall_result="FAIL",
                    alert_message=msg,
                    should_generate_report=False,
                    steps_selected=self._steps_selected,
                    steps_available=self._steps_available,
                )

        # The Total bar is weighted by expected time, not by step count: the
        # script's 19 steps include four waits that hold ~31 of its ~32 minutes,
        # so counting steps showed 36 % one minute into a half-hour run.
        weights = [self.step_delay_s(s) + _STEP_BASE_S for s in normal_steps]
        polarity_s = _POLARITY_SETTLE_MS / 1000 if polarity_pending else 0.0
        self._progress_total_s = (sum(weights) + polarity_s) * self._loop_count
        self._progress_done_s = 0.0
        self._step_weight_s = 0.0
        self._progress_unit = (unit_index, total_units)
        abort_loops = False
        overall_passed = True
        loop_number = 1

        for loop_idx in range(self._loop_count):
            if self._stop_requested:
                self._emit_log("info", "Test execution aborted by user.")
                break
            loop_number = loop_idx + 1
            if self._loop_count > 1:
                self.loop_started.emit(loop_number, self._loop_count)
                self._emit_log("info", f"--- Loop {loop_number}/{self._loop_count} ---")

            for step_idx, step in enumerate(normal_steps):
                self._pause_event.wait()
                if self._stop_requested:
                    abort_loops = True
                    break

                self._step_weight_s = weights[step_idx]
                self._current_test_name = step.name
                self.current_test.emit(step.name)
                self.progress_test.emit(0)
                self._configure_load_for_step(step)
                self._emit_log(
                    "cmd",
                    f"Executing: {step.name}... [Unit: {unit.serial_number}]",
                )

                attempts_total = step.retry_count + 1
                passed = False
                value: float | None = None
                for attempt in range(1, attempts_total + 1):
                    if self._stop_requested:
                        break
                    passed, value = self._run_step(step)
                    if passed or attempt == attempts_total:
                        break
                    self._emit_log(
                        "info",
                        f"{step.name}: attempt {attempt}/{attempts_total} failed, retrying...",
                    )

                if not passed:
                    overall_passed = False

                payload: TestResultPayload = {
                    "value": value if value is not None else 0.0,
                    "min": step.min_val if step.min_val is not None else float("nan"),
                    "max": step.max_val if step.max_val is not None else float("nan"),
                    "unit": step.unit,
                    "passed": passed,
                    "is_measurement": value is not None,
                }
                if not passed and self._fail_note:
                    payload["note"] = self._fail_note
                if not getattr(step, "hidden", False):
                    # Report fields let the results table merge one time
                    # point's V/I/P/R steps into a single row; the loop number
                    # keeps loop 2's points from merging into loop 1's rows.
                    self._emit_result(
                        unit, step.name,
                        {**payload, "loop": loop_number, **self._report_fields(step)},
                    )
                    record.results.append(
                        {
                            "test_name": step.name,
                            "loop": loop_number,
                            **self._report_fields(step),
                            **dict(payload),
                        }
                    )

                self.progress_test.emit(100)
                self._progress_done_s += self._step_weight_s
                self._step_weight_s = 0.0
                self._emit_total()

                if step.is_critical and not passed:
                    self._emit_log(
                        "error",
                        f"CRITICAL ABORT: {step.name} failed for {unit.serial_number}.",
                    )
                    abort_loops = True
                    break

                # Spec §1.1.4.2 / Appendix A: LED is Test 1 and gates everything
                # after it; Polarity is Test 2. Run the polarity gate as soon as
                # the LED step has passed, before any load is applied.
                if polarity_pending and self.is_led_step(step):
                    polarity_pending = False
                    polarity_ok = self._check_polarity(record, unit)
                    self._progress_done_s += polarity_s
                    self._emit_total()
                    if not polarity_ok:
                        msg = (
                            f"Polarity check failed for {unit.serial_number}"
                            f" at {unit.position_label}."
                        )
                        self._emit_log("error", f"CRITICAL ABORT: {msg}")
                        self.unit_alert.emit(msg)
                        overall_passed = False
                        abort_loops = True
                        break

                if self._stop_on_fail and not passed:
                    self._emit_log(
                        "info",
                        f"Stop on fail: {step.name} failed for {unit.serial_number}.",
                    )
                    abort_loops = True
                    break

            if abort_loops:
                break

        if abort_loops:
            self._backfill_skipped(record, unit, normal_steps, loop_number)

        # Captured before teardown on purpose: a Stop that arrives while the
        # `Always` steps run comes after every test finished, so that unit's
        # result is complete and keeps its report.
        stopped = self._stop_requested

        # Teardown: always run `Always` steps, regardless of abort/stop, so the
        # load is turned off even when an earlier critical step failed.
        for step in teardown_steps:
            self._run_teardown_step(step, unit)
        # The engine-level `loadoff` happens in `_run_single_unit`'s `finally`,
        # so it covers this path and every early return alike.

        record.overall_passed = overall_passed and not stopped
        record.end_time = datetime.now()
        if stopped:
            # An operator Stop is not a unit failure. Archiving it would leave a
            # FAIL record and report indistinguishable from a faulty supply (in
            # CAMSTAR too), so the run is neither saved nor reported. The rows
            # stay on the report object for the on-screen table only.
            self._emit_log(
                "info",
                f"{unit.serial_number}: run stopped by operator - not saved, no report.",
            )
            return BatchUnitReport(
                unit=unit,
                record=record,
                tester_name=self.tester_name,
                employee_id=self.employee_id,
                uut_type=self.uut_type,
                test_program_name=self._logical_script_name,
                overall_result="STOPPED",
                aborted_by_operator=True,
                steps_selected=self._steps_selected,
                steps_available=self._steps_available,
            )
        self._save_record(record)
        return BatchUnitReport(
            unit=unit,
            record=record,
            tester_name=self.tester_name,
            employee_id=self.employee_id,
            uut_type=self.uut_type,
            test_program_name=self._logical_script_name,
            overall_result="PASS" if record.overall_passed else "FAIL",
            steps_selected=self._steps_selected,
            steps_available=self._steps_available,
        )

    def _check_input_connection(self, record: TestRunRecord, unit: BatchUnit) -> bool:
        self.current_test.emit("Input Connection Check")
        self.progress_test.emit(0)
        meta = getattr(self, "_script_metadata", {})
        target = _as_float(meta.get("input_target", ""), self._connected_target_v)
        tol = _as_float(meta.get("input_tol", ""), self._connected_tol_pct)
        min_v = target * (1.0 - tol / 100.0)
        max_v = target * (1.0 + tol / 100.0)
        try:
            value = self._hw_execute("readinput", [str(unit.slot_index)])
        except Exception as exc:
            self._emit_log("error", f"{unit.serial_number}: input read failed: {exc}")
            value = 0.0
        passed = min_v <= value <= max_v
        payload: TestResultPayload = {
            "value": value,
            "min": min_v,
            "max": max_v,
            "unit": "V",
            "passed": passed,
            "is_measurement": True,
        }
        self._emit_result(unit, "Input Connection Check", payload)
        record.results.append(
            {"test_name": "Input Connection Check", "loop": 1, **dict(payload)}
        )
        if not passed:
            self._emit_log(
                "error",
                f"{unit.serial_number}: input voltage {value:g} V out of range"
                f" at {unit.position_label}.",
            )
            record.overall_passed = False
        return passed

    def _check_polarity(self, record: TestRunRecord, unit: BatchUnit) -> bool:
        """Verify the UUT output polarity by voltage readback (spec §1.1.7.1).

        Pass criteria: the output voltage is **positive and actually present**.
        A bare `>= 0` test would pass a dead UUT reading 0.00 V and a
        disconnected lead sitting on the noise floor, so the reading must also
        clear `_polarity_floor_v()`.

        This is a wiring check, not an accuracy check: the upper bound is left
        open deliberately. Voltage accuracy against 22.8-25.2 V is the job of
        the Low Load and Burn-In tests. The driver raises rather than guessing
        when it cannot read, and that lands here as a FAIL.
        """
        # Engine gates are not script steps, so they announce themselves for
        # the unit's progress row (otherwise it would still read "LED").
        self.current_test.emit("Polarity Check")
        self.progress_test.emit(0)
        floor_v = self._polarity_floor_v()
        # §1.1.7.1 requires the readback with the load OFF, and 3 s to settle.
        # Enforced here rather than trusting a script step to have run first:
        # the gate now fires right after the LED check, which may be before the
        # script reaches its own `:Polarity Check Setup`.
        try:
            self._hw_execute("loadoff", [])
            self.msleep(_POLARITY_SETTLE_MS)
        except Exception as exc:
            self._emit_log(
                "error",
                f"{unit.serial_number}: could not switch the load off before the"
                f" polarity check: {exc}",
            )
        try:
            polarity = self._hw_execute("checkpolarity", [str(unit.slot_index)])
        except Exception as exc:
            self._emit_log("error", f"{unit.serial_number}: polarity check failed: {exc}")
            polarity = 0.0
        passed = polarity >= floor_v
        payload: TestResultPayload = {
            "value": polarity,
            "min": floor_v,
            # No upper bound: this is a wiring check, so any positive output
            # above the floor passes and accuracy is Tests 3/4's job. Reported
            # as a wide finite ceiling rather than `inf`, which the results
            # table and PDF would render literally as "inf".
            "max": _POLARITY_MAX_REPORTED_V,
            "unit": "V",
            "passed": passed,
            "is_measurement": True,
        }
        self._emit_result(unit, "Polarity Check", payload)
        record.results.append(
            {
                "test_name": "Polarity Check",
                "loop": 1,
                # Unlike the other engine fixture checks, this one is a *spec*
                # test (Appendix A §2.1.3.2 / Appendix B), so it must reach the
                # CAMSTAR XML. Carrying the report mapping here lets the script
                # drop its own weaker `:Polarity Check` step, which duplicated
                # this row with a laxer limit (>= 0 V vs the gate's floor).
                "report_test": "Polarity Check",
                "report_quantity": "Voltage",
                **dict(payload),
            }
        )
        if not passed:
            reason = (
                "reversed polarity"
                if polarity < 0
                else f"no output detected (below {floor_v:g} V)"
            )
            self._emit_log(
                "error",
                f"{unit.serial_number}: polarity check failed at"
                f" {unit.position_label}: read {polarity:g} V - {reason}.",
            )
            record.overall_passed = False
        return passed

    def _polarity_floor_v(self) -> float:
        """Smallest positive voltage accepted as "output present" (§1.1.7.1).

        Derived from the configured input target rather than hardcoded, so a
        station running a non-24 V product scales with it. A tenth of target
        sits far above converter leakage and meter noise while staying far
        below any genuine output, so it separates "dead" from "live" without
        second-guessing the accuracy tests.
        """
        target = _as_float(
            getattr(self, "_script_metadata", {}).get("input_target", ""),
            self._connected_target_v,
        )
        # Rounded so the threshold is an exact decimal: `24.0 * 0.1` is
        # 2.4000000000000004, which would reject a reading of exactly 2.4 V and
        # make a borderline failure report look self-contradictory.
        return round(abs(target) * _POLARITY_FLOOR_FRACTION, 6)

    @staticmethod
    def _result_row(
        test_name: str,
        passed: bool,
        value: float,
        min_v: float,
        max_v: float,
        unit: str,
    ) -> dict[str, Any]:
        return {
            "test_name": test_name,
            "loop": 1,
            "value": value,
            "min": min_v,
            "max": max_v,
            "unit": unit,
            "passed": passed,
            "is_measurement": True,
        }

    def _emit_result(
        self, unit: BatchUnit, test_name: str, payload: TestResultPayload
    ) -> None:
        row = dict(payload)
        row["serial_number"] = unit.serial_number
        row["position_label"] = unit.position_label
        row["slot_index"] = unit.slot_index
        row["load_channel"] = unit.load_channel
        self.test_result.emit(test_name, row)
        if self._secure_log is not None:
            try:
                self._secure_log.log(
                    "test_result",
                    {
                        "serial_number": unit.serial_number,
                        "position_label": unit.position_label,
                        "test_name": test_name,
                        "passed": bool(payload.get("passed")),
                        "value": payload.get("value"),
                        "min": payload.get("min"),
                        "max": payload.get("max"),
                        "unit": payload.get("unit"),
                    },
                )
            except Exception:
                pass

    @staticmethod
    def _report_fields(step: TestStep) -> dict[str, Any]:
        """CAMSTAR XML placement carried from the step onto its result row.

        Kept in one place so the normal-result and skipped-result append sites
        cannot drift apart — a row missing these silently vanishes from the
        nested XML report.
        """
        return {
            "report_test": step.report_test,
            "report_quantity": step.report_quantity,
            "report_index": step.report_index,
            "report_time": step.report_time,
            "report_time_uom": step.report_time_uom,
        }

    @staticmethod
    def _band_for_step(step: TestStep) -> str | None:
        """Map a step to its managed load band by name, or None if unmanaged.

        Checked longest-marker-first so "100w" is not shadowed by a substring
        match on "00w".
        """
        name = step.name.lower()
        for band in ("100w", "300w", "50w"):
            if band in name:
                return band
        return None

    @staticmethod
    def _step_sets_resistance(step: TestStep) -> bool:
        return any(str(c["cmd"]).lower() == "setresistance" for c in step.commands)

    def _configure_load_for_step(self, step: TestStep) -> None:
        """Ensure a managed-band step runs at the right load resistance.

        Source-of-truth order: the `.tst` script wins. The `.env`
        `LOAD_RESISTANCE_*` value is applied only as a fallback when the script
        did not set this band's resistance during the current run (e.g. a
        measurement step run without its Setup step). When the fallback fires it
        is logged, so the override is never silent.
        """
        band = self._band_for_step(step)
        if band is None:
            return  # step is not part of a managed load band
        if self._step_sets_resistance(step):
            return  # this step sets it itself — let the script win
        if band in self._tst_set_bands:
            return  # a prior (Setup) step already set it this run — inherit it
        # Partial run: nothing in the script set this band yet → .env fallback.
        resistance = self._load_res_by_band[band]
        try:
            self._hw_execute("setresistance", [f"{resistance:g}"])
            self._emit_log(
                "info",
                f"{band.upper()} load: script did not set resistance this run; "
                f"applying .env fallback {resistance:g} ohm.",
            )
        except Exception as exc:
            self._emit_log(
                "error", f"Failed to set fallback resistance for {step.name}: {exc}"
            )

    def _save_record(self, record: TestRunRecord) -> None:
        # `persist_runs` is the Capability.ARCHIVE_RUN decision, made by the
        # caller. Every construction site must pass it explicitly: it used to be
        # supplied only on the single-step path, so a Maintenance user running a
        # full sequence was correctly denied a PDF/XML but still wrote rows into
        # the production `test_runs` table. Hiding the "Save as log" checkbox did
        # not help -- a hidden box stays checked, and this method never consulted
        # it. See roles.py: Maintenance deliberately lacks ARCHIVE_RUN.
        if not self._persist_runs:
            return
        if self._link_lost():
            return  # an instrument fault, not a UUT result: see aborted_link_lost
        try:
            self._db.save_run(record)
        except Exception as exc:
            self._emit_log("error", f"ERROR: failed to save run to database: {exc!s}")

    def _run_step(self, step: TestStep) -> tuple[bool, float | None]:
        """Execute every command in `step`; return (passed, last_measurement)."""
        last_measurement: float | None = None
        n = max(1, len(step.commands))
        # Lets `_execute_command` recognise the LED step's PromptYesNo.
        self._current_step = step
        self._fail_note = ""

        step_ohms: float | None = None
        for idx, cmd in enumerate(step.commands, start=1):
            if self._stop_requested:
                return False, last_measurement
            try:
                result = self._execute_command(cmd)
                if result is not None:
                    last_measurement = result
                name = str(cmd["cmd"]).lower()
                # Record that the script itself set this band's resistance, so
                # later measurement steps in the same band inherit it instead of
                # being forced to the .env fallback.
                if name == "setresistance":
                    band = self._band_for_step(step)
                    if band:
                        self._tst_set_bands.add(band)
                    try:
                        step_ohms = float((cmd.get("args") or [""])[0])
                    except (TypeError, ValueError):
                        step_ohms = None
                elif name == "loadon" and step_ohms:
                    self._verify_load(step_ohms)
            except _MonitorOutOfRange as exc:
                # The monitored voltage left its band mid-delay. Report the
                # offending reading as this step's measurement so the results
                # table shows the actual value against the limits.
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: command {cmd['cmd']!r} raised: {exc!s}",
                )
                self._fail_note = f"at {exc.elapsed_s:.1f} s"
                return False, exc.value
            except InvalidReadingError as exc:
                # An instrument code (e.g. 9999), not a measurement: fail the
                # step with no value, and say it was the instrument.
                self._emit_log("error", f"ERROR in {step.name}: {exc!s}")
                self._fail_note = (
                    f"invalid reading from the instrument ({exc.value:g}) - "
                    "check the load's front panel"
                )
                return False, None
            except _MonitorReadFailed as exc:
                # The instrument stopped answering mid-delay and did not come
                # back within the driver's reconnect budget. There is no
                # reading to report, but the step must still fail: the spec
                # requires monitoring for the whole duration, and an unmonitored
                # remainder cannot be certified as passing.
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: monitoring aborted - {exc!s}",
                )
                self._fail_note = (
                    f"no reading - instrument not responding ({exc.elapsed_s:.1f} s)"
                )
                return False, None
            except Exception as exc:
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: command {cmd['cmd']!r} raised: {exc!s}",
                )
                self._fail_note = f"{cmd['cmd']} failed: {str(exc)[:80]}"
                return False, last_measurement

            self.progress_test.emit(min(99, int((idx / n) * 100)))

        if step.has_limits:
            if last_measurement is None:
                self._emit_log(
                    "error",
                    f"{step.name}: validation error - Limits set but no "
                    "measurement command executed; marking FAIL.",
                )
                return False, None
            assert step.min_val is not None and step.max_val is not None
            in_spec = step.min_val <= last_measurement <= step.max_val
            return in_spec, last_measurement

        # PromptYesNo without explicit Limits: Yes (1.0) → pass, No (0.0) → fail
        if last_measurement is not None and self._has_promptyesno(step):
            return last_measurement > 0.5, last_measurement

        return True, last_measurement

    def _retry_connect(self, first_error: Exception) -> bool:
        """Retry `connect()` for a bounded time. True once connected.

        A dropped or late-plugged LAN cable is the usual reason a run cannot
        start, and the load needs a moment after the link returns. Retrying here
        means the operator plugs the cable back in and presses Start again
        successfully, instead of the app being stuck.

        This deliberately does **not** fall back to `MockHardware`. That is what
        it used to do, and it was the reason reconnecting the cable never helped:
        the real driver was replaced by a simulator for the rest of the session,
        so every later read came from the mock — reporting a healthy 24 V on
        every channel, whether or not a UUT was attached. Demo mode is a
        deliberate choice via `HARDWARE_BACKEND=mock`, never an automatic
        consolation prize during a production run.
        """
        deadline = time.monotonic() + _CONNECT_RETRY_BUDGET_S
        attempt = 0
        last = first_error
        self._emit_log(
            "error",
            f"Hardware connection failed: {last}. Retrying for up to "
            f"{_CONNECT_RETRY_BUDGET_S:g}s - check the instrument's cable and power.",
        )
        while time.monotonic() < deadline and not self._stop_requested:
            attempt += 1
            time.sleep(_CONNECT_RETRY_DELAY_S)
            try:
                if self._hw.connect():
                    self._emit_log(
                        "info", f"Hardware connected on retry {attempt}."
                    )
                    return True
            except Exception as exc:
                last = exc
        self._emit_log(
            "error",
            f"Could not connect to the instrument after "
            f"{_CONNECT_RETRY_BUDGET_S:g}s ({attempt} attempts): {last}. "
            "Aborting the run - no test data is produced. Check the cable, "
            "power and PRODIGIT_VISA_RESOURCE, then start again.",
        )
        self.connect_error = str(last)
        return False

    def _force_load_off(self, unit: BatchUnit | None = None) -> bool:
        """Command the electronic load off, whatever else happened. Returns success.

        The script's `Always` teardown step already sends `loadoff`, but three
        things can leave a unit dissipating full power anyway:

        * the script has no `Always`/`loadoff` step at all (most `.tst` files
          in `data/` do not),
        * the run died before reaching teardown, or
        * comms were down when teardown ran — pulling the load's LAN cable
          aborts the test but leaves it drawing 300 W.

        So this is belt-and-braces: it runs from the engine, not from script
        content, on every exit path. `_io_with_resilience` inside the driver
        reconnects and retries for its budget, which covers the case where the
        cable is back by the time we get here.

        On failure it emits `load_shutdown_failed` so the operator is told to
        intervene physically. Software cannot switch off a load it cannot reach;
        the honest outcome is a loud warning, not a silent log line.
        """
        # The channel is named so the operator knows which load to switch off.
        label = f" [CH{unit.slot_index} {unit.serial_number}]" if unit is not None else ""

        def _send() -> str:
            # The 3300G applies LOAD OFF to whichever channel was selected last.
            # Select this unit's channel first, inside the same lock: in a
            # parallel run the last selection usually belongs to another unit
            # that just polled its voltage, and switching *its* load off mid
            # burn-in let it pass a 300 W test it never ran — while this unit's
            # load stayed on.
            if unit is not None and hasattr(self._hw, "activate_slot"):
                self._hw.activate_slot(
                    unit.slot_index, load_serial_number=unit.load_serial_number
                )
            # Read the state back rather than trusting the write: on the
            # station three units logged "Load off confirmed" and stayed on.
            verify = getattr(self._hw, "load_off_verified", None)
            if callable(verify):
                return str(verify())
            self._hw.execute_command("loadoff", [])
            return "not read back"

        try:
            if self._hw_lock is not None:
                with self._hw_lock:
                    reply = _send()
            else:
                reply = _send()
            self._emit_log("info", f"Load off verified{label} (LOAD? -> {reply}).")
            return True
        except Exception as exc:
            reason = (
                f"Could not switch the electronic load OFF{label}: {exc}. "
                "The UUT may still be under full load - disconnect it manually "
                "and check the load's front panel."
            )
            self._emit_log("error", reason)
            self.load_shutdown_failed.emit(reason)
            return False

    def _run_teardown_step(self, step: TestStep, unit: BatchUnit) -> None:
        """Run an `Always` step's commands unconditionally (safety teardown).

        Bypasses the stop/abort guards so a final `loadoff` always reaches the
        hardware, even after a critical failure or a user stop. Errors are
        logged but do not change the run verdict, and the step is not added to
        the results table (teardown is housekeeping, not a measured test).
        """
        self._emit_log(
            "cmd", f"Teardown: {step.name}... [Unit: {unit.serial_number}]"
        )
        for cmd in step.commands:
            try:
                self._execute_command(cmd)
            except Exception as exc:
                self._emit_log(
                    "error",
                    f"Teardown {step.name}: command {cmd['cmd']!r} raised: {exc!s}",
                )

    @staticmethod
    def _has_promptyesno(step: TestStep) -> bool:
        return any(str(cmd["cmd"]).lower() == "promptyesno" for cmd in step.commands)

    @classmethod
    def is_led_step(cls, step: TestStep) -> bool:
        """True for the manual LED check that gates the rest of the run.

        Detected by its `PromptYesNo` — the operator-answered step — rather than
        by name, so a renamed or translated step still gates correctly. Falls
        back to the spec's `Report` name for a step that asks nothing.
        """
        if cls._has_promptyesno(step):
            return True
        return str(getattr(step, "report_test", "")).strip() == "LED Indication Test"

    def _execute_command(self, cmd: dict) -> float | None:
        name = str(cmd["cmd"]).lower()
        args = cmd["args"]

        if name == "delay":
            if not args:
                raise ValueError("'Delay' requires a millisecond argument")
            total_ms = int(float(args[0]))
            monitor_min = float(args[1]) if len(args) >= 2 else None
            monitor_max = float(args[2]) if len(args) >= 3 else None
            # Poll/monitor cadence during the wait. 300 ms keeps continuous
            # voltage monitoring responsive without flooding the load's comms
            # link with queries over long burn-in delays; 500 ms when units
            # share the connection (see _SHARED_POLL_S).
            shared = self._hw_lock is not None
            chunk_s = _SHARED_POLL_S if shared else 0.3
            monitored = monitor_min is not None and monitor_max is not None
            live_interval_s = _SHARED_LIVE_INTERVAL_S if shared else _LIVE_READING_INTERVAL_MS / 1000
            live_warned = False
            live_retry_at = 0.0
            self._live_cache = {}  # no A/W from the previous step in the log
            # Timed against the wall clock, not by counting polls. Counting
            # added a fixed 300 ms per poll but ignored the time each poll's
            # instrument queries took, so a "30 min" burn-in ran 10-20 % long
            # (more in a parallel run, where every unit queues on one
            # connection) and its t=5/15/30 report labels were wrong. Each poll
            # now sleeps only what remains until the next 300 ms slot, and the
            # wait ends at the deadline however slow the comms are.
            start = time.monotonic()
            deadline = start + total_ms / 1000
            next_poll = start
            # First sample one interval in, not right after LOAD ON: a load that
            # has not come up yet reads 0 W.
            last_live = start
            while not self._stop_requested:
                now = time.monotonic()
                if now >= deadline:
                    break
                if next_poll < now - chunk_s:
                    # Fell behind (e.g. a slow reconnect): resume the cadence
                    # from now rather than firing a burst of back-to-back polls.
                    next_poll = now
                next_poll += chunk_s
                self.msleep(max(0, int((min(next_poll, deadline) - now) * 1000)))
                elapsed_ms = (time.monotonic() - start) * 1000
                self.progress_test.emit(min(99, int(elapsed_ms * 100 / max(1, total_ms))))
                if self._step_weight_s:
                    self._emit_total(min(1.0, elapsed_ms / 1000 / self._step_weight_s))
                if monitored:
                    # A read failure here has already survived the driver's
                    # reconnect budget (`_io_with_resilience`: retry + reconnect
                    # for _RECONNECT_BUDGET_S). Reaching this point therefore
                    # means the instrument is genuinely gone, not blipping.
                    # Fail the step: silently skipping the check would leave the
                    # rest of a 30-minute burn-in unmonitored while the run
                    # still passed, defeating spec §1.1.7.3's requirement that
                    # monitoring run for the *entire* duration.
                    try:
                        v = self._hw_execute("measvoltage", [])
                    except InvalidReadingError:
                        raise  # reported as an instrument fault, see _run_step
                    except Exception as exc:
                        raise _MonitorReadFailed(exc, elapsed_ms / 1000) from exc
                    if v is None:
                        raise _MonitorReadFailed(
                            RuntimeError("no voltage returned"), elapsed_ms / 1000
                        )
                    self._emit_live({"V": v})
                    if not (monitor_min <= v <= monitor_max):
                        raise _MonitorOutOfRange(
                            v, monitor_min, monitor_max, elapsed_ms / 1000
                        )
                # After the monitoring check, so the extra queries never delay
                # a trip. Voltage is skipped when monitoring just read it.
                now = time.monotonic()
                if now >= live_retry_at and now - last_live >= live_interval_s:
                    last_live = now
                    if not self._sample_live_readings(include_voltage=not monitored):
                        live_retry_at = now + _LIVE_RETRY_S
                        if not live_warned:
                            live_warned = True
                            slot = self._current_unit.slot_index if self._current_unit else "?"
                            self._emit_log(
                                "error",
                                f"Live readings unavailable for CH{slot}; "
                                f"retrying every {_LIVE_RETRY_S:g} s.",
                            )
            return None

        if name == "log":
            self.script_log.emit(" ".join(args))
            return None

        if name == "prompt":
            # Check *after* clearing, not before: `stop()` may have set the
            # event in the gap between the check and the clear, and clearing
            # would then discard the only wake-up this thread will ever get.
            self._prompt_event.clear()
            if self._stop_requested:
                return None
            self.prompt_request.emit(" ".join(args))
            self._wait_for_operator(self._prompt_event)
            return None

        if name == "promptyesno":
            # The LED check is answered in the pre-test dialog, before the run
            # starts. Use that answer instead of asking again: the step still
            # executes, so LED stays Test 1 on the report and the polarity gate
            # still fires right after it (spec §1.1.4.2, Appendix A).
            unit = self._current_unit
            step = self._current_step
            if (
                unit is not None
                and unit.led_verdict is not None
                and step is not None
                and self.is_led_step(step)
            ):
                self._emit_log(
                    "info",
                    f"LED Indication: {'PASS' if unit.led_verdict else 'FAIL'}"
                    " (answered at pre-test setup)",
                )
                return 1.0 if unit.led_verdict else 0.0
            self._yesno_event.clear()
            # Same race as above. Losing this wake-up is what left the process
            # alive with an invisible window after the operator closed the app
            # while a prompt was pending: the runner blocked forever on an
            # answer that could no longer arrive, and Python will not exit
            # while a non-daemon QThread is still running.
            if self._stop_requested:
                return None
            self._yesno_answer = False
            msg = " ".join(args)
            if self._current_unit is not None:
                msg = (
                    f"[{self._current_unit.serial_number}"
                    f" - {self._current_unit.position_label}]\n\n{msg}"
                )
            self.prompt_yesno_request.emit(msg)
            self._wait_for_operator(self._yesno_event)
            answer_val = 1.0 if self._yesno_answer else 0.0
            self._emit_log(
                "info",
                f"PromptYesNo response: {'Yes' if self._yesno_answer else 'No'}"
                f" ({answer_val:g})",
            )
            return answer_val

        value = self._hw_execute(name, args)
        live_key = _LIVE_READING_KEYS.get(name)
        if live_key is not None and value is not None:
            self._emit_live({live_key: value})
        return value if name in self._hw.measurement_commands else None

    def resume(self) -> None:
        """Unblock a thread parked on a `Prompt`."""
        self._prompt_event.set()

    def submit_yesno_answer(self, answer: bool) -> None:
        """Supply the Yes/No answer and unblock the runner."""
        self._yesno_answer = answer
        self._yesno_event.set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume_pause(self) -> None:
        self._pause_event.set()

    def _wait_for_operator(self, event: Event) -> None:
        """Block until the operator answers, or until the run is stopped.

        An operator prompt has no time limit — a technician may reasonably take
        minutes over the LED check — so this waits indefinitely by design. What
        it must never do is outlive the application: a bare `event.wait()`
        cannot notice `stop()` if the set/clear ordering goes against it, and a
        non-daemon QThread stuck here keeps the whole interpreter alive after
        the window has closed, leaving an invisible process holding the
        instrument and the single-instance lock.

        Polling in short slices rather than waiting outright means the stop
        flag is honoured within one slice no matter how the race falls.
        """
        while not self._stop_requested:
            if event.wait(timeout=_OPERATOR_PROMPT_POLL_S):
                return

    def stop(self) -> None:
        self._stop_requested = True
        # End the driver's reconnect attempts now: with the load's cable out,
        # each command used to retry for 15 s and a Stop took minutes.
        abort = getattr(self._hw, "abort_io", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        self._prompt_event.set()
        self._yesno_event.set()
        self._pause_event.set()

    def report_snapshot(self) -> tuple[dict, list[dict]]:
        """Header meta and result rows for the last unit (PDF/CSV after finished)."""
        if not self._reports:
            return {}, []
        last = self._reports[-1]
        return last.meta(), last.rows()

    def report_snapshots(self) -> list[BatchUnitReport]:
        """All per-unit reports from this batch run."""
        return list(self._reports)
