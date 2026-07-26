"""Admin: database audit entries and decrypted daily hardware/security logs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import LOG_ENCRYPTION_PASSWORD
from logic.database_manager import DatabaseManager
from logic.secure_logger import get_secure_logger
from ui.ui_helpers import attach_password_visibility_toggle


class _NumericTableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically instead of lexicographically."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except (ValueError, TypeError):
            return super().__lt__(other)


class _ExportPasswordDialog(QDialog):
    """Small modal that collects the admin password with a visibility toggle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Security Verification")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter Admin Password to export:"))
        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        attach_password_visibility_toggle(self._edit)
        layout.addWidget(self._edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def password(self) -> str:
        return self._edit.text()


class AuditViewerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self.setWindowTitle("Logs")
        self.resize(980, 560)

        root = QVBoxLayout(self)

        intro = QLabel("Recorded application and security actions (newest first).")
        root.addWidget(intro)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search / Filter:"))
        self._edit_filter = QLineEdit()
        self._edit_filter.setPlaceholderText("Type to filter rows...")
        self._edit_filter.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._edit_filter, stretch=1)
        self._btn_export_txt = QPushButton("Export Selected to TXT")
        self._btn_export_txt.clicked.connect(self._export_selected_to_txt)
        filter_row.addWidget(self._btn_export_txt)
        self._btn_view_details = QPushButton("View Selected Details Below")
        self._btn_view_details.clicked.connect(self._view_selected_db_rows)
        filter_row.addWidget(self._btn_view_details)
        root.addLayout(filter_row)

        self._audit_table = QTableWidget(0, 6)
        self._audit_table.setHorizontalHeaderLabels(
            ["ID", "Timestamp", "Username", "Employee ID", "Action", "Details"]
        )
        self._audit_table.horizontalHeader().setStretchLastSection(True)
        self._audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._audit_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._audit_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._audit_table.setSortingEnabled(True)
        root.addWidget(self._audit_table, stretch=1)

        hw_box = QGroupBox("Daily Hardware Trace")
        hw_root = QVBoxLayout(hw_box)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Date"))
        self._hw_date = QDateEdit()
        self._hw_date.setCalendarPopup(True)
        self._hw_date.setDate(date.today())
        self._hw_date.setDisplayFormat("yyyy-MM-dd")
        self._hw_date.setMinimumWidth(130)
        ctrl.addWidget(self._hw_date)
        self._btn_today = QPushButton("Today")
        self._btn_today.clicked.connect(lambda: self._hw_date.setDate(QDate.currentDate()))
        ctrl.addWidget(self._btn_today)
        btn_decrypt = QPushButton("Decrypt & View")
        btn_decrypt.clicked.connect(self._decrypt_hardware_day)
        ctrl.addWidget(btn_decrypt)
        btn_load_file = QPushButton("Load File...")
        btn_load_file.clicked.connect(self._load_manual_file)
        ctrl.addWidget(btn_load_file)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_audit_table)
        ctrl.addWidget(btn_refresh)
        ctrl.addStretch()
        hw_root.addLayout(ctrl)

        pwd_row = QHBoxLayout()
        pwd_row.addWidget(QLabel("Password"))
        self._hw_password = QLineEdit()
        self._hw_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._hw_password.setPlaceholderText("Log decryption password")
        attach_password_visibility_toggle(self._hw_password)
        pwd_row.addWidget(self._hw_password, stretch=1)
        hw_root.addLayout(pwd_row)

        self._hw_text = QTextEdit()
        self._hw_text.setReadOnly(True)
        hw_root.addWidget(self._hw_text, stretch=1)

        root.addWidget(hw_box, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

        self._setup_calendar_widget()
        QTimer.singleShot(0, self._setup_calendar_widget)
        self._refresh_audit_table()
        self._highlight_available_dates()


    def _make_point_font(
        self,
        source_font: QFont | None = None,
        point_size: int = 10,
        bold: bool = False,
    ) -> QFont:
        """Return a fresh font with an explicit, valid point size.

        Qt sometimes stores fonts with pointSize() == -1, especially when fonts
        are inherited from stylesheets or resolved through widgets. Constructing
        a fresh font avoids copying an invalid point size into calendar formats.
        """
        family = source_font.family() if source_font is not None else ""
        font = QFont(family or "Segoe UI")
        font.setPointSize(point_size)
        font.setBold(bold)
        return font


    def _setup_calendar_widget(self) -> None:
        """Configure the popup calendar and force a valid base font."""
        calendar = self._hw_date.calendarWidget()

        calendar.setVerticalHeaderFormat(calendar.VerticalHeaderFormat.NoVerticalHeader)
        calendar.setGridVisible(False)

        safe_calendar_font = self._make_point_font(calendar.font(), point_size=10)
        calendar.setFont(safe_calendar_font)

        safe_date_edit_font = self._make_point_font(self._hw_date.font(), point_size=10)
        self._hw_date.setFont(safe_date_edit_font)

        for child in calendar.findChildren(QWidget):
            child.setFont(self._make_point_font(child.font(), point_size=10))


    def _highlight_available_dates(self) -> None:
        """Bold+blue dates in the calendar widget that have a ``sys_*.dat`` file."""
        logger = get_secure_logger()
        logs_dir: Path = logger._dir

        if not logs_dir.is_dir():
            return

        calendar = self._hw_date.calendarWidget()

        highlight_font = self._make_point_font(
            calendar.font(), point_size=10, bold=True
        )

        fmt = QTextCharFormat()
        fmt.setFont(highlight_font)
        fmt.setForeground(QColor("#0284c7"))
        fmt.setBackground(QColor("#e0f2fe"))

        for p in logs_dir.glob("sys_*.dat"):
            stem = p.stem
            date_str = stem.removeprefix("sys_")

            if len(date_str) != 8:
                continue

            try:
                qd = QDate(
                    int(date_str[:4]),
                    int(date_str[4:6]),
                    int(date_str[6:8]),
                )
            except (ValueError, IndexError):
                continue

            if qd.isValid():
                calendar.setDateTextFormat(qd, fmt)

    def _refresh_audit_table(self) -> None:
        self._audit_table.setSortingEnabled(False)
        rows = self._db.get_audit_logs()
        self._audit_table.setRowCount(len(rows))
        numeric_columns = {0, 3}  # ID, Employee ID
        for r, row in enumerate(rows):
            values = (
                row["id"],
                row["timestamp"],
                row["username"],
                row["employee_id"],
                row["action"],
                row["details"],
            )
            for c, cell in enumerate(values):
                if c in numeric_columns:
                    item = _NumericTableItem(cell)
                else:
                    item = QTableWidgetItem(cell)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._audit_table.setItem(r, c, item)
        self._audit_table.setSortingEnabled(True)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for r in range(self._audit_table.rowCount()):
            if not needle:
                self._audit_table.setRowHidden(r, False)
                continue
            visible = False
            for c in range(self._audit_table.columnCount()):
                item = self._audit_table.item(r, c)
                if item and needle in item.text().lower():
                    visible = True
                    break
            self._audit_table.setRowHidden(r, not visible)

    def _export_selected_to_txt(self) -> None:
        dlg = _ExportPasswordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        password = dlg.password()
        if password != LOG_ENCRYPTION_PASSWORD:
            QMessageBox.warning(self, "Export", "Incorrect password.")
            return

        selected_rows = sorted(set(item.row() for item in self._audit_table.selectedItems()))
        if not selected_rows:
            QMessageBox.warning(self, "Export", "No rows selected. Select one or more rows first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audit Logs", "audit_export.txt", "Text Files (*.txt)"
        )
        if not path:
            return
        lines: list[str] = []
        for r in selected_rows:
            ts = self._audit_table.item(r, 1)
            user = self._audit_table.item(r, 2)
            action = self._audit_table.item(r, 4)
            details = self._audit_table.item(r, 5)
            lines.append(
                f"[{ts.text() if ts else ''}] "
                f"| User: {user.text() if user else ''} "
                f"| Action: {action.text() if action else ''} "
                f"| Details: {details.text() if details else ''}"
            )
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            QMessageBox.information(
                self, "Export", f"Exported {len(lines)} row(s) to:\n{path}"
            )
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _decrypt_hardware_day(self) -> None:
        pwd = self._hw_password.text()
        if not pwd:
            QMessageBox.warning(self, "Hardware log", "Enter the log password.")
            return
        qd = self._hw_date.date()
        day = date(qd.year(), qd.month(), qd.day())
        try:
            logger = get_secure_logger()
            path = logger.day_file_path(day)
            records = logger.read_day(day, pwd)
        except Exception as exc:
            QMessageBox.critical(self, "Hardware log", str(exc))
            return

        if not records and not path.is_file():
            self._hw_text.clear()
            QMessageBox.information(
                self,
                "Audit Log",
                "No log file for selected date.",
            )
            return
        elif not records:
            self._hw_text.clear()
            QMessageBox.critical(
                self,
                "Decryption Error",
                "Incorrect password or corrupted log file.",
            )
            return

        self._hw_text.setPlainText(self._format_records(records))

    def _format_records(self, records: list[dict]) -> str:
        """Render decrypted log records into human-readable lines."""
        lines: list[str] = []
        for rec in records:
            ts_s = str(rec.get("ts", ""))
            cat_s = str(rec.get("category", ""))
            payload = rec.get("payload")
            if isinstance(payload, str):
                payload_s = payload
            elif payload is not None:
                try:
                    payload_s = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                except (TypeError, ValueError):
                    payload_s = repr(payload)
            else:
                payload_s = ""
            lines.append(f"[{ts_s}] {cat_s} {payload_s}")
        return "\n".join(lines)

    def _load_manual_file(self) -> None:
        """Load and decrypt an arbitrary .dat log file from disk."""
        pwd = self._hw_password.text()
        if not pwd:
            QMessageBox.warning(self, "Hardware log", "Enter the log password.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Log File", "", "Encrypted Logs (*.dat)"
        )
        if not file_path:
            return
        try:
            records = get_secure_logger().read_file(Path(file_path), pwd)
        except Exception as exc:
            QMessageBox.critical(self, "Load File", str(exc))
            return
        if not records:
            self._hw_text.clear()
            QMessageBox.critical(
                self,
                "Decryption Error",
                "Incorrect password or corrupted log file.",
            )
            return
        self._hw_text.setPlainText(self._format_records(records))

    def _view_selected_db_rows(self) -> None:
        """Dump selected audit-table rows into the bottom text area for easy reading."""
        selected_rows = sorted(
            set(item.row() for item in self._audit_table.selectedItems())
        )
        if not selected_rows:
            QMessageBox.warning(
                self, "View Details", "No rows selected. Select one or more rows first."
            )
            return
        headers = [
            self._audit_table.horizontalHeaderItem(c).text()
            for c in range(self._audit_table.columnCount())
        ]
        blocks: list[str] = ["--- Selected DB Audit Rows ---", ""]
        for r in selected_rows:
            parts: list[str] = []
            for c, label in enumerate(headers):
                item = self._audit_table.item(r, c)
                parts.append(f"{label}: {item.text() if item else ''}")
            blocks.append("\n".join(parts))
            blocks.append("")
        self._hw_text.setPlainText("\n".join(blocks))
