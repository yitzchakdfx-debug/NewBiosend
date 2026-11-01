"""MES (CAMSTAR) folder warnings that only an Admin or Maintenance user may clear.

Customer POD "XML Transfer Service":
* at startup, when the MES folder is unreachable, only an Administrator or
  Maintenance user may authorize continued operation;
* after a run whose XML reports could not be transferred, one warning is shown
  and only an Administrator or Maintenance user may dismiss it.

An Operator sees the same warning with an "Authorize..." button that asks for an
Admin or Maintenance user name and password. Each authorization is audited.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from logic.database_manager import DatabaseManager
from logic.roles import Capability, can, role_label
from ui.ui_helpers import attach_password_visibility_toggle


class AuthorizeDialog(QDialog):
    """Asks for the name and password of a user allowed to clear an MES warning."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Authorization required")
        self.authorized_by: dict | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("An Administrator or Maintenance user must authorize this."))
        form = QFormLayout()
        self.edit_user = QLineEdit()
        self.edit_user.setObjectName("mes_auth_user")
        self.edit_password = QLineEdit()
        self.edit_password.setObjectName("mes_auth_password")
        self.edit_password.setEchoMode(QLineEdit.EchoMode.Password)
        attach_password_visibility_toggle(self.edit_password)
        form.addRow("User name", self.edit_user)
        form.addRow("Password", self.edit_password)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setObjectName("mes_auth_error")
        self.error.setStyleSheet("color: #dc2626;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check(self) -> None:
        user = DatabaseManager().verify_login(self.edit_user.text().strip(), self.edit_password.text())
        if user is None:
            self.error.setText("Wrong user name or password.")
            return
        if not can(str(user.get("role", "")), Capability.AUTHORIZE_MES_OFFLINE):
            self.error.setText(
                f"{user.get('username', '')} is {role_label(str(user.get('role', '')))}: "
                "only an Administrator or Maintenance user can authorize."
            )
            return
        self.authorized_by = user
        self.accept()


class MesWarningDialog(QDialog):
    """The MES warning; closes only when an Admin/Maintenance user clears it.

    `allow_logout` adds a "Log out" button (startup check): choosing it, or
    closing the window, returns `Rejected`. Without it (end of a run) the dialog
    cannot be closed at all until it is cleared.
    """

    def __init__(
        self,
        text: str,
        *,
        title: str,
        user_info: dict,
        action: str,
        allow_logout: bool = False,
        detail: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, allow_logout)
        self._user = user_info
        self._action = action
        self._allow_logout = allow_logout
        self.cleared_by: str = ""

        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setObjectName("mes_warning_text")
        message.setWordWrap(True)
        message.setStyleSheet("font-weight: bold;")
        layout.addWidget(message)
        if detail:
            extra = QLabel(detail)
            extra.setWordWrap(True)
            extra.setObjectName("mes_warning_detail")
            layout.addWidget(extra)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_logout = None
        if allow_logout:
            self.btn_logout = QPushButton("Log out")
            self.btn_logout.setObjectName("mes_logout")
            self.btn_logout.clicked.connect(self.reject)
            row.addWidget(self.btn_logout)
        if can(str(user_info.get("role", "")), Capability.AUTHORIZE_MES_OFFLINE):
            self.btn_clear = QPushButton("Continue" if allow_logout else "OK")
        else:
            self.btn_clear = QPushButton("Authorize...")
        self.btn_clear.setObjectName("mes_clear")
        self.btn_clear.setDefault(True)
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        layout.addLayout(row)
        self.resize(520, self.sizeHint().height())

    def _clear(self) -> None:
        if can(str(self._user.get("role", "")), Capability.AUTHORIZE_MES_OFFLINE):
            by = self._user
        else:
            auth = AuthorizeDialog(self)
            if auth.exec() != QDialog.DialogCode.Accepted or auth.authorized_by is None:
                return
            by = auth.authorized_by
        self.cleared_by = str(by.get("username", ""))
        try:
            DatabaseManager().log_audit_action(
                self._action,
                username=str(self._user.get("username", "")),
                employee_id=str(self._user.get("employee_id", "")),
                details=f"authorized by {self.cleared_by} ({by.get('role', '')})",
            )
        except Exception:
            pass
        self.accept()

    def reject(self) -> None:
        # Esc / the close button: only the startup check offers a way out, and
        # that way out is logging off. The end-of-run warning stays up.
        if self._allow_logout:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._allow_logout or self.result() == QDialog.DialogCode.Accepted:
            super().closeEvent(event)
        else:
            event.ignore()


class MesAccessProbe(QObject):
    """Runs the MES-folder access check off the GUI thread.

    An unreachable network path can take many seconds to time out, so the
    window stays responsive; `result(available, reason)` arrives in the GUI
    thread (queued signal).
    """

    result = Signal(bool, str)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        from logic import xml_transfer

        try:
            ok, reason = xml_transfer.check_access(xml_transfer.mes_dir())
        except Exception as exc:
            ok, reason = False, str(exc)
        self.result.emit(ok, reason)
