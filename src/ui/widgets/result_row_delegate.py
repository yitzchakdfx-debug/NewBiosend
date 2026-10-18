"""Result-cell delegate that paints PASS / FAIL background tints.

Install with ``table.setItemDelegateForColumn(4, ResultRowDelegate(table))``.

Loop-separator rows are created via ``QTableWidget.setSpan(row, 0, 1, cols)``
which means the cells in columns 1-4 are not painted for those rows, so this
delegate is never invoked on them — no special handling required.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import QStyledItemDelegate


class ResultRowDelegate(QStyledItemDelegate):
    _PASS_BG = QColor("#22c55e")
    _FAIL_BG = QColor("#ef4444")
    _NA_BG = QColor("#94a3b8")
    _ON_COLOR_TEXT = QColor("#ffffff")

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip().upper()
        # A failing row carries its reason after the word ("FAIL — 14.97 V …").
        if text == "PASS":
            bg = self._PASS_BG
        elif text.startswith("FAIL"):
            bg = self._FAIL_BG
        elif text == "N/A":
            bg = self._NA_BG  # not performed: grey, not red
        else:
            return

        option.backgroundBrush = QBrush(bg)
        option.palette.setColor(QPalette.ColorRole.Text, self._ON_COLOR_TEXT)
        option.palette.setColor(QPalette.ColorRole.HighlightedText, self._ON_COLOR_TEXT)
        option.palette.setColor(QPalette.ColorRole.Highlight, bg)
        option.font.setBold(True)
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter
