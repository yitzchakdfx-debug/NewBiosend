"""Batch pre-test setup dialog for scanning multiple UUT serials."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
)

from config import LOAD_SERIALS, UUT_TYPES
from logic.models import BatchUnit


class _ScanThread(QThread):
    """Reads input voltage on every load slot and emits the active slot list."""

    scan_done = Signal(list)  # list[int]

    def __init__(self, parent=None, *, target_v: float = 24.0, tol_pct: float = 5.0) -> None:
        super().__init__(parent)
        self._target_v = target_v
        self._tol_pct = tol_pct

    def run(self) -> None:
        from config import PRODIGIT_SCAN_TIMEOUT_MS
        from drivers.factory import create_driver
        from drivers.mock_hardware import MockHardware

        target = self._target_v
        tol = self._tol_pct
        min_v = target * (1.0 - tol / 100.0)
        max_v = target * (1.0 + tol / 100.0)

        # The scan must be fast: use a short timeout and skip the *IDN? identity
        # probe so empty/silent channels cap at ~1.5 s instead of the 5 s used
        # during real test runs.
        try:
            scan_timeout = float(PRODIGIT_SCAN_TIMEOUT_MS)
        except (TypeError, ValueError):
            scan_timeout = 1500.0

        driver = create_driver(timeout_ms=scan_timeout, probe_identity=False)
        try:
            driver.connect()
        except Exception:
            try:
                driver = MockHardware()
                driver.connect()
            except Exception:
                self.scan_done.emit([])
                return

        active: list[int] = []
        for idx, load_serial in enumerate(LOAD_SERIALS):
            slot = idx + 1
            try:
                if hasattr(driver, "activate_slot"):
                    driver.activate_slot(slot, load_serial_number=load_serial)  # type: ignore[attr-defined]
                v = driver.execute_command("readinput", [str(slot)])
                if min_v <= v <= max_v:
                    active.append(slot)
            except Exception:
                pass

        try:
            driver.disconnect()
        except Exception:
            pass

        self.scan_done.emit(active)


class BatchPreTestDialog(QDialog):
    """Collect batch size and serial numbers while keeping a fixed slot mapping."""

    def __init__(
        self,
        tester_name_default: str,
        default_uut_type: str = "",
        scan_target_v: float = 24.0,
        scan_tol_pct: float = 5.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch Pre-Test Setup")
        self.resize(660, 460)
        self._serial_edits: list[QLineEdit] = []
        self._slot_indices: list[int] = []
        self._row_checks: list[QCheckBox] = []
        self._detected_slots: list[int] | None = None

        root = QVBoxLayout(self)
        form = QFormLayout()

        from PySide6.QtWidgets import QComboBox

        self._combo_uut = QComboBox()
        self._combo_uut.addItems(UUT_TYPES)
        if default_uut_type:
            self._combo_uut.setCurrentText(default_uut_type.strip())
        form.addRow("UUT Type", self._combo_uut)

        self._edit_tester = QLineEdit()
        self._edit_tester.setText(tester_name_default.strip())
        form.addRow("Tester Name", self._edit_tester)
        root.addLayout(form)

        # Scanning status
        self._scan_label = QLabel("Scanning for connected UUTs…")
        self._scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scan_label)
        self._scan_bar = QProgressBar()
        self._scan_bar.setRange(0, 0)
        self._scan_bar.setTextVisible(False)
        self._scan_bar.setFixedHeight(6)
        root.addWidget(self._scan_bar)

        self._units_box = QGroupBox("Serial Scan")
        self._grid = QGridLayout(self._units_box)
        self._grid.setContentsMargins(10, 10, 10, 10)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        root.addWidget(self._units_box)

        self._error = QLabel("")
        self._error.setObjectName("lbl_error")
        root.addWidget(self._error)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        self._buttons.accepted.connect(self._try_accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._rebuild_rows()

        self._scanner = _ScanThread(self, target_v=scan_target_v, tol_pct=scan_tol_pct)
        self._scanner.scan_done.connect(self._on_scan_done)
        self._scanner.start()

    # ------------------------------------------------------------------
    # Scan result handler
    # ------------------------------------------------------------------

    def _on_scan_done(self, active_slots: list[int]) -> None:
        self._scan_bar.hide()
        self._detected_slots = active_slots or None

        if active_slots:
            channels = ", ".join(f"CH{s}" for s in active_slots)
            self._scan_label.setText(
                f"{len(active_slots)} UUT(s) detected on {channels} — "
                "check or uncheck any channel to add or remove a device."
            )
        else:
            self._scan_label.setText(
                "No UUTs detected — check the channels you want to test manually."
            )

        # Pre-select the detected channels; the operator can still toggle any
        # channel on or off afterwards to add or remove devices from the batch.
        self._apply_detection()
        self._ok_btn.setEnabled(True)
        for idx, chk in enumerate(self._row_checks):
            if chk.isChecked():
                self._serial_edits[idx].setFocus(
                    Qt.FocusReason.ActiveWindowFocusReason
                )
                break

    def _apply_detection(self) -> None:
        """Tick the detected channels and enable their serial fields."""
        detected = set(self._detected_slots or [])
        for idx, chk in enumerate(self._row_checks):
            on = self._slot_indices[idx] in detected
            chk.setChecked(on)
            self._serial_edits[idx].setEnabled(on)

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def _clear_layout(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_rows(self) -> None:
        """Render one checkable row per physical load channel (CH1..CHn).

        Every channel is always shown so the operator can add a device (tick an
        un-detected channel) or remove one (untick a detected channel) after the
        scan. Detected channels are ticked by `_apply_detection()`.
        """
        self._clear_layout()
        self._serial_edits.clear()
        self._slot_indices.clear()
        self._row_checks.clear()

        headers = ["", "Position", "Load channel", "Load serial", "UUT serial"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setObjectName("lbl_batch_header")
            self._grid.addWidget(lbl, 0, col)

        for idx in range(len(LOAD_SERIALS)):
            slot = idx + 1
            self._slot_indices.append(slot)
            load_serial_str = (
                LOAD_SERIALS[slot - 1] if slot - 1 < len(LOAD_SERIALS) else f"LOAD-{slot}"
            )
            row = idx + 1

            edit = QLineEdit()
            edit.setPlaceholderText("Scan or type serial number")
            edit.setEnabled(False)  # enabled only when its channel is ticked
            edit.returnPressed.connect(
                lambda idx=idx: self._focus_next_serial_field(idx)
            )
            self._serial_edits.append(edit)

            chk = QCheckBox()
            chk.toggled.connect(edit.setEnabled)
            chk.toggled.connect(self._on_check_toggled)
            self._row_checks.append(chk)

            self._grid.addWidget(chk, row, 0, Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(QLabel(f"Position {slot}"), row, 1)
            self._grid.addWidget(QLabel(f"CH{slot}"), row, 2)
            self._grid.addWidget(QLabel(load_serial_str), row, 3)
            self._grid.addWidget(edit, row, 4)

    def _on_check_toggled(self, checked: bool) -> None:
        """When a channel is ticked, focus its serial field for quick entry."""
        if not checked:
            return
        for idx, chk in enumerate(self._row_checks):
            if chk is self.sender():
                self._serial_edits[idx].setFocus(Qt.FocusReason.OtherFocusReason)
                break

    def _focus_next_serial_field(self, current_index: int) -> None:
        for i in range(current_index + 1, len(self._serial_edits)):
            nxt = self._serial_edits[i]
            if nxt.isEnabled():
                nxt.setFocus(Qt.FocusReason.TabFocusReason)
                nxt.selectAll()
                return

    # ------------------------------------------------------------------
    # Validation & result
    # ------------------------------------------------------------------

    def _try_accept(self) -> None:
        tester = self._edit_tester.text().strip()
        uut = self._combo_uut.currentText().strip()
        if not tester or not uut:
            self._error.setText("Tester name and UUT type are required.")
            return

        active_indices = self._active_row_indices()
        if not active_indices:
            self._error.setText("Select at least one unit.")
            return

        serials = [self._serial_edits[i].text().strip() for i in active_indices]
        if any(not s for s in serials):
            self._error.setText("Every selected position must have a serial number.")
            return
        if len({s.casefold() for s in serials}) != len(serials):
            self._error.setText("Duplicate serial numbers are not allowed.")
            return

        self._error.clear()
        self.accept()

    def _active_row_indices(self) -> list[int]:
        if self._row_checks:
            return [i for i, chk in enumerate(self._row_checks) if chk.isChecked()]
        return list(range(len(self._serial_edits)))

    def result_dict(self) -> dict[str, object]:
        units: list[BatchUnit] = []
        for idx in self._active_row_indices():
            slot = self._slot_indices[idx] if idx < len(self._slot_indices) else idx + 1
            load_serial = (
                LOAD_SERIALS[slot - 1] if slot - 1 < len(LOAD_SERIALS) else f"LOAD-{slot}"
            )
            units.append(
                BatchUnit(
                    serial_number=self._serial_edits[idx].text().strip(),
                    slot_index=slot,
                    position_label=f"Position {slot}",
                    load_channel=f"CH{slot}",
                    load_serial_number=load_serial,
                )
            )
        return {
            "uut_type": self._combo_uut.currentText().strip(),
            "tester_name": self._edit_tester.text().strip(),
            "batch_units": units,
        }

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self._scanner.isRunning():
            self._scanner.quit()
            self._scanner.wait(2000)
        super().closeEvent(event)
