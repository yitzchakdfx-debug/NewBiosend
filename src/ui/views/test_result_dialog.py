"""Modal end-of-sequence result dialog (big PASS / FAIL banner)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TestResultDialog(QDialog):
    def __init__(self, passed: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sequence Result")
        self.setModal(True)
        self.setFixedSize(480, 260)

        status_text = "Test Pass" if passed else "Test Fail"
        banner_name = "lbl_banner_pass" if passed else "lbl_banner_fail"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        self._banner = QLabel(status_text)
        self._banner.setObjectName(banner_name)
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setMinimumHeight(120)
        layout.addWidget(self._banner, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._btn_close = QPushButton("Close")
        self._btn_close.setFixedWidth(140)
        self._btn_close.setMinimumHeight(32)
        self._btn_close.setDefault(True)
        self._btn_close.clicked.connect(self.accept)
        button_row.addWidget(self._btn_close)
        button_row.addStretch()
        layout.addLayout(button_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.geometry().center()
            self.move(
                center.x() - self.width() // 2,
                center.y() - self.height() // 2,
            )
