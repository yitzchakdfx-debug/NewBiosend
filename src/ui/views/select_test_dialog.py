"""Operator: pick a test configuration from the database."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from logic.database_manager import DatabaseManager
from paths import user_tmp_path


class SelectTestDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._temp_path: Path | None = None
        self._selected_row: dict | None = None

        self.setWindowTitle("Product Selection")
        self.resize(900, 420)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Choose a test version from the library:"))

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Name", "UUT Type", "Version", "Date"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self._table)

        row = QHBoxLayout()
        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self._populate)
        row.addWidget(self._btn_refresh)
        row.addStretch()
        root.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_row)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()

    def _populate(self) -> None:
        rows = self._db.list_test_versions()
        self._table.setRowCount(0)
        for r in rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            self._table.setItem(i, 0, QTableWidgetItem(str(r["test_name"])))
            self._table.setItem(i, 1, QTableWidgetItem(str(r["uut_type"])))
            self._table.setItem(i, 2, QTableWidgetItem(str(r["version_name"])))
            self._table.setItem(i, 3, QTableWidgetItem(str(r["created_at"])))
            for c in range(4):
                it = self._table.item(i, c)
                if it is not None:
                    it.setData(Qt.ItemDataRole.UserRole, int(r["id"]))

    def _accept_row(self) -> None:
        cr = self._table.currentRow()
        if cr < 0:
            QMessageBox.information(self, "Product Selection", "Select a row first.")
            return
        item0 = self._table.item(cr, 0)
        if item0 is None:
            return
        vid = item0.data(Qt.ItemDataRole.UserRole)
        if vid is None:
            return
        rec = self._db.get_test_version(int(vid))
        if rec is None:
            QMessageBox.warning(self, "Product Selection", "Version was removed.")
            self._populate()
            return
        try:
            tmp_dir = user_tmp_path()
            tmp_dir.mkdir(parents=True, exist_ok=True)
            self._temp_path = tmp_dir / f"dfx_op_{uuid.uuid4().hex}.tst"
            self._temp_path.write_text(rec["test_content"], encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Product Selection", f"Could not create temp file:\n{exc}")
            return
        self._selected_row = {
            "id": rec["id"],
            "test_name": rec["test_name"],
            "uut_type": rec["uut_type"],
            "version_name": rec["version_name"],
        }
        self.accept()

    def selected_path(self) -> Path | None:
        return self._temp_path

    def selected_catalog(self) -> dict | None:
        return self._selected_row
