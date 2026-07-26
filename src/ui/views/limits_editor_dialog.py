"""Admin: view and edit step parameters (value, limits, channel, critical flag)."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from logic.database_manager import DatabaseManager
from logic.models import ScriptDocument, TestStep
from logic.script_manager import ScriptManager
from paths import user_tmp_path


# Commands that never carry a numeric setpoint as their first arg. Used only by
# the numeric-Value detection pass; on/off (relay/setlogic) values are detected
# separately as a fallback when a step has no numeric setpoint.
_NON_NUMERIC_CMDS: frozenset[str] = frozenset({
    "relay", "setlogic", "readchannel", "getid",
    "delay", "log", "prompt", "promptyesno",
})

_ONOFF_CMDS: frozenset[str] = frozenset({"relay", "setlogic"})
_ONOFF_VALUES: frozenset[str] = frozenset({"on", "off"})

# Column layout. Named so a column can be inserted without renumbering every
# literal index in the populate/validate passes.
COL_TEST = 0
COL_VALUE = 1
COL_LOW = 2
COL_HIGH = 3
COL_DELAY = 4
COL_UNIT = 5
COL_CHANNEL = 6
COL_CRIT = 7

_HEADERS = ["Test", "Value", "Low", "High", "Delay (ms)", "Unit", "Channel", "Crit"]
_COL_WIDTHS = {
    COL_VALUE: 100,
    COL_LOW: 90,
    COL_HIGH: 90,
    COL_DELAY: 90,
    COL_UNIT: 70,
    COL_CHANNEL: 80,
    COL_CRIT: 50,
}


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


@dataclass
class _RowDesc:
    step_idx: int
    value_cmd_idx: int | None    # command index providing the editable Value
    value_kind: str              # "numeric" | "onoff" | "none"
    value_arg_idx: int           # which arg within that command holds the value
    channel_cmd_idx: int | None  # command index for readchannel (channel)
    delay_cmd_idx: int | None    # command index for Delay (test duration, ms)


def _locked_dash() -> QTableWidgetItem:
    it = QTableWidgetItem("—")
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class LimitsEditorDialog(QDialog):
    """Table editor for step parameters in the loaded test.

    One row per step. Editable columns: Value, Low, High, Delay (ms), Unit,
    Channel (readchannel arg), and Critical flag. Together these cover the three
    parameters spec Rev.1.1 requires an Admin to be able to adjust — test
    duration (Delay), voltage limits/tolerance (Low/High), and load resistance
    (Value, i.e. the `setresistance` setpoint).

    The Value column holds the step's single numeric setpoint when present;
    otherwise it falls back to the on/off state of a relay/setlogic command.
    Yes/No prompts (PromptYesNo) have no stored value — the answer is given at
    runtime — so they show "—", as does any column a step does not use.

    Retry and Prompt text remain out of scope (edit via the raw .tst editor).
    Multi-command steps are fine; only the identified Value/Delay/Channel args
    plus step-level fields are written back, leaving other lines intact.
    """

    def __init__(
        self,
        document: ScriptDocument,
        script_manager: ScriptManager,
        catalog_test_name: str,
        catalog_uut_type: str,
        catalog_version: str,
        username: str,
        employee_id: str,
        db: DatabaseManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._script_manager = script_manager
        self._catalog_test_name = catalog_test_name.strip()
        self._catalog_uut_type = catalog_uut_type.strip()
        self._catalog_version = catalog_version.strip()
        self._username = username.strip()
        self._employee_id = employee_id.strip()
        self._db = db
        self._metadata = document.metadata

        # Work on deep copies so edits never touch the live document.
        self._all_steps: list[TestStep] = copy.deepcopy(document.steps)
        self._rows: list[_RowDesc] = self._build_rows()

        self._apply_mode = False
        self._temp_path: Path | None = None

        # Allow the user to maximize/restore the dialog from the title bar.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.setWindowTitle(
            f"Limits — {self._catalog_test_name} {self._catalog_version}".strip()
        )
        self._apply_initial_size()

        root = QVBoxLayout(self)

        if not self._all_steps:
            root.addWidget(QLabel("No steps found in this test."))
        else:
            root.addWidget(
                QLabel(
                    f"Edit parameters for {self._catalog_test_name!r}. "
                    "Changes take effect when you Apply or Save."
                )
            )
            self._table = QTableWidget(0, len(_HEADERS))
            self._table.setObjectName("limits_table")
            self._table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
            self._populate_table()
            root.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        self._btn_zoom = QPushButton("⛶ Maximize")
        self._btn_zoom.setObjectName("btn_limits_zoom")
        self._btn_zoom.clicked.connect(self._toggle_maximize)
        self._btn_save = QPushButton("Save && update version")
        self._btn_save.setObjectName("btn_limits_save")
        self._btn_apply = QPushButton("Apply / run")
        self._btn_apply.setObjectName("btn_limits_apply")
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btn_limits_cancel")

        no_steps = not self._all_steps
        self._btn_save.setEnabled(not no_steps)
        self._btn_apply.setEnabled(not no_steps)

        self._btn_save.clicked.connect(self._on_save)
        self._btn_apply.clicked.connect(self._on_apply)
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self._btn_zoom)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_apply)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Window sizing / zoom
    # ------------------------------------------------------------------

    def _apply_initial_size(self) -> None:
        """Open at a size that always fits on screen so the buttons are visible.

        The table scrolls internally when there are more rows than fit; the
        user can still maximize via the title bar or the Maximize button.
        """
        desired_w = 900
        desired_h = 160 + len(self._all_steps) * 30

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = int(avail.width() * 0.95)
            max_h = int(avail.height() * 0.90)
            self.resize(min(desired_w, max_w), min(desired_h, max_h))
        else:
            self.resize(desired_w, min(desired_h, 800))

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self._btn_zoom.setText("⛶ Maximize")
        else:
            self.showMaximized()
            self._btn_zoom.setText("❐ Restore")

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def is_apply_mode(self) -> bool:
        return self._apply_mode

    def temp_path(self) -> Path | None:
        return self._temp_path

    # ------------------------------------------------------------------
    # Row descriptor builder
    # ------------------------------------------------------------------

    @staticmethod
    def _find_onoff_arg(args: list[str]) -> int | None:
        for ai, a in enumerate(args):
            if str(a).lower() in _ONOFF_VALUES:
                return ai
        return None

    def _build_rows(self) -> list[_RowDesc]:
        rows: list[_RowDesc] = []
        for idx, step in enumerate(self._all_steps):
            value_cmd_idx: int | None = None
            value_kind = "none"
            value_arg_idx = 0
            channel_cmd_idx: int | None = None
            delay_cmd_idx: int | None = None

            # Pass 1: numeric setpoint (highest priority) + channel + delay.
            for ci, c in enumerate(step.commands):
                cmd_lower = c["cmd"].lower()
                if (
                    channel_cmd_idx is None
                    and cmd_lower == "readchannel"
                    and c["args"]
                ):
                    channel_cmd_idx = ci
                if delay_cmd_idx is None and cmd_lower == "delay" and c["args"]:
                    delay_cmd_idx = ci
                if (
                    value_cmd_idx is None
                    and cmd_lower not in _NON_NUMERIC_CMDS
                    and c["args"]
                    and _is_float(c["args"][0])
                ):
                    value_cmd_idx = ci
                    value_kind = "numeric"
                    value_arg_idx = 0

            # Pass 2: no numeric value — fall back to an on/off state.
            if value_cmd_idx is None:
                for ci, c in enumerate(step.commands):
                    if c["cmd"].lower() in _ONOFF_CMDS:
                        onoff_idx = self._find_onoff_arg(c["args"])
                        if onoff_idx is not None:
                            value_cmd_idx = ci
                            value_kind = "onoff"
                            value_arg_idx = onoff_idx
                            break

            rows.append(
                _RowDesc(
                    idx,
                    value_cmd_idx,
                    value_kind,
                    value_arg_idx,
                    channel_cmd_idx,
                    delay_cmd_idx,
                )
            )
        return rows

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self) -> None:
        self._table.clearContents()
        self._table.setRowCount(len(self._rows))

        self._table.setHorizontalHeaderLabels(_HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_TEST, QHeaderView.ResizeMode.Stretch)
        for column, width in _COL_WIDTHS.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)

        for row, rd in enumerate(self._rows):
            step = self._all_steps[rd.step_idx]

            # Test — always read-only
            name_item = QTableWidgetItem(step.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, COL_TEST, name_item)

            # Value — numeric setpoint or on/off; locked "—" if neither.
            # For the load tests this is the CR resistance (`setresistance`).
            if rd.value_cmd_idx is not None:
                args = step.commands[rd.value_cmd_idx]["args"]
                val = args[rd.value_arg_idx] if rd.value_arg_idx < len(args) else ""
                self._table.setItem(row, COL_VALUE, QTableWidgetItem(str(val)))
            else:
                self._table.setItem(row, COL_VALUE, _locked_dash())

            # Low / High / Unit — always editable step-level fields
            low_text = f"{step.min_val:g}" if step.min_val is not None else ""
            high_text = f"{step.max_val:g}" if step.max_val is not None else ""
            self._table.setItem(row, COL_LOW, QTableWidgetItem(low_text))
            self._table.setItem(row, COL_HIGH, QTableWidgetItem(high_text))
            self._table.setItem(row, COL_UNIT, QTableWidgetItem(step.unit or ""))

            # Delay — the step's test duration in ms; locked "—" if it has none
            if rd.delay_cmd_idx is not None:
                args = step.commands[rd.delay_cmd_idx]["args"]
                self._table.setItem(
                    row, COL_DELAY, QTableWidgetItem(args[0] if args else "")
                )
            else:
                self._table.setItem(row, COL_DELAY, _locked_dash())

            # Channel — readchannel arg; locked "—" if no readchannel
            if rd.channel_cmd_idx is not None:
                args = step.commands[rd.channel_cmd_idx]["args"]
                self._table.setItem(
                    row, COL_CHANNEL, QTableWidgetItem(args[0] if args else "")
                )
            else:
                self._table.setItem(row, COL_CHANNEL, _locked_dash())

            # Crit — checkbox; always shown and editable
            crit_item = QTableWidgetItem()
            crit_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            crit_item.setCheckState(
                Qt.CheckState.Checked if step.is_critical else Qt.CheckState.Unchecked
            )
            self._table.setItem(row, COL_CRIT, crit_item)

    # ------------------------------------------------------------------
    # Cell read helper
    # ------------------------------------------------------------------

    def _emit_monitor_dropped(self, step_name: str) -> None:
        """Warn that clearing limits also removed a step's continuous monitoring."""
        self._monitor_dropped.append(step_name)

    def _get_text(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        if it is None:
            return ""
        text = it.text().strip()
        return "" if text == "—" else text

    # ------------------------------------------------------------------
    # Validation + write-back
    # ------------------------------------------------------------------

    def _validate_and_apply_table(self) -> bool:
        """Read table cells back into self._all_steps. Returns False on error."""
        self._monitor_dropped: list[str] = []
        for row, rd in enumerate(self._rows):
            step = self._all_steps[rd.step_idx]

            # --- Value ---
            if rd.value_cmd_idx is not None:
                val_text = self._get_text(row, COL_VALUE)
                if rd.value_kind == "numeric":
                    if not _is_float(val_text):
                        self._error(
                            f"Row {row + 1} ({step.name!r}): Value must be a number."
                        )
                        return False
                elif rd.value_kind == "onoff":
                    if val_text.lower() not in _ONOFF_VALUES:
                        self._error(
                            f"Row {row + 1} ({step.name!r}): Value must be 'on' or 'off'."
                        )
                        return False
                    val_text = val_text.lower()
                args = step.commands[rd.value_cmd_idx]["args"]
                if rd.value_arg_idx < len(args):
                    args[rd.value_arg_idx] = val_text
                else:
                    args.append(val_text)

            # --- Low / High ---
            low_text = self._get_text(row, COL_LOW)
            high_text = self._get_text(row, COL_HIGH)
            if low_text or high_text:
                try:
                    low_val = float(low_text) if low_text else None
                except ValueError:
                    self._error(
                        f"Row {row + 1} ({step.name!r}): Low limit must be a number."
                    )
                    return False
                try:
                    high_val = float(high_text) if high_text else None
                except ValueError:
                    self._error(
                        f"Row {row + 1} ({step.name!r}): High limit must be a number."
                    )
                    return False
                if (
                    low_val is not None
                    and high_val is not None
                    and low_val > high_val
                ):
                    self._error(
                        f"Row {row + 1} ({step.name!r}): "
                        f"Low ({low_val:g}) must not exceed High ({high_val:g})."
                    )
                    return False
                step.min_val = low_val
                step.max_val = high_val
            else:
                # Both cleared — remove limits entirely.
                step.min_val = None
                step.max_val = None

            # --- Unit ---
            # Keep "" (not None): TestStep.unit is declared `str` and the
            # serializer uses `if step.unit:` which treats "" as "no unit".
            step.unit = self._get_text(row, COL_UNIT)

            # --- Delay ---
            # The duration (first arg) comes from this column. Trailing
            # monitoring bounds are re-derived from Low/High rather than left
            # alone: they exist to police the same window during the wait, and
            # letting them drift would mean continuous monitoring enforced a
            # different range than the step's own pass/fail.
            if rd.delay_cmd_idx is not None:
                delay_text = self._get_text(row, COL_DELAY)
                try:
                    delay_ms = int(float(delay_text))
                except ValueError:
                    self._error(
                        f"Row {row + 1} ({step.name!r}): "
                        "Delay must be a whole number of milliseconds."
                    )
                    return False
                if delay_ms < 0:
                    self._error(
                        f"Row {row + 1} ({step.name!r}): Delay cannot be negative."
                    )
                    return False
                args = step.commands[rd.delay_cmd_idx]["args"]
                monitored = len(args) >= 3
                new_args = [str(delay_ms)]
                if monitored and step.min_val is not None and step.max_val is not None:
                    new_args += [f"{step.min_val:g}", f"{step.max_val:g}"]
                elif monitored:
                    # Limits were cleared, so there is nothing to monitor against.
                    self._emit_monitor_dropped(step.name)
                step.commands[rd.delay_cmd_idx]["args"] = new_args

            # --- Channel ---
            if rd.channel_cmd_idx is not None:
                ch_text = self._get_text(row, COL_CHANNEL)
                try:
                    int(ch_text)
                except ValueError:
                    self._error(
                        f"Row {row + 1} ({step.name!r}): Channel must be an integer."
                    )
                    return False
                args = step.commands[rd.channel_cmd_idx]["args"]
                if args:
                    args[0] = ch_text
                else:
                    args.append(ch_text)

            # --- Crit ---
            crit_item = self._table.item(row, COL_CRIT)
            if crit_item is not None:
                step.is_critical = (
                    crit_item.checkState() == Qt.CheckState.Checked
                )

        if self._monitor_dropped:
            QMessageBox.warning(
                self,
                "Continuous monitoring removed",
                "These steps had continuous voltage monitoring during their wait, "
                "but their limits were cleared, so monitoring has been removed:\n\n"
                + "\n".join(f"  • {name}" for name in self._monitor_dropped)
                + "\n\nRe-enter Low and High to restore it.",
            )
        return True

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _serialize(self) -> str:
        return self._script_manager.serialize_ordered_steps(
            self._all_steps, metadata=self._metadata
        )

    def _write_temp(self) -> Path | None:
        try:
            tmp_dir = user_tmp_path()
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp = tmp_dir / f"dfx_limits_{uuid.uuid4().hex}.tst"
            tmp.write_text(self._serialize(), encoding="utf-8")
            return tmp
        except OSError as exc:
            self._error(f"Could not write temp file:\n{exc}")
            return None

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Limits", msg)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_apply(self) -> None:
        if not self._validate_and_apply_table():
            return
        tmp = self._write_temp()
        if tmp is None:
            return
        self._temp_path = tmp
        self._apply_mode = True
        self.accept()

    def _on_save(self) -> None:
        if not self._validate_and_apply_table():
            return

        existing = [
            str(v["version_name"])
            for v in self._db.list_test_versions()
            if str(v["test_name"]) == self._catalog_test_name
        ]

        new_ver, ok = QInputDialog.getText(
            self,
            "New Version Name",
            "Enter a new unique version name\n"
            f"(current: {self._catalog_version!r} — the selected row is preserved):",
            text="V1.1",
        )
        if not ok:
            return
        try:
            new_ver = ScriptManager.validate_version_name(new_ver, existing)
        except ValueError as exc:
            self._error(str(exc))
            return

        content = self._serialize()
        try:
            self._db.add_test_version(
                self._catalog_test_name,
                self._catalog_uut_type,
                new_ver,
                content,
                self._username,
            )
        except Exception as exc:
            self._error(f"Save failed:\n{exc}")
            return

        try:
            self._db.log_audit_action(
                "Saved limits as new version",
                username=self._username,
                employee_id=self._employee_id,
                details=(
                    f"test={self._catalog_test_name!r} "
                    f"new_version={new_ver!r} "
                    f"from={self._catalog_version!r}"
                ),
            )
        except Exception:
            pass

        QMessageBox.information(
            self, "Limits", f"Saved as version {new_ver!r} for {self._catalog_test_name!r}."
        )
        self._apply_mode = False
        self.accept()
