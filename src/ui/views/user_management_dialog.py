"""Admin-only user management dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from logic.auth_manager import AuthManager
from logic.database_manager import DatabaseManager
from logic.roles import ALL_ROLES
from ui.ui_helpers import attach_password_visibility_toggle


class UserEditDialog(QDialog):
    _ROLES = ALL_ROLES

    def __init__(
        self,
        *,
        mode: str,
        db: DatabaseManager,
        auth: AuthManager,
        username: str = "",
        role: str = "Operator",
        employee_id: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._auth = auth
        self._mode = mode
        self._original_username = username.strip()
        self.setWindowTitle("Add User" if mode == "add" else "Edit User")
        self.resize(420, 260)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self._edit_username = QLineEdit()
        self._edit_username.setText(self._original_username if mode == "edit" else "")
        self._edit_username.setReadOnly(mode == "edit")
        form.addRow("Username", self._edit_username)

        self._edit_employee_id = QLineEdit()
        self._edit_employee_id.setText(employee_id)
        form.addRow("Employee ID", self._edit_employee_id)

        self._combo_role = QComboBox()
        self._combo_role.addItems(list(self._ROLES))
        if role:
            idx = self._combo_role.findText(role)
            self._combo_role.setCurrentIndex(max(0, idx))
        form.addRow("Role", self._combo_role)

        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd_confirm = QLineEdit()
        self._pwd_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        attach_password_visibility_toggle(self._pwd)
        attach_password_visibility_toggle(self._pwd_confirm)
        
        if mode == "add":
            form.addRow("Password", self._pwd)
            form.addRow("Confirm", self._pwd_confirm)
        else:
            pwd_label = QLabel("Leave blank to keep current password.")
            pwd_label.setObjectName("lbl_pwd_hint")
            form.addRow("", pwd_label)
            form.addRow("New password", self._pwd)
            form.addRow("Confirm", self._pwd_confirm)

        root.addLayout(form)
        self._error = QLabel("")
        self._error.setObjectName("lbl_error")
        root.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _submit(self) -> None:
        uname = self._edit_username.text().strip()
        if not uname:
            self._error.setText("Username is required.")
            return

        pwd = self._pwd.text()
        confirm = self._pwd_confirm.text()
        role = self._combo_role.currentText()
        eid = self._edit_employee_id.text().strip()

        if self._mode == "add":
            if not pwd:
                self._error.setText("Password is required.")
                return
            if pwd != confirm:
                self._error.setText("Passwords do not match.")
                return
            try:
                self._db.create_user(uname, pwd, role, eid)
            except Exception as exc:
                QMessageBox.warning(self, "Add User Failed", str(exc))
                return
        else:
            if pwd:
                if pwd != confirm:
                    self._error.setText("Passwords do not match.")
                    return
                try:
                    self._db.update_user(uname, role=role, employee_id=eid, password=pwd)
                except Exception as exc:
                    QMessageBox.warning(self, "Update Failed", str(exc))
                    return
            else:
                try:
                    self._db.update_user(uname, role=role, employee_id=eid)
                except Exception as exc:
                    QMessageBox.warning(self, "Update Failed", str(exc))
                    return

        self.accept()


class UserManagementDialog(QDialog):
    def __init__(self, current_username: str, parent=None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._auth = AuthManager(self._db)
        self._current_username = current_username
        self.setWindowTitle("User Management")
        self.resize(660, 400)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Manage users and role permissions."))

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Username", "Role", "Employee ID"])
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self._table)

        row = QHBoxLayout()
        self._btn_add = QPushButton("Add User")
        self._btn_add.clicked.connect(self._add_user)
        row.addWidget(self._btn_add)

        self._btn_edit = QPushButton("Edit User")
        self._btn_edit.clicked.connect(self._edit_user)
        row.addWidget(self._btn_edit)

        self._btn_delete = QPushButton("Delete User")
        self._btn_delete.clicked.connect(self._delete_user)
        row.addWidget(self._btn_delete)

        row.addStretch()
        root.addLayout(row)

        self._refresh()

    def _selected_username(self) -> str | None:
        idx = self._table.currentRow()
        if idx < 0:
            return None
        item = self._table.item(idx, 0)
        return item.text() if item else None

    def _selected_record(self) -> dict | None:
        uname = self._selected_username()
        if not uname:
            return None
        for u in self._db.list_users():
            if str(u["username"]).lower() == uname.lower():
                return dict(u)
        return None

    def _refresh(self) -> None:
        users = self._db.list_users()
        self._table.setRowCount(0)
        for user in users:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(str(user["username"])))
            self._table.setItem(r, 1, QTableWidgetItem(str(user["role"])))
            self._table.setItem(r, 2, QTableWidgetItem(str(user.get("employee_id", ""))))

    def _add_user(self) -> None:
        dlg = UserEditDialog(mode="add", db=self._db, auth=self._auth, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _edit_user(self) -> None:
        rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "Edit User", "Select a user first.")
            return
        dlg = UserEditDialog(
            mode="edit",
            db=self._db,
            auth=self._auth,
            username=str(rec["username"]),
            role=str(rec["role"]),
            employee_id=str(rec.get("employee_id", "")),
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _delete_user(self) -> None:
        username = self._selected_username()
        if not username:
            QMessageBox.information(self, "Delete User", "Select a user first.")
            return
        if username == self._current_username:
            QMessageBox.warning(self, "Delete User", "You cannot delete the current user.")
            return
        choice = QMessageBox.question(
            self,
            "Delete User",
            f"Delete '{username}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self._db.delete_user(username)
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Delete User Failed", str(exc))