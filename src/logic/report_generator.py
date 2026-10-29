"""PDF and CSV test reports with role-based detail and archiving."""

from __future__ import annotations

import csv
import io
import math
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

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
    EXPECTED_FROM_LIMITS,
    read_pdf_template,
    template_dir,
)
from logic.report_xml import (
    build_camstar_tree,
    group_by_report_test,
    has_report_mapping,
    template_values,
    tree_to_text,
)
from logic import report_paths
from logic.roles import Capability, can, role_label

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
    """Writes PDF archives under src/data/results/<UUT>/<Serial>_<dd-mm-yyyy>_<HHhMMmSSs>/ and optional manual exports."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir if base_dir is not None else report_paths.results_dir()
        #: Set when a report could not be written to the chosen folder (e.g. a
        #: network share that is down) and was saved locally instead.
        self.fallback_notes: list[str] = []

    def _write_archived(self, run_meta: dict[str, Any], name: Callable[[str], str],
                        write: Callable[[Path], None]) -> Path:
        """Write one archive file; fall back to the local folder if the chosen one fails.

        A report must never be lost because the reports folder — typically a
        network share — is unreachable at that moment. The fallback keeps the
        same `<UUT>/<SN>_<date_time>/` layout under the station's own folder and
        records a note the caller shows the operator.
        """
        archive_dir, stem = self._resolved_archive_paths(run_meta)
        path = archive_dir / name(stem)
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            write(path)
            return path
        except OSError as exc:
            local = report_paths.local_results_dir()
            if str(self._base) == str(local):
                raise
            local_dir = local / archive_dir.relative_to(self._base)
            local_dir.mkdir(parents=True, exist_ok=True)
            fallback = local_dir / name(stem)
            write(fallback)
            note = (
                f"The reports folder {self._base} is not reachable ({exc}) - the "
                f"report was saved locally in {local_dir}. Copy it to the shared "
                "folder when it is reachable again."
            )
            if note not in self.fallback_notes:
                self.fallback_notes.append(note)
            return fallback

    def suggested_export_path(self, run_meta: dict[str, Any], suffix: str = ".pdf") -> Path:
        """Return the path that ``generate_pdf_auto_archive`` would write to.

        Useful for showing the operator a preview destination before the run
        completes.  The timestamp component will differ from the actual write
        time; callers should treat this as an *approximate* path.
        """
        archive_dir, stem = self._resolved_archive_paths(run_meta)
        return archive_dir / (stem + suffix)

    def _resolved_archive_paths(self, run_meta: dict[str, Any]) -> tuple[Path, str]:
        """Sanitized subdirectory (uut/serial_date_time), timestamp stem for filenames.

        Each test gets its own folder, named with the unit's serial and when its
        test started, day first: `2456_19-10-2026_13h06m53s` (Windows forbids
        `:`). The start time, not the write time, so the PDF and the XML —
        written by two separate calls — always land in the same folder.
        """
        uut_seg = sanitize_path_segment(str(run_meta.get("uut_type", "")).strip())
        sn_seg = sanitize_path_segment(str(run_meta.get("serial_number", "")).strip())
        test_name_meta = str(run_meta.get("test_program_name", "report")).strip()
        stem_test = sanitize_path_segment(test_name_meta)

        try:
            started = datetime.fromisoformat(str(run_meta.get("start_time", "")))
        except ValueError:
            started = datetime.now()
        archive_dir = self._base / uut_seg / f"{sn_seg}_{started.strftime('%d-%m-%Y_%Hh%Mm%Ss')}"
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
        return self._write_archived(
            run_meta,
            lambda stem: f"{stem}_{role_key.replace(' ', '_')}.pdf",
            lambda path: write_pdf_report(path, run_meta, results, role_key),
        )

    def generate_xml_auto_archive(
        self,
        run_meta: dict[str, Any],
        results: list[dict[str, Any]],
        role: str,
    ) -> Path:
        """Write XML report alongside the PDF in the same archive folder."""
        _reject_if_reports_forbidden(role.strip().title())
        xml_path = self._write_archived(
            run_meta,
            lambda stem: f"{stem}.xml",
            lambda path: write_xml_report(path, run_meta, results),
        )
        self._copy_to_xml_folder(xml_path)
        return xml_path

    def xml_folder(self) -> Path:
        """Where every XML report is also collected (see `logic.report_paths.xml_dir`)."""
        return report_paths.xml_dir(self._base)

    def _copy_to_xml_folder(self, xml_path: Path) -> Path:
        """Copy a written XML report into the shared XML folder, never overwriting.

        The unit-folder copy already exists when this runs, so a failure here
        (e.g. a network share that is down) loses nothing; it raises
        `XmlCopyError` naming where the report is so it can be copied by hand.
        """
        folder = self.xml_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / xml_path.name
            n = 2
            while target.exists():
                target = folder / f"{xml_path.stem}_{n}{xml_path.suffix}"
                n += 1
            shutil.copy2(xml_path, target)
            return target
        except OSError as exc:
            raise XmlCopyError(xml_path, folder, exc) from exc

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


def _tests_passed(run_meta: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """`"2 / 4"`: report tests whose every row passed, out of the unit's tests.

    The denominator is the tests the unit's rows cover — the script's tests,
    since a test not reached after a failure still leaves N/A rows, and
    Polarity after an LED fail is backfilled as N/A. It used to be the PDF
    template's section list (always the spec's four), so a script with a test
    removed reported a fully passing unit as "3 / 4". A deselected-steps run
    says so.
    """
    groups = group_by_report_test(results)
    passed = sum(
        1 for _, rows in groups
        if rows and all(r.get("passed") and not r.get("skipped") for r in rows)
    )
    total = len(groups)
    if not total:
        return ""
    text = f"{passed} / {total}"
    if "partial" in str(run_meta.get("steps_executed", "")):
        text += " (partial run)"
    return text


def _header_rows(
    run_meta: dict[str, Any], role: str, results: list[dict[str, Any]] | None = None
) -> list[tuple[str, str]]:
    """Header label/value pairs, ordered and selected by the PDF template."""
    values = template_values(run_meta)
    values["tests_passed"] = _tests_passed(run_meta, results or [])
    values["role"] = role_label(role)  # the spec's name, e.g. "Technician (Operator)"
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
        values["overall_result"] = "-"

    rows: list[tuple[str, str]] = []
    for entry in read_pdf_template().get("header_fields", []):
        try:
            label, field = entry
        except (TypeError, ValueError):
            continue  # malformed row in an edited template — skip, don't crash
        value = values.get(str(field), "")
        # Step coverage is only meaningful when it was recorded, and only worth
        # a line when it says something. Printing an empty "Tests Executed:"
        # row on every full run would add noise and invite the reader to wonder
        # what is missing.
        if str(field) == "steps_executed" and not str(value).strip():
            continue
        rows.append((str(label), value))
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
    # Without a Unit column the unit rides with the value: "24 V", "1 pass".
    unit_in_value = all(field != "unit" for _, field in columns)
    cells: list[Any] = []
    for _, field in columns:
        if field == "test_name":
            cells.append(Paragraph(escape(str(row.get("test_name", ""))), styles["Normal"]))
        elif field == "value" and unit_in_value and "value_text" not in row:
            unit = str(row.get("unit", "")).strip()
            unit = "Ω" if unit.lower() == "ohm" else unit
            text = "" if skipped else _fmt_num(row.get("value"))
            cells.append(f"{text} {unit}".strip() if text else "")
        elif field == "result":
            cells.append(status)
        elif field == "unit":
            cells.append("" if skipped else str(row.get("unit", "")))
        elif field == "value" and "value_text" in row:
            # A merged measurement point: "23.6 V · 0.436 A · 11.2 W · 57.6 Ω".
            text = "" if skipped else str(row["value_text"])
            cells.append(Paragraph(escape(text), styles["Normal"]))
        else:  # min / max / value — blank rather than 0 for a step never run
            cells.append("" if skipped else _fmt_num(row.get(field)))
    return cells


_QUANTITY_ORDER = {"voltage": 0, "current": 1, "power": 2, "resistance": 3}


def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _merge_point_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows for "All Recorded Steps": one per measurement point, as on screen.

    The V/I/P/R steps of one point (same loop, `report_test` and `Measurement`
    index) become one row named like the results table ("Burn-In / Continuous
    Load Test  t2 (15 min)"), with the values joined in the Value column. Min,
    Max and the verdict come from the limited quantity (voltage); any failing
    member fails the row; a point whose steps were all skipped is N/A. Steps
    without a `Quantity` (input check, LED, older scripts) and single-reading
    points (polarity) pass through unchanged. PDF only — the CSV keeps one row per step.
    """
    ordered: list[dict[str, Any]] = []
    points: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    for row in results:
        test = str(row.get("report_test") or "").strip()
        if not test or not str(row.get("report_quantity") or "").strip():
            ordered.append(row)
            continue
        try:
            idx = int(row.get("report_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        key = (int(row.get("loop", 1) or 1), test, idx)
        if key not in points:
            points[key] = []
            ordered.append({"_point": key})
        points[key].append(row)

    rows: list[dict[str, Any]] = []
    for item in ordered:
        key = item.get("_point")
        if key is None:
            rows.append(item)
            continue
        loop, test, idx = key
        if len(points[key]) == 1:
            rows.append(points[key][0])  # a lone reading (polarity) keeps its row
            continue
        members = sorted(
            points[key],
            key=lambda r: _QUANTITY_ORDER.get(str(r.get("report_quantity", "")).strip().lower(), 9),
        )
        name = f"{test}  t{idx or 1}"
        timed = next((r for r in members if str(r.get("report_time", "")).strip()), None)
        if timed is not None:
            uom = str(timed.get("report_time_uom", "")).strip() or "min"
            name += f" ({str(timed['report_time']).strip()} {uom})"
        ran = [r for r in members if not r.get("skipped")]
        values = []
        for r in ran:
            text = _fmt_num(r.get("value")) if r.get("is_measurement", True) else ""
            if text:
                unit = str(r.get("unit", "")).strip()
                values.append(f"{text} {'Ω' if unit.lower() == 'ohm' else unit}".rstrip())
        limited = next(
            (r for r in ran if _is_finite_number(r.get("min")) and _is_finite_number(r.get("max"))),
            None,
        )
        rows.append({
            "test_name": name,
            "loop": loop,
            "value_text": " · ".join(values) or "N/A",
            "min": limited.get("min") if limited else None,
            "max": limited.get("max") if limited else None,
            "unit": "",
            "passed": all(bool(r.get("passed")) for r in ran),
            "skipped": not ran,
        })
    return rows


#: Quantities printed on an Appendix A measurement line, in spec order.
_APPENDIX_A_QUANTITIES = (
    ("Voltage", "V"),
    ("Current", "A"),
    ("Power", "W"),
    ("Resistance", "Ω"),
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
            parts.append(f"{quantity} {shown or '-'} [{uom}]")
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


def _expected_text(text: str, rows: list[dict[str, Any]]) -> str:
    """Fill {nominal} {tol} {min} {max} from the test's voltage limits.

    Taken from the first voltage row with real limits (N/A rows carry their
    step's limits too), so a limits change in the editor reaches the report.
    Text without placeholders is returned as is; with placeholders but no
    limits to fill them, the line is left out rather than printed half-empty.
    """
    if "{" not in text:
        return text
    for row in rows:
        if str(row.get("report_quantity", "")).strip().lower() != "voltage":
            continue
        lo, hi = row.get("min"), row.get("max")
        if _is_finite_number(lo) and _is_finite_number(hi) and hi > lo:
            nominal = (lo + hi) / 2
            tol = (hi - lo) / 2 / nominal * 100 if nominal else 0.0
            return (
                text.replace("{nominal}", f"{nominal:g}")
                .replace("{tol}", f"{round(tol, 2):g}")
                .replace("{min}", f"{lo:g}")
                .replace("{max}", f"{hi:g}")
            )
    return ""


def _appendix_a_sections(
    results: list[dict[str, Any]],
    template: dict[str, Any],
    styles: Any,
) -> list[Any]:
    """Per-test Appendix A §2.1.3 sections, or [] if the script has no mapping.

    Returning [] lets the caller fall back to the flat results table, so
    non-spec products (SPREOS, 12VDC RF, …) still get a report.
    """
    grouped = group_by_report_test(results)
    if not grouped:
        return []

    sections_cfg = template.get("test_sections") or {}
    flow: list[Any] = []
    for number, (test_name, rows) in enumerate(grouped, start=1):
        cfg = sections_cfg.get(test_name)
        if cfg is None:
            # A test the Admin added to the script but not to the template:
            # state its limits like the spec's load tests do (all tests run in
            # CR, spec §1.1.3.4). Without voltage limits `_expected_text`
            # returns "" and the line is left out.
            cfg = (
                {"expected": EXPECTED_FROM_LIMITS, "load_mode": "Constant Resistance (CR)"}
                if _expected_text(EXPECTED_FROM_LIMITS, rows)
                else {}
            )
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
        expected = _expected_text(str(cfg.get("expected", "")).strip(), rows)
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
    # The PDF prints the unit inside Value; the CSV keeps it in its own column
    # so the values stay plain numbers.
    if any(f == "value" for _, f in columns) and not any(f == "unit" for _, f in columns):
        at = next(i for i, (_, f) in enumerate(columns) if f == "value") + 1
        columns = columns[:at] + [("Unit", "unit")] + columns[at:]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Field", "Value"])
        for k, v in _header_rows(run_meta, role, results):
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
    for h, v in _header_rows(run_meta, role, results):
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

    # Always `detail_columns`, never `summary_columns`. Appendix A §2.1.3.2-
    # 2.1.3.4 require the Measured Output Value on Tests 2/3/4 with no role
    # qualifier, so the archived PDF always carries the numbers. This used to
    # select the column list from `Capability.VIEW_MEASURED_DETAIL`, which
    # Operator lacks — meaning a normal production run archived a
    # PDF of test names and PASS/FAIL with no measurements at all. The
    # capability still gates the live results table and trace log in the UI;
    # what the permanent record must contain is a separate question.
    #
    # `summary_columns` therefore has no effect here and is CSV-only — see
    # `_write_csv`. Selecting it via a dead `if` was worse than not offering it:
    # an admin could edit it, be told the template was valid, save, and see no
    # change in the PDF, which is exactly what spec §1.1.4.4 Note 2 forbids.
    columns = _column_pairs(template, "detail_columns")
    headers = [label for label, _ in columns]
    # The first column absorbs the slack, so renaming or reordering columns in
    # the template cannot push the table off the page.
    table_rows = _merge_point_rows(results)
    rest = 0.75 * inch
    col_w = [rest] * len(headers)
    if any("value_text" in r for r in table_rows):
        # Merged points carry four values; widen Value so they wrap to two lines.
        for i, (_, field) in enumerate(columns):
            if i > 0 and field == "value":
                col_w[i] = 1.45 * inch
    col_w[0] = max(1.2 * inch, 6.35 * inch - sum(col_w[1:]))

    n_cols = len(headers)
    # The rows themselves say how many loops ran; run_meta carries no
    # loop_count, so reading it from there never printed the loop headers.
    loop_total = max(
        [int(run_meta.get("loop_count", 1) or 1)]
        + [int(r.get("loop", 1) or 1) for r in table_rows]
    )
    has_loops = loop_total > 1

    data: list[list[Any]] = [headers]
    loop_header_rows: list[int] = []
    fail_rows: list[int] = []
    current_loop: int | None = None

    for row in table_rows:
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


class XmlCopyError(OSError):
    """The XML report was written to the unit folder but not copied to the XML folder."""

    def __init__(self, xml_path: Path, folder: Path, cause: Exception) -> None:
        super().__init__(f"{xml_path} could not be copied to {folder}: {cause}")
        self.xml_path, self.folder, self.cause = xml_path, folder, cause


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
        # Distinguish "the script cannot be mapped" from "the run never reached a
        # mappable step". Both leave `results` without a `report_test`, but the
        # causes are unrelated and only the first is the script author's problem.
        # Blaming the script for the second sent an operator to fix a correct
        # `.tst` after what was really a comms failure.
        if not any(str(r.get("test_name", "")).strip() for r in results):
            raise XmlMappingMissingError(
                "Cannot write the CAMSTAR XML report: the run stopped before any "
                "test was performed, so there are no results to report. This is "
                "usually a station fault rather than a script problem - check "
                "that the electronic load is powered and reachable at the "
                "configured address (Admin > Connections), or switch to the "
                "Simulator backend to test without hardware. The script itself "
                "was not the cause. The PDF report is unaffected."
            )
        raise XmlMappingMissingError(
            f"Cannot write the CAMSTAR XML report: {script} declares no "
            "'Report' directives, so its results cannot be mapped onto the "
            "Appendix B structure. Add 'Report <TestName>' (and 'Quantity "
            "Voltage|Current|Power|Resistance' on measurement steps, plus "
            "'Measurement <n>' / 'TimePoint <n> min' for multi-point tests) to "
            "the script's steps. The PDF report is unaffected."
        )
    path.write_text(tree_to_text(build_camstar_tree(run_meta, results)), encoding="utf-8")

