"""Background worker that renders/archives a PDF report off the GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from logic.report_generator import ReportGenerator


class ReportWorker(QThread):
    archived = Signal(str)
    failed = Signal(str)

    def __init__(self, meta: dict, rows: list[dict], role: str, parent=None) -> None:
        super().__init__(parent)
        self._meta, self._rows, self._role = dict(meta), list(rows), role

    def run(self) -> None:
        try:
            gen = ReportGenerator()
            path = gen.generate_pdf_auto_archive(self._meta, self._rows, self._role)
            try:
                gen.generate_xml_auto_archive(self._meta, self._rows, self._role)
            except Exception:
                pass  # XML failure must not prevent the PDF from being archived
            self.archived.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc))
