"""Admin: configure global instrument connection strings (COM ports / VISA resources)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from config import INSTRUMENTS
from logic.database_manager import DatabaseManager


class ConnectionsDialog(QDialog):
    def __init__(self, username: str, employee_id: str, parent=None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._username = username
        self._employee_id = employee_id

        self.setWindowTitle("Connections")
        self.resize(520, 80 + len(INSTRUMENTS) * 36)

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel("Enter a COM port (e.g. COM9) or VISA string for each instrument.")
        )

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 8)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(12)

        stored = self._db.list_instrument_connections()
        self._fields: dict[str, QLineEdit] = {}
        for name in INSTRUMENTS:
            edit = QLineEdit()
            edit.setObjectName(f"conn_field_{name.replace(' ', '_')}")
            edit.setPlaceholderText("COM9  or  USB0::0x0957::…::INSTR")
            edit.setText(stored.get(name, ""))
            form.addRow(name, edit)
            self._fields[name] = edit

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        changed: list[str] = []
        stored = self._db.list_instrument_connections()
        for name, edit in self._fields.items():
            value = edit.text().strip()
            if value != stored.get(name, ""):
                changed.append(name)
            self._db.upsert_instrument_connection(name, value, self._username)

        if changed:
            try:
                self._db.log_audit_action(
                    "Updated instrument connections",
                    username=self._username,
                    employee_id=self._employee_id,
                    details=f"changed={changed!r}",
                )
            except Exception:
                pass

        QMessageBox.information(self, "Connections", "Instrument connections saved.")
        self.accept()
