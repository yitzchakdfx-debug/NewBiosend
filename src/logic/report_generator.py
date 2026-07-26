"""PDF and CSV test reports with role-based detail and archiving."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from xml.dom import minidom

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

from config import TESTER_SERIAL_NUMBER
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
from version import __version__

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
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


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
    show_detail = can(role, Capability.VIEW_MEASURED_DETAIL)
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


def write_xml_report(path: Path, run_meta: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """Write the XML test report alongside the PDF.

    Scripts whose steps carry `Report` directives produce the nested CAMSTAR
    structure of spec Rev.1.1 Appendix B. Scripts without them — including
    every version archived before those directives existed — keep producing the
    older flat document, so no migration of `test_versions` is required.
    """
    if has_report_mapping(results):
        path.write_text(tree_to_text(build_camstar_tree(run_meta, results)), encoding="utf-8")
        return
    _write_flat_xml_report(path, run_meta, results)


def _write_flat_xml_report(
    path: Path, run_meta: dict[str, Any], results: list[dict[str, Any]]
) -> None:
    """Legacy one-node-per-row XML, used when no step declares a mapping."""
    root = ET.Element("TestReport")

    ET.SubElement(root, "ReportName").text = (
        "Power Supply EM-5406-00F / EM-5406-00 Load Test Report"
    )
    ET.SubElement(root, "Revision").text = "TI-5406-00"

    info = ET.SubElement(root, "Information")
    ET.SubElement(info, "ProductName").text = str(run_meta.get("uut_type", ""))
    ET.SubElement(info, "OperatorName").text = str(run_meta.get("tester_name", ""))
    ET.SubElement(info, "SerialNumber").text = str(run_meta.get("serial_number", ""))
    ET.SubElement(info, "PartNumber").text = str(run_meta.get("part_number", ""))
    ET.SubElement(info, "TestDateTime").text = str(run_meta.get("start_time", ""))
    load_sn = str(run_meta.get("load_serial_number", ""))
    load_ch = str(run_meta.get("load_channel", ""))
    ET.SubElement(info, "TestLoadChannelID").text = (
        f"{load_ch} / {load_sn}" if load_ch and load_sn else load_ch or load_sn
    )
    ET.SubElement(info, "TesterSerial").text = TESTER_SERIAL_NUMBER
    ET.SubElement(info, "SoftwareVersion").text = __version__

    ET.SubElement(root, "OverallResult").text = str(run_meta.get("overall_result", "FAIL"))

    tests_el = ET.SubElement(root, "Tests")
    for idx, row in enumerate(results, start=1):
        test_el = ET.SubElement(tests_el, "Test")
        test_el.set("id", str(idx))
        ET.SubElement(test_el, "Name").text = str(row.get("test_name", ""))
        ET.SubElement(test_el, "Result").text = "Pass" if row.get("passed") else "Fail"
        if row.get("min") is not None:
            ET.SubElement(test_el, "Min").text = _fmt_num(row["min"])
        if row.get("max") is not None:
            ET.SubElement(test_el, "Max").text = _fmt_num(row["max"])
        if row.get("value") is not None:
            val_el = ET.SubElement(test_el, "MeasuredValue")
            val_el.text = _fmt_num(row["value"])
            if row.get("unit"):
                val_el.set("unit", str(row["unit"]))

    raw = ET.tostring(root, encoding="unicode", xml_declaration=False)
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)
    # toprettyxml inserts its own declaration; replace with a UTF-8 one
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    path.write_text("\n".join(lines), encoding="utf-8")
