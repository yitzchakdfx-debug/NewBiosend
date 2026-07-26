"""Right-hand ATE control rail (start/stop, user, unit, status)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def _make_form(parent: QWidget) -> QFormLayout:
    """Compact QFormLayout with tight vertical spacing for sidebar use."""
    form = QFormLayout(parent)
    form.setContentsMargins(6, 6, 6, 6)
    form.setVerticalSpacing(3)
    form.setHorizontalSpacing(6)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return form


class ControlPanelWidget(QWidget):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, user_info: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root_layout = QVBoxLayout(self)
        root = self._root_layout
        root.setSpacing(3)
        root.setContentsMargins(4, 4, 4, 4)

        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setMinimumHeight(32)
        self.btn_start.clicked.connect(self.start_requested)
        root.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setMinimumHeight(26)
        self.btn_stop.clicked.connect(self.stop_requested)
        root.addWidget(self.btn_stop)

        self.chk_save_log = QCheckBox("Save as log")
        self.chk_save_log.setObjectName("chk_save_log")
        self.chk_save_log.setChecked(True)
        self.chk_save_log.setToolTip(
            "When checked, the sequence PDF report is archived at end-of-run."
        )
        root.addWidget(self.chk_save_log)

        self.user_box = QGroupBox("User")
        self.user_box.setObjectName("grp_user")
        user_form = _make_form(self.user_box)
        self.edit_user_name = QLineEdit()
        self.edit_user_name.setReadOnly(True)
        self.edit_user_name.setText(str(user_info.get("name", "")))
        user_form.addRow("Name", self.edit_user_name)
        self.edit_user_level = QLineEdit()
        self.edit_user_level.setReadOnly(True)
        self.edit_user_level.setText(str(user_info.get("role", "")))
        user_form.addRow("Level", self.edit_user_level)
        root.addWidget(self.user_box)

        unit_box = QGroupBox("Unit")
        unit_box.setObjectName("grp_unit")
        unit_form = _make_form(unit_box)
        self.edit_uut_type = QLineEdit()
        self.edit_uut_type.setReadOnly(True)
        self.edit_uut_type.setPlaceholderText("—")
        unit_form.addRow("UUT type", self.edit_uut_type)
        self.edit_part_number = QLineEdit()
        unit_form.addRow("Part number", self.edit_part_number)
        self.edit_serial_number = QLineEdit()
        unit_form.addRow("Serial number", self.edit_serial_number)
        root.addWidget(unit_box)

        status_box = QGroupBox("Status")
        status_box.setObjectName("grp_status")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(6, 6, 6, 6)
        status_layout.setSpacing(4)

        status_form = QFormLayout()
        status_form.setContentsMargins(0, 0, 0, 0)
        status_form.setVerticalSpacing(3)
        status_form.setHorizontalSpacing(6)
        status_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.edit_current_test = QLineEdit()
        self.edit_current_test.setReadOnly(True)
        self.edit_current_test.setPlaceholderText("—")
        status_form.addRow("Current", self.edit_current_test)

        self.edit_current_unit = QLineEdit()
        self.edit_current_unit.setReadOnly(True)
        self.edit_current_unit.setPlaceholderText("—")
        status_form.addRow("Unit", self.edit_current_unit)

        self.progress_test = QProgressBar()
        self.progress_test.setMaximumHeight(16)
        status_form.addRow("Test", self.progress_test)
        self._status_form = status_form

        self.progress_total = QProgressBar()
        self.progress_total.setMaximumHeight(16)
        status_form.addRow("Total", self.progress_total)
        status_layout.addLayout(status_form)

        # Container for per-unit bars shown in parallel mode (hidden by default)
        self._parallel_bars_container = QWidget()
        self._parallel_bars_container.setVisible(False)
        self._parallel_bars_layout = QVBoxLayout(self._parallel_bars_container)
        self._parallel_bars_layout.setContentsMargins(0, 0, 0, 0)
        self._parallel_bars_layout.setSpacing(2)
        status_layout.addWidget(self._parallel_bars_container)

        counters = QHBoxLayout()
        counters.setSpacing(4)
        self.label_pass = QLabel("PASS: 0")
        self.label_pass.setObjectName("lbl_pass_counter")
        self.label_pass.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_fail = QLabel("FAIL: 0")
        self.label_fail.setObjectName("lbl_fail_counter")
        self.label_fail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        counters.addWidget(self.label_pass)
        counters.addWidget(self.label_fail)
        status_layout.addLayout(counters)

        self.label_batch_summary = QLabel("Batch: 0 PASS / 0 FAIL")
        self.label_batch_summary.setObjectName("lbl_batch_summary")
        self.label_batch_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.label_batch_summary)

        loops_row = QHBoxLayout()
        loops_row.setSpacing(4)
        loops_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.chk_loops = QCheckBox("Loops")
        self.spin_loops = QSpinBox()
        self.spin_loops.setMinimum(1)
        self.spin_loops.setMaximum(999)
        self.spin_loops.setValue(1)
        self.spin_loops.setMinimumWidth(64)
        self.spin_loops.setEnabled(False)
        self.chk_loops.toggled.connect(self.spin_loops.setEnabled)
        loops_row.addWidget(self.chk_loops, 0, Qt.AlignmentFlag.AlignVCenter)
        loops_row.addWidget(self.spin_loops, 1, Qt.AlignmentFlag.AlignVCenter)
        status_layout.addLayout(loops_row)

        self.chk_stop_on_fail = QCheckBox("Stop on fail")
        status_layout.addWidget(self.chk_stop_on_fail)

        root.addWidget(status_box)
        root.addStretch()

    def set_running_state(self, running: bool) -> None:
        """Toggle button states and labels to reflect whether a run is active."""
        self.btn_stop.setEnabled(running)
        if not running:
            self.btn_start.setText("Start")

    def show_parallel_progress(self, position_labels: list[str]) -> list[QProgressBar]:
        """Switch to per-unit progress bars for parallel mode. Returns the bar list."""
        label_widget = self._status_form.labelForField(self.progress_test)
        if label_widget:
            label_widget.setVisible(False)
        self.progress_test.setVisible(False)

        while self._parallel_bars_layout.count():
            item = self._parallel_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        bars: list[QProgressBar] = []
        for lbl_text in position_labels:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(4)
            lbl = QLabel(f"{lbl_text}:")
            lbl.setFixedWidth(100)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setMaximumHeight(14)
            row_l.addWidget(lbl)
            row_l.addWidget(bar)
            self._parallel_bars_layout.addWidget(row_w)
            bars.append(bar)

        self._parallel_bars_container.setVisible(True)
        return bars

    def hide_parallel_progress(self) -> None:
        """Restore the single test progress bar."""
        self._parallel_bars_container.setVisible(False)
        label_widget = self._status_form.labelForField(self.progress_test)
        if label_widget:
            label_widget.setVisible(True)
        self.progress_test.setVisible(True)
        self.progress_test.setValue(0)

    def take_user_box(self) -> QGroupBox:
        """Detach the User QGroupBox so MainWindow can re-parent it into the left sidebar.

        Safe to call exactly once. The QLineEdit children (edit_user_name,
        edit_user_level) remain attributes of this widget for callers.
        """
        self._root_layout.removeWidget(self.user_box)
        self.user_box.setParent(None)
        return self.user_box
