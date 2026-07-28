"""Admin-editable report templates for the XML and PDF outputs.

Spec Rev.1.1 §1.1.4.4 Notes 1 and 2 require that an authorized administrator be
able to update the XML report template and the final PDF report template, with
the changes reflected in generated reports. Both were previously hardcoded in
`report_generator.py`, so any change meant a new software build.

What is templated and what is not
---------------------------------
The *fixed* parts are templated: the report wrapper, which `<Information>`
fields appear and in what order, the PDF title, and the PDF header/column
layout. The *repeating* parts are not: the `<Tests>` subtree is generated from
the `Report`/`Quantity`/`Measurement` directives carried by the test steps (see
`logic.report_xml`), because its shape is dictated by what the run actually
measured and must stay valid for CAMSTAR. In the XML template the generated
subtree is spliced in wherever an empty `<Tests/>` element appears.

Templates live under `data/templates/` and are seeded from the built-in
defaults on first use. A missing, unreadable, or malformed template falls back
to the default rather than failing the run — losing a report would be worse
than ignoring a bad edit, and the fallback is logged by the caller.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from paths import user_data_path

TEMPLATE_DIR_NAME = "templates"
XML_TEMPLATE_NAME = "report_template.xml"
PDF_TEMPLATE_NAME = "report_template.json"

#: Marker element in the XML template that the generated <Tests> subtree
#: replaces. Present as an empty element so the template stays valid XML.
TESTS_PLACEHOLDER = "Tests"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

DEFAULT_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!--
  CAMSTAR test-report template.

  Placeholders written as {{ name }} (without the spaces) are replaced with run
  values. Available names:
    uut_type, tester_name, employee_id, serial_number, part_number,
    start_time, end_time, overall_result, test_program_name,
    load_channel_id, test_load_model, tester_serial, software_version

  The empty <Tests/> element is replaced by the generated test results. Keep it
  exactly once, and do not put anything inside it.

  Elements and attributes may be added, removed, renamed or reordered. Anything
  that is not a placeholder is copied through verbatim.
-->
<TestReport>
    <Information>
        <ProductName>{{uut_type}}</ProductName>
        <OperatorName>{{tester_name}}</OperatorName>
        <SerialNumber>{{serial_number}}</SerialNumber>
        <TestDateTime>{{start_time}}</TestDateTime>
        <TestLoad>
            <Model>{{test_load_model}}</Model>
            <ChannelID>{{load_channel_id}}</ChannelID>
        </TestLoad>
    </Information>
    <Tests/>
</TestReport>
"""

DEFAULT_PDF_TEMPLATE: dict[str, Any] = {
    "title": "Power Supply EM-5406-00F / EM-5406-00 Load Test Report",
    "subtitle": "Complies with TI-5406-00, latest revision.",
    "logo_path": "",
    "header_fields": [
        ["Result (Overall)", "overall_result"],
        ["User Name", "tester_name"],
        ["Employee ID", "employee_id"],
        ["Product Name", "test_program_name"],
        ["UUT Type", "uut_type"],
        ["PartNumber", "part_number"],
        ["SN", "serial_number"],
        ["Test Load 3316G Channel ID (SN)", "load_channel_id"],
        ["Start Time", "start_time"],
        ["End Time", "end_time"],
        ["SW SN", "tester_serial"],
        ["SW Version", "software_version"],
        ["Role / Report Detail", "role"],
        # Discloses a partial run. A role holding SELECT_STEPS may untick steps,
        # and deselected steps are filtered out before execution — leaving no
        # N/A row — so without this line a reduced run reads as a full pass.
        # Renders as "17 / 20 (partial)", or is omitted when the run was full.
        ["Tests Executed", "steps_executed"],
    ],
    # [column label, value field]. Fields: test_name, min, max, value, unit,
    # result. Labels are free text; fields must come from that set.
    #
    # `detail_columns` drives the PDF's "All Recorded Steps" table, and the CSV
    # export for roles holding `Capability.VIEW_MEASURED_DETAIL`.
    "detail_columns": [
        ["Test Name", "test_name"],
        ["Min", "min"],
        ["Max", "max"],
        ["Value", "value"],
        ["Unit", "unit"],
        ["Status", "result"],
    ],
    # CSV-ONLY, for roles without `Capability.VIEW_MEASURED_DETAIL`. The PDF
    # ignores this and always uses `detail_columns`: Appendix A requires the
    # measured values in the archived report regardless of who ran the test.
    # See the comment in `report_generator.write_pdf_report`.
    "summary_columns": [
        ["Test Name", "test_name"],
        ["Result", "result"],
    ],
    # Appendix A §2.1.3: per-test sections. Keyed by the `Report <name>` value a
    # step declares in the .tst, which is also the Appendix B <TestName>. Each
    # entry supplies the static text the spec prints above the measurements:
    #   expected   -> "Expected Value:" line
    #   load_mode  -> "Load Mode:" line (omitted when blank, e.g. Tests 1-2)
    # Admin-editable, per spec §1.1.4.4 Note 2. A test with no entry here still
    # renders its measurements, just without the two static lines.
    "test_sections": {
        "LED Indication Test": {
            "expected": "Green LED ON and Steady",
            "load_mode": "",
        },
        "Polarity Check": {
            "expected": "Positive (+24VDC)",
            "load_mode": "",
        },
        "Low Load Stability Test": {
            "expected": "24VDC ±5%",
            "load_mode": "Constant Resistance (CR)",
        },
        "Burn-In / Continuous Load Test": {
            "expected": "24VDC ±5%",
            "load_mode": "Constant Resistance (CR)",
        },
    },
}

#: Per-row value fields a PDF results column may reference.
COLUMN_FIELDS: frozenset[str] = frozenset(
    {"test_name", "min", "max", "value", "unit", "result"}
)


def template_dir():
    return user_data_path(TEMPLATE_DIR_NAME)


def _template_path(name: str):
    return template_dir() / name


def ensure_templates_seeded() -> None:
    """Write the default templates to disk if they are not there yet."""
    directory = template_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        xml_path = _template_path(XML_TEMPLATE_NAME)
        if not xml_path.exists():
            xml_path.write_text(DEFAULT_XML_TEMPLATE, encoding="utf-8")
        pdf_path = _template_path(PDF_TEMPLATE_NAME)
        if not pdf_path.exists():
            pdf_path.write_text(
                json.dumps(DEFAULT_PDF_TEMPLATE, indent=2), encoding="utf-8"
            )
    except OSError:
        pass  # read-only install: callers fall back to the built-in defaults


def read_xml_template() -> str:
    """Current XML template text, or the built-in default."""
    try:
        text = _template_path(XML_TEMPLATE_NAME).read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_XML_TEMPLATE
    return text if text.strip() else DEFAULT_XML_TEMPLATE


def write_xml_template(text: str) -> None:
    validate_xml_template(text)  # refuse to persist something unusable
    template_dir().mkdir(parents=True, exist_ok=True)
    _template_path(XML_TEMPLATE_NAME).write_text(text, encoding="utf-8")


def read_pdf_template() -> dict[str, Any]:
    """Current PDF template config, with defaults filled in for missing keys."""
    merged = dict(DEFAULT_PDF_TEMPLATE)
    try:
        loaded = json.loads(_template_path(PDF_TEMPLATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return merged
    if isinstance(loaded, dict):
        merged.update(loaded)
    return merged


def write_pdf_template(text: str) -> None:
    validate_pdf_template(text)
    template_dir().mkdir(parents=True, exist_ok=True)
    _template_path(PDF_TEMPLATE_NAME).write_text(text, encoding="utf-8")


class TemplateError(ValueError):
    """A template edit that would break report generation."""


def validate_xml_template(text: str) -> None:
    """Reject an XML template that cannot produce a report."""
    if not text.strip():
        raise TemplateError("Template is empty.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TemplateError(f"Not well-formed XML: {exc}") from exc

    markers = [root] if root.tag == TESTS_PLACEHOLDER else []
    markers += root.findall(f".//{TESTS_PLACEHOLDER}")
    if len(markers) != 1:
        raise TemplateError(
            f"Template must contain exactly one empty <{TESTS_PLACEHOLDER}/> element "
            f"marking where results are inserted (found {len(markers)})."
        )
    if len(markers[0]) or (markers[0].text or "").strip():
        raise TemplateError(
            f"<{TESTS_PLACEHOLDER}/> must be empty — its contents are generated."
        )

    # Comments are dropped when the template is parsed, so placeholders inside
    # them never render — don't hold the author to the known-name list there.
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    unknown = sorted(
        set(_PLACEHOLDER_RE.findall(without_comments)) - set(placeholder_names())
    )
    if unknown:
        raise TemplateError(
            "Unknown placeholder(s): "
            + ", ".join(f"{{{{{name}}}}}" for name in unknown)
            + ".\nAvailable: "
            + ", ".join(sorted(placeholder_names()))
        )


def validate_pdf_template(text: str) -> None:
    """Reject a PDF template config that cannot produce a report."""
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        raise TemplateError(f"Not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TemplateError("Template must be a JSON object.")

    fields = loaded.get("header_fields", [])
    if not isinstance(fields, list):
        raise TemplateError("'header_fields' must be a list of [label, field] pairs.")
    known = set(placeholder_names()) | {"role"}
    for entry in fields:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise TemplateError(
                f"Each header field must be a [label, field] pair; got {entry!r}."
            )
        if entry[1] not in known:
            raise TemplateError(
                f"Unknown header field {entry[1]!r}.\nAvailable: "
                + ", ".join(sorted(known))
            )
    for key in ("detail_columns", "summary_columns"):
        if key not in loaded:
            continue
        columns = loaded[key]
        if not isinstance(columns, list) or not columns:
            raise TemplateError(
                f"'{key}' must be a non-empty list of [label, field] pairs."
            )
        for entry in columns:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TemplateError(
                    f"Each {key} entry must be a [label, field] pair; got {entry!r}."
                )
            if entry[1] not in COLUMN_FIELDS:
                raise TemplateError(
                    f"Unknown column field {entry[1]!r}.\nAvailable: "
                    + ", ".join(sorted(COLUMN_FIELDS))
                )

    sections = loaded.get("test_sections")
    if sections is not None:
        # Free-form keys (they match whatever `Report <name>` a script declares,
        # so new products need no code change), but the shape must hold or the
        # Appendix A renderer would fail mid-report.
        if not isinstance(sections, dict):
            raise TemplateError(
                "'test_sections' must be an object keyed by test name."
            )
        for name, cfg in sections.items():
            if not isinstance(cfg, dict):
                raise TemplateError(
                    f"'test_sections[{name!r}]' must be an object with "
                    "'expected' and/or 'load_mode' text."
                )
            for field in ("expected", "load_mode"):
                if field in cfg and not isinstance(cfg[field], str):
                    raise TemplateError(
                        f"'test_sections[{name!r}].{field}' must be text."
                    )


def placeholder_names() -> set[str]:
    """Placeholder names the XML template may use."""
    return {
        "uut_type",
        "tester_name",
        "employee_id",
        "serial_number",
        "part_number",
        "start_time",
        "end_time",
        "overall_result",
        "test_program_name",
        "load_channel_id",
        # "17 / 20 (partial)" when steps were deselected before the run; blank
        # for a full run. Must stay in step with `report_xml.template_values` —
        # a name missing here is rejected by `validate_pdf_template` even though
        # the report layer would render it fine.
        "steps_executed",
        "test_load_model",
        "tester_serial",
        "software_version",
    }


def substitute(text: str, values: dict[str, str]) -> str:
    """Replace `{{name}}` with `values[name]`; unknown names become empty."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), text)
