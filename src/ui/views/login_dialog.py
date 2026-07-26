"""Pre-launch login dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QFrame,
)

from logic.auth_manager import AuthManager
from logic.database_manager import DatabaseManager
from paths import resource_path
from ui.ui_helpers import attach_password_visibility_toggle


class LoginDialog(QDialog):
    """Collect credentials and resolve role via AuthManager."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle("DFX ATE Login")
        self.setObjectName("login_dialog")

        # Size similar to the mockup
        self.setFixedSize(390, 580)

        self._user_info: dict = {}
        self._auth = AuthManager()

        # Outer layout - used only to center the login card
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(32, 32, 32, 32)
        outer_layout.setSpacing(0)

        outer_layout.addStretch(1)

        # White login card
        card = QFrame()
        card.setObjectName("login_card")
        card.setFixedWidth(310)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 34, 32, 34)
        card_layout.setSpacing(10)

        outer_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)

        outer_layout.addStretch(1)

        # Title
        title = QLabel("DFX ATE Login")
        title.setObjectName("login_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)

        card_layout.addSpacing(20)

        # Username label
        lbl_user = QLabel("Username")
        lbl_user.setObjectName("login_label")
        card_layout.addWidget(lbl_user)

        # Username input
        self._username = QLineEdit()
        self._username.setObjectName("login_input")
        self._username.setPlaceholderText("Enter username")
        self._username.setMinimumHeight(48)
        card_layout.addWidget(self._username)

        card_layout.addSpacing(8)

        # Password label
        lbl_pass = QLabel("Password")
        lbl_pass.setObjectName("login_label")
        card_layout.addWidget(lbl_pass)

        # Password input
        self._password = QLineEdit()
        self._password.setObjectName("login_input")
        self._password.setPlaceholderText("Enter password")
        self._password.setMinimumHeight(48)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)

        # Password visibility toggle
        attach_password_visibility_toggle(self._password)

        card_layout.addWidget(self._password)

        card_layout.addSpacing(20)

        # Login button
        btn = QPushButton("Login")
        btn.setObjectName("login_button")
        btn.setMinimumHeight(52)
        btn.clicked.connect(self._on_login)
        card_layout.addWidget(btn)

        card_layout.addSpacing(10)

        # Error label
        self._error_label = QLabel("")
        self._error_label.setObjectName("login_error")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setMinimumHeight(20)
        card_layout.addWidget(self._error_label)

        # Press Enter to login
        self._username.returnPressed.connect(self._on_login)
        self._password.returnPressed.connect(self._on_login)

        try:
            qss_path = resource_path("ui", "assets", "style.qss")
            if qss_path.is_file():
                self.setStyleSheet(qss_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Failed to load login stylesheet: {e}")

    def _on_login(self) -> None:
        user_info = self._auth.login(
            self._username.text().strip(),
            self._password.text(),
        )

        if user_info is None:
            self._error_label.setText("Invalid username or password.")
            return

        self._error_label.clear()
        self._user_info = user_info

        try:
            DatabaseManager().log_audit_action(
                "User Logged In",
                username=str(user_info.get("username", user_info.get("name", ""))),
                employee_id=str(user_info.get("employee_id", "")),
                details="",
            )
        except Exception:
            pass

        self.accept()

    def get_user_info(self) -> dict:
        """Return the dict from the last successful login(); call after exec() returns Accepted."""
        return dict(self._user_info)
