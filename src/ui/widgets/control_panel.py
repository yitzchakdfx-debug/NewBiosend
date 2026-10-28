"""Right-hand ATE control rail (start/stop, user, unit, status)."""

from __future__ import annotations

import re
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal

from logic.roles import role_label
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


#: Applied in order to shorten a step name for its unit's progress row, e.g.
#: "Voltage Measurement [V] - 300W - 15min" -> "V 300W 15m". The full name stays
#: in the bar's tooltip.
_SHORT_NAME_RULES: tuple[tuple[str, str], ...] = (
    (r"^Voltage Measurement[^-]*", "V"),
    (r"^Current Measurement[^-]*", "I"),
    (r"^Power Measurement[^-]*", "P"),
    (r"^Resistance Measurement[^-]*", "R"),
    (r"^LED Indication Test$", "LED"),
    (r"^Polarity Check$", "Polarity"),
    (r"^Input Connection Check$", "Input 24V"),
    (r"^(?:Low Load|Burn-In) (\d+W) Setup$", r"Setup \1"),
    (r"\s*-\s*", " "),
    (r"(\d+)\s*min\b", r"\1m"),
)
_SHORT_NAME_MAX = 16


def short_step_name(name: str) -> str:
    """A step name short enough to sit inside a narrow progress bar."""
    text = name.strip()
    for pattern, replacement in _SHORT_NAME_RULES:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= _SHORT_NAME_MAX else text[: _SHORT_NAME_MAX - 1] + "…"


def _mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class UnitProgressRow(QWidget):
    """One UUT's current step: a header line (channel, step, timer) over a bar.

    The step name sits on its own line above the bar, not inside it: drawn
    inside, the bar's fill covered it and dark text on the fill was unreadable.

    The timer shows real time since the step started, plus the step's total
    wait when it has one (`03:20/10:00`), so the operator can tell how long is
    left. Step names carry the report time point ("15m"), not the step's own
    duration, which is why the total comes from the script instead.
    """

    def __init__(
        self,
        slot: int,
        serial: str,
        duration_lookup: Callable[[str], float | None] | None = None,
        parent: QWidget | None = None,
        label_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._duration_lookup = duration_lookup
        self._label_lookup = label_lookup
        self._started: float | None = None
        self._total_s: float | None = None
        self._done = False
        #: This unit's share of the Total bar (parallel run), and whether it
        #: has ended. Set by `set_total` / `on_step("")`.
        self.total_percent = 0
        self.finished = False
        self.on_total_changed: Callable[[], None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        # No fixed widths: a fixed 30 px cut "CH1" to "CH" in the station's
        # bold font. Each label takes what its font needs; the step name gets
        # the rest of the line.
        owner = QLabel(f"CH{slot}")
        owner.setObjectName("unit_progress_owner")
        owner.setToolTip(serial)

        self._step = QLabel()
        self._step.setObjectName("unit_progress_step")

        self._time = QLabel()
        self._time.setObjectName("unit_progress_time")
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        header.addWidget(owner)
        header.addWidget(self._step, stretch=1)
        header.addWidget(self._time)
        layout.addLayout(header)

        self.bar = QProgressBar()
        self.bar.setObjectName("unit_progress_bar")
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)  # the timer says how far along it is
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)
        self.reset()

    def reset(self) -> None:
        self._started = None
        self._done = False
        self._failed = False
        self.total_percent = 0
        self.finished = False
        self._set_failed_style(False)
        self.bar.setValue(0)
        self._step.setText("-")
        self._step.setToolTip("")
        self._time.setText("--:--")

    def step_text(self) -> str:
        """The short step name currently shown (for tests and accessibility)."""
        return self._step.text()

    def on_step(self, name: str) -> None:
        """Slot for the runner's `current_test`; an empty name ends the run."""
        if not name:
            self.tick()  # record the true end time, then freeze it
            self._started = None
            self.finished = True
            if self.on_total_changed is not None:
                self.on_total_changed()
            if self._failed:
                self._step.setText(self._fail_text)
                self._done = True
                self.bar.setValue(100)
            elif self._step.toolTip():
                self._step.setText("Done")
                self._done = True
                self.bar.setValue(100)
            else:
                self._step.setText("-")
            return
        self._done = False
        self._started = time.monotonic()
        self._total_s = self._duration_lookup(name) if self._duration_lookup else None
        self.bar.setValue(0)
        # "Burn-In t2" from the script when available; the abbreviation of the
        # step name ("V 300W 15m") only for steps it cannot place.
        label = self._label_lookup(name) if self._label_lookup else None
        if label and len(label) > _SHORT_NAME_MAX:
            label = label[: _SHORT_NAME_MAX - 1] + "…"
        self._step.setText(label or short_step_name(name))
        self._step.setToolTip(name)
        self.tick()

    def mark_failed(self, text: str = "FAIL") -> None:
        """The unit failed: red full bar and `text` ("FAIL", "NO COMM") next to its channel."""
        self._failed = True
        self._fail_text = text
        self._done = True
        self._step.setText(text)
        self.bar.setValue(100)
        self._set_failed_style(True)

    def _set_failed_style(self, failed: bool) -> None:
        # A dynamic property, styled in the .qss (`[failed="true"]`); a widget
        # must be re-polished for a property change to take effect.
        for widget in (self.bar, self._step):
            widget.setProperty("failed", failed)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def set_total(self, value: int) -> None:
        """Slot for the runner's `progress_total` in a parallel run."""
        self.total_percent = int(value)
        if self.on_total_changed is not None:
            self.on_total_changed()

    def set_progress(self, value: int) -> None:
        """Slot for the runner's `progress_test`.

        Ignored once the run is done: the runner resets progress to 0 on its way
        out, which would leave an empty bar next to "Done".
        """
        if not self._done:
            self.bar.setValue(value)

    def tick(self) -> None:
        if self._started is None:
            return
        text = _mmss(time.monotonic() - self._started)
        if self._total_s:
            text += f"/{_mmss(self._total_s)}"
        self._time.setText(text)


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
        self.edit_user_level.setText(role_label(str(user_info.get("role", ""))))
        user_form.addRow("Level", self.edit_user_level)
        root.addWidget(self.user_box)

        # Hidden (2026-10-28): in a parallel run these showed one unit only; the
        # CH1-CH4 rows, results headers and live table show every unit. The
        # fields stay as data holders (part number, UUT type, serial feed the
        # run meta and reports). Shown again only when a script has no PartNum,
        # so the operator can type one.
        unit_box = QGroupBox("Unit")
        unit_box.setObjectName("grp_unit")
        self.unit_box = unit_box
        unit_form = _make_form(unit_box)
        self.edit_uut_type = QLineEdit()
        self.edit_uut_type.setReadOnly(True)
        self.edit_uut_type.setPlaceholderText("-")
        unit_form.addRow("UUT type", self.edit_uut_type)
        self.edit_part_number = QLineEdit()
        unit_form.addRow("Part number", self.edit_part_number)
        self.edit_serial_number = QLineEdit()
        unit_form.addRow("Serial number", self.edit_serial_number)
        root.addWidget(unit_box)
        unit_box.setVisible(False)

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
        self.edit_current_unit = QLineEdit()
        self.edit_current_unit.setReadOnly(True)
        self.edit_current_unit.setPlaceholderText("-")
        status_form.addRow("Unit", self.edit_current_unit)
        status_form.setRowVisible(self.edit_current_unit, False)  # see unit_box

        self.progress_total = QProgressBar()
        self.progress_total.setMaximumHeight(16)
        status_form.addRow("Total", self.progress_total)
        status_layout.addLayout(status_form)

        # One row per UUT in the run (see `UnitProgressRow`), rebuilt by
        # `show_unit_progress` at each run start. Replaces the former shared
        # "Current" field and single "Test" bar, which in a parallel run showed
        # whichever unit happened to report last.
        self._unit_rows: list[UnitProgressRow] = []
        self._unit_rows_layout = QVBoxLayout()
        self._unit_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._unit_rows_layout.setSpacing(2)
        status_layout.addLayout(self._unit_rows_layout)
        self._tick_timer = QTimer(self)
        # Several ticks per second: the timer is not aligned to step starts, so
        # a 1 s tick could show a step's clock up to a second behind.
        self._tick_timer.setInterval(250)
        self._tick_timer.timeout.connect(self._tick_rows)

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

    def show_unit_progress(
        self,
        units: list[tuple[int, str]],
        duration_lookup: Callable[[str], float | None] | None = None,
        label_lookup: Callable[[str], str | None] | None = None,
    ) -> dict[int, UnitProgressRow]:
        """Rebuild one progress row per (slot_index, serial), in slot order.

        Returns the rows keyed by slot so each runner thread can be wired to its
        own unit's row. `duration_lookup(step_name)` gives a step's total wait;
        `label_lookup(step_name)` the name to show ("Burn-In t2"), or None.
        """
        while self._unit_rows_layout.count():
            item = self._unit_rows_layout.takeAt(0)
            old = item.widget()
            if old is not None:
                # Hidden at once: until the deferred delete runs, a widget taken
                # out of the layout is still a visible child and paints at (0,0).
                old.hide()
                old.deleteLater()
        self._unit_rows = []
        rows: dict[int, UnitProgressRow] = {}
        for slot, serial in sorted(units):
            row = UnitProgressRow(
                slot, serial, duration_lookup, parent=self, label_lookup=label_lookup
            )
            row.on_total_changed = self._refresh_total
            self._unit_rows_layout.addWidget(row)
            self._unit_rows.append(row)
            rows[slot] = row
        self._tick_timer.start()
        return rows

    def _refresh_total(self) -> None:
        """Total bar in a parallel run: the slowest unit still running.

        Every unit used to write its own percentage straight to the bar, so it
        flickered between them (2 % / 3 % / 2 %...). A unit that has ended no
        longer holds the bar back.
        """
        running = [r.total_percent for r in self._unit_rows if not r.finished]
        if running:
            self.progress_total.setValue(min(running))
        elif self._unit_rows:
            self.progress_total.setValue(max(r.total_percent for r in self._unit_rows))

    def stop_unit_progress(self) -> None:
        """Freeze the rows at their final state; they stay until the next run."""
        self._tick_timer.stop()

    def reset_unit_progress(self) -> None:
        for row in self._unit_rows:
            row.reset()

    def _tick_rows(self) -> None:
        for row in self._unit_rows:
            row.tick()

    def take_user_box(self) -> QGroupBox:
        """Detach the User QGroupBox so MainWindow can re-parent it into the left sidebar.

        Safe to call exactly once. The QLineEdit children (edit_user_name,
        edit_user_level) remain attributes of this widget for callers.
        """
        self._root_layout.removeWidget(self.user_box)
        self.user_box.setParent(None)
        return self.user_box
