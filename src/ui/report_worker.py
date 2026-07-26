"""Background worker that renders/archives a PDF report off the GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from logic.report_generator import ReportGenerator


class ReportWorker(QThread):
    archived = Signal(str)
    failed = Signal(str)
    #: XML export failed while the PDF still archived successfully. Separate
    #: from `failed` so the operator is told the CAMSTAR file is missing
    #: without being led to think the run lost its PDF record too.
    xml_failed = Signal(str)

    def __init__(self, meta: dict, rows: list[dict], role: str, parent=None) -> None:
        super().__init__(parent)
        self._meta, self._rows, self._role = dict(meta), list(rows), role

    def run(self) -> None:
        try:
            gen = ReportGenerator()
            path = gen.generate_pdf_auto_archive(self._meta, self._rows, self._role)
            try:
                gen.generate_xml_auto_archive(self._meta, self._rows, self._role)
            except Exception as exc:
                # An XML failure must not prevent the PDF from being archived,
                # but it must not be silent either: without the CAMSTAR file the
                # run cannot be ingested by MES, and swallowing this made a
                # missing export look like a successful one.
                self.xml_failed.emit(str(exc))
            self.archived.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc))
