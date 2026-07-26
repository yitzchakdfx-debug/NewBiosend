"""Reusable UI helper utilities shared across dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLineEdit


def _create_emoji_icon(emoji: str) -> QIcon:
    """Create a QIcon from a text emoji without needing image files."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    font = painter.font()
    font.setPointSize(10)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
    painter.end()
    return QIcon(pixmap)


def attach_password_visibility_toggle(edit: QLineEdit) -> None:
    """Add a trailing eye/lock action that toggles password echo mode."""
    icon_show = _create_emoji_icon("\U0001f441")
    icon_hide = _create_emoji_icon("\U0001f512")

    act = QAction(icon_show, "", edit)
    act.setToolTip("Show or hide password")

    def on_trigger(_checked: bool = False) -> None:
        if edit.echoMode() == QLineEdit.EchoMode.Password:
            edit.setEchoMode(QLineEdit.EchoMode.Normal)
            act.setIcon(icon_hide)
        else:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            act.setIcon(icon_show)

    act.triggered.connect(on_trigger)
    edit.addAction(act, QLineEdit.ActionPosition.TrailingPosition)
