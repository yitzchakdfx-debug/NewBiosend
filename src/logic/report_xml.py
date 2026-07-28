"""CAMSTAR test-report XML, built from report-mapped result rows.

The engine emits one flat row per executed step. The XML schema the customer
mandates (spec Rev.1.1 Appendix B) is nested: one `<Test>` per logical test,
each holding typed, optionally time-stamped measurements. The bridge is the
`report_*` fields a step carries from its `.tst` `Report` / `Quantity` /
`Measurement` / `TimePoint` directives — see `logic.script_manager`.

Rows without a `report_test` (engine fixture checks such as Input Connection
Check and Slot Activation) are intentionally absent from this report; they
verify the test station, not the UUT.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from xml.dom import minidom

from config import TEST_LOAD_MODEL, TESTER_SERIAL_NUMBER
from logic.report_templates import (
    DEFAULT_PDF_TEMPLATE,
    DEFAULT_XML_TEMPLATE,
    TESTS_PLACEHOLDER,
    TemplateError,
    ensure_templates_seeded,
    read_xml_template,
    substitute,
    validate_xml_template,
)
from version import __version__

#: Unit of measure per quantity, fixed by the schema. Deliberately not taken
#: from the step's `Unit` string: a typo there must not produce XML that
#: CAMSTAR rejects.
CANONICAL_UOM: dict[str, str] = {
    "Voltage": "V",
    "Current": "A",
    "Power": "W",
    "Resistance": "ohm",
}

#: Emission order of quantities inside a measurement block, matching the
#: customer's template.
QUANTITY_ORDER: tuple[str, ...] = ("Voltage", "Current", "Power", "Resistance")


def has_report_mapping(results: list[dict[str, Any]]) -> bool:
    """True if any row declares a CAMSTAR placement."""
    return any(str(row.get("report_test", "")).strip() for row in results)


def group_by_report_test(
    results: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group rows by their `Report <name>`, in the order Appendix A numbers them.

    Shared by the CAMSTAR XML and the PDF so the two can never disagree about
    test order. Rows with no `report_test` (station fixtures such as Input
    Connection Check) are left out — the report lists UUT tests only.

    Ordering is pinned to the spec rather than taken from the order rows were
    recorded. Row order is not a reliable proxy: the Polarity Check row is
    appended by an engine gate in `TestRunnerThread`, not by a script step, so
    its position depends on where that gate happens to fire. It currently fires
    after the LED step and so lands correctly, but the fallback path taken when
    a run has no LED step records it first, and any future change to the gate
    would silently reorder customer-facing output. Sorting here makes the spec
    order an invariant of the report instead of an accident of the engine.

    Tests absent from the spec's section list keep their first-seen order after
    the known ones, so a non-spec product still renders.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        name = str(row.get("report_test", "")).strip()
        if name:
            grouped.setdefault(name, []).append(row)

    spec_order = list(DEFAULT_PDF_TEMPLATE.get("test_sections", {}))
    return sorted(
        grouped.items(),
        key=lambda kv: (
            spec_order.index(kv[0]) if kv[0] in spec_order else len(spec_order),
        ),
    )


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _quantity_element(parent: ET.Element, tag: str, quantity: str, row: dict[str, Any]) -> None:
    """Emit `<tag><Value>..</Value><UOM>..</UOM></tag>`.

    A skipped step leaves `<Value>` empty rather than reporting the 0.0
    placeholder the engine uses for N/A rows.
    """
    node = ET.SubElement(parent, tag)
    value = ET.SubElement(node, "Value")
    if not row.get("skipped"):
        value.text = _fmt(row.get("value"))
    ET.SubElement(node, "UOM").text = CANONICAL_UOM[quantity]


def _time_element(parent: ET.Element, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if str(row.get("report_time", "")).strip():
            node = ET.SubElement(parent, "Time")
            ET.SubElement(node, "Value").text = str(row["report_time"])
            ET.SubElement(node, "UOM").text = str(row.get("report_time_uom", ""))
            return


def _measurement_block(parent: ET.Element, rows: list[dict[str, Any]], suffix: str) -> None:
    """Fill one measurement block; `suffix` numbers the tags ("" = unnumbered)."""
    _time_element(parent, rows)
    by_quantity = {
        str(r.get("report_quantity", "")): r
        for r in rows
        if str(r.get("report_quantity", ""))
    }
    for quantity in QUANTITY_ORDER:
        row = by_quantity.get(quantity)
        if row is not None:
            _quantity_element(parent, f"{quantity}{suffix}", quantity, row)


def _test_result(rows: list[dict[str, Any]]) -> str:
    """Aggregate row outcomes into the single Pass/Fail/N/A the schema allows."""
    if all(row.get("skipped") for row in rows):
        return "N/A"
    if all(row.get("passed") for row in rows):
        return "Pass"
    return "Fail"


def template_values(run_meta: dict[str, Any]) -> dict[str, str]:
    """Flatten run metadata into the placeholder names a template may use."""
    return {
        "uut_type": str(run_meta.get("uut_type", "")),
        "tester_name": str(run_meta.get("tester_name", "")),
        "employee_id": str(run_meta.get("employee_id", "")),
        "serial_number": str(run_meta.get("serial_number", "")),
        "part_number": str(run_meta.get("part_number", "")),
        "start_time": str(run_meta.get("start_time", "")),
        "end_time": str(run_meta.get("end_time", "")),
        "overall_result": str(run_meta.get("overall_result", "")),
        "test_program_name": str(run_meta.get("test_program_name", "")),
        "load_channel_id": str(
            run_meta.get("load_serial_number", "") or run_meta.get("load_channel", "")
        ),
        "test_load_model": TEST_LOAD_MODEL,
        "tester_serial": TESTER_SERIAL_NUMBER,
        "software_version": __version__,
        # "17 / 20 (partial)" when a role with SELECT_STEPS unticked steps
        # before the run; blank when the whole script ran. Deselected steps
        # leave no N/A row, so without this a reduced run would be
        # indistinguishable from a full pass on the report.
        "steps_executed": str(run_meta.get("steps_executed", "")),
    }


def build_tests_element(results: list[dict[str, Any]]) -> ET.Element:
    """Build the `<Tests>` subtree defined in spec Rev.1.1 Appendix B.

    Generated rather than templated: its shape follows what the run measured,
    and it must stay valid for CAMSTAR regardless of template edits.
    """
    tests_el = ET.Element("Tests")
    # Ordered by the spec's test numbering, not by the order rows were recorded
    # — see `group_by_report_test` for why row order cannot be trusted here.
    for test_name, rows in group_by_report_test(results):
        test_el = ET.SubElement(tests_el, "Test")
        ET.SubElement(test_el, "TestName").text = test_name

        indexed: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            if str(row.get("report_quantity", "")):
                indexed.setdefault(int(row.get("report_index", 0) or 0), []).append(row)

        if indexed:
            if set(indexed) == {0}:
                # Single unnumbered measurement: <MeasuredOutput>, bare tags.
                _measurement_block(
                    ET.SubElement(test_el, "MeasuredOutput"), indexed[0], ""
                )
            else:
                # Several timed measurements: <MeasuredOutputs><MeasurementN>,
                # tags suffixed with N. The asymmetry with the unnumbered case
                # is the customer's schema, reproduced literally.
                outputs = ET.SubElement(test_el, "MeasuredOutputs")
                for index in sorted(i for i in indexed if i > 0):
                    _measurement_block(
                        ET.SubElement(outputs, f"Measurement{index}"),
                        indexed[index],
                        str(index),
                    )

        ET.SubElement(test_el, "Result").text = _test_result(rows)

    return tests_el


def build_camstar_tree(run_meta: dict[str, Any], results: list[dict[str, Any]]) -> ET.Element:
    """Render the admin-editable XML template with this run's data.

    The template supplies the wrapper and the `<Information>` fields; the
    generated `<Tests>` subtree replaces the empty `<Tests/>` marker. A template
    that fails to parse or lacks the marker falls back to the built-in default,
    because a malformed edit must not cost the operator a completed 30-minute
    burn-in report.
    """
    ensure_templates_seeded()
    text = read_xml_template()
    try:
        validate_xml_template(text)
    except TemplateError:
        text = DEFAULT_XML_TEMPLATE

    root = ET.fromstring(substitute(text, template_values(run_meta)))
    tests_el = build_tests_element(results)

    if root.tag == TESTS_PLACEHOLDER:
        return tests_el
    for parent in root.iter():
        for index, child in enumerate(list(parent)):
            if child.tag == TESTS_PLACEHOLDER:
                parent.remove(child)
                parent.insert(index, tests_el)
                return root
    return root


def tree_to_text(root: ET.Element) -> str:
    """Serialize with a UTF-8 declaration and two-space indentation."""
    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    return "\n".join(line for line in lines if line.strip())
