"""Background worker that renders/archives a PDF report off the GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from logic.report_generator import ReportGenerator, XmlCopyError


class ReportWorker(QThread):
    archived = Signal(str)
    failed = Signal(str)
    #: XML export failed while the PDF still archived successfully. Separate
    #: from `failed` so the operator is told the CAMSTAR file is missing
    #: without being led to think the run lost its PDF record too.
    xml_failed = Signal(str)
    #: The XML was written to the unit folder but not copied to the shared XML
    #: folder. Separate from `xml_failed`: here the CAMSTAR file does exist.
    xml_copy_failed = Signal(str)
    #: The chosen reports folder was unreachable; the report was saved locally.
    saved_locally = Signal(str)

    def __init__(self, meta: dict, rows: list[dict], role: str, parent=None) -> None:
        super().__init__(parent)
        self._meta, self._rows, self._role = dict(meta), list(rows), role

    def run(self) -> None:
        # The PDF and the XML are attempted independently: a PDF failure (a bad
        # logo, a layout error) used to skip the XML, so the unit lost its
        # CAMSTAR record too.
        try:
            gen = ReportGenerator()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        pdf_path = None
        pdf_error = None
        try:
            pdf_path = gen.generate_pdf_auto_archive(self._meta, self._rows, self._role)
        except Exception as exc:
            pdf_error = exc
        try:
            gen.generate_xml_auto_archive(self._meta, self._rows, self._role)
        except XmlCopyError as exc:
            self.xml_copy_failed.emit(str(exc))
        except Exception as exc:
            # Not silent: without the CAMSTAR file the run cannot be ingested
            # by MES, and swallowing this made a missing export look fine.
            self.xml_failed.emit(str(exc))
        if gen.fallback_notes:
            self.saved_locally.emit("\n\n".join(gen.fallback_notes))
        if pdf_error is not None:
            self.failed.emit(str(pdf_error))
        else:
            self.archived.emit(str(pdf_path))
