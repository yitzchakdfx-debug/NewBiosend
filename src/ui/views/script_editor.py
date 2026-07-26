"""Modal editor for `.tst` test-script files.

Inherits the application's active theme via Qt stylesheet propagation
(parent widget -> child dialog) and uses `ScriptManager` for all disk I/O.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from logic.script_manager import ScriptManager


class ScriptEditorDialog(QDialog):
    """Edit the raw text of a single `.tst` file."""

    _MIN_WIDTH: int = 760
    _MIN_HEIGHT: int = 520

    def __init__(
        self,
        script_manager: ScriptManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._script_manager = script_manager
        self._current_path: Path | None = None
        self._dirty: bool = False
        self._virtual_edit: bool = False

        self.setWindowTitle("Test Script Editor")
        self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)
        self.setSizeGripEnabled(True)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._path_label = QLabel("No file loaded.")
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_label.setWordWrap(True)
        root.addWidget(self._path_label)

        self._editor = QPlainTextEdit()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setFixedPitch(True)
        mono.setPointSize(11)
        self._editor.setFont(mono)
        self._editor.setTabChangesFocus(False)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.textChanged.connect(self._mark_dirty)
        root.addWidget(self._editor, stretch=1)

        self._status_label = QLabel("")
        self._status_label.setObjectName("lbl_status_hint")
        root.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._btn_save = QPushButton("Save")
        self._btn_save.clicked.connect(self.save_file)
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setObjectName("btn_stop")
        self._btn_cancel.clicked.connect(self.reject)
        button_row.addWidget(self._btn_save)
        button_row.addWidget(self._btn_cancel)
        root.addLayout(button_row)

    def load_file(self, path: Path | str) -> None:
        path = Path(path)
        self._set_busy(True, f"Loading {path.name}...")
        try:
            text = self._script_manager.read_script(path)
        except (OSError, ValueError) as exc:
            self._set_busy(False)
            QMessageBox.critical(self, "Failed to load", f"Could not open file:\n{exc}")
            return

        self._virtual_edit = False
        self._current_path = path
        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._dirty = False

        self._path_label.setText(f"<b>File:</b> {path}")
        self.setWindowTitle(f"Test Script Editor - {path.name}")
        self._set_busy(False, f"Loaded {path.name}.")

    def load_catalog_version(
        self, test_name: str, version_name: str, content: str
    ) -> None:
        """Load raw script text from DB catalog; Save finishes without writing a file path."""
        self._virtual_edit = True
        self._current_path = None
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.blockSignals(False)
        self._dirty = False
        self._path_label.setText(
            f"<b>Catalog version:</b> {test_name} / {version_name}<br>"
            "<i>Save creates a new catalog version only (this row is not overwritten).</i>"
        )
        self.setWindowTitle(
            f"Test Script Editor — {test_name} ({version_name})"
        )
        self._status_label.setText("")

    def result_text(self) -> str:
        return self._editor.toPlainText()

    def save_file(self) -> None:
        if self._virtual_edit:
            self._dirty = False
            self._set_busy(False, "")
            self.accept()
            return
        if self._current_path is None:
            QMessageBox.warning(self, "No file", "There is no file to save.")
            return

        self._set_busy(True, f"Saving {self._current_path.name}...")
        try:
            self._script_manager.write_script(
                self._current_path, self._editor.toPlainText()
            )
        except (OSError, ValueError) as exc:
            self._set_busy(False)
            QMessageBox.critical(self, "Failed to save", f"Could not save file:\n{exc}")
            return

        self._dirty = False
        self._set_busy(False, f"Saved {self._current_path.name}.")
        QTimer.singleShot(150, self.accept)

    def reject(self) -> None:
        if self._dirty:
            choice = QMessageBox.question(
                self,
                "Discard changes?",
                "You have unsaved changes. Discard and close?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            if self._current_path is not None:
                self.setWindowTitle(f"Test Script Editor - {self._current_path.name} *")
            elif self._virtual_edit:
                t = self.windowTitle().replace(" *", "")
                self.setWindowTitle(t + " *")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """Toggle controls and process pending GUI events to stay responsive."""
        self._btn_save.setEnabled(not busy)
        self._btn_cancel.setEnabled(not busy)
        self._editor.setReadOnly(busy)
        if message:
            self._status_label.setText(message)
        QGuiApplication.processEvents()
