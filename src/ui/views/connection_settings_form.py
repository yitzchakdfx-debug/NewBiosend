"""Admin form for editing a test-version's serial connection parameters.

The DB stores the parameters as a single pipe-delimited string:
    ``PORT|BAUD|PARITY|STOP_BITS``  e.g. ``COM3|115200|N|1``

This dialog owns the serialization round-trip so the rest of the app
never has to parse the raw string.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

try:
    from serial.tools import list_ports as _list_ports
except Exception:
    _list_ports = None


BAUD_RATES: tuple[str, ...] = ("9600", "19200", "38400", "57600", "115200")
PARITY_VALUES: tuple[tuple[str, str], ...] = (
    ("N", "None"),
    ("E", "Even"),
    ("O", "Odd"),
    ("M", "Mark"),
    ("S", "Space"),
)
STOP_BITS: tuple[str, ...] = ("1", "1.5", "2")

_DEFAULT_PORT = "COM1"
_DEFAULT_BAUD = "115200"
_DEFAULT_PARITY = "N"
_DEFAULT_STOP_BITS = "1"


def _enumerate_com_ports() -> list[str]:
    """System COM ports via pyserial; falls back to COM1..COM8 if unavailable."""
    if _list_ports is None:
        return [f"COM{i}" for i in range(1, 9)]
    try:
        ports = [p.device for p in _list_ports.comports()]
    except Exception:
        ports = []
    if not ports:
        ports = [f"COM{i}" for i in range(1, 9)]
    return ports


class ConnectionSettingsForm(QDialog):
    """Modal editor for a single connection-parameters string.

    Use as::

        dlg = ConnectionSettingsForm(parent=...)
        dlg.populate_form_from_db(existing_string)
        if dlg.exec() == QDialog.Accepted:
            db_value = dlg.get_form_as_db_string()
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Edit Connection Parameters",
        subtitle: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        root = QVBoxLayout(self)
        if subtitle:
            lbl = QLabel(subtitle)
            lbl.setWordWrap(True)
            root.addWidget(lbl)

        form = QFormLayout()

        self._cmb_port = QComboBox()
        self._cmb_port.setEditable(True)
        self._cmb_port.addItems(_enumerate_com_ports())
        form.addRow("COM Port:", self._cmb_port)

        self._cmb_baud = QComboBox()
        self._cmb_baud.addItems(BAUD_RATES)
        self._cmb_baud.setCurrentText(_DEFAULT_BAUD)
        form.addRow("Baud Rate:", self._cmb_baud)

        self._cmb_parity = QComboBox()
        for code, label in PARITY_VALUES:
            self._cmb_parity.addItem(f"{label} ({code})", userData=code)
        self._select_parity(_DEFAULT_PARITY)
        form.addRow("Parity:", self._cmb_parity)

        self._cmb_stop = QComboBox()
        self._cmb_stop.addItems(STOP_BITS)
        self._cmb_stop.setCurrentText(_DEFAULT_STOP_BITS)
        form.addRow("Stop Bits:", self._cmb_stop)

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---- serialization ----------------------------------------------------

    def populate_form_from_db(self, db_string: str) -> None:
        """Hydrate the four combos from a ``PORT|BAUD|PARITY|STOP_BITS`` string.

        Missing or malformed segments fall back to defaults; unknown values
        are appended to the combo (editable port; selected as-is elsewhere)
        so admins can edit pre-existing rows without losing data.
        """
        parts = [s.strip() for s in str(db_string or "").split("|")]
        while len(parts) < 4:
            parts.append("")
        port, baud, parity, stop = parts[:4]

        self._set_combo_text(self._cmb_port, port or _DEFAULT_PORT)
        self._set_combo_text(self._cmb_baud, baud or _DEFAULT_BAUD)
        self._select_parity(parity or _DEFAULT_PARITY)
        self._set_combo_text(self._cmb_stop, stop or _DEFAULT_STOP_BITS)

    def get_form_as_db_string(self) -> str:
        """Pack the current combo selections into ``PORT|BAUD|PARITY|STOP_BITS``."""
        port = self._cmb_port.currentText().strip() or _DEFAULT_PORT
        baud = self._cmb_baud.currentText().strip() or _DEFAULT_BAUD
        parity = self._current_parity_code()
        stop = self._cmb_stop.currentText().strip() or _DEFAULT_STOP_BITS
        return f"{port}|{baud}|{parity}|{stop}"

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return
        if combo.isEditable():
            combo.setEditText(value)
        else:
            combo.addItem(value)
            combo.setCurrentIndex(combo.count() - 1)

    def _select_parity(self, code: str) -> None:
        target = (code or "").strip().upper() or _DEFAULT_PARITY
        for i in range(self._cmb_parity.count()):
            if str(self._cmb_parity.itemData(i)) == target:
                self._cmb_parity.setCurrentIndex(i)
                return
        self._cmb_parity.addItem(f"{target} ({target})", userData=target)
        self._cmb_parity.setCurrentIndex(self._cmb_parity.count() - 1)

    def _current_parity_code(self) -> str:
        data = self._cmb_parity.currentData()
        return str(data) if data is not None else _DEFAULT_PARITY
