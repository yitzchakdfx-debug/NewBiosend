"""List item delegate that sizes word-wrapped, checkable rows correctly."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem


class WrapItemDelegate(QStyledItemDelegate):
    """Row height from the text wrapped at the width it is actually drawn in.

    Qt's default size hint for a word-wrapped item ignores the space the check
    indicator takes, so a line that only just fits is measured as one line,
    drawn as two, and the second gets elided ("Positive …"). This measures
    against the viewport width minus the indicator and margins.
    """

    _PADDING = 6

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        width = view.viewport().width() if view is not None else opt.rect.width()
        style = view.style() if view is not None else None
        if style is not None:
            indicator = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, opt, view)
            spacing = style.pixelMetric(QStyle.PixelMetric.PM_CheckBoxLabelSpacing, opt, view)
        else:
            indicator, spacing = 16, 6
        text_width = max(40, width - indicator - spacing - 4 * self._PADDING)
        rect = opt.fontMetrics.boundingRect(
            QRect(0, 0, text_width, 100000),
            int(Qt.TextFlag.TextWordWrap),
            opt.text,
        )
        return QSize(width, rect.height() + 2 * self._PADDING)
