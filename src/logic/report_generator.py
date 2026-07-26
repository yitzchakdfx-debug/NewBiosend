"""PDF and CSV test reports with role-based detail and archiving."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tables import LongTable

from logic.report_templates import (
    COLUMN_FIELDS,
    DEFAULT_PDF_TEMPLATE,
    read_pdf_template,
    template_dir,
)
from logic.report_xml import (
    build_camstar_tree,
    has_report_mapping,
    template_values,
    tree_to_text,
)
from logic.roles import Capability, can
from paths import user_data_path

class ReportsForbiddenError(PermissionError):
    """Raised when a role that may not produce reports asks for one."""


def _reject_if_reports_forbidden(role: str) -> None:
    """Enforce the report ban in the logic layer, not only in the UI.

    Spec Rev.1.1 §1.1.4.3 forbids reports in Maintenance mode. Hiding the menu
    action is not enough — a stray call from a background worker would still
    write a production-looking artifact to the archive.
    """
    if not can(role, Capability.GENERATE_REPORTS):
        raise ReportsForbiddenError(
            f"role {role!r} may not generate test reports"
        )


def sanitize_path_segment(value: str) -> str:
    """Flatten whitespace and strip characters that break filesystem paths."""
    cleaned = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]+', "", cleaned) or "unknown"


class ReportGenerator:
    """Writes PDF archives under src/data/results/<UUT>/<Serial>/ and optional manual exports."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir if base_dir is not None else user_data_path("results")

    def suggested_export_path(self, run_meta: dict[str, Any], suffix: str = ".pdf") -> Path:
        """Return the path that ``generate_pdf_auto_archive`` would write to.

        Useful for showing the operator a preview destination before the run
        completes.  The timestamp component will differ from the actual write
        time; callers should treat this as an *approximate* path.
        """
        archive_dir, stem = self._resolved_archive_paths(run_meta)
        return archive_dir / (stem + suffix)

    def _resolved_archive_paths(self, run_meta: dict[str, Any]) -> tuple[Path, str]:
        """Sanitized subdirectory (uut/serial), timestamp stem for filenames."""
        uut_seg = sanitize_path_segment(str(run_meta.get("uut_type", "")).strip())
        sn_seg = sanitize_path_segment(str(run_meta.get("serial_number", "")).strip())
        test_name_meta = str(run_meta.get("test_program_name", "report")).strip()
        stem_test = sanitize_path_segment(test_name_meta)

        archive_dir = self._base / uut_seg / sn_seg
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = "_".join(
            [
                stem_test,
                ts,
                sn_seg,
            ]
        )
        return archive_dir, stem

    def generate_pdf_auto_archive(
        self,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        """Persist only PDF under the structured archive folder (CSV is manual-export only)."""
        role_key = role.strip().title()
        _reject_if_reports_forbidden(role_key)
        archive_dir, stem = self._resolved_archive_paths(run_meta)
        archive_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = archive_dir / f"{stem}_{role_key.replace(' ', '_')}.pdf"
        write_pdf_report(pdf_path, run_meta, results, role_key)
        return pdf_path

    def generate_xml_auto_archive(
        self,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        """Write XML report alongside the PDF in the same archive folder."""
        _reject_if_reports_forbidden(role.strip().title())
        archive_dir, stem = self._resolved_archive_paths(run_meta)
        archive_dir.mkdir(parents=True, exist_ok=True)
        xml_path = archive_dir / f"{stem}.xml"
        write_xml_report(xml_path, run_meta, results)
        return xml_path

    def generate_csv_file(
        self,
        dest: Path | str,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(path, run_meta, results, role.strip().title())
        return path

    def generate_pdf_file(
        self,
        dest: Path | str,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_pdf_report(path, run_meta, results, role.strip().title())
        return path

    # Backward-compat name used elsewhere
    def generate(
        self,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        """Auto-archive PDF only."""
        return self.generate_pdf_auto_archive(run_meta, results, role)


def _fmt_num(v: Any) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    # A step with no `Limits` carries NaN bounds (see `test_engine`), which is
    # correct for steps the spec records but does not bound — Current, Power and
    # Resistance. Render those as blank: printing the literal "nan" in a
    # customer-facing Min/Max column reads as a software defect.
    if not math.isfinite(f):
        return ""
    return f"{f:g}"


def _header_rows(run_meta: dict[str, Any], role: str) -> list[tuple[str, str]]:
    """Header label/value pairs, ordered and selected by the PDF template."""
    values = template_values(run_meta)
    values["role"] = role
    # The PDF has always shown channel as "CH1 / SN"; the XML uses the bare SN.
    load_ch = str(run_meta.get("load_serial_number", "")).strip()
    if run_meta.get("load_channel"):
        load_ch = (
            f"{run_meta['load_channel']} / {load_ch}"
            if load_ch
            else str(run_meta["load_channel"])
        )
    values["load_channel_id"] = load_ch
    if not values.get("overall_result"):
        values["overall_result"] = "—"

    rows: list[tuple[str, str]] = []
    for entry in read_pdf_template().get("header_fields", []):
        try:
            label, field = entry
        except (TypeError, ValueError):
            continue  # malformed row in an edited template — skip, don't crash
        rows.append((str(label), values.get(str(field), "")))
    return rows


def _column_pairs(template: dict[str, Any], key: str) -> list[tuple[str, str]]:
    """`[label, field]` pairs for a results table, falling back to the default."""
    raw = template.get(key) or DEFAULT_PDF_TEMPLATE[key]
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        try:
            label, field = entry
        except (TypeError, ValueError):
            continue  # malformed row in an edited template — skip it
        if field in COLUMN_FIELDS:
            pairs.append((str(label), str(field)))
    return pairs or [(str(a), str(b)) for a, b in DEFAULT_PDF_TEMPLATE[key]]


def _row_cells(
    row: dict[str, Any],
    columns: list[tuple[str, str]],
    styles: Any,
    *,
    skipped: bool,
    ok: bool,
) -> list[Any]:
    """Render one results row in the column order the template specifies."""
    status = "N/A" if skipped else ("PASS" if ok else "FAIL")
    cells: list[Any] = []
    for _, field in columns:
        if field == "test_name":
            cells.append(Paragraph(escape(str(row.get("test_name", ""))), styles["Normal"]))
        elif field == "result":
            cells.append(status)
        elif field == "unit":
            cells.append("" if skipped else str(row.get("unit", "")))
        else:  # min / max / value — blank rather than 0 for a step never run
            cells.append("" if skipped else _fmt_num(row.get(field)))
    return cells


#: Quantities printed on an Appendix A measurement line, in spec order.
_APPENDIX_A_QUANTITIES = (
    ("Voltage", "V"),
    ("Current", "A"),
    ("Power", "W"),
    ("Resistance", "Ω"),
)


def _grouped_by_report_test(
    results: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group rows by their `Report <name>`, preserving first-seen order.

    Rows with no `report_test` (station fixtures such as Input Connection Check)
    are left out — Appendix A lists UUT tests only.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        name = str(row.get("report_test", "")).strip()
        if name:
            grouped.setdefault(name, []).append(row)

    # Appendix A numbers the tests 1-4 in a fixed order. Script order differs:
    # the engine's polarity gate runs before the scripted LED prompt, so
    # first-seen order would print Polarity as "Test 1". Known tests are pinned
    # to the spec order; anything else keeps first-seen order after them, so a
    # non-spec product still renders.
    spec_order = list(DEFAULT_PDF_TEMPLATE.get("test_sections", {}))
    return sorted(
        grouped.items(),
        key=lambda kv: (
            spec_order.index(kv[0]) if kv[0] in spec_order else len(spec_order),
        ),
    )


def _measurement_lines(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """`(label, text)` measurement lines for one Appendix A test section.

    Multi-point tests (Burn-In) are keyed by `report_index` and labelled
    t1/t2/t3; single-point tests get one unlabelled line. Quantities come from
    `report_quantity`, so the order follows the spec rather than script order.
    """
    by_index: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            idx = int(row.get("report_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        by_index.setdefault(idx, []).append(row)

    multi = sorted(i for i in by_index if i > 0)
    ordered = [(i, by_index[i]) for i in multi] or [(0, by_index.get(0, []))]

    lines: list[tuple[str, str]] = []
    for position, (idx, group) in enumerate(ordered, start=1):
        by_quantity = {
            str(r.get("report_quantity", "")).strip(): r
            for r in group
            if str(r.get("report_quantity", "")).strip()
        }
        parts: list[str] = []
        for quantity, uom in _APPENDIX_A_QUANTITIES:
            row = by_quantity.get(quantity)
            if row is None:
                continue
            shown = "N/A" if row.get("skipped") else _fmt_num(row.get("value"))
            parts.append(f"{quantity} {shown or '—'} [{uom}]")
        if not parts:
            continue
        label = ""
        if multi:
            label = f"t{position}"
            time_val = str(group[0].get("report_time", "")).strip()
            if time_val:
                uom = str(group[0].get("report_time_uom", "")).strip() or "min"
                label = f"{label} ({time_val} {uom})"
        lines.append((label, "   ".join(parts)))
    return lines


def _appendix_a_sections(
    results: list[dict[str, Any]],
    template: dict[str, Any],
    styles: Any,
) -> list[Any]:
    """Per-test Appendix A §2.1.3 sections, or [] if the script has no mapping.

    Returning [] lets the caller fall back to the flat results table, so
    non-spec products (SPREOS, 12VDC RF, …) still get a report.
    """
    grouped = _grouped_by_report_test(results)
    if not grouped:
        return []

    sections_cfg = template.get("test_sections") or {}
    flow: list[Any] = []
    for number, (test_name, rows) in enumerate(grouped, start=1):
        cfg = sections_cfg.get(test_name) or {}
        # A test counts as skipped only when nothing in it ran; a partially-run
        # test that failed must read FAIL, not N/A (spec §1.1.4.5).
        if all(r.get("skipped") for r in rows):
            verdict = "N/A"
        elif all(r.get("passed") for r in rows if not r.get("skipped")):
            verdict = "Pass"
        else:
            verdict = "Fail"

        flow.append(Spacer(1, 0.16 * inch))
        flow.append(
            Paragraph(
                f"<b>Test {number}: {escape(test_name)}</b>", styles["Heading3"]
            )
        )
        colour = "#b91c1c" if verdict == "Fail" else "#000000"
        flow.append(
            Paragraph(
                f'Result [Pass / Fail]: <font color="{colour}"><b>{verdict}</b></font>',
                styles["Normal"],
            )
        )
        expected = str(cfg.get("expected", "")).strip()
        if expected:
            flow.append(
                Paragraph(f"Expected Value: {escape(expected)}", styles["Normal"])
            )
        load_mode = str(cfg.get("load_mode", "")).strip()
        if load_mode:
            flow.append(Paragraph(f"Load Mode: {escape(load_mode)}", styles["Normal"]))

        lines = _measurement_lines(rows)
        if lines:
            flow.append(
                Paragraph(
                    "Measured Output Value"
                    + ("s" if len(lines) > 1 else "")
                    + ":",
                    styles["Normal"],
                )
            )
            for label, text in lines:
                prefix = f"<b>{escape(label)}:</b> " if label else ""
                flow.append(
                    Paragraph(f"&nbsp;&nbsp;&nbsp;{prefix}{escape(text)}", styles["Normal"])
                )
    return flow


def _write_csv(
    path: Path,
    run_meta: dict[str, Any],
    results: list[dict[str, Any]],
    role: str,
) -> None:
    template = read_pdf_template()
    key = "detail_columns" if can(role, Capability.VIEW_MEASURED_DETAIL) else "summary_columns"
    columns = _column_pairs(template, key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Field", "Value"])
        for k, v in _header_rows(run_meta, role):
            writer.writerow([k, v])
        writer.writerow([])
        writer.writerow([label for label, _ in columns])
        for row in results:
            skipped = bool(row.get("skipped"))
            status = "N/A" if skipped else ("PASS" if row.get("passed") else "FAIL")
            cells: list[str] = []
            for _, field in columns:
                if field == "test_name":
                    cells.append(str(row.get("test_name", "")))
                elif field == "result":
                    cells.append(status)
                elif field == "unit":
                    cells.append("" if skipped else str(row.get("unit", "")))
                else:
                    cells.append("" if skipped else _fmt_num(row.get(field)))
            writer.writerow(cells)




def write_pdf_report(
    path: Path,
    run_meta: dict[str, Any],
    results: list[dict[str, Any]],
    role: str,
) -> None:
    """Build paginated PDF (SimpleDocTemplate + LongTable for results splits across pages)."""
    # Appendix A §2.1.3.2-2.1.3.4 require the Measured Output Value on Tests
    # 2/3/4 with no role qualifier, so the archived report always carries the
    # numbers. This used to follow `Capability.VIEW_MEASURED_DETAIL`, which
    # Operator and Technician lack — meaning a normal production run archived a
    # PDF containing only test names and PASS/FAIL, with no measurements at all.
    # The capability still gates the live results table and trace log in the UI;
    # what the permanent record must contain is a separate question.
    show_detail = True
    template = read_pdf_template()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    flow: list[Any] = []

    logo_path = str(template.get("logo_path", "")).strip()
    if logo_path:
        candidate = Path(logo_path)
        if not candidate.is_absolute():
            candidate = template_dir() / candidate
        if candidate.is_file():
            try:
                flow.append(Image(str(candidate), width=2.2 * inch, height=0.55 * inch))
                flow.append(Spacer(1, 0.1 * inch))
            except Exception:
                pass  # an unreadable logo must not cost the run its report

    flow.append(
        Paragraph(f"<b>{escape(str(template.get('title', '')))}</b>", styles["Heading1"])
    )
    subtitle = str(template.get("subtitle", "")).strip()
    if subtitle:
        flow.append(Paragraph(escape(subtitle), styles["Normal"]))
    flow.append(Spacer(1, 0.15 * inch))

    header_data = [["Field", "Value"]]
    for h, v in _header_rows(run_meta, role):
        header_data.append([h, v])
    ht = Table(header_data, colWidths=[2.4 * inch, 4 * inch])
    ht.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f65ca")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    flow.append(ht)
    flow.append(Spacer(1, 0.25 * inch))

    # Appendix A §2.1.3: per-test sections with Result / Expected Value / Load
    # Mode / measured values. Present only for scripts that declare `Report`
    # directives; other products fall through to the flat table alone.
    sections = _appendix_a_sections(results, template, styles)
    if sections:
        flow.append(Paragraph("<b>Tests</b>", styles["Heading2"]))
        flow.extend(sections)
        flow.append(Spacer(1, 0.28 * inch))
        flow.append(Paragraph("<b>All Recorded Steps</b>", styles["Heading2"]))
        flow.append(Spacer(1, 0.1 * inch))

    key = "detail_columns" if show_detail else "summary_columns"
    columns = _column_pairs(template, key)
    headers = [label for label, _ in columns]
    # The first column absorbs the slack, so renaming or reordering columns in
    # the template cannot push the table off the page.
    rest = 0.75 * inch if show_detail else 1 * inch
    col_w = [6.35 * inch - rest * (len(headers) - 1)] + [rest] * (len(headers) - 1)

    n_cols = len(headers)
    loop_total = max(1, int(run_meta.get("loop_count", 1) or 1))
    has_loops = loop_total > 1 and any(int(r.get("loop", 1)) > 1 for r in results)

    data: list[list[Any]] = [headers]
    loop_header_rows: list[int] = []
    fail_rows: list[int] = []
    current_loop: int | None = None

    for row in results:
        loop_num = int(row.get("loop", 1))
        if has_loops and loop_num != current_loop:
            loop_header_rows.append(len(data))
            data.append(
                [f"Loop {loop_num} of {loop_total}"] + [""] * (n_cols - 1)
            )
            current_loop = loop_num

        skipped = bool(row.get("skipped"))
        ok = bool(row.get("passed"))
        # Spec Rev.1.1 §1.1.4.5: steps after a failure are not performed and are
        # reported N/A — not as a failed measurement of 0.
        if not ok and not skipped:
            fail_rows.append(len(data))

        cells = _row_cells(row, columns, styles, skipped=skipped, ok=ok)
        data.append(cells)

    rt = LongTable(data, colWidths=col_w, repeatRows=1)

    fail_bg = colors.HexColor("#fee2e2")   # light pink — highlights the row
    fail_fg = colors.HexColor("#b91c1c")   # strong red — bold FAIL text

    style_cmds: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444444")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for ridx in loop_header_rows:
        style_cmds.extend(
            [
                ("SPAN", (0, ridx), (-1, ridx)),
                ("BACKGROUND", (0, ridx), (-1, ridx), colors.HexColor("#2f65ca")),
                ("TEXTCOLOR", (0, ridx), (-1, ridx), colors.whitesmoke),
                ("FONTNAME", (0, ridx), (-1, ridx), "Helvetica-Bold"),
                ("ALIGN", (0, ridx), (-1, ridx), "CENTER"),
                ("TOPPADDING", (0, ridx), (-1, ridx), 6),
                ("BOTTOMPADDING", (0, ridx), (-1, ridx), 6),
            ]
        )
    for ridx in fail_rows:
        style_cmds.extend(
            [
                ("BACKGROUND", (0, ridx), (-1, ridx), fail_bg),
                ("TEXTCOLOR", (-1, ridx), (-1, ridx), fail_fg),
                ("FONTNAME", (-1, ridx), (-1, ridx), "Helvetica-Bold"),
            ]
        )
    rt.setStyle(TableStyle(style_cmds))
    flow.append(rt)

    doc.build(flow)
    path.write_bytes(buffer.getvalue())
    buffer.close()


class XmlMappingMissingError(RuntimeError):
    """Raised when a script cannot produce the Appendix B XML structure.

    The nested CAMSTAR document is built from the `Report` / `Quantity`
    directives on a script's steps. Without them there is nothing to map rows
    onto `<Test>` / `<MeasuredOutput>` nodes.

    This used to fall back to a flat one-node-per-row document. That document
    is not what CAMSTAR ingests — `<Name>` instead of `<TestName>`, no
    `<TestLoad>`, no `<Measurement1..3>` — so the fallback produced a file that
    looked like a successful export and was rejected downstream. Failing loudly
    is the point: a missing mapping is a script defect, not a format choice.
    """


def write_xml_report(path: Path, run_meta: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """Write the CAMSTAR XML test report (spec Rev.1.1 Appendix B).

    Raises `XmlMappingMissingError` if the script carries no `Report`
    directives, rather than emitting a non-conformant document.
    """
    if not has_report_mapping(results):
        script = str(run_meta.get("test_program_name") or "this script").strip()
        raise XmlMappingMissingError(
            f"Cannot write the CAMSTAR XML report: {script} declares no "
            "'Report' directives, so its results cannot be mapped onto the "
            "Appendix B structure. Add 'Report <TestName>' (and 'Quantity "
            "Voltage|Current|Power|Resistance' on measurement steps, plus "
            "'Measurement <n>' / 'TimePoint <n> min' for multi-point tests) to "
            "the script's steps. The PDF report is unaffected."
        )
    path.write_text(tree_to_text(build_camstar_tree(run_meta, results)), encoding="utf-8")

