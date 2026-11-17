"""Help > About: the supplier's logo, the product badge, name and version."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from version import APP_TITLE

#: Supplier logo, shown first. Either format; the SVG wins when both exist.
_SUPPLIER_LOGOS = ("ShanyTechLogo.svg", "ShanyTechLogo.png")


def _logo_label(path: Path, size: QSize) -> QLabel | None:
    if not path.is_file():
        return None
    if path.suffix.lower() == ".svg":
        pixmap = QIcon(str(path)).pixmap(size)
    else:
        pixmap = QPixmap(str(path)).scaled(
            size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    if pixmap.isNull():
        return None
    label = QLabel()
    label.setPixmap(pixmap)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class AboutDialog(QDialog):
    def __init__(self, icons_dir: Path, version: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setFixedWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(12)

        supplier = next(
            (lbl for name in _SUPPLIER_LOGOS if (lbl := _logo_label(icons_dir / name, QSize(260, 90)))),
            None,
        )
        if supplier is not None:
            layout.addWidget(supplier)
        badge = _logo_label(icons_dir / "about_badge.png", QSize(56, 56))
        if badge is not None:
            layout.addWidget(badge)

        # Explicit line breaks, no word wrap: a wrapped label in this fixed-width
        # dialog was given one line too few at 150 % display scaling, cutting
        # off "(CR mode)." (seen 2026-11-17). Unwrapped, its height is exact.
        text = QLabel(
            f"<b>{APP_TITLE}</b><br>Version {version}<br><br>"
            "Electronic load: Prodigit 3300G / 3316G<br>(CR mode)."
        )
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
