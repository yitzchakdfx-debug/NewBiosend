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
    LOAD_SERIALS,
)
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
    ) -> None:
        super().__init__()
        self._script_path = Path(script_path)
        self._logical_script_name = logical_script_name.strip() or self._script_path.stem
        self._selected_names = set(selected_names)
        self._loop_count = max(1, loop_count)
        self._stop_on_fail = stop_on_fail
        self._script_manager = script_manager or ScriptManager()
        self._hw: BaseDriver = driver or create_driver()
        self._hw_lock: Lock | None = hw_lock
        self._skip_hw_lifecycle: bool = skip_hw_lifecycle
        # A maintenance run is a diagnostic, not a production test: the
        # station-integrity gates (24 V presence, polarity) would abort it and
        # inject rows for checks the user did not ask for, and its results must
        # not enter the production record.
        self._production_flow: bool = production_flow
        self._persist_runs: bool = persist_runs
        self._stop_requested = False
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
        self._current_test_name = ""
        if batch_units:
            self._batch_units = list(batch_units)
        else:
            load_serial = LOAD_SERIALS[0] if LOAD_SERIALS else "LOAD-CH1"
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
        """Execute a hardware command, re-selecting the slot atomically when shared."""
        if self._hw_lock is not None and self._current_unit is not None:
            with self._hw_lock:
                self._hw.activate_slot(
                    self._current_unit.slot_index,
                    load_serial_number=self._current_unit.load_serial_number,
                )
                return self._hw.execute_command(command, args)
        return self._hw.execute_command(command, args)

    def run(self) -> None:
        self.progress_total.emit(0)
        self.progress_test.emit(0)
        self.current_test.emit("")

        try:
            if not self._skip_hw_lifecycle:
                try:
                    if not self._hw.connect():
                        self._emit_log("error", "Hardware connect() returned False; aborting.")
                        return
                except HardwareError as exc:
                    self._emit_log("error", f"Hardware connection failed: {exc}")
                    from drivers.mock_hardware import MockHardware
                    if not isinstance(self._hw, MockHardware):
                        self._emit_log("info", "⚠ Falling back to demo mode (MockHardware).")
                        self._hw = MockHardware()
                        self._hw.connect()
                    else:
                        return

            try:
                doc = self._script_manager.load_document(self._script_path)
            except ScriptParseError as exc:
                self._emit_log("error", f"Script load failed at line {exc.line_no}: {exc.msg}")
                return
            except (OSError, ValueError) as exc:
                self._emit_log("error", f"Script load failed: {exc}")
                return

            self._script_metadata = doc.metadata
            steps = [s for s in doc.steps if s.name in self._selected_names]
            if not steps:
                self._emit_log("info", "No steps selected to run.")
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
            self._save_record(record)
            return BatchUnitReport(
                unit=unit,
                record=record,
                tester_name=self.tester_name,
                employee_id=self.employee_id,
                uut_type=self.uut_type,
                test_program_name=self._logical_script_name,
                overall_result="FAIL",
            )

        if self._production_flow and not self._check_input_connection(record, unit):
            record.end_time = datetime.now()
            self._save_record(record)
            return BatchUnitReport(
                unit=unit,
                record=record,
                tester_name=self.tester_name,
                employee_id=self.employee_id,
                uut_type=self.uut_type,
                test_program_name=self._logical_script_name,
                overall_result="FAIL",
            )

        polarity_ok = self._check_polarity(record, unit) if self._production_flow else True
        if not polarity_ok:
            record.end_time = datetime.now()
            msg = (
                f"Polarity check failed for {unit.serial_number}"
                f" at {unit.position_label}. Skipping report for this unit."
            )
            self.unit_alert.emit(msg)
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
            )

        # Steps marked `Always` are teardown steps (e.g. a final loadoff). They
        # are pulled out of the normal sequence and run unconditionally at the
        # end — even after a critical abort or a user stop — so the load is never
        # left energized after a failure.
        normal_steps = [s for s in steps if not getattr(s, "always", False)]
        teardown_steps = [s for s in steps if getattr(s, "always", False)]

        total_steps = len(normal_steps) * self._loop_count
        completed = 0
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

            for step in normal_steps:
                self._pause_event.wait()
                if self._stop_requested:
                    abort_loops = True
                    break

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
                if not getattr(step, "hidden", False):
                    self._emit_result(unit, step.name, payload)
                    record.results.append(
                        {
                            "test_name": step.name,
                            "loop": loop_number,
                            **self._report_fields(step),
                            **dict(payload),
                        }
                    )

                self.progress_test.emit(100)
                completed += 1
                step_progress = int((completed / max(1, total_steps)) * 100)
                self.progress_total.emit(
                    int(
                        (((unit_index - 1) + (step_progress / 100.0)) / max(1, total_units))
                        * 100
                    )
                )

                if step.is_critical and not passed:
                    self._emit_log(
                        "error",
                        f"CRITICAL ABORT: {step.name} failed for {unit.serial_number}.",
                    )
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
            executed_names = {r["test_name"] for r in record.results}
            for step in normal_steps:
                if step.name not in executed_names and not getattr(step, "hidden", False):
                    na_payload: TestResultPayload = {
                        "value": 0.0,
                        "min": 0.0,
                        "max": 0.0,
                        "unit": step.unit,
                        "passed": False,
                        "is_measurement": False,
                    }
                    self._emit_result(unit, step.name, na_payload)
                    record.results.append(
                        {
                            "test_name": step.name,
                            "loop": loop_number,
                            "skipped": True,
                            **self._report_fields(step),
                            **dict(na_payload),
                        }
                    )

        # Teardown: always run `Always` steps, regardless of abort/stop, so the
        # load is turned off even when an earlier critical step failed.
        for step in teardown_steps:
            self._run_teardown_step(step, unit)

        record.overall_passed = overall_passed and not self._stop_requested
        record.end_time = datetime.now()
        self._save_record(record)
        return BatchUnitReport(
            unit=unit,
            record=record,
            tester_name=self.tester_name,
            employee_id=self.employee_id,
            uut_type=self.uut_type,
            test_program_name=self._logical_script_name,
            overall_result="PASS" if record.overall_passed else "FAIL",
        )

    def _check_input_connection(self, record: TestRunRecord, unit: BatchUnit) -> bool:
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
        floor_v = self._polarity_floor_v()
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
                f" {unit.position_label}: read {polarity:g} V — {reason}.",
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
        if not self._persist_runs:
            return
        try:
            self._db.save_run(record)
        except Exception as exc:
            self._emit_log("error", f"ERROR: failed to save run to database: {exc!s}")

    def _run_step(self, step: TestStep) -> tuple[bool, float | None]:
        """Execute every command in `step`; return (passed, last_measurement)."""
        last_measurement: float | None = None
        n = max(1, len(step.commands))

        for idx, cmd in enumerate(step.commands, start=1):
            if self._stop_requested:
                return False, last_measurement
            try:
                result = self._execute_command(cmd)
                if result is not None:
                    last_measurement = result
                # Record that the script itself set this band's resistance, so
                # later measurement steps in the same band inherit it instead of
                # being forced to the .env fallback.
                if str(cmd["cmd"]).lower() == "setresistance":
                    band = self._band_for_step(step)
                    if band:
                        self._tst_set_bands.add(band)
            except _MonitorOutOfRange as exc:
                # The monitored voltage left its band mid-delay. Report the
                # offending reading as this step's measurement so the results
                # table shows the actual value against the limits.
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: command {cmd['cmd']!r} raised: {exc!s}",
                )
                return False, exc.value
            except _MonitorReadFailed as exc:
                # The instrument stopped answering mid-delay and did not come
                # back within the driver's reconnect budget. There is no
                # reading to report, but the step must still fail: the spec
                # requires monitoring for the whole duration, and an unmonitored
                # remainder cannot be certified as passing.
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: monitoring aborted — {exc!s}",
                )
                return False, None
            except Exception as exc:
                self._emit_log(
                    "error",
                    f"ERROR in {step.name}: command {cmd['cmd']!r} raised: {exc!s}",
                )
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

    def _execute_command(self, cmd: dict) -> float | None:
        name = str(cmd["cmd"]).lower()
        args = cmd["args"]

        if name == "delay":
            if not args:
                raise ValueError("'Delay' requires a millisecond argument")
            total_ms = int(float(args[0]))
            monitor_min = float(args[1]) if len(args) >= 2 else None
            monitor_max = float(args[2]) if len(args) >= 3 else None
            elapsed_ms = 0
            # Poll/monitor cadence during the wait. 300 ms keeps continuous
            # voltage monitoring responsive without flooding the load's comms
            # link with queries over long burn-in delays.
            chunk = 300
            while elapsed_ms < total_ms and not self._stop_requested:
                self.msleep(min(chunk, total_ms - elapsed_ms))
                elapsed_ms += chunk
                self.progress_test.emit(min(99, int(elapsed_ms * 100 / total_ms)))
                if monitor_min is not None and monitor_max is not None:
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
                    except Exception as exc:
                        raise _MonitorReadFailed(exc, elapsed_ms / 1000) from exc
                    if v is None:
                        raise _MonitorReadFailed(
                            RuntimeError("no voltage returned"), elapsed_ms / 1000
                        )
                    if not (monitor_min <= v <= monitor_max):
                        raise _MonitorOutOfRange(
                            v, monitor_min, monitor_max, elapsed_ms / 1000
                        )
            return None

        if name == "log":
            self.script_log.emit(" ".join(args))
            return None

        if name == "prompt":
            self._prompt_event.clear()
            self.prompt_request.emit(" ".join(args))
            self._prompt_event.wait()
            return None

        if name == "promptyesno":
            self._yesno_event.clear()
            self._yesno_answer = False
            msg = " ".join(args)
            if self._current_unit is not None:
                msg = (
                    f"[{self._current_unit.serial_number}"
                    f" — {self._current_unit.position_label}]\n\n{msg}"
                )
            self.prompt_yesno_request.emit(msg)
            self._yesno_event.wait()
            answer_val = 1.0 if self._yesno_answer else 0.0
            self._emit_log(
                "info",
                f"PromptYesNo response: {'Yes' if self._yesno_answer else 'No'}"
                f" ({answer_val:g})",
            )
            return answer_val

        value = self._hw_execute(name, args)
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

    def stop(self) -> None:
        self._stop_requested = True
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
