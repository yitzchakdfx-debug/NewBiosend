"""Domain models for limits, parsed test steps, and result payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict

from logic.roles import ALL_ROLES


@dataclass(frozen=True, slots=True)
class TestLimit:
    """Specification limits for a named test (legacy JSON-driven format)."""

    test_name: str
    min_val: float
    max_val: float
    unit: str


@dataclass(slots=True)
class TestStep:
    """One parsed step from a `.tst` script.

    `commands` is a list of `{"cmd": str, "args": list[str]}` dicts. A step
    with `min_val` / `max_val` set is a measured test; otherwise it is a
    setup/teardown block whose pass/fail is determined solely by whether all
    commands ran without raising.
    """

    name: str
    commands: list[dict] = field(default_factory=list)
    min_val: float | None = None
    max_val: float | None = None
    unit: str = ""
    is_critical: bool = False
    retry_count: int = 0  # extra attempts beyond the first
    hidden: bool = False  # Hidden — step runs but is not shown in the results table
    group: str = ""       # Group <name> — groups related steps under one header in the report
    always: bool = False  # Always — teardown step that runs even after a critical abort or stop

    # --- CAMSTAR XML placement (spec Rev.1.1 Appendix B) ---
    # A step declares where its measurement lands in the nested XML tree. The
    # mapping lives in the step (and therefore in the versioned .tst content)
    # rather than in an external file, so renaming a step cannot silently
    # detach it from the report.
    report_test: str = ""       # Report <name> — which <Test> node this feeds
    report_quantity: str = ""   # Quantity Voltage|Current|Power|Resistance
    report_index: int = 0       # Measurement <n>; 0 = singular <MeasuredOutput>
    report_time: str = ""       # TimePoint <value> <uom>
    report_time_uom: str = ""

    @property
    def has_limits(self) -> bool:
        return self.min_val is not None and self.max_val is not None

    @property
    def has_report_mapping(self) -> bool:
        return bool(self.report_test)


@dataclass
class ScriptDocument:
    """Parsed script payload: preamble metadata and executable steps."""

    metadata: dict[str, str] = field(default_factory=dict)
    steps: list[TestStep] = field(default_factory=list)


class TestResultPayload(TypedDict):
    """Structured row data emitted from the test thread to the UI."""

    value: float
    min: float
    max: float
    unit: str
    passed: bool
    is_measurement: bool


@dataclass(frozen=True, slots=True)
class BatchUnit:
    """One UUT in a batch run, bound to a fixed physical position/load."""

    serial_number: str
    slot_index: int
    position_label: str
    load_channel: str
    load_serial_number: str


_VALID_ROLES: frozenset[str] = frozenset(ALL_ROLES)


def normalize_role(raw: str) -> str:
    """Normalize a role string to Title Case and validate it.

    Raises ValueError for unknown roles so callers get a single consistent
    error message without duplicating the role list.
    """
    normalized = raw.strip().title()
    if normalized not in _VALID_ROLES:
        raise ValueError(
            f"Invalid role {raw!r}. Expected one of: {', '.join(sorted(_VALID_ROLES))}"
        )
    return normalized


@dataclass
class TestRunRecord:
    """Final payload for one completed test run before database insertion."""

    operator: str
    part_number: str
    serial_number: str
    overall_passed: bool = True
    batch_label: str = ""
    batch_index: int = 1
    slot_index: int = 1
    position_label: str = ""
    load_channel: str = ""
    load_serial_number: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BatchUnitReport:
    """Finished per-unit run snapshot used for DB persistence and reporting."""

    unit: BatchUnit
    record: TestRunRecord
    tester_name: str
    employee_id: str
    uut_type: str
    test_program_name: str
    overall_result: str
    alert_message: str = ""
    should_generate_report: bool = True
    #: Steps actually run vs. steps the script defines. A Technician may untick
    #: steps before a run (`Capability.SELECT_STEPS`), and deselected steps are
    #: filtered out before execution — so unlike a step skipped *after* a
    #: failure they leave no N/A row and would otherwise be invisible. Spec
    #: §1.1.4.3 grants the production roles no configuration privileges, so a
    #: reduced run must at minimum be disclosed on the report rather than
    #: reading as a full pass. 0 means "not recorded".
    steps_selected: int = 0
    steps_available: int = 0

    def step_coverage(self) -> str:
        """`"17 / 20 (partial)"` for a reduced run, `"20 / 20"` for a full one."""
        if not self.steps_available:
            return ""
        text = f"{self.steps_selected} / {self.steps_available}"
        if self.steps_selected < self.steps_available:
            text += " (partial)"
        return text

    def meta(self) -> dict[str, Any]:
        end = self.record.end_time or datetime.now()
        return {
            "steps_executed": self.step_coverage(),
            "overall_result": self.overall_result,
            "tester_name": self.tester_name,
            "employee_id": self.employee_id,
            "test_program_name": self.test_program_name,
            "uut_type": self.uut_type,
            "part_number": self.record.part_number,
            "serial_number": self.record.serial_number,
            "start_time": self.record.start_time.isoformat(timespec="seconds"),
            "end_time": end.isoformat(timespec="seconds"),
            "position_label": self.unit.position_label,
            "slot_index": self.unit.slot_index,
            "load_channel": self.unit.load_channel,
            "load_serial_number": self.unit.load_serial_number,
            "alert_message": self.alert_message,
        }

    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.record.results:
            rows.append(
                {
                    "test_name": item.get("test_name", ""),
                    "value": item.get("value"),
                    "min": item.get("min"),
                    "max": item.get("max"),
                    "unit": item.get("unit", ""),
                    "passed": bool(item.get("passed")),
                    "is_measurement": item.get("is_measurement", True),
                    "loop": int(item.get("loop", 1)),
                    # A skipped step is N/A, not a measured 0.0 failure. The
                    # report layer must be able to tell them apart.
                    "skipped": bool(item.get("skipped", False)),
                    "report_test": item.get("report_test", ""),
                    "report_quantity": item.get("report_quantity", ""),
                    "report_index": int(item.get("report_index", 0) or 0),
                    "report_time": item.get("report_time", ""),
                    "report_time_uom": item.get("report_time_uom", ""),
                    "position_label": self.unit.position_label,
                    "slot_index": self.unit.slot_index,
                    "load_channel": self.unit.load_channel,
                    "load_serial_number": self.unit.load_serial_number,
                }
            )
        return rows
