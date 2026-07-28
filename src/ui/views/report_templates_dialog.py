"""Admin editor for the XML and PDF report templates.

Spec Rev.1.1 §1.1.4.4 Notes 1 and 2: an authorized administrator can update the
XML report template and the final PDF report template, and the changes must
appear in generated reports. Edits are validated before they are written, so a
malformed template cannot reach the report path and cost an operator a completed
30-minute burn-in.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from logic.database_manager import DatabaseManager
from logic.report_templates import (
    DEFAULT_PDF_TEMPLATE,
    DEFAULT_XML_TEMPLATE,
    PDF_TEMPLATE_NAME,
    XML_TEMPLATE_NAME,
    TemplateError,
    ensure_templates_seeded,
    read_pdf_template,
    read_xml_template,
    template_dir,
    validate_pdf_template,
    validate_xml_template,
    write_pdf_template,
    write_xml_template,
)
import json


class _TemplateTab(QWidget):
    """One template: a monospaced editor, a validate button, and a reset."""

    def __init__(
        self,
        *,
        description: str,
        text: str,
        default_text: str,
        validator,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._validator = validator
        self._default_text = default_text

        layout = QVBoxLayout(self)
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.editor, 1)

        row = QHBoxLayout()
        self.btn_validate = QPushButton("Check")
        self.btn_validate.clicked.connect(self._on_validate)
        row.addWidget(self.btn_validate)
        self.btn_reset = QPushButton("Restore Default")
        self.btn_reset.clicked.connect(self._on_reset)
        row.addWidget(self.btn_reset)
        row.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        layout.addLayout(row)

    def text(self) -> str:
        return self.editor.toPlainText()

    def validate(self) -> str | None:
        """Return an error message, or None when the template is usable."""
        try:
            self._validator(self.text())
        except TemplateError as exc:
            return str(exc)
        return None

    def _on_validate(self) -> None:
        error = self.validate()
        if error is None:
            self.status.setText("Template is valid.")
        else:
            self.status.setText(error)

    def _on_reset(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Restore default",
                "Replace the editor contents with the built-in default template?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.editor.setPlainText(self._default_text)
            self.status.setText("Default restored — not saved yet.")


class ReportTemplatesDialog(QDialog):
    """Edit the XML and PDF report templates (Admin only)."""

    def __init__(self, *, username: str = "", employee_id: str = "", parent=None) -> None:
        super().__init__(parent)
        self._username = username
        self._employee_id = employee_id
        self.setWindowTitle("Report Templates")
        self.resize(900, 700)

        ensure_templates_seeded()

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"Templates are stored in: {template_dir()}")
        )

        self.tabs = QTabWidget()
        self.tab_xml = _TemplateTab(
            description=(
                "<b>XML report template (CAMSTAR).</b> Replace values with "
                "<code>{{placeholders}}</code>. The empty <code>&lt;Tests/&gt;</code> "
                "element marks where the measured results are inserted — keep it "
                "exactly once and leave it empty; its contents are generated from "
                "the test script and cannot be edited here."
            ),
            text=read_xml_template(),
            default_text=DEFAULT_XML_TEMPLATE,
            validator=validate_xml_template,
        )
        self.tabs.addTab(self.tab_xml, XML_TEMPLATE_NAME)

        self.tab_pdf = _TemplateTab(
            description=(
                "<b>PDF report template.</b> JSON controlling the title, subtitle, "
                "optional logo, which header fields appear and in what order, and "
                "the results columns. <code>header_fields</code> and the column "
                "lists are <code>[label, field]</code> pairs: the label is free "
                "text, the field must be a known name.<br>"
                "The PDF results table always uses <code>detail_columns</code>, so "
                "the archived report keeps its measured values whoever ran the "
                "test. <code>summary_columns</code> affects <b>CSV export only</b>, "
                "for roles that may not view measured detail — editing it will not "
                "change the PDF."
            ),
            text=json.dumps(read_pdf_template(), indent=2),
            default_text=json.dumps(DEFAULT_PDF_TEMPLATE, indent=2),
            validator=validate_pdf_template,
        )
        self.tabs.addTab(self.tab_pdf, PDF_TEMPLATE_NAME)
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        # Validate both tabs before writing either, so a bad PDF template does
        # not leave a half-applied change with the XML already saved.
        for tab, name in ((self.tab_xml, "XML"), (self.tab_pdf, "PDF")):
            error = tab.validate()
            if error is not None:
                self.tabs.setCurrentWidget(tab)
                tab.status.setText(error)
                QMessageBox.warning(
                    self, f"{name} template is not valid", error
                )
                return

        try:
            write_xml_template(self.tab_xml.text())
            write_pdf_template(self.tab_pdf.text())
        except (TemplateError, OSError) as exc:
            QMessageBox.warning(self, "Could not save templates", str(exc))
            return

        try:
            DatabaseManager().log_audit_action(
                "Report templates updated",
                username=self._username,
                employee_id=self._employee_id,
                details=f"files={XML_TEMPLATE_NAME}, {PDF_TEMPLATE_NAME}",
            )
        except Exception:
            pass

        QMessageBox.information(
            self,
            "Saved",
            "Report templates saved. They apply to the next generated report.",
        )
        self.accept()
