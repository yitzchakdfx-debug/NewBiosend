"""Group a script's steps into the spec's tests for the test list.

The test list used to show every script step — Setup, one row per measured
quantity, Cleanup — 20 rows for the four tests spec Rev.1.1 defines (Appendix A,
Tests 1-4). The engine-run Polarity Check did not appear at all, and a role
allowed to untick steps could drop a test's Setup and run its measurements with
the load never switched on.

A group is one spec test. Its steps are found from what the script already
declares:

* a step with `Report X` belongs to test X;
* a `Hidden` step without `Report` (a Setup) belongs to the test that follows it;
* `Always` steps (teardown) belong to no group — they always run;
* a visible step without `Report` (older scripts) is a group of its own.

The Polarity Check is not a script step but an engine gate, so it gets a
synthetic group placed right after the LED test, where the engine runs it.

Selecting or deselecting works on whole groups only. Burn-In's t1/t2/t3 cannot
be dropped individually: its waits are cumulative, so skipping a point would
shorten the burn-in and mislabel every later time point on the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from logic.models import TestStep

POLARITY_TEST_NAME = "Polarity Check"

_UOM_SHORT = {"min": "m", "minute": "m", "minutes": "m", "s": "s", "sec": "s", "ms": "ms", "hr": "h"}


@dataclass
class TestGroup:
    """One row of the test list: a spec test and the script steps it runs."""

    name: str
    steps: list[TestStep] = field(default_factory=list)
    is_polarity: bool = False
    detail: str = ""

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]


def _resistance(steps: Iterable[TestStep]) -> tuple[str, str]:
    """(mode, ohms) from the group's setmode / setresistance commands."""
    mode, ohms = "", ""
    for step in steps:
        for cmd in step.commands:
            name = str(cmd.get("cmd", "")).lower()
            args = cmd.get("args") or []
            if name == "setmode" and args and not mode:
                mode = str(args[0]).upper()
            elif name == "setresistance" and args and not ohms:
                ohms = str(args[0])
    return mode, ohms


def _time_points(steps: Iterable[TestStep]) -> list[tuple[str, str]]:
    """Distinct (value, uom) report time points, in script order."""
    seen: list[tuple[str, str]] = []
    for step in steps:
        if step.report_time and (step.report_time, step.report_time_uom) not in seen:
            seen.append((step.report_time, step.report_time_uom))
    return seen


def _detail(group: TestGroup, expected: str, is_led: bool, polarity_settle_s: float) -> str:
    """The one-line description shown under the test's name."""
    if group.is_polarity:
        text = f"{polarity_settle_s:g} s - load OFF"
        return f"{text}, {expected}" if expected else text
    if is_led:
        return f"manual - {expected}" if expected else "manual"

    parts: list[str] = []
    mode, ohms = _resistance(group.steps)
    if ohms:
        # As written in the script ("2.0", "5.76"), matching the spec's wording.
        parts.append(f"{mode + ' ' if mode else ''}{ohms} Ω")
    points = _time_points(group.steps)
    if len(points) == 1:
        value, uom = points[0]
        parts.append(f"t1 @ {value} {uom}".strip())
    elif points:
        parts.append(" · ".join(
            f"t{i} {value}{_UOM_SHORT.get(uom.strip().lower(), uom)}"
            for i, (value, uom) in enumerate(points, start=1)
        ))
    return " - ".join(parts)


def build_test_groups(
    steps: list[TestStep],
    is_led_step: Callable[[TestStep], bool],
    expected_text: Callable[[str], str] = lambda name: "",
    polarity_settle_s: float = 3.0,
) -> list[TestGroup]:
    """Group `steps` into spec tests, in script order, plus the Polarity Check.

    `is_led_step` is the engine's LED detector (it places the Polarity group);
    `expected_text(test_name)` supplies the "Expected Value" line from the PDF
    template, so admin edits there show up in the list too.
    """
    groups: list[TestGroup] = []
    by_name: dict[str, TestGroup] = {}
    pending_setup: list[TestStep] = []

    for step in steps:
        if step.always:
            continue
        key = (step.report_test or "").strip()
        if not key and step.hidden:
            pending_setup.append(step)  # joins the next test
            continue
        key = key or step.name
        group = by_name.get(key)
        if group is None:
            group = by_name[key] = TestGroup(key)
            groups.append(group)
        group.steps.extend(pending_setup)
        pending_setup = []
        group.steps.append(step)

    if pending_setup:  # Setup steps with no test after them
        if groups:
            groups[-1].steps.extend(pending_setup)
        else:
            groups.append(TestGroup(pending_setup[0].name, list(pending_setup)))

    led_index = next(
        (i for i, g in enumerate(groups) if any(is_led_step(s) for s in g.steps)), None
    )
    polarity = TestGroup(POLARITY_TEST_NAME, is_polarity=True)
    groups.insert(0 if led_index is None else led_index + 1, polarity)

    for group in groups:
        is_led = any(is_led_step(s) for s in group.steps)
        group.detail = _detail(group, expected_text(group.name), is_led, polarity_settle_s)
    return groups


def always_step_names(steps: list[TestStep]) -> list[str]:
    """Teardown steps: part of every selection, never shown in the list."""
    return [s.name for s in steps if s.always]
