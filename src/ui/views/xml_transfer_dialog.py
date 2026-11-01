"""XML transfer service: status and transfer log (read-only).

Customer POD test 3: the GUI shows the service status (Running), the MES status
(Available / Unavailable), the transfer cycle, and a log of every attempt with
file name, time, result (Successful / Failed) and the file's current status
(Pending / Transferred, taken from the folder the file is in now). Nothing here
can change or delete a log entry.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from logic import xml_transfer as xt

_COLUMNS = ("XML report", "Attempt time", "Result", "Current status", "Detail")


def _short_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso or "-")


class XmlTransferDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("XML Transfer Service")
        self.resize(980, 620)
        layout = QVBoxLayout(self)

        box = QGroupBox("Status")
        form = QFormLayout(box)
        self.lbl_service = QLabel()
        self.lbl_service.setObjectName("xml_service_status")
        self.lbl_mes = QLabel()
        self.lbl_mes.setObjectName("xml_mes_status")
        self.lbl_mes.setWordWrap(True)
        self.lbl_folder = QLabel()
        self.lbl_folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_cycle = QLabel()
        self.lbl_cycle.setObjectName("xml_cycle")
        self.lbl_files = QLabel()
        self.lbl_files.setObjectName("xml_files")
        form.addRow("Transfer service:", self.lbl_service)
        form.addRow("MES communication:", self.lbl_mes)
        form.addRow("MES folder:", self.lbl_folder)
        form.addRow("Transfer cycle:", self.lbl_cycle)
        form.addRow("Local XML reports:", self.lbl_files)
        layout.addWidget(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Transfer log"))
        row.addStretch(1)
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("Filter (file name, result, status)...")
        self.edit_filter.textChanged.connect(self._apply_filter)
        row.addWidget(self.edit_filter, 1)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setObjectName("xml_log_table")
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.btn_refresh = QPushButton("Refresh")
        buttons.addButton(self.btn_refresh, QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_refresh.clicked.connect(self.refresh)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        status = xt.read_status()
        running = xt.service_running(status)
        self.lbl_service.setText("Running" if running else "Stopped")
        self.lbl_service.setStyleSheet(f"font-weight: bold; color: {'#16a34a' if running else '#dc2626'};")
        mes = str(status.get("mes", "")) if running else ""
        if mes:
            when = _short_time(status.get("mes_checked", ""))
            reason = status.get("mes_reason", "")
            self.lbl_mes.setText(f"{mes}  (checked {when})" + (f"\n{reason}" if reason else ""))
            colour = "#16a34a" if mes == "Available" else "#dc2626"
        else:
            self.lbl_mes.setText("Unknown - the transfer service is not running")
            colour = "#dc2626"
        self.lbl_mes.setStyleSheet(f"font-weight: bold; color: {colour};")
        folder = xt.mes_dir()
        self.lbl_folder.setText(str(folder) if folder else "(not set - Connections > Instrument Setup)")
        if running:
            every = status.get("cycle_seconds", xt.CYCLE_SECONDS)
            if status.get("cycle_running"):
                now = "running now"
            else:
                now = f"next {_short_time(status.get('next_cycle', ''))}"
            self.lbl_cycle.setText(
                f"every {float(every):g} s - last {_short_time(status.get('last_cycle_end', ''))}, {now}"
            )
        else:
            self.lbl_cycle.setText("-")
        pending = len(xt.pending_files())
        transferred = sum(1 for _ in xt.transferred_dir().glob("*.xml")) if xt.transferred_dir().is_dir() else 0
        self.lbl_files.setText(f"Pending: {pending}    Transferred: {transferred}")
        self._fill_log()

    def _fill_log(self) -> None:
        try:
            entries = xt.TransferLog().entries()
        except Exception:
            entries = []
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            current = xt.file_status(entry["filename"]) or "-"
            values = (
                entry["filename"],
                _short_time(entry["timestamp"]),
                entry["result"],
                current,
                entry.get("detail", ""),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 2:
                    item.setForeground(Qt.GlobalColor.darkGreen if value == xt.RESULT_OK else Qt.GlobalColor.red)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self._apply_filter(self.edit_filter.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            cells = (self.table.item(row, c) for c in range(self.table.columnCount()))
            line = " ".join(c.text().lower() for c in cells if c is not None)
            self.table.setRowHidden(row, bool(needle) and needle not in line)
