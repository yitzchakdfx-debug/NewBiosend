"""Top-bar labels that never decide the window's width.

A plain QLabel's minimum width is its whole text (or pixmap), so a status text
that got longer during a run ("Load not answering - switch the 3300G off and
on") widened the main window past the right edge of the screen (2026-11-11).
These give way instead: the text is shortened with "..." (full text in the
tooltip) and a logo is scaled down, keeping its proportions.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "..." when space is short."""

    def __init__(
        self, text: str = "", parent: QWidget | None = None, *, min_chars: int = 6, tooltip: bool = True
    ) -> None:
        super().__init__(text, parent)
        self._min_chars = min_chars
        self._tooltip = tooltip
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        width = self.fontMetrics().horizontalAdvance("M" * self._min_chars)
        return QSize(min(hint.width(), width), hint.height())

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setText(text)
        if self._tooltip:
            # The full text stays readable on hover when it is cut.
            self.setToolTip(text)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), text)


class ScaledLogo(QLabel):
    """A logo that shrinks with the space it gets, never stretched or cropped."""

    def __init__(self, pixmap: QPixmap, max_size: QSize, min_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = pixmap
        self._max = max_size
        self._min_width = min_width
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(max_size.height())
        self._rescale(max_size.width())

    def sizeHint(self) -> QSize:
        return self._max

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min_width, self._max.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._rescale(min(event.size().width(), self._max.width()))
        super().resizeEvent(event)

    def _rescale(self, width: int) -> None:
        if self._source.isNull() or width <= 0:
            return
        self.setPixmap(
            self._source.scaled(
                width, self._max.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
