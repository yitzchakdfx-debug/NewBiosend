"""Main ATE window: layout, styling, and wiring to the script-driven runner."""

from __future__ import annotations

import html
import math
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import threading

import shiboken6

from config import LOAD_SERIALS, SHOW_NIGHT_MODE, SHOW_SEARCH_BAR
from drivers.base_driver import BaseDriver, HardwareError
from drivers.factory import create_driver, is_simulated, resolve_station_config, resolved_visa_resource
from logic.database_manager import DatabaseManager
from logic.report_templates import read_pdf_template
from logic.test_groups import TestGroup, always_step_names, build_test_groups
from ui.report_worker import ReportWorker
from logic.models import ScriptDocument, TestResultPayload
from logic.report_generator import ReportGenerator, sanitize_path_segment
from logic.roles import Capability, can
from logic.script_manager import ScriptManager, ScriptParseError
from logic.secure_logger import get_secure_logger
from logic.test_engine import POLARITY_SETTLE_S, TestRunnerThread
from paths import resource_path, user_tmp_path
from ui.views.audit_viewer_dialog import AuditViewerDialog
from ui.views.connections_dialog import ConnectionsDialog
from ui.views.led_check_dialog import LedCheckDialog
from ui.views.limits_editor_dialog import LimitsEditorDialog
from ui.views.batch_pre_test_dialog import BatchPreTestDialog
from ui.views.report_templates_dialog import ReportTemplatesDialog
from ui.views.select_test_dialog import SelectTestDialog

from ui.views.test_result_dialog import TestResultDialog
from ui.views.user_management_dialog import UserManagementDialog
from ui.views.version_manager_dialog import VersionManagerDialog
from ui.widgets.control_panel import ControlPanelWidget, UnitProgressRow
from ui.widgets.link_led import LinkLed, PingProbe, host_from_visa
from logic import report_paths
from ui.widgets.result_row_delegate import ResultRowDelegate
from ui.widgets.wrap_item_delegate import WrapItemDelegate
from version import __version__


# The spec Rev.1.1 sequence (§1.1.6-§1.1.7): CR mode, 5.76 ohm / 2.0 ohm, load
# off before the polarity check, monitored delays, and the Report/Quantity
# directives the CAMSTAR XML is built from. Previously this pointed at
# `sequence.tst`, a 5 V demo unrelated to the spec, so an operator who did not
# load a script explicitly ran the wrong test.
_DEFAULT_SCRIPT_NAME = "biosense_test.tst"
_NA = "-"


class _ResizeRelay(QObject):
    """Calls `callback` whenever the watched widget is resized."""

    def __init__(self, callback, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            # Deferred: the header re-lays out its stretching section after this
            # event, so measuring now would still see the old column width.
            QTimer.singleShot(0, self._callback)
        return False


def _format_measurement(value: float) -> str:
    """Human-readable numeric string without excessive trailing zeros."""
    if math.isnan(value):
        return _NA
    return f"{value:g}"


class MainWindow(QMainWindow):
    _ICONS_DIR = resource_path("ui", "assets", "icons")
    #: Live-table rows: `live_reading` key -> row label. One column per UUT.
    _LIVE_QUANTITIES = {"V": "Voltage [V]", "A": "Current [A]", "W": "Power [W]"}

    sequence_finished = Signal(bool)

    def __init__(self, user_info: dict) -> None:
        super().__init__()
        self._user_info = user_info
        self.setWindowTitle("DFX Tester - Component ATE")
        icon_path = self._ICONS_DIR / "DFXAppIcon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1200, 800)
        self._setup_menu_bar()
        self.test_thread: TestRunnerThread | None = None
        self._parallel_threads: list[TestRunnerThread] = []
        self._parallel_finished_count: int = 0
        self._parallel_shared_driver: BaseDriver | None = None
        self._parallel_section_order: list[str] = []
        self._parallel_section_cursors: dict[str, int] = {}
        self.is_dark_mode: bool = False
        self._script_manager = ScriptManager()
        self._active_script_path: Path = (
            self._script_manager.scripts_dir / _DEFAULT_SCRIPT_NAME
        )
        # Parsed form of the active script, refreshed by
        # `_reload_script_into_list`. Lets the single-step maintenance run
        # inspect Group/Hidden/Always without re-parsing the file.
        self._active_document: ScriptDocument | None = None
        self._trace_history: list[dict] = []
        self._part_number_user_edited = False
        self._catalog_test_name: str = Path(_DEFAULT_SCRIPT_NAME).stem
        self._catalog_uut_type: str = ""
        self._operator_temp_script: Path | None = None
        self._pre_test_locked: bool = False
        try:
            self._secure = get_secure_logger()
        except Exception:
            self._secure = None
        self._last_run_meta: dict | None = None
        self._last_run_rows: list[dict] = []
        # One entry per in-flight report. A list rather than a single slot so a
        # parallel run's workers cannot overwrite each other's reference.
        self._report_workers: list[ReportWorker] = []
        # Set once the window has agreed to close. After that no result dialog
        # may open: see closeEvent for why one would leave the app running.
        self._closing = False
        # Pin the station's hardware config now, while the window is opening and
        # before the admin can reach the Connections dialog. The factory caches on
        # first read, so without this the first read could happen *after* a
        # mid-session save — and the whole point of caching is that the backend and
        # load address the shutdown `loadoff` path sees are the ones the run used.
        resolve_station_config()
        self._setup_ui()
        self._apply_theme_file()
        self._update_icons(self.is_dark_mode)
        self._apply_role_permissions()
        # Every role starts on the newest catalog version; Select Product opens
        # by itself only when there is none to start on.
        started_on_latest = self._load_latest_version()
        if started_on_latest:
            pass
        elif not self._can(Capability.SELECT_STEPS):
            # Roles that cannot pick individual steps start with no script at
            # all and must choose a product first.
            self._active_script_path = Path()
            self.label_active_script.setText("No test - use Select Product.")
            self.test_list.clear()
            self._catalog_test_name = ""
            self._catalog_uut_type = ""
        else:
            self._reload_script_into_list()
        self._fit_initial_size_to_screen()
        if not started_on_latest:
            QTimer.singleShot(0, self.load_script)

    def _make_ribbon_button(self, object_name: str, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName(object_name)
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        btn.setFixedSize(80, 70)
        btn.setIconSize(QSize(28, 28))
        btn.setAutoRaise(True)
        return btn

    def _icon(self, name: str, fallback: QStyle.StandardPixmap) -> QIcon:
        for ext in (".svg", ".png"):
            candidate = self._ICONS_DIR / f"{name}{ext}"
            if candidate.is_file():
                return QIcon(str(candidate))
        return self.style().standardIcon(fallback)

    def _update_icons(self, is_dark: bool) -> None:
        """Load theme-aware and ribbon icons; falls back to standard icons if assets missing."""
        SP = QStyle.StandardPixmap
        if SHOW_NIGHT_MODE:
            theme_name = "sun" if is_dark else "moon"
            theme_fallback = SP.SP_DialogYesButton if is_dark else SP.SP_DialogNoButton
            self.btn_toggle_theme.setIcon(self._icon(theme_name, theme_fallback))
        self.btn_load_script.setIcon(self._icon("folder", SP.SP_DirOpenIcon))
        self.btn_limits.setIcon(self._icon("list", SP.SP_FileDialogListView))
        self.btn_run_step.setIcon(self._icon("play", SP.SP_MediaPlay))
        self.btn_versions.setIcon(self._icon("list", SP.SP_FileDialogDetailedView))
        self.btn_audit.setIcon(self._icon("list", SP.SP_FileDialogListView))
        self.btn_logout.setIcon(self._icon("logout", SP.SP_ArrowBack))
        self.btn_exit.setIcon(self._icon("power", SP.SP_DialogCloseButton))

    def show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About DFX Tester",
            "<b>DFX Tester - Component ATE</b><br>"
            f"Version {__version__}<br><br>"
            "In development.",
        )

    def _setup_menu_bar(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        file_menu.addAction("&Select Product...", self.load_script)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        test_menu = menu.addMenu("&Test")
        test_menu.addAction("&Run", self._on_start_clicked)
        test_menu.addAction("&Stop", self.stop_tests)
        test_menu.addSeparator()
        test_menu.addAction("Re&set Counters", self.reset_counters)

        results_menu = menu.addMenu("&Results")
        results_menu.addAction("Open Results &Folder", self.open_results_folder)
        results_menu.addSeparator()
        self._act_export_csv = results_menu.addAction(
            "Export to &CSV...", self.export_results_csv
        )
        self._act_export_pdf = results_menu.addAction(
            "Export to &PDF...", self.export_results_pdf
        )

        if self._can(Capability.MANAGE_USERS) or self._can(Capability.VIEW_AUDIT_LOG):
            users_menu = menu.addMenu("&Users")
            if self._can(Capability.VIEW_AUDIT_LOG):
                users_menu.addAction("&Logs...", self.open_audit_viewer)
            if self._can(Capability.MANAGE_USERS):
                users_menu.addAction("&User Management...", self.open_user_management)

        if self._can(Capability.EDIT_CONNECTIONS):
            conn_menu = menu.addMenu("C&onnections")
            conn_menu.addAction("&Instrument Setup...", self.open_connections_dialog)

        if self._can(Capability.EDIT_TEMPLATES):
            results_menu.addSeparator()
            results_menu.addAction(
                "Report &Templates...", self.open_report_templates_dialog
            )

        help_menu = menu.addMenu("&Help")
        help_menu.addAction("&About", self.show_about_dialog)

    def _current_role(self) -> str:
        return str(self._user_info.get("role", "")).strip().title()

    def _can(self, capability: Capability) -> bool:
        """Whether the logged-in user may perform `capability`.

        Gate on this rather than on a role name: adding a role should mean
        editing the matrix in `logic.roles`, not auditing every call site.
        """
        return can(self._current_role(), capability)

    def _apply_role_permissions(self) -> None:
        show_detail = self._can(Capability.VIEW_MEASURED_DETAIL)
        for column in (1, 2, 3):  # Min / Max / Value
            self.results_table.setColumnHidden(column, not show_detail)

        self.btn_versions.setVisible(self._can(Capability.MANAGE_VERSIONS))
        self.btn_audit.setVisible(self._can(Capability.VIEW_AUDIT_LOG))
        self.btn_limits.setVisible(self._can(Capability.EDIT_LIMITS))
        self.btn_run_step.setVisible(self._can(Capability.RUN_SINGLE_STEP))

        self.btn_load_script.setText("Product")

        # Live readings are for every role; the message log stays diagnostic.
        self.bottom_tabs.setTabVisible(
            self.bottom_tabs.indexOf(self.trace_container),
            self._can(Capability.VIEW_TRACE_LOG),
        )
        self.control_panel.chk_save_log.setVisible(self._can(Capability.ARCHIVE_RUN))

        list_actions_visible = self._can(Capability.SELECT_STEPS)
        self.btn_select_all_tests.setVisible(list_actions_visible)
        self.btn_clear_all_tests.setVisible(list_actions_visible)

        may_report = self._can(Capability.GENERATE_REPORTS)
        for action in (self._act_export_csv, self._act_export_pdf):
            action.setEnabled(may_report)
        if not may_report:
            tip = "Reports are not generated in Maintenance mode."
            self._act_export_csv.setToolTip(tip)
            self._act_export_pdf.setToolTip(tip)

    def open_version_manager(self) -> None:
        if not self._can(Capability.MANAGE_VERSIONS):
            return
        VersionManagerDialog(
            current_username=str(self._user_info.get("username", "")),
            script_manager=self._script_manager,
            employee_id=str(self._user_info.get("employee_id", "")),
            parent=self,
        ).exec()

    def open_audit_viewer(self) -> None:
        if not self._can(Capability.VIEW_AUDIT_LOG):
            QMessageBox.warning(self, "Not allowed", "Only Admin users can open the audit log.")
            return
        AuditViewerDialog(parent=self).exec()

    def open_user_management(self) -> None:
        if not self._can(Capability.MANAGE_USERS):
            QMessageBox.warning(self, "Not allowed", "Only Admin users can manage users.")
            return
        dialog = UserManagementDialog(
            current_username=str(self._user_info.get("username", "")),
            parent=self,
        )
        dialog.exec()

    def open_connections_dialog(self) -> None:
        if not self._can(Capability.EDIT_CONNECTIONS):
            return
        ConnectionsDialog(
            username=str(self._user_info.get("username", "")),
            employee_id=str(self._user_info.get("employee_id", "")),
            parent=self,
        ).exec()

    def open_report_templates_dialog(self) -> None:
        if not self._can(Capability.EDIT_TEMPLATES):
            return
        ReportTemplatesDialog(
            username=str(self._user_info.get("username", "")),
            employee_id=str(self._user_info.get("employee_id", "")),
            parent=self,
        ).exec()

    def open_limits_editor(self) -> None:
        if not self._can(Capability.EDIT_LIMITS):
            return
        if self._run_active():
            # Saving switches the active test; that must not happen under a run.
            QMessageBox.information(self, "Limits", "Stop the test before editing limits.")
            return
        if not self._active_script_path.is_file():
            QMessageBox.information(self, "Limits", "Load a test first.")
            return
        try:
            document = self._script_manager.load_document(self._active_script_path)
        except ScriptParseError as exc:
            QMessageBox.warning(
                self,
                "Script parse error",
                f"Could not parse {self._active_script_path.name}:\n"
                f"line {exc.line_no}: {exc.msg}",
            )
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Limits", f"Could not load script:\n{exc}")
            return

        dialog = LimitsEditorDialog(
            document=document,
            script_manager=self._script_manager,
            catalog_test_name=self._catalog_test_name or self._active_script_path.stem,
            catalog_uut_type=self._catalog_uut_type,
            catalog_version=getattr(self, "_catalog_version", ""),
            username=str(self._user_info.get("username", "")),
            employee_id=str(self._user_info.get("employee_id", "")),
            db=DatabaseManager(),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Rejected:
            return
        saved = dialog.saved_version()
        if saved is None:
            return
        version, content = saved
        # The new version becomes the active test at once, so the next run and
        # its report are that version (not the one the editor was opened on).
        self._activate_catalog_content(
            self._catalog_test_name or self._active_script_path.stem,
            version,
            self._catalog_uut_type,
            content,
        )

    def reset_counters(self) -> None:
        self.control_panel.label_pass.setText("PASS: 0")
        self.control_panel.label_fail.setText("FAIL: 0")
        self.control_panel.progress_total.setValue(0)
        self.control_panel.reset_unit_progress()

    def _ensure_results_dir(self) -> Path:
        """The reports folder the Admin chose (Connections), else data/results."""
        folder = report_paths.results_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = report_paths.local_results_dir()  # chosen share unreachable
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _default_export_basename(self) -> str:
        part = sanitize_path_segment(self.control_panel.edit_part_number.text())
        serial = sanitize_path_segment(self.control_panel.edit_serial_number.text())
        if part != "unknown" and serial != "unknown":
            return f"{part}_{serial}"
        return "report"

    def _export_metadata(self, title: str) -> dict[str, str]:
        return {
            "title": title,
            "part_number": self.control_panel.edit_part_number.text().strip(),
            "serial_number": self.control_panel.edit_serial_number.text().strip(),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_name": str(self._user_info.get("name", "")),
            "user_role": str(self._user_info.get("role", "")),
        }

    def _format_text_header(self, meta: dict[str, str]) -> str:
        return (
            f"Title: {meta['title']}\n"
            f"Part Number: {meta['part_number']}\n"
            f"Serial Number: {meta['serial_number']}\n"
            f"Date: {meta['date']}\n"
            f"User: {meta['user_name']} ({meta['user_role']})\n"
            "-------------------\n"
        )

    def open_results_folder(self) -> None:
        folder = self._ensure_results_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _require_report_capability(self) -> bool:
        """Guard manual exports; shows why when the role may not produce reports."""
        if self._can(Capability.GENERATE_REPORTS):
            return True
        QMessageBox.warning(
            self,
            "Not allowed",
            "Test reports are not generated in Maintenance mode.",
        )
        return False

    def export_results_csv(self) -> None:
        if not self._require_report_capability():
            return
        if self._last_run_meta is None:
            QMessageBox.information(
                self,
                "Export",
                "No completed test results are cached. Run a test sequence first.",
            )
            return
        
        rg = ReportGenerator()
        archive_dir, stem = rg._resolved_archive_paths(self._last_run_meta)
        archive_dir.mkdir(parents=True, exist_ok=True)  # may not exist when "Save as log" is off
        role_key = self._current_role().replace(" ", "_")
        suggested = archive_dir / f"{stem}_{role_key}.csv"
        
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export results as CSV",
            str(suggested),
            "CSV Files (*.csv)",
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        try:
            rg.generate_csv_file(
                path,
                self._last_run_meta,
                self._last_run_rows,
                self._current_role(),
            )
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def export_results_pdf(self) -> None:
        if not self._require_report_capability():
            return
        if self._last_run_meta is None:
            QMessageBox.information(
                self,
                "Export",
                "No completed test results are cached. Run a test sequence first.",
            )
            return
        
        rg = ReportGenerator()
        archive_dir, stem = rg._resolved_archive_paths(self._last_run_meta)
        archive_dir.mkdir(parents=True, exist_ok=True)  # may not exist when "Save as log" is off
        role_key = self._current_role().replace(" ", "_")
        suggested = archive_dir / f"{stem}_{role_key}.pdf"
        
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export results as PDF",
            str(suggested),
            "PDF Files (*.pdf)",
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        try:
            rg.generate_pdf_file(
                path,
                self._last_run_meta,
                self._last_run_rows,
                self._current_role(),
            )
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _make_log_button(
        self,
        name: str,
        tip: str,
        fallback: QStyle.StandardPixmap,
        icon_name: str,
    ) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName(name)
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        btn.setFixedSize(28, 28)
        btn.setIconSize(QSize(18, 18))
        btn.setIcon(self._icon(icon_name, fallback))
        return btn

    def save_log_to_file(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save trace log",
            str(self._ensure_results_dir() / f"{self._default_export_basename()}.txt"),
            "Text Files (*.txt)",
        )
        if not selected:
            return
        meta = self._export_metadata("DFX Tester - Trace Log")
        payload = self._format_text_header(meta) + "\n" + self.trace_log.toPlainText()
        Path(selected).write_text(payload, encoding="utf-8")

    def copy_log_to_clipboard(self) -> None:
        self.trace_log.selectAll()
        self.trace_log.copy()
        cursor = self.trace_log.textCursor()
        cursor.clearSelection()
        self.trace_log.setTextCursor(cursor)

    def _setup_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        ribbon = QHBoxLayout()
        if SHOW_NIGHT_MODE:
            self.btn_toggle_theme = self._make_ribbon_button("btn_toggle_theme", "Theme")
            self.btn_toggle_theme.clicked.connect(self.toggle_theme)
            ribbon.addWidget(self.btn_toggle_theme)

        self.btn_load_script = self._make_ribbon_button(
            "btn_load_script", "Select Product"
        )
        self.btn_load_script.clicked.connect(self.load_script)
        ribbon.addWidget(self.btn_load_script)

        self.btn_limits = self._make_ribbon_button("btn_limits", "Limits")
        self.btn_limits.clicked.connect(self.open_limits_editor)
        ribbon.addWidget(self.btn_limits)

        self.btn_run_step = self._make_ribbon_button("btn_run_step", "Run Step")
        self.btn_run_step.setToolTip(
            "Run the selected step on its own, outside the production sequence.\n"
            "No report is generated and the run is not archived."
        )
        self.btn_run_step.clicked.connect(self._on_run_single_step)
        ribbon.addWidget(self.btn_run_step)

        self.btn_versions = self._make_ribbon_button("btn_versions", "Versions")
        self.btn_versions.clicked.connect(self.open_version_manager)
        ribbon.addWidget(self.btn_versions)

        self.btn_audit = self._make_ribbon_button("btn_audit", "Logs")
        self.btn_audit.clicked.connect(self.open_audit_viewer)
        ribbon.addWidget(self.btn_audit)

        self.label_active_script = QLabel("")
        ribbon.addSpacing(12)
        ribbon.addWidget(self.label_active_script)

        # Load-link indicator. Between runs it is fed by an ICMP ping (never a
        # TCP connection: the 3300G takes only one, and the scan or the next run
        # needs it); during a run by the driver's own link events.
        self.link_led = LinkLed(self)
        ribbon.addSpacing(24)
        ribbon.addWidget(self.link_led, 0, Qt.AlignmentFlag.AlignVCenter)
        self._link_host = host_from_visa(resolved_visa_resource())
        #: A load-off could not be confirmed (link lost / failed). The next
        #: successful ping switches every channel off — see _on_ping_result.
        self._loads_unconfirmed = False
        self._ping = PingProbe(self)
        self._ping.result.connect(self._on_ping_result)
        self._link_timer = QTimer(self)
        self._link_timer.setInterval(5000)
        self._link_timer.timeout.connect(self._probe_link)
        self._link_timer.start()
        QTimer.singleShot(0, self._probe_link)

        ribbon.addStretch()

        self.lbl_brand_icon = QLabel()
        self.lbl_brand_icon.setObjectName("brand_icon")
        self.lbl_brand_icon.setFixedSize(220, 64)
        self.lbl_brand_icon.setScaledContents(False)
        self.lbl_brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_brand_icon.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        bird_path = self._ICONS_DIR / "DFXLogo.png"
        if bird_path.is_file():
            pix = QPixmap(str(bird_path)).scaled(
                self.lbl_brand_icon.width(),
                self.lbl_brand_icon.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_brand_icon.setPixmap(pix)
        self.lbl_brand_icon.setToolTip("DFX Tester")
        ribbon.addWidget(self.lbl_brand_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        ribbon.addSpacing(12)

        self.btn_logout = self._make_ribbon_button("btn_logout", "Log Out")
        self.btn_logout.clicked.connect(self.logout)
        ribbon.addWidget(self.btn_logout)

        self.btn_exit = self._make_ribbon_button("btn_exit", "Exit")
        self.btn_exit.clicked.connect(self.close)
        ribbon.addWidget(self.btn_exit)

        main_layout.addLayout(ribbon)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(8)

        self.control_panel = ControlPanelWidget(self._user_info)
        self.control_panel.setObjectName("control_panel")
        self.control_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding
        )
        self.control_panel.setMinimumWidth(280)
        self.control_panel.setMaximumWidth(340)

        self.test_list = QListWidget()
        self.test_list.setObjectName("test_list")
        self.test_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.test_list.setItemAlignment(Qt.AlignmentFlag.AlignTop)
        # Each row carries a detail line (load, time points) that can be wider
        # than the sidebar; wrap it instead of cutting it off.
        self.test_list.setWordWrap(True)
        self.test_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.test_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Re-measure rows when the sidebar is resized, with a delegate that
        # accounts for the check indicator (Qt's default cuts the last line).
        self.test_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.test_list.setItemDelegate(WrapItemDelegate(self.test_list))

        self.left_sidebar = QWidget()
        self.left_sidebar.setMinimumWidth(220)
        self.left_sidebar.setMaximumWidth(340)
        sidebar_layout = QVBoxLayout(self.left_sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(8)

        sidebar_layout.addWidget(self.control_panel.take_user_box())

        self.lbl_test_cases = QLabel("Test Cases")
        self.lbl_test_cases.setObjectName("lbl_test_cases")
        sidebar_layout.addWidget(self.lbl_test_cases)
        sidebar_layout.addWidget(self.test_list, stretch=1)

        test_list_actions = QHBoxLayout()
        test_list_actions.setSpacing(4)
        test_list_actions.setContentsMargins(0, 0, 0, 0)
        self.btn_select_all_tests = QPushButton("Select All")
        self.btn_select_all_tests.setObjectName("btn_select_all_tests")
        self.btn_select_all_tests.setMinimumHeight(26)
        self.btn_select_all_tests.clicked.connect(self._on_select_all_tests)
        self.btn_clear_all_tests = QPushButton("Clear All")
        self.btn_clear_all_tests.setObjectName("btn_clear_all_tests")
        self.btn_clear_all_tests.setMinimumHeight(26)
        self.btn_clear_all_tests.clicked.connect(self._on_clear_all_tests)
        test_list_actions.addWidget(self.btn_select_all_tests)
        test_list_actions.addWidget(self.btn_clear_all_tests)
        sidebar_layout.addLayout(test_list_actions)

        results_table_container = QWidget()
        center_layout = QVBoxLayout(results_table_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(6)

        if SHOW_SEARCH_BAR:
            self.edit_filter_results = QLineEdit()
            self.edit_filter_results.setObjectName("edit_filter_results")
            self.edit_filter_results.setClearButtonEnabled(True)
            self.edit_filter_results.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self.edit_filter_results.setPlaceholderText(
                "\U0001F50D Search results (Test Name, Status...)"
            )
            self.edit_filter_results.textChanged.connect(self._filter_table_results)
            center_layout.addWidget(self.edit_filter_results)
        else:
            self.edit_filter_results = None

        self.results_table = QTableWidget(0, 5)
        self.results_table.setObjectName("results_table")
        self.results_table.setHorizontalHeaderLabels(
            ["Test Name", "Value", "Min", "Max", "Result"]
        )
        header = self.results_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.results_table.setColumnWidth(0, 250)
        # Value is wide enough for a merged measurement point
        # ("25.62 V · 0.436 A · 11.18 W · 57.6 Ω"); Min/Max hold one number.
        self.results_table.setColumnWidth(1, 240)
        self.results_table.setColumnWidth(2, 70)
        self.results_table.setColumnWidth(3, 70)
        # Re-flow row heights when the table changes width: a wrapped failure
        # reason otherwise keeps the height it got while its column was narrow.
        # The stretching Result column does not emit sectionResized on a window
        # resize, so the viewport's own resize event is watched.
        self._results_resize_relay = _ResizeRelay(
            self.results_table.resizeRowsToContents, self.results_table
        )
        self.results_table.viewport().installEventFilter(self._results_resize_relay)
        self.results_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.results_table.setItemDelegateForColumn(
            4, ResultRowDelegate(self.results_table)
        )
        center_layout.addWidget(self.results_table, stretch=1)

        self.trace_log = QTextEdit()
        self.trace_log.setObjectName("trace_log")
        self.trace_log.setReadOnly(True)
        self.trace_log.setMinimumHeight(80)

        log_bar = QHBoxLayout()
        log_bar.addWidget(QLabel("HW Trace"))
        log_bar.addStretch()

        SP = QStyle.StandardPixmap
        self.btn_save_log = self._make_log_button(
            "btn_save_log", "Save log", SP.SP_DialogSaveButton, "save"
        )
        self.btn_save_log.clicked.connect(self.save_log_to_file)
        log_bar.addWidget(self.btn_save_log)

        self.btn_copy_log = self._make_log_button(
            "btn_copy_log", "Copy log", SP.SP_FileIcon, "copy"
        )
        self.btn_copy_log.clicked.connect(self.copy_log_to_clipboard)
        log_bar.addWidget(self.btn_copy_log)

        self.btn_clear_log = self._make_log_button(
            "btn_clear_log", "Clear log", SP.SP_TrashIcon, "clear"
        )
        self.btn_clear_log.clicked.connect(self._clear_trace)
        log_bar.addWidget(self.btn_clear_log)

        log_bar.addSpacing(16)
        self.chk_display_cmds = QCheckBox("Display tst file commands")
        self.chk_display_cmds.setChecked(True)
        self.chk_display_cmds.toggled.connect(self._refresh_trace_display)
        log_bar.addWidget(self.chk_display_cmds)

        self.trace_container = QWidget()
        trace_layout = QVBoxLayout(self.trace_container)
        trace_layout.setContentsMargins(0, 0, 0, 0)
        trace_layout.addLayout(log_bar)
        trace_layout.addWidget(self.trace_log)

        # One column per UUT in the current run (1-4), one row per quantity,
        # filled from the runner's `live_reading` signal. Columns are rebuilt
        # at each run start.
        self.live_table = QTableWidget(len(self._LIVE_QUANTITIES), 0)
        self.live_table.setObjectName("live_readings_table")
        self.live_table.setVerticalHeaderLabels(list(self._LIVE_QUANTITIES.values()))
        self.live_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.live_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.live_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.live_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.live_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._live_columns: dict[int, int] = {}  # slot_index -> table column
        self._unit_rows: dict[int, UnitProgressRow] = {}  # slot_index -> progress row
        self._test_groups: list[TestGroup] = []  # one per test-list row
        # Results-table rows that merge one measurement point's V/I/P/R steps,
        # keyed by (unit, report test, index, time point). See update_results_table.
        self._point_rows: dict[tuple, dict] = {}
        # Batch counters. Initialised here, not only in `_start_test_run`: a
        # Maintenance "Run Step" emits unit_finished without ever starting a
        # production run, and the counter slot raised AttributeError.
        self._batch_pass_count = 0
        self._batch_fail_count = 0

        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("bottom_tabs")
        self.bottom_tabs.addTab(self.live_table, "Live Readings")
        self.bottom_tabs.addTab(self.trace_container, "Messages")

        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.setObjectName("center_splitter")
        v_splitter.addWidget(results_table_container)
        v_splitter.addWidget(self.bottom_tabs)
        v_splitter.setStretchFactor(0, 7)
        v_splitter.setStretchFactor(1, 3)
        v_splitter.setSizes([700, 300])
        v_splitter.setCollapsible(0, False)
        v_splitter.setCollapsible(1, False)
        v_splitter.setHandleWidth(4)

        self.control_panel.start_requested.connect(self._on_start_clicked)
        self.control_panel.stop_requested.connect(self.stop_tests)
        self.control_panel.edit_part_number.textEdited.connect(
            self._mark_part_number_user_edited
        )

        right_panel = QWidget()
        right_panel.setObjectName("right_panel_contents")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        right_layout.addWidget(self.control_panel, stretch=1)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("right_panel_scroll")
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        right_scroll.setMinimumWidth(340)
        right_scroll.setMaximumWidth(420)
        right_scroll.setWidget(right_panel)

        main_row.addWidget(self.left_sidebar)
        main_row.addWidget(v_splitter, stretch=1)
        main_row.addWidget(right_scroll)

        main_layout.addLayout(main_row, stretch=1)

        self.sequence_finished.connect(self._show_result_dialog)

    def _fit_initial_size_to_screen(self) -> None:
        """Keep the first show inside the usable desktop area on scaled displays."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 800)
            return

        available = screen.availableGeometry()
        margin = 24
        min_size = self.minimumSizeHint()
        width = min(1200, max(min_size.width(), available.width() - margin))
        height = min(800, max(min_size.height(), available.height() - margin))
        self.resize(width, height)

    def _filter_table_results(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.results_table.rowCount()):
            if not needle:
                self.results_table.setRowHidden(row, False)
                continue
            name_item = self.results_table.item(row, 0)
            result_item = self.results_table.item(row, 4)
            haystack = " ".join(
                i.text().lower() for i in (name_item, result_item) if i is not None
            )
            self.results_table.setRowHidden(row, needle not in haystack)

    def _theme_path(self) -> Path:
        name = "dark_theme.qss" if self.is_dark_mode else "light_theme.qss"
        return resource_path("ui", "assets", name)

    def _apply_theme_file(self) -> None:
        path = self._theme_path()
        qss = path.read_text(encoding="utf-8")
        self.setStyleSheet(qss)

    def toggle_theme(self) -> None:
        self.is_dark_mode = not self.is_dark_mode
        self._apply_theme_file()
        self._update_icons(self.is_dark_mode)

    def _apply_test_item_permissions(self, item: QListWidgetItem, *, fixed: bool = False) -> None:
        """`fixed` rows (the engine's Polarity Check) are always on: in a
        production run that gate is mandatory, so it cannot be unticked."""
        flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        if fixed or not self._can(Capability.SELECT_STEPS):
            flags &= ~Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)

    def _mark_part_number_user_edited(self, _text: str) -> None:
        self._part_number_user_edited = True

    def _cleanup_operator_temp(self) -> None:
        if self._operator_temp_script is None:
            return
        path = self._operator_temp_script
        self._operator_temp_script = None
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _parse_labeled_counter(text: str, prefix: str) -> int:
        needle = prefix + ":"
        if needle in text:
            try:
                return int(text.split(needle, 1)[1].strip())
            except ValueError:
                return 0
        return 0

    def _unlock_pre_test_fields(self) -> None:
        self._pre_test_locked = False
        self.control_panel.edit_serial_number.setReadOnly(False)
        self.control_panel.edit_uut_type.setReadOnly(False)
        self.control_panel.edit_user_name.setReadOnly(True)
        self.control_panel.edit_user_name.setText(str(self._user_info.get("name", "")))
        self.control_panel.edit_serial_number.clear()
        self.control_panel.edit_uut_type.clear()

    def _finalize_run_reports(self) -> None:
        th = self.test_thread
        if th is None:
            return
        # Checked first, ahead of the role and "Save as log" gates: an operator
        # Stop is not a unit result, so nothing about it may be archived or left
        # cached for a manual export from the Results menu.
        reports = th.report_snapshots()
        if not reports:
            # The run ended before any unit was tested (no connection, script
            # failed to load, Stop before the first unit). There is nothing to
            # report; archiving here produced empty PDFs under unknown/unknown/
            # plus a spurious "XML not generated" modal.
            self._last_run_meta = None
            self._last_run_rows = []
            self.append_trace("No unit was tested - no report generated.")
            return
        if reports[-1].aborted_by_operator:
            self._last_run_meta = None
            self._last_run_rows = []
            self.append_trace(
                f"{reports[-1].unit.serial_number}: stopped by operator - "
                "no report saved."
            )
            return
        if reports[-1].aborted_link_lost:
            self._last_run_meta = None
            self._last_run_rows = []
            self.append_trace(
                f"{reports[-1].unit.serial_number}: communication with the load "
                "was lost - no report saved."
            )
            return
        meta, rows = th.report_snapshot()
        logical_name = f"{self._catalog_test_name} {getattr(self, '_catalog_version', '')}".strip()
        meta["test_program_name"] = logical_name
        self._last_run_meta = meta
        self._last_run_rows = list(rows)
        # Spec Rev.1.1 §1.1.4.3: no test reports in Maintenance mode. The
        # on-screen results above are still populated — only the persisted
        # artifacts are suppressed, so bench troubleshooting cannot produce a
        # document that looks like a production record. This check must live
        # here rather than at the call sites: the parallel-run path invokes
        # this method once per unit inside a loop.
        if not self._can(Capability.GENERATE_REPORTS):
            self.append_trace(
                "Maintenance mode - no test report generated for this run."
            )
            return
        if not self.control_panel.chk_save_log.isChecked():
            self.append_trace("Save as log disabled - skipping PDF archive.")
            return
        # Kept in a list, not a single attribute. A parallel run calls this once
        # per UUT in a tight loop, and a lone slot meant unit N+1 overwrote unit
        # N's only reference while its `deleteLater` was still pending — the
        # worker could be collected mid-flight and that unit silently lost its
        # CAMSTAR XML (observed in the archive: a serial with a PDF and no XML).
        # Spec §1.1.4.4 requires a report per UUT, so every worker must outlive
        # the loop that started it.
        worker = ReportWorker(meta, rows, self._current_role(), parent=self)
        self._report_workers.append(worker)
        worker.archived.connect(
            lambda p: self.append_trace(f"Report archived: {Path(p).name}")
        )
        worker.failed.connect(
            lambda e: self.append_trace(f"Report generation failed: {e}")
        )
        # The PDF archived but the CAMSTAR XML did not. Surfaced separately and
        # prominently: the run looks complete, yet MES has nothing to ingest.
        worker.xml_failed.connect(self._on_xml_report_failed)
        worker.xml_copy_failed.connect(self._on_xml_copy_failed)
        worker.saved_locally.connect(self._on_report_saved_locally)
        # A bound method, not a lambda: it is queued to the GUI thread reliably.
        # The lambda this replaces could miss, leaving a deleted worker in the
        # list — closing the app then crashed in _shutdown_threads.
        worker.finished.connect(self._prune_report_workers)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _prune_report_workers(self) -> None:
        """Drop workers that have finished, or whose C++ object is already gone."""
        self._report_workers = [w for w in self._report_workers if self._is_running(w)]

    def load_script(self) -> None:
        """Load a test from the database catalog (temp file) for every role."""
        dlg = SelectTestDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.selected_path()
        if path is None:
            return
        catalog = dlg.selected_catalog() or {}
        self._activate_script_file(
            path,
            str(catalog.get("test_name", path.stem)),
            str(catalog.get("version_name", "")),
            str(catalog.get("uut_type", "")),
        )

    def _activate_script_file(self, path: Path, test_name: str, version: str, uut_type: str) -> None:
        """Make a catalog version (already written to `path`) the active test."""
        self._cleanup_operator_temp()
        self._operator_temp_script = path
        self._active_script_path = path
        self._catalog_test_name = test_name
        self._catalog_version = version
        self._catalog_uut_type = uut_type
        self.control_panel.edit_uut_type.setText(uut_type)
        self._reload_script_into_list()

    def _activate_catalog_content(self, test_name: str, version: str, uut_type: str, content: str) -> bool:
        """Write a catalog version to a temp file and make it the active test."""
        try:
            tmp_dir = user_tmp_path()
            tmp_dir.mkdir(parents=True, exist_ok=True)
            path = tmp_dir / f"dfx_op_{uuid.uuid4().hex}.tst"
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Test", f"Could not create temp file:\n{exc}")
            return False
        self._activate_script_file(path, test_name, version, uut_type)
        return True

    def _load_latest_version(self) -> bool:
        """Start with the newest catalog version; False if the catalog is empty."""
        try:
            rec = DatabaseManager().latest_test_version()
        except Exception:
            rec = None
        if not rec:
            return False
        return self._activate_catalog_content(
            str(rec["test_name"]),
            str(rec["version_name"]),
            str(rec["uut_type"]),
            str(rec["test_content"]),
        )

    def _reload_script_into_list(self) -> None:
        """Parse the active script and repopulate the test list checkboxes."""
        self.test_list.clear()
        self._active_document = None
        logical_name = f"{self._catalog_test_name} {getattr(self, '_catalog_version', '')}".strip()
        self.label_active_script.setText(logical_name)

        if not self._active_script_path.is_file():
            self.append_trace(
                f"Active script not found: {self._active_script_path}"
            )
            return

        try:
            document = self._script_manager.load_document(self._active_script_path)
            steps = document.steps
        except ScriptParseError as exc:
            QMessageBox.warning(
                self,
                "Script parse error",
                f"Could not parse {self._active_script_path.name}:\n"
                f"line {exc.line_no}: {exc.msg}",
            )
            self.append_trace(
                f"Script parse error at line {exc.line_no}: {exc.msg}"
            )
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Script load error",
                f"Could not load {self._active_script_path.name}:\n{exc}",
            )
            return

        # Kept so a run can expand the ticked tests into their steps, and the
        # progress rows can look up step durations, without re-parsing.
        self._active_document = document

        # One row per spec test, not per script step (see logic.test_groups).
        sections = read_pdf_template().get("test_sections") or {}
        self._test_groups = build_test_groups(
            steps,
            TestRunnerThread.is_led_step,
            lambda name: str((sections.get(name) or {}).get("expected", "")),
            POLARITY_SETTLE_S,
        )
        for index, group in enumerate(self._test_groups):
            text = f"{index + 1}. {group.name}"
            if group.detail:
                # No indent: a wrapped detail line then keeps the same left edge.
                text += f"\n{group.detail}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(
                "Run by the engine right after the LED check: load OFF, settle, "
                "then read the output voltage. Always performed in production."
                if group.is_polarity
                else "Steps:\n" + "\n".join(group.step_names)
            )
            self._apply_test_item_permissions(item, fixed=group.is_polarity)
            item.setCheckState(Qt.CheckState.Checked)
            self.test_list.addItem(item)

        script_part_number = document.metadata.get("part_number", "").strip()
        if script_part_number and not self._part_number_user_edited:
            self.control_panel.edit_part_number.setText(script_part_number)

        self.append_trace(
            f"Loaded {len(self._test_groups)} test(s) ({len(steps)} step(s)) from {logical_name}."
        )
        # The Unit group is hidden; it reappears (editable) only when the script
        # declares no PartNum, which a run requires.
        has_part_number = bool(self.control_panel.edit_part_number.text().strip())
        self.control_panel.edit_part_number.setReadOnly(has_part_number)
        self.control_panel.unit_box.setVisible(not has_part_number)

    def _group_for_item(self, item: QListWidgetItem | None) -> TestGroup | None:
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int) and 0 <= index < len(self._test_groups):
            return self._test_groups[index]
        return None

    def _checked_groups(self) -> list[TestGroup]:
        """Ticked tests, excluding the Polarity row (it has no script steps)."""
        groups: list[TestGroup] = []
        for i in range(self.test_list.count()):
            item = self.test_list.item(i)
            group = self._group_for_item(item)
            if group and not group.is_polarity and item.checkState() == Qt.CheckState.Checked:
                groups.append(group)
        return groups

    def _always_step_names(self) -> list[str]:
        document = self._active_document
        return always_step_names(document.steps) if document else []

    def _selected_test_names(self) -> list[str]:
        """Script step names to run: every step of each ticked test, plus teardown.

        The list shows spec tests, but the engine runs script steps, so this
        expands the selection — a test always brings its Setup, and the
        `Always` teardown is always included.
        """
        names: list[str] = []
        for group in self._checked_groups():
            names.extend(group.step_names)
        if names:
            names.extend(self._always_step_names())
        return names

    def _on_start_clicked(self) -> None:
        txt = self.control_panel.btn_start.text()
        if txt == "Pause":
            if self._is_running(self.test_thread):
                self.test_thread.pause()
            self.control_panel.btn_start.setText("Resume")
            return

        if txt == "Resume":
            if self._is_running(self.test_thread):
                self.test_thread.resume_pause()
            self.control_panel.btn_start.setText("Pause")
            return

        # Checked before the dialog, not after it: the dialog can now record an
        # LED failure report, which needs the script, its steps and the part
        # number.
        tests_to_run = self._selected_test_names()
        if not tests_to_run:
            QMessageBox.information(
                self, "No tests selected", "Tick at least one test in the list."
            )
            return

        if not self._active_script_path.is_file():
            QMessageBox.warning(
                self,
                "No script",
                f"Active script does not exist: {self._active_script_path}",
            )
            return

        part_number = self.control_panel.edit_part_number.text().strip()
        if not part_number:
            QMessageBox.warning(
                self,
                "Missing unit info",
                "Part Number is required before starting a test.",
            )
            return

        _scan_target_v = 24.0
        _scan_tol_pct = 5.0
        try:
            _meta = self._script_manager.load_document(self._active_script_path).metadata
            _scan_target_v = float(_meta.get("input_target", "") or 24.0)
            _scan_tol_pct = float(_meta.get("input_tol", "") or 5.0)
        except Exception:
            pass

        # Cleared now rather than at run start, so the trace lines of any LED
        # failure recorded from the dialog stay visible.
        self._clear_trace()

        # The LED check is answered per unit in the dialog (spec §1.1.4.2: it is
        # the first test) — only when the selected steps include it.
        selected = set(tests_to_run)
        require_led = self._active_document is not None and any(
            step.name in selected and TestRunnerThread.is_led_step(step)
            for step in self._active_document.steps
        )
        dlg = BatchPreTestDialog(
            tester_name_default=str(self._user_info.get("name", "")),
            default_uut_type=self.control_panel.edit_uut_type.text(),
            scan_target_v=_scan_target_v,
            scan_tol_pct=_scan_tol_pct,
            require_led=require_led,
            on_led_failed=self._report_led_failure,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        dialog_results = dlg.result_dict()
        batch_units = list(dialog_results["batch_units"])
        first_serial = batch_units[0].serial_number if batch_units else ""
        self.control_panel.edit_serial_number.setText(first_serial)
        self.control_panel.edit_user_name.setText(dialog_results["tester_name"])

        self.control_panel.edit_uut_type.setText(dialog_results["uut_type"])
        self.control_panel.edit_serial_number.setReadOnly(True)
        self.control_panel.edit_uut_type.setReadOnly(True)
        self.control_panel.edit_user_name.setReadOnly(True)
        self._pre_test_locked = True
        self.control_panel.edit_current_unit.setText(
            f"1 / {len(batch_units)} - {first_serial}"
        )

        self._start_test_run(dialog_results)

    def _report_led_failure(self, unit: BatchUnit, uut_type: str, tester_name: str) -> None:
        """Record the FAIL report for a unit whose LED the operator confirmed OFF.

        Called from the pre-test dialog. The unit never enters the run, so the
        report is built by an unstarted runner (`record_led_failure` touches no
        hardware) and archived through the normal `_finalize_run_reports` path —
        which also enforces the Maintenance no-report rule.
        """
        employee_id = str(self._user_info.get("employee_id", ""))
        logical_script_name = (
            f"{self._catalog_test_name} {getattr(self, '_catalog_version', '')}"
        ).strip()
        thread = TestRunnerThread(
            self._active_script_path,
            set(self._selected_test_names()),
            operator=tester_name,
            tester_name=tester_name,
            employee_id=employee_id,
            uut_type=uut_type,
            part_number=self.control_panel.edit_part_number.text().strip(),
            serial_number=unit.serial_number,
            batch_units=[unit],
            script_manager=self._script_manager,
            logical_script_name=logical_script_name,
            persist_runs=self._can(Capability.ARCHIVE_RUN),
        )
        thread.log_msg.connect(self.append_trace)
        if thread.record_led_failure() is None:
            QMessageBox.warning(
                self,
                "LED failure not recorded",
                f"Could not build the report for {unit.serial_number}. "
                "See the Messages tab for details.",
            )
            return

        # `_finalize_run_reports` reads `self.test_thread`; swap it in the same
        # way the parallel-run path does for each of its units.
        previous = self.test_thread
        self.test_thread = thread
        try:
            self._finalize_run_reports()
        finally:
            self.test_thread = previous

        try:
            DatabaseManager().log_audit_action(
                "Unit failed LED at setup",
                username=str(self._user_info.get("username", "")),
                employee_id=employee_id,
                details=(
                    f"script={logical_script_name} sn={unit.serial_number!r} "
                    f"channel={unit.load_channel}"
                ),
            )
        except Exception:
            pass

    def _step_delay_s(self, step_name: str) -> float | None:
        """Total `Delay` time of a step in seconds, or None if it waits for nothing.

        Lets a unit's progress row show `elapsed/total`. Step names cannot be
        used for this: "... - 300W - 15min" is the report time point, while the
        step itself waits 10 minutes (Burn-In delays are cumulative).
        """
        document = self._active_document
        step = next((s for s in document.steps if s.name == step_name), None) if document else None
        if step is None:
            return None
        return TestRunnerThread.step_delay_s(step) or None

    #: Report test names as shown on a unit's progress row.
    _SHORT_TEST_NAMES = {
        "Low Load Stability Test": "Low Load",
        "Burn-In / Continuous Load Test": "Burn-In",
        "LED Indication Test": "LED",
    }

    def _step_label(self, step_name: str) -> str | None:
        """What a unit's progress row shows for a step: "Burn-In t2", "Low Load setup".

        A point's V/I/P/R steps share one label, so the row does not flicker
        between them. A step without `Report` (a load setup) takes the next
        reported test, as in the limits editor. None for steps the script does
        not hold (the engine's polarity / input checks) or cannot place; the row
        then abbreviates the step name instead.
        """
        document = self._active_document
        steps = list(document.steps) if document else []
        index = next((i for i, s in enumerate(steps) if s.name == step_name), None)
        if index is None:
            return None
        step = steps[index]
        test = (step.report_test or "").strip()
        if test:
            short = self._SHORT_TEST_NAMES.get(test, test)
            if (step.report_quantity or "").strip():
                return f"{short} t{step.report_index or 1}"
            return short
        following = next((s.report_test.strip() for s in steps[index + 1:]
                          if (s.report_test or "").strip()), "")
        if not following or not step.hidden:
            return None
        return f"{self._SHORT_TEST_NAMES.get(following, following)} setup"

    def _reset_live_readings(self, units: list[tuple[int, str]]) -> None:
        """Rebuild the live table with one column per (slot_index, serial), in slot order.

        The header names both the load channel and the UUT serial, so each
        column can be matched to a physical supply at a glance. Values start as
        "--" and stay at their last reading after the run ends, so the operator
        can still read them once the result dialog is closed.
        """
        units = sorted(units)
        self.live_table.setColumnCount(len(units))
        self.live_table.setHorizontalHeaderLabels(
            [f"CH{slot}\n{serial}" for slot, serial in units]
        )
        self._live_columns = {}
        for col, (slot, _serial) in enumerate(units):
            for row in range(len(self._LIVE_QUANTITIES)):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.live_table.setItem(row, col, item)
            self._live_columns[slot] = col
        self.bottom_tabs.setCurrentWidget(self.live_table)

    def _on_live_reading(self, data: dict) -> None:
        """Merge a (possibly partial) V/A/W reading into its unit's column."""
        col = self._live_columns.get(int(data.get("slot", 0)))
        if col is None:
            return
        for row, key in enumerate(self._LIVE_QUANTITIES):
            if key not in data:
                continue
            try:
                text = f"{float(data[key]):.2f}"
            except (TypeError, ValueError):
                continue
            item = self.live_table.item(row, col)
            if item is not None:
                item.setText(text)

    def _wire_test_thread_signals(self, thread: TestRunnerThread, row: UnitProgressRow) -> None:
        """Connect a runner thread to the UI. Shared by production and maintenance runs.

        `row` is that unit's progress row. Connected to the row's own methods
        (not lambdas) so the queued, cross-thread delivery lands in the GUI thread.
        """
        thread.log_msg.connect(self.append_trace)
        thread.test_result.connect(self.update_results_table)
        thread.loop_started.connect(self._on_loop_started)
        thread.progress_total.connect(self.control_panel.progress_total.setValue)
        thread.progress_test.connect(row.set_progress)
        thread.current_test.connect(row.on_step)
        thread.current_unit_changed.connect(self._on_current_unit_changed)
        thread.unit_finished.connect(self._on_unit_finished)
        thread.unit_alert.connect(self._on_unit_alert)
        thread.load_shutdown_failed.connect(self._on_load_shutdown_failed)
        thread.live_reading.connect(self._on_live_reading)
        thread.link_state.connect(self._on_link_state)
        thread.prompt_request.connect(self._on_prompt_request)
        thread.prompt_yesno_request.connect(self._on_prompt_yesno_request)
        thread.script_log.connect(self._on_script_log)
        thread.finished.connect(self.on_tests_finished)
        thread.finished.connect(thread.deleteLater)

    def _on_run_single_step(self) -> None:
        """Run one step out of sequence — Maintenance/Admin diagnostics only."""
        if not self._can(Capability.RUN_SINGLE_STEP):
            return
        if self._is_running(self.test_thread):
            QMessageBox.information(self, "Busy", "A test is already running.")
            return
        group = self._group_for_item(self.test_list.currentItem())
        if group is None:
            QMessageBox.information(
                self, "Run step", "Select a test in the list first."
            )
            return
        if not self._active_script_path.is_file():
            QMessageBox.warning(self, "No script", "Load a test first.")
            return

        # A whole spec test — its Setup, every measurement point, and the
        # teardown — matching spec §1.1.4.3 "execute individual test scenarios".
        # The Polarity Check has no script steps; the engine runs it on request.
        step_name = group.name
        selection = set(group.step_names) | set(self._always_step_names())
        polarity_only = group.is_polarity
        if (
            QMessageBox.question(
                self,
                "Run single test",
                f"Run '{step_name}' outside the production sequence?\n\n"
                f"{len(selection)} step(s) will execute (setup and teardown included).\n"
                "No test report will be generated and the run will not be archived.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self.results_table.setRowCount(0)
        self._point_rows = {}
        self.control_panel.progress_total.setValue(0)
        rows = self.control_panel.show_unit_progress(
            [(1, "MAINT")], self._step_delay_s, self._step_label
        )
        self.control_panel.btn_stop.setEnabled(True)
        self._clear_trace()
        self.append_trace(f"Maintenance run: {sorted(selection)}")
        # The runner builds its default unit on slot 1 with this serial.
        self._reset_live_readings([(1, "MAINT")])

        employee_id = str(self._user_info.get("employee_id", ""))
        self.test_thread = TestRunnerThread(
            self._active_script_path,
            selection,
            operator=self.control_panel.edit_user_name.text().strip(),
            tester_name=str(self._user_info.get("username", "")),
            employee_id=employee_id,
            uut_type=self.control_panel.edit_uut_type.text().strip(),
            part_number=self.control_panel.edit_part_number.text().strip(),
            serial_number="MAINT",
            script_manager=self._script_manager,
            logical_script_name=self._catalog_test_name or self._active_script_path.stem,
            production_flow=False,
            persist_runs=False,
            polarity_check=polarity_only,
        )
        self._wire_test_thread_signals(self.test_thread, rows[1])

        try:
            DatabaseManager().log_audit_action(
                "Maintenance step executed",
                username=str(self._user_info.get("username", "")),
                employee_id=employee_id,
                details=f"step={step_name!r} ran_with={sorted(selection)}",
            )
        except Exception:
            pass

        self.test_thread.start()

    def _start_test_run(self, pre_meta: dict) -> None:
        tests_to_run = self._selected_test_names()

        self.results_table.setRowCount(0)
        self._point_rows = {}
        self.control_panel.progress_total.setValue(0)
        self.control_panel.edit_current_unit.clear()
        self.control_panel.label_pass.setText("PASS: 0")
        self.control_panel.label_fail.setText("FAIL: 0")
        self.control_panel.label_batch_summary.setText("Batch: 0 PASS / 0 FAIL")
        self._batch_pass_count = 0
        self._batch_fail_count = 0

        self.control_panel.btn_stop.setEnabled(True)
        self.control_panel.btn_start.setText("Pause")

        loop_count = (
            self.control_panel.spin_loops.value()
            if self.control_panel.chk_loops.isChecked()
            else 1
        )
        stop_on_fail = self.control_panel.chk_stop_on_fail.isChecked()

        batch_units = list(pre_meta["batch_units"])
        unit_keys = [(u.slot_index, u.serial_number) for u in batch_units]
        self._reset_live_readings(unit_keys)
        self._unit_rows = self.control_panel.show_unit_progress(
            unit_keys, self._step_delay_s, self._step_label
        )
        operator = self.control_panel.edit_user_name.text().strip()
        part_number = self.control_panel.edit_part_number.text().strip()
        serial_number = batch_units[0].serial_number if batch_units else ""

        started = datetime.now()
        employee_id = str(self._user_info.get("employee_id", ""))
        logical_script_name = (
            f"{self._catalog_test_name} {getattr(self, '_catalog_version', '')}"
        ).strip()

        if len(batch_units) > 1:
            self._start_parallel_run(
                batch_units, pre_meta, operator, part_number, employee_id,
                logical_script_name, started, loop_count, stop_on_fail, tests_to_run,
            )
            return

        self.test_thread = TestRunnerThread(
            self._active_script_path,
            set(tests_to_run),
            loop_count=loop_count,
            stop_on_fail=stop_on_fail,
            operator=operator,
            tester_name=pre_meta["tester_name"].strip(),
            employee_id=employee_id,
            uut_type=pre_meta["uut_type"].strip(),
            part_number=part_number,
            serial_number=serial_number,
            batch_units=batch_units,
            script_manager=self._script_manager,
            start_time=started,
            logical_script_name=logical_script_name,
            persist_runs=self._can(Capability.ARCHIVE_RUN),
        )
        self._wire_test_thread_signals(self.test_thread, self._unit_rows[batch_units[0].slot_index])

        try:
            DatabaseManager().log_audit_action(
                "Test run started",
                username=str(self._user_info.get("username", "")),
                employee_id=employee_id,
                details=(
                    f"script={logical_script_name} "
                    f"part={part_number!r} sn={serial_number!r} "
                    f"steps={len(tests_to_run)}"
                ),
            )
        except Exception:
            pass

        self.test_thread.start()

    def _set_unit_header_row(
        self, row: int, serial: str, index: int, total: int, position_label: str
    ) -> None:
        col_count = self.results_table.columnCount()
        self.results_table.setSpan(row, 0, 1, col_count)
        item = QTableWidgetItem(
            f"=== Unit {index}/{total}: {serial} @ {position_label} ==="
        )
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setBackground(QBrush(QColor("#374151")))
        item.setForeground(QBrush(QColor("white")))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.results_table.setItem(row, 0, item)

    def _on_current_unit_changed(
        self,
        serial_number: str,
        index: int,
        total: int,
        position_label: str,
    ) -> None:
        self.control_panel.edit_current_unit.setText(
            f"{index}/{total} - {serial_number} - {position_label}"
        )
        self.control_panel.edit_serial_number.setText(serial_number)

        if position_label in self._parallel_section_cursors:
            return  # parallel mode: header already pre-added

        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        self._set_unit_header_row(row, serial_number, index, total, position_label)

    def _run_active(self) -> bool:
        return (self.test_thread is not None and self._is_running(self.test_thread)) or any(
            self._is_running(t) for t in self._parallel_threads
        )

    def _probe_link(self) -> None:
        """Between runs: ping the load (a run reports the real link itself)."""
        if self._run_active():
            return
        if is_simulated():
            self.link_led.set_state("sim", "HARDWARE_BACKEND is a simulator")
        elif not self._link_host:
            self.link_led.set_state("unknown", "No network address for the load (USB or auto-discovery)")
        else:
            self._ping.probe(self._link_host)

    def _on_ping_result(self, ok: bool) -> None:
        if self._run_active():
            return  # a run started meanwhile; its own link events win
        self.link_led.set_state("ok" if ok else "down", f"Load at {self._link_host} (ping)")
        if ok and self._loads_unconfirmed:
            self._loads_off_after_reconnect()

    def _loads_off_after_reconnect(self) -> None:
        """The link is back after a loss: switch every channel off (Req 26).

        Set by a failed load-off or a lost link. Retried on each successful
        ping until it works; only success is announced, so a still-flaky link
        does not pop a dialog every five seconds.
        """
        failures = self._switch_all_loads_off()
        if failures:
            self.append_trace(
                "Link back, but the load-off did not complete yet - retrying: "
                + "; ".join(failures)
            )
            return
        self._loads_unconfirmed = False
        self._record_trace("info", "Load communication restored - all channels switched OFF and verified.")
        try:
            DatabaseManager().log_audit_action(
                "Load communication restored - all channels switched off",
                username=str(self._user_info.get("username", "")),
                employee_id=str(self._user_info.get("employee_id", "")),
            )
        except Exception:
            pass
        QMessageBox.information(
            self,
            "Load communication restored",
            "Communication with the electronic load is back - all channels "
            "were switched OFF and verified.",
        )

    def _on_link_state(self, state: str) -> None:
        self.link_led.set_state(state, f"Load at {self._link_host or 'VISA'} (during the run)")

    def _on_unit_finished(self, payload: dict) -> None:
        if payload.get("link_lost"):
            self._loads_unconfirmed = True
            # Lost the load mid-test: no verdict, not counted, no report.
            row = self._unit_rows.get(int(payload.get("slot_index", 0) or 0))
            if row is not None:
                row.mark_failed("NO COMM")
            return
        if payload.get("aborted"):
            return  # stopped by the operator: neither a pass nor a fail
        passed = bool(payload.get("passed"))
        if passed:
            self._batch_pass_count += 1
        else:
            self._batch_fail_count += 1
            row = self._unit_rows.get(int(payload.get("slot_index", 0) or 0))
            if row is not None:
                row.mark_failed()
        self.control_panel.label_batch_summary.setText(
            f"Batch: {self._batch_pass_count} PASS / {self._batch_fail_count} FAIL"
        )

    def _on_unit_alert(self, message: str) -> None:
        QMessageBox.warning(self, "Unit Alert", message)

    def _on_load_shutdown_failed(self, reason: str) -> None:
        """Warn that the electronic load may still be energized.

        Uses `critical`, not `warning`: the previous behaviour was a trace-log
        line, which is exactly how a UUT ends up sitting under 300 W after a
        comms drop with nobody realising. Software cannot switch off a load it
        cannot reach, so the operator has to act physically.
        """
        self._record_trace("error", reason)
        self._loads_unconfirmed = True
        QMessageBox.critical(
            self,
            "LOAD MAY STILL BE ON",
            f"{reason}\n\n"
            "Switch the load off at its front panel, or disconnect the UUT, "
            "before continuing.",
        )

    def _on_prompt_request(self, msg: str) -> None:
        """Show a modal prompt; resume the runner once the operator clicks OK."""
        QMessageBox.information(self, "Test Prompt", msg)
        if self.test_thread is not None:
            self.test_thread.resume()

    def _on_prompt_yesno_request(self, msg: str) -> None:
        """Ask the operator a pass/fail question; relay it to the right thread.

        The LED indication test gets the Pass/Fail checkboxes spec §1.1.6.3
        names explicitly. Any other `PromptYesNo` step keeps the plain Yes/No
        box: the spec mandates the checkbox for the LED check specifically, and
        a generic script question reads better as Yes/No.
        """
        if self._is_led_prompt(msg):
            dlg = LedCheckDialog(msg, parent=self)
            dlg.exec()
            answer = dlg.result_is_pass()
        else:
            result = QMessageBox.question(
                self,
                "Test Prompt",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            answer = result == QMessageBox.StandardButton.Yes
        sender = self.sender()
        if sender is not None and hasattr(sender, "submit_yesno_answer"):
            sender.submit_yesno_answer(answer)  # type: ignore[attr-defined]
        elif self.test_thread is not None:
            self.test_thread.submit_yesno_answer(answer)

    @staticmethod
    def _is_led_prompt(msg: str) -> bool:
        """True when a PromptYesNo is the manual LED indication check.

        Matched on the prompt text rather than the step name: the engine sends
        only the message. "led" alone is enough of a signal here — a script's
        other yes/no questions are about fixtures and connections, and the cost
        of a false positive is merely showing Pass/Fail instead of Yes/No.
        """
        return "led" in msg.lower()

    def _on_script_log(self, msg: str) -> None:
        """Append an operator-authored Log line to the trace, distinctly styled."""
        stamped = f"{datetime.now().strftime('[%H:%M:%S]')} {msg}"
        self._record_trace("log", stamped)
        if self._secure is not None:
            try:
                self._secure.log(
                    "script_log",
                    {"message": msg, "display": stamped},
                )
            except Exception:
                pass

    def _format_trace_html(self, entry_type: str, text: str) -> str:
        if entry_type == "log":
            return (
                '<span style="color:#22d3ee;"><i>[LOG]</i> '
                f"{html.escape(text)}</span>"
            )
        return html.escape(text)

    def _entry_passes_filter(self, entry: dict) -> bool:
        if self.chk_display_cmds.isChecked():
            return True
        return entry["type"] != "cmd"

    def _record_trace(self, entry_type: str, text: str) -> None:
        entry = {
            "type": entry_type,
            "text": text,
            "html": self._format_trace_html(entry_type, text),
        }
        self._trace_history.append(entry)
        if self._entry_passes_filter(entry):
            self.trace_log.append(entry["html"])

    def _refresh_trace_display(self) -> None:
        self.trace_log.clear()
        for entry in self._trace_history:
            if self._entry_passes_filter(entry):
                self.trace_log.append(entry["html"])

    def _clear_trace(self) -> None:
        self._trace_history.clear()
        self.trace_log.clear()

    @staticmethod
    def _is_running(thread: QThread | None) -> bool:
        """`isRunning()` that tolerates a thread whose C++ object is gone.

        Calling a method on a deleted QThread raises RuntimeError. In a parallel
        run one unit can finish long before the others; its thread used to be
        deleted at once, so Stop crashed on it and never reached the units still
        running — pressing Stop did nothing.
        """
        if thread is None:
            return False
        try:
            return shiboken6.isValid(thread) and thread.isRunning()
        except RuntimeError:
            return False

    def stop_tests(self) -> None:
        if self._parallel_threads:
            for th in self._parallel_threads:
                if self._is_running(th):
                    th.stop()
            self.append_trace("Stopping all parallel sequences...")
        elif self._is_running(self.test_thread):
            self.test_thread.stop()
            self.append_trace("Stopping sequence...")

    def _shutdown_threads(self) -> None:
        # try/finally: the exit load-off must run whatever goes wrong above.
        # A stale (deleted) report worker once raised here, and the close then
        # skipped _release_load_on_exit entirely.
        try:
            for th in list(self._parallel_threads):
                if self._is_running(th):
                    th.stop()
                    th.wait(5000)
            if self._is_running(self.test_thread):
                self.test_thread.stop()
                if not self.test_thread.wait(5000):
                    self.append_trace("Warning: test thread did not stop within 5s.")
            # Wait on every outstanding worker, not just the most recent: a
            # parallel run has one per UUT, and closing while one is mid-write
            # would truncate that unit's report.
            for worker in list(self._report_workers):
                if self._is_running(worker) and not worker.wait(5000):
                    self.append_trace(
                        "Warning: a report worker did not finish within 5s."
                    )
        finally:
            self._release_load_on_exit()

    def _release_load_on_exit(self) -> None:
        """Last-chance load shutdown when the app is closing.

        A stopped runner thread already drops its own load via the engine's
        `finally`, so this covers what that cannot: closing without ever having
        started a run, or after a run whose thread had already gone away, where
        the load could still be holding a state set by an aborted attempt.

        Every channel is switched off and read back. If one still reports ON,
        or the instrument cannot be reached, the operator is warned *before*
        the window closes (this runs from closeEvent while it is still shown):
        a silent trace line here is how loads were once left on unnoticed.
        """
        failures = self._switch_all_loads_off()
        if not failures:
            self.append_trace("Load off verified on exit (all channels).")
            return
        detail = "\n".join(failures)
        self.append_trace(f"Could not verify load off on exit:\n{detail}")
        QMessageBox.critical(
            self,
            "LOAD MAY STILL BE ON",
            "Could not confirm that every electronic load channel is OFF:\n\n"
            f"{detail}\n\n"
            "If a UUT is connected, switch the load off at its front panel or "
            "disconnect the UUT.",
        )

    def _switch_all_loads_off(self) -> list[str]:
        """LOAD OFF on every channel, each read back. Returns the failures ([] = all off)."""
        failures: list[str] = []
        try:
            # Short timeout and no identity probe: the window is closing, so a
            # missing instrument must not stall the exit.
            driver = create_driver(timeout_ms=1500, probe_identity=False)
            try:
                driver.connect()
                # LOAD OFF acts on the selected channel only, so a bare one
                # switched off a single channel of up to four. Select each in
                # turn, and read each back.
                verify = getattr(driver, "load_off_verified", None)
                for slot, load_serial in enumerate(LOAD_SERIALS or [""], start=1):
                    try:
                        if hasattr(driver, "activate_slot"):
                            driver.activate_slot(slot, load_serial_number=load_serial)
                        if callable(verify):
                            verify()
                        else:
                            driver.execute_command("loadoff", [])
                    except Exception as exc:
                        failures.append(f"CH{slot}: {exc}")
            finally:
                driver.disconnect()
        except Exception as exc:
            failures.append(f"instrument not reachable: {exc}")
        return failures

    def logout(self) -> None:
        """Closes the main window and signals the application to restart the login flow."""
        confirm = QMessageBox.question(
            self,
            "Log Out",
            "Are you sure you want to log out and switch users?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                DatabaseManager().log_audit_action(
                    "User Logged Out",
                    username=str(self._user_info.get("username", "")),
                    employee_id=str(self._user_info.get("employee_id", "")),
                    details="",
                )
            except Exception:
                pass
            self._cleanup_operator_temp()
            self._shutdown_threads()
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.setProperty("logout_requested", True)
            app = QApplication.instance()
            if app is not None:
                app.setProperty("logout_requested", True)
            self.close()

    def closeEvent(self, event) -> None:
        if self._is_running(self.test_thread) or any(
            self._is_running(th) for th in self._parallel_threads
        ):
            confirm = QMessageBox.question(
                self,
                "Test in progress",
                "A test is still running. Stop it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        app = QApplication.instance()
        if app is not None and not bool(app.property("logout_requested")):
            try:
                DatabaseManager().log_audit_action(
                    "User Logged Out",
                    username=str(self._user_info.get("username", "")),
                    employee_id=str(self._user_info.get("employee_id", "")),
                    details="",
                )
            except Exception:
                pass
        self._shutdown_threads()
        self._cleanup_operator_temp()
        super().closeEvent(event)
        # Leave the event loop explicitly instead of relying on Qt's "last
        # window closed" quit. That check runs only at the moment this window
        # closes: a runner stopped by the close finishes a moment later and
        # used to open its result dialog ("Run stopped", PASS/FAIL), which made
        # Qt cancel the quit — and closing that dialog never re-checks. The
        # process then sat in app.exec() with no window, holding the
        # single-instance lock ("Application is already running"). exit()
        # cannot be cancelled; main() then handles logout or exit as before.
        if app is not None:
            app.exit(0)

    def _on_loop_started(self, loop_number: int, loop_total: int) -> None:
        """Insert a visual separator row before each new loop iteration."""
        col_count = self.results_table.columnCount()
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        self.results_table.setSpan(row, 0, 1, col_count)

        item = QTableWidgetItem(f"──  Loop {loop_number} of {loop_total}  ──")
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setBackground(QBrush(QColor("#2f65ca")))
        item.setForeground(QBrush(QColor("white")))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.results_table.setItem(row, 0, item)

    @staticmethod
    def _failure_text(result: TestResultPayload) -> str:
        """What the unit did when it failed, against the range it had to meet.

        e.g. "FAIL — 14.97 V (OK 22.8–25.2 V), at 0.3 s". Shown in
        the Result column for every role: the operator has to know why a unit
        failed, and the Value/Min/Max columns are hidden for some roles.
        """
        unit = str(result.get("unit", "") or "")
        note = str(result.get("note", "") or "")
        if unit == "pass":  # a PromptYesNo step such as the LED check
            return "FAIL - answered Fail"
        value, lo, hi = result.get("value"), result.get("min"), result.get("max")
        measured = ""
        if result.get("is_measurement", True) and isinstance(value, (int, float)) and math.isfinite(value):
            measured = f"{value:.2f} {unit}".rstrip()
        allowed = ""
        if (
            isinstance(lo, (int, float)) and isinstance(hi, (int, float))
            and math.isfinite(lo) and math.isfinite(hi)
        ):
            allowed = f"(OK {lo:g}-{hi:g} {unit})".replace(" )", ")")
        if measured:
            text = f"FAIL - {measured} {allowed}".rstrip()
            return f"{text}, {note}" if note else text
        # No reading: lead with why, then the range it had to meet.
        return f"FAIL - {note or 'no reading'} {allowed}".rstrip()

    def _fill_result_row(self, row: int, test_name: str, result: TestResultPayload) -> None:
        passed = bool(result["passed"])
        skipped = bool(result.get("skipped", False))
        if skipped:
            status = "N/A"  # not performed (spec §1.1.4.5), not a failure
        elif passed:
            status = "PASS"
        else:
            status = self._failure_text(result)
        is_measurement = bool(result.get("is_measurement", True)) and not skipped

        if is_measurement:
            unit_str = result["unit"]
            val_str = f"{_format_measurement(result['value'])} {unit_str}".rstrip()
            min_str = _format_measurement(result["min"])
            max_str = _format_measurement(result["max"])
        else:
            val_str = _NA
            min_str = _NA
            max_str = _NA

        self.results_table.setItem(row, 0, QTableWidgetItem(test_name))
        self.results_table.setItem(row, 1, QTableWidgetItem(val_str))
        self.results_table.setItem(row, 2, QTableWidgetItem(min_str))
        self.results_table.setItem(row, 3, QTableWidgetItem(max_str))

        status_item = QTableWidgetItem(status)
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        status_item.setToolTip(status)
        self.results_table.setItem(row, 4, status_item)
        if not passed and not skipped:
            # The reason can be wider than the column; let the row grow so it
            # wraps instead of being cut off with "...".
            self.results_table.resizeRowToContents(row)

    def _update_pass_fail_counters(self, result: TestResultPayload) -> None:
        if result.get("skipped"):
            return  # N/A rows are neither a pass nor a fail
        passed = bool(result["passed"])
        if passed:
            n = self._parse_labeled_counter(self.control_panel.label_pass.text(), "PASS") + 1
            self.control_panel.label_pass.setText(f"PASS: {n}")
        else:
            n = self._parse_labeled_counter(self.control_panel.label_fail.text(), "FAIL") + 1
            self.control_panel.label_fail.setText(f"FAIL: {n}")

    def update_results_table(self, test_name: str, result: TestResultPayload) -> None:
        """Add a step result; the steps of one measurement point share a row.

        Steps carrying a `TimePoint` (the V/I/P/R reads of "Low Load t1",
        "Burn-In t2", ...) merge into one row per point, the way spec Appendix A
        lays out each point ("t1: Voltage .. Current .. Power .. Resistance").
        Everything else — input check, LED, polarity, older scripts — keeps one
        row per step.
        """
        key = self._point_key(result)
        if key is not None and key in self._point_rows:
            entry = self._point_rows[key]
            entry["members"].append((test_name, result))
            self._render_point_row(entry)
        else:
            row = self._insert_result_row(result)
            if key is None:
                self._fill_result_row(row, test_name, result)
            else:
                items = [QTableWidgetItem("") for _ in range(5)]
                for col, item in enumerate(items):
                    self.results_table.setItem(row, col, item)
                entry = {"items": items, "members": [(test_name, result)], "result": result}
                self._point_rows[key] = entry
                self._render_point_row(entry)
        # Count each point once — by its limited quantity (voltage); the
        # documentation-only reads (current, power, resistance) do not count.
        if key is None or self._has_limits(result):
            self._update_pass_fail_counters(result)

    def _insert_result_row(self, result: TestResultPayload) -> int:
        """Insert an empty row for `result`, inside its unit's section in a parallel run."""
        pos = result.get("position_label", "")
        if pos and pos in self._parallel_section_cursors:
            insert_at = self._parallel_section_cursors[pos]
            self.results_table.insertRow(insert_at)
            self._parallel_section_cursors[pos] += 1
            past = False
            for p in self._parallel_section_order:
                if p == pos:
                    past = True
                    continue
                if past:
                    self._parallel_section_cursors[p] += 1
            return insert_at
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        return row

    @staticmethod
    def _point_key(result: TestResultPayload) -> tuple | None:
        """Which measurement point a step reads, or None for a standalone step.

        A point is a report test plus its measurement index: the script tags
        only the Voltage step with a TimePoint, while Current/Power/Resistance
        carry the same `Quantity` + `Measurement` index. Steps without a
        quantity (LED, engine gates, older scripts) stay one row each.
        """
        if not str(result.get("report_quantity", "") or "") or not result.get("report_test"):
            return None
        return (
            result.get("position_label", ""),
            result.get("serial_number", ""),
            int(result.get("loop", 1) or 1),  # each Loops pass gets its own rows
            result.get("report_test", ""),
            result.get("report_index", 0),
        )

    @staticmethod
    def _has_limits(result: TestResultPayload) -> bool:
        lo, hi = result.get("min"), result.get("max")
        return (
            isinstance(lo, (int, float)) and isinstance(hi, (int, float))
            and math.isfinite(lo) and math.isfinite(hi)
        )

    _QUANTITY_ORDER = {"voltage": 0, "current": 1, "power": 2, "resistance": 3}

    def _render_point_row(self, entry: dict) -> None:
        """(Re)draw a merged measurement-point row from all of its steps so far."""
        first = entry["result"]
        row = self.results_table.row(entry["items"][0])
        if row < 0:
            return
        members = sorted(
            entry["members"],
            key=lambda m: self._QUANTITY_ORDER.get(str(m[1].get("report_quantity", "")).lower(), 9),
        )

        index = int(first.get("report_index", 0) or 0) or 1
        timed = next((m[1] for m in members if m[1].get("report_time")), None)
        time_txt = (
            f"{timed.get('report_time', '')} {timed.get('report_time_uom', '')}".strip()
            if timed else ""
        )
        name = f"{first.get('report_test', '')}  t{index}" + (f" ({time_txt})" if time_txt else "")

        values: list[str] = []
        tips: list[str] = []
        limited: TestResultPayload | None = None
        failed: TestResultPayload | None = None
        all_skipped = True
        for step_name, res in members:
            skipped = bool(res.get("skipped"))
            all_skipped = all_skipped and skipped
            unit = "Ω" if str(res.get("unit", "")).lower() == "ohm" else str(res.get("unit", ""))
            value = res.get("value")
            shown = ""
            if not skipped and res.get("is_measurement", True) and isinstance(value, (int, float)) and math.isfinite(value):
                shown = f"{_format_measurement(value)} {unit}".rstrip()
                values.append(shown)
            if not skipped and self._has_limits(res) and limited is None:
                limited = res
            if not skipped and not res.get("passed") and failed is None:
                failed = res
            verdict = "N/A" if skipped else ("PASS" if res.get("passed") else "FAIL")
            tips.append(f"{step_name}: {shown or '-'}  {verdict}")

        if failed is not None:
            status = self._failure_text(failed)
        elif all_skipped:
            status = "N/A"
        else:
            status = "PASS"

        cells = [
            name,
            " · ".join(values) if values else _NA,
            _format_measurement(limited["min"]) if limited else _NA,
            _format_measurement(limited["max"]) if limited else _NA,
            status,
        ]
        tooltip = "\n".join(tips)
        for col, (item, text) in enumerate(zip(entry["items"], cells)):
            item.setText(text)
            item.setToolTip(tooltip if col < 4 else f"{status}\n\n{tooltip}")
        entry["items"][4].setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_table.resizeRowToContents(row)

    def append_trace(self, msg: str) -> None:
        stamped = f"{datetime.now().strftime('[%H:%M:%S]')} {msg}"
        entry_type = "cmd" if msg.startswith("Executing:") else "info"
        self._record_trace(entry_type, stamped)

    def _on_report_saved_locally(self, note: str) -> None:
        """The chosen reports folder (e.g. a share) was unreachable; nothing was lost."""
        self._record_trace("error", note)
        QMessageBox.warning(self, "Report saved locally", note)

    def _on_xml_copy_failed(self, error: str) -> None:
        """The XML exists in the unit folder; only the shared-folder copy failed."""
        self._record_trace("error", f"XML report not copied to the XML folder: {error}")
        QMessageBox.warning(
            self,
            "XML not copied",
            "The XML report was saved in the unit's folder, but could not be "
            f"copied to the XML folder:\n\n{error}\n\nCopy it there manually.",
        )

    def _on_xml_report_failed(self, error: str) -> None:
        """Report a CAMSTAR XML export failure without hiding it in the trace.

        The PDF archived fine, so the run *looks* finished. A missing XML means
        MES cannot ingest it, which is only discovered much later downstream —
        hence the modal rather than a trace line alone.
        """
        self._record_trace("error", f"CAMSTAR XML report NOT written: {error}")
        QMessageBox.warning(
            self,
            "CAMSTAR XML not generated",
            "The PDF report was archived, but the CAMSTAR XML report was not "
            "written.\n\nThis run cannot be imported into MES until the cause "
            f"is fixed.\n\n{error}",
        )

    def _start_parallel_run(
        self,
        batch_units: list,
        pre_meta: dict,
        operator: str,
        part_number: str,
        employee_id: str,
        logical_script_name: str,
        started: datetime,
        loop_count: int,
        stop_on_fail: bool,
        tests_to_run: list[str],
    ) -> None:
        shared_driver = create_driver()
        try:
            shared_driver.connect()
            if hasattr(shared_driver, "on_link_state"):
                self.link_led.set_state("ok", f"Load at {self._link_host or 'VISA'} (run started)")
        except HardwareError as exc:
            self.link_led.set_state("down", f"Could not connect: {exc}")
            # No silent demo-mode fallback: substituting MockHardware here would
            # report a healthy 24 V on every channel and archive the result as a
            # real production run. Abort and tell the operator instead.
            self._record_trace("error", f"Hardware connection failed: {exc}")
            QMessageBox.critical(
                self,
                "Instrument not connected",
                f"Could not connect to the electronic load:\n\n{exc}\n\n"
                "Check the cable and power, then start the run again.",
            )
            return
        hw_lock = threading.Lock()
        self._parallel_shared_driver = shared_driver

        total = len(batch_units)
        self._parallel_section_order = [u.position_label for u in batch_units]
        self._parallel_section_cursors = {}
        for i, unit in enumerate(batch_units):
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self._set_unit_header_row(row, unit.serial_number, i + 1, total, unit.position_label)
            self._parallel_section_cursors[unit.position_label] = row + 1

        self._parallel_threads = []
        self._parallel_finished_count = 0
        for unit in batch_units:
            th = TestRunnerThread(
                self._active_script_path,
                set(tests_to_run),
                loop_count=loop_count,
                stop_on_fail=stop_on_fail,
                operator=operator,
                tester_name=pre_meta["tester_name"].strip(),
                employee_id=employee_id,
                uut_type=pre_meta["uut_type"].strip(),
                part_number=part_number,
                serial_number=unit.serial_number,
                batch_units=[unit],
                script_manager=self._script_manager,
                start_time=started,
                logical_script_name=logical_script_name,
                driver=shared_driver,
                hw_lock=hw_lock,
                skip_hw_lifecycle=True,
                persist_runs=self._can(Capability.ARCHIVE_RUN),
            )
            th.log_msg.connect(self.append_trace)
            th.test_result.connect(self.update_results_table)
            th.loop_started.connect(self._on_loop_started)
            row = self._unit_rows[unit.slot_index]
            # Through the unit's row, not straight to the bar: the panel shows
            # the slowest running unit instead of whichever reported last.
            th.progress_total.connect(row.set_total)
            th.progress_test.connect(row.set_progress)
            th.current_test.connect(row.on_step)
            th.current_unit_changed.connect(self._on_current_unit_changed)
            th.unit_finished.connect(self._on_unit_finished)
            th.unit_alert.connect(self._on_unit_alert)
            th.load_shutdown_failed.connect(self._on_load_shutdown_failed)
            th.live_reading.connect(self._on_live_reading)
            th.link_state.connect(self._on_link_state)
            th.prompt_request.connect(self._on_prompt_request)
            th.prompt_yesno_request.connect(self._on_prompt_yesno_request)
            th.script_log.connect(self._on_script_log)
            th.finished.connect(self._on_parallel_thread_finished)
            # Not deleted on its own `finished`: Stop, close and the final
            # report pass all walk this list, and a unit can finish minutes
            # before the others. Deleted together in _on_parallel_thread_finished.
            self._parallel_threads.append(th)

        try:
            DatabaseManager().log_audit_action(
                "Test run started (parallel)",
                username=str(self._user_info.get("username", "")),
                employee_id=employee_id,
                details=(
                    f"script={logical_script_name} "
                    f"part={part_number!r} units={total} "
                    f"steps={len(tests_to_run)}"
                ),
            )
        except Exception:
            pass

        for th in self._parallel_threads:
            th.start()

    def _on_parallel_thread_finished(self) -> None:
        self._parallel_finished_count += 1
        if self._parallel_finished_count < len(self._parallel_threads):
            return

        try:
            if self._parallel_shared_driver is not None:
                self._parallel_shared_driver.disconnect()
        except Exception:
            pass
        self._parallel_shared_driver = None

        overall_passed = True
        stopped = self._stopped_serials(self._parallel_threads)
        lost = self._link_lost_serials(self._parallel_threads)
        total_units = len(self._parallel_threads)
        original_thread = self.test_thread
        for th in self._parallel_threads:
            self.test_thread = th
            try:
                self._finalize_run_reports()
                meta, _ = th.report_snapshot()
                if str(meta.get("overall_result", "")).upper() != "PASS":
                    overall_passed = False
            except Exception as exc:
                # Never silent: this used to be `except Exception: pass`, so a
                # unit that failed to produce its report looked like a clean
                # run. A missing report is a spec §1.1.4.4 breach and the
                # operator has to know which unit it was.
                units = getattr(th, "_batch_units", None) or []
                serial = getattr(units[0], "serial_number", "?") if units else "?"
                self._record_trace(
                    "error", f"Report generation failed for unit {serial}: {exc}"
                )
                overall_passed = False
        self.test_thread = original_thread

        try:
            DatabaseManager().log_audit_action(
                "Test run stopped by operator (parallel)"
                if stopped
                else "Test run ended - load communication lost (parallel)"
                if lost
                else "Test run completed (parallel)",
                username=str(self._user_info.get("username", "")),
                employee_id=str(self._user_info.get("employee_id", "")),
                details=(
                    f"not reported={stopped + lost}"
                    if stopped or lost
                    else f"overall={'PASS' if overall_passed else 'FAIL'}"
                ),
            )
        except Exception:
            pass

        for th in self._parallel_threads:
            th.deleteLater()  # all finished and finalised now
        self._parallel_threads = []
        self._parallel_section_cursors = {}
        self._parallel_section_order = []
        self._parallel_finished_count = 0
        self.control_panel.stop_unit_progress()

        self.control_panel.set_running_state(False)
        self.control_panel.edit_part_number.setReadOnly(False)
        if self._active_script_path.is_file():
            self.control_panel.edit_part_number.setReadOnly(True)
        self._unlock_pre_test_fields()
        self.append_trace("All parallel sequences complete.")
        if lost:
            self._show_link_lost_message(
                lost, some_completed=len(lost) + len(stopped) < total_units
            )
        elif stopped:
            self._show_stopped_message(
                stopped, some_completed=len(stopped) < total_units
            )
        else:
            self.sequence_finished.emit(overall_passed)

    def on_tests_finished(self) -> None:
        th = self.test_thread
        if th is None:
            return

        self.append_trace("Sequence Complete.")
        self.control_panel.stop_unit_progress()

        stopped = self._stopped_serials([th])
        lost = self._link_lost_serials([th])
        nothing_tested = not th.report_snapshots()
        overall_passed = False
        try:
            self._finalize_run_reports()
        finally:
            try:
                meta, _rows = th.report_snapshot()
                overall_passed = (
                    str(meta.get("overall_result", "")).strip().upper() == "PASS"
                )
                if nothing_tested:
                    action = "Test run ended before any unit was tested"
                elif stopped:
                    action = "Test run stopped by operator"
                elif lost:
                    action = "Test run ended - load communication lost"
                else:
                    action = "Test run completed"
                DatabaseManager().log_audit_action(
                    action,
                    username=str(self._user_info.get("username", "")),
                    employee_id=str(self._user_info.get("employee_id", "")),
                    details=(
                        f"overall={meta.get('overall_result', '?')} "
                        f"script={meta.get('test_program_name', '')}"
                        + (f" not reported={stopped + lost}" if stopped or lost else "")
                    ),
                )
            except Exception:
                pass
            th.resume_pause()

        self.control_panel.set_running_state(False)
        self.control_panel.edit_part_number.setReadOnly(False)
        if self._active_script_path.is_file():
            self.control_panel.edit_part_number.setReadOnly(True)
        self._unlock_pre_test_fields()
        self.test_thread = None

        if self._closing:
            return  # no dialogs once the window is closing — see closeEvent
        if nothing_tested:
            # A FAIL banner here would blame a unit that was never tested, and
            # an Operator cannot see the Messages tab that holds the reason.
            QMessageBox.information(
                self,
                "Run did not start",
                "The run ended before any unit was tested (for example, the "
                "electronic load did not respond).\n\nNo report was generated.",
            )
        elif lost:
            self._show_link_lost_message(lost)
        elif stopped:
            self._show_stopped_message(stopped)
        else:
            self.sequence_finished.emit(overall_passed)

    def _show_result_dialog(self, passed: bool) -> None:
        if self._closing:
            return
        TestResultDialog(passed, parent=self).exec()

    @staticmethod
    def _stopped_serials(threads: list[TestRunnerThread]) -> list[str]:
        """Serials of units whose tests the operator's Stop cut short."""
        return [
            report.unit.serial_number
            for th in threads
            for report in th.report_snapshots()
            if report.aborted_by_operator
        ]

    @staticmethod
    def _link_lost_serials(threads: list[TestRunnerThread]) -> list[str]:
        """Serials of units cut short by a lost (and not restored) load link."""
        return [
            report.unit.serial_number
            for th in threads
            for report in th.report_snapshots()
            if report.aborted_link_lost
        ]

    def _show_link_lost_message(self, serials: list[str], *, some_completed: bool = False) -> None:
        """Shown instead of the PASS/FAIL banner when the load's link was lost."""
        if self._closing:
            return
        text = (
            "Communication with the electronic load was lost and could not be "
            f"restored.\n\nNo report was saved for: {', '.join(serials)}.\n\n"
            "Check the load's cable and power, then test these units again."
        )
        if some_completed:
            text += "\n\nUnits that finished before were reported normally."
        QMessageBox.warning(self, "Load communication lost", text)

    def _show_stopped_message(self, serials: list[str], *, some_completed: bool = False) -> None:
        """Shown instead of the PASS/FAIL banner: a stopped run has no verdict."""
        if self._closing:
            return
        text = (
            "Run stopped by operator.\n\n"
            f"No report was saved for: {', '.join(serials)}."
        )
        if some_completed:
            text += "\n\nUnits that finished before the stop were reported normally."
        QMessageBox.information(self, "Run stopped", text)

    def _on_select_all_tests(self) -> None:
        for i in range(self.test_list.count()):
            item = self.test_list.item(i)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def _on_clear_all_tests(self) -> None:
        # Skips the fixed Polarity row: that gate always runs in production.
        for i in range(self.test_list.count()):
            item = self.test_list.item(i)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)


