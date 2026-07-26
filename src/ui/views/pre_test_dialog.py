"""Pre-test metadata dialog before execution starts."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from config import UUT_TYPES


class PreTestDialog(QDialog):
    def __init__(
        self,
        tester_name_default: str,
        default_uut_type: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pre-Test Setup")
        self.resize(460, 200)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self._combo_uut = QComboBox()
        self._combo_uut.addItems(UUT_TYPES)
        if default_uut_type:
            self._combo_uut.setCurrentText(default_uut_type.strip())
        form.addRow("UUT Type", self._combo_uut)

        self._edit_sn = QLineEdit()
        self._edit_sn.setPlaceholderText("Scan or type serial number")
        form.addRow("Serial Number", self._edit_sn)

        self._edit_tester = QLineEdit()
        self._edit_tester.setText(tester_name_default.strip())
        form.addRow("Tester Name", self._edit_tester)

        root.addLayout(form)
        self._error = QLabel("")
        self._error.setObjectName("lbl_error")
        root.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._edit_sn.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _try_accept(self) -> None:
        uut = self._combo_uut.currentText().strip()
        sn = self._edit_sn.text().strip()
        tester = self._edit_tester.text().strip()
        if not uut or not sn or not tester:
            self._error.setText("All fields are required.")
            return
        self._error.clear()
        self.accept()

    def result_dict(self) -> dict[str, str]:
        return {
            "uut_type": self._combo_uut.currentText().strip(),
            "serial_number": self._edit_sn.text().strip(),
            "tester_name": self._edit_tester.text().strip(),
        }
