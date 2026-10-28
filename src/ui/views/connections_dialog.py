"""Admin: configure the station — hardware backend, load address, instrument ports."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import HARDWARE_BACKEND, INSTRUMENTS, PRODIGIT_VISA_RESOURCE
from drivers.factory import BACKEND_SETTING_KEY, LOAD_RESOURCE_KEY, RESULTS_DIR_KEY, XML_DIR_KEY
from logic import report_paths
from logic.database_manager import DatabaseManager

#: Label shown to the admin -> value stored. An empty stored value means "no
#: override, follow `.env`" -- both resolvers in `drivers.factory` fall through on
#: it -- which is what lets an admin undo a choice instead of shadowing the file
#: for good. The other accepted aliases ("simulator"/"demo", "visa"/"usb") are
#: not offered: they behave identically to the two below and would only confuse.
_USE_ENV = ""
_BACKEND_CHOICES: tuple[tuple[str, str], ...] = (
    ("Simulator (no hardware)", "mock"),
    ("Prodigit 3300G / 3316G (real hardware)", "prodigit"),
)


class ConnectionsDialog(QDialog):
    def __init__(self, username: str, employee_id: str, parent=None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._username = username
        self._employee_id = employee_id

        self.setWindowTitle("Connections")
        self.resize(560, 200 + len(INSTRUMENTS) * 36)

        root = QVBoxLayout(self)

        # --- Hardware backend -------------------------------------------------
        # `drivers.factory` resolves the backend once per process and caches it,
        # so saving here genuinely takes effect only on the next launch -- which
        # is what the note below promises. That is not merely cosmetic: the 3300G
        # accepts one TCP connection, and the exit `loadoff` safety path builds a driver of its own. A mid-session
        # change would let that path command a simulator and report "load off"
        # while a real load stayed energized with a UUT attached.
        root.addWidget(QLabel("<b>Hardware</b>"))
        backend_form = QFormLayout()
        backend_form.setContentsMargins(0, 4, 0, 4)
        backend_form.setHorizontalSpacing(12)

        # Both controls are seeded from the STORED override, not from the
        # effective value. Seeding from the effective value looked friendlier but
        # meant merely opening this dialog and pressing Save copied the `.env`
        # settings into the database, shadowing the file permanently with no way
        # back. The `.env` value is shown as the placeholder / "keep" label
        # instead, so the admin can still see what is in force.
        stored_settings = self._db.list_instrument_connections()

        self._combo_backend = QComboBox()
        self._combo_backend.setObjectName("conn_backend")
        self._combo_backend.addItem(
            f"Use .env default  ({HARDWARE_BACKEND})", _USE_ENV
        )
        for label, value in _BACKEND_CHOICES:
            self._combo_backend.addItem(label, value)
        self._initial_backend = stored_settings.get(BACKEND_SETTING_KEY, "").strip().lower()
        idx = self._combo_backend.findData(self._initial_backend)
        # An unrecognised stored value (hand-edited row, older build) is not
        # silently normalised to a real choice -- it resolves to `.env` anyway,
        # so show that.
        self._combo_backend.setCurrentIndex(idx if idx >= 0 else 0)
        backend_form.addRow("Backend", self._combo_backend)

        self._edit_load = QLineEdit()
        self._edit_load.setObjectName("conn_load_resource")
        env_addr = PRODIGIT_VISA_RESOURCE.strip()
        self._edit_load.setPlaceholderText(
            f"blank = use .env  ({env_addr or 'auto-discover USB'})"
        )
        self._initial_load = stored_settings.get(LOAD_RESOURCE_KEY, "").strip()
        self._edit_load.setText(self._initial_load)
        backend_form.addRow("Electronic load address", self._edit_load)
        root.addLayout(backend_form)

        note = QLabel(
            "Changing the backend takes effect the next time the application "
            "starts. Leave a field empty to fall back to the .env default shown "
            "in grey - that is also how you undo a change made here."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        # --- Report folders ------------------------------------------------------
        # Applied to the next report (no restart). A network share works; a UNC
        # path is preferred, as a mapped drive letter may not exist for every
        # session. If the folder is unreachable when a report is written, the
        # report is saved locally and the operator is told (ReportGenerator).
        root.addWidget(QLabel("<b>Reports</b>"))
        reports_form = QFormLayout()
        reports_form.setContentsMargins(0, 4, 0, 4)
        reports_form.setHorizontalSpacing(12)
        self._initial_results = stored_settings.get(RESULTS_DIR_KEY, "").strip()
        self._initial_xml = stored_settings.get(XML_DIR_KEY, "").strip()
        self._edit_results = QLineEdit(self._initial_results)
        self._edit_results.setObjectName("conn_results_dir")
        self._edit_results.setPlaceholderText(f"blank = {report_paths.local_results_dir()}")
        self._edit_xml = QLineEdit(self._initial_xml)
        self._edit_xml.setObjectName("conn_xml_dir")
        self._edit_xml.setPlaceholderText("blank = <reports folder>\\XML  (or XML_EXPORT_DIR in .env)")
        reports_form.addRow("Reports folder", self._folder_row(self._edit_results))
        reports_form.addRow("XML folder (copy of every XML)", self._folder_row(self._edit_xml))
        root.addLayout(reports_form)
        reports_note = QLabel(
            "Takes effect on the next report. For a shared network folder prefer a "
            "path like \\\\server\\share\\reports. If it is unreachable when a "
            "report is written, the report is saved locally and you are told."
        )
        reports_note.setWordWrap(True)
        root.addWidget(reports_note)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line2)

        # --- Per-instrument connection strings --------------------------------
        root.addWidget(QLabel("<b>Other instruments</b>"))
        root.addWidget(
            QLabel("Enter a COM port (e.g. COM9) or VISA string for each instrument.")
        )

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 8)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(12)

        stored = self._db.list_instrument_connections()
        self._fields: dict[str, QLineEdit] = {}
        for name in INSTRUMENTS:
            edit = QLineEdit()
            edit.setObjectName(f"conn_field_{name.replace(' ', '_')}")
            edit.setPlaceholderText("COM9  or  USB0::0x0957::…::INSTR")
            edit.setText(stored.get(name, ""))
            form.addRow(name, edit)
            self._fields[name] = edit

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _folder_row(self, edit: QLineEdit) -> QWidget:
        """A path field with a Browse... button."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._browse(edit))
        layout.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or str(report_paths.local_results_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if chosen:
            edit.setText(str(Path(chosen)))

    @staticmethod
    def _folder_problem(text: str) -> str:
        """"" if `text` is empty or a folder this station can create and write to."""
        if not text:
            return ""
        folder = Path(text)
        probe = folder / f".dfx_write_test_{uuid.uuid4().hex}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return ""
        except OSError as exc:
            return f"{folder}: {exc}"

    def _save(self) -> None:
        """Persist only the rows the admin actually changed.

        Every write used to be unconditional, with the `if` deciding only what
        went in the audit log. Combined with fields seeded from the effective
        value, that meant opening this dialog and pressing Save -- to edit some
        unrelated COM port, say -- silently copied the `.env` backend and load
        address into the database, where they then shadowed the file forever with
        no way back, and reported a restart-required for a change nobody made.
        """
        results_value = self._edit_results.text().strip()
        xml_value = self._edit_xml.text().strip()
        problems = [p for p in (self._folder_problem(results_value), self._folder_problem(xml_value)) if p]
        if problems:
            QMessageBox.warning(
                self,
                "Folder not usable",
                "This station cannot write to:\n\n" + "\n".join(problems)
                + "\n\nCheck the path and the access rights, or leave it blank.",
            )
            return  # keep the dialog open

        changed: list[str] = []
        stored = self._db.list_instrument_connections()

        def _write(key: str, value: str, label: str, previous: str) -> None:
            """Upsert `key` only if `value` differs from what was loaded."""
            if value == previous:
                return
            changed.append(label)
            # Empty is stored, not deleted: both resolvers in `drivers.factory`
            # treat an empty override as absent, so this is how "go back to the
            # .env default" is expressed without a delete API.
            self._db.upsert_instrument_connection(key, value, self._username)

        backend_value = str(self._combo_backend.currentData() or "")
        _write(
            BACKEND_SETTING_KEY,
            backend_value,
            f"backend={backend_value or 'use .env'}",
            self._initial_backend,
        )

        load_value = self._edit_load.text().strip()
        _write(LOAD_RESOURCE_KEY, load_value, "load address", self._initial_load)
        _write(RESULTS_DIR_KEY, results_value, f"reports folder={results_value or 'default'}", self._initial_results)
        _write(XML_DIR_KEY, xml_value, f"XML folder={xml_value or 'default'}", self._initial_xml)

        for name, edit in self._fields.items():
            _write(name, edit.text().strip(), name, stored.get(name, ""))

        if changed:
            try:
                self._db.log_audit_action(
                    "Updated instrument connections",
                    username=self._username,
                    employee_id=self._employee_id,
                    details=f"changed={changed!r}",
                )
            except Exception:
                pass

        backend_switched = any(c.startswith("backend=") for c in changed)
        if not changed:
            message = "No changes to save."
        else:
            message = "Instrument connections saved."
            if backend_switched:
                message += (
                    "\n\nRestart the application for the backend change to apply."
                )
        QMessageBox.information(self, "Connections", message)
        self.accept()
