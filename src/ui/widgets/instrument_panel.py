"""Live hardware readout panel: label, value, unit per row; updated via `update_values`."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


_DEFAULT_READOUTS: list[dict[str, str]] = [
    {
        "key": "V",
        "label": "Voltage",
        "unit": "V",
        "value_object_name": "readout_value_V",
    },
    {
        "key": "A",
        "label": "Current",
        "unit": "A",
        "value_object_name": "readout_value_A",
    },
]


class InstrumentPanelWidget(QWidget):
    def __init__(
        self,
        readouts: Sequence[Mapping[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("instrument_panel")

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(2)

        title = QLabel("Power Supply")
        title.setObjectName("instrument_panel_title")
        root.addWidget(title)

        self._value_labels: dict[str, QLabel] = {}

        configs = list(readouts) if readouts is not None else _DEFAULT_READOUTS
        for cfg in configs:
            key = str(cfg["key"])
            label_text = str(cfg["label"])
            unit_text = str(cfg["unit"])
            value_on = str(cfg.get("value_object_name", f"readout_value_{key}"))

            row = QFrame()
            row.setObjectName("readout_row")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(2, 2, 2, 2)
            row_layout.setSpacing(8)

            lbl = QLabel(label_text)
            lbl.setObjectName("readout_label")

            val = QLabel("--")
            val.setObjectName(value_on)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            unit = QLabel(unit_text)
            unit.setObjectName("readout_unit")

            row_layout.addWidget(lbl, stretch=0)
            row_layout.addWidget(val, stretch=1)
            row_layout.addWidget(unit, stretch=0)

            self._value_labels[key] = val
            root.addWidget(row)

    def update_values(self, data: dict[str, Any]) -> None:
        """Update numeric string from keys present in *data* (e.g. ``V``, ``A``)."""
        for key, label in self._value_labels.items():
            if key not in data:
                continue
            raw = data[key]
            try:
                num = float(raw)
            except (TypeError, ValueError):
                label.setText(str(raw))
                continue
            label.setText(f"{num:.2f}")
