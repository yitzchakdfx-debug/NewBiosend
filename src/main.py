"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import ctypes


from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ui.views.login_dialog import LoginDialog
from ui.views.main_window import MainWindow
from logic.file_lock import AlreadyRunningError, SingleInstanceLock
from logic.report_templates import ensure_templates_seeded
from paths import ensure_user_data_seeded, resource_path, user_data_path, user_tmp_path


def _sweep_tmp() -> None:
    """Delete leftover temp files from a previous crash before the next session."""
    import shutil
    tmp = user_tmp_path()
    if tmp.is_dir():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)


def main() -> int:
    try:
        myappid = 'mycompany.dfxtester.ate.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception as e:
        print(f"Note: Could not set AppUserModelID: {e}")

    ensure_user_data_seeded()
    ensure_templates_seeded()
    _sweep_tmp()

    app = QApplication(sys.argv)
    icon_path = resource_path("ui", "assets", "icons", "DFXAppIcon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        print(f"Warning: Icon not found at {icon_path}")

    lock_path = user_data_path("app.lock")
    lock = SingleInstanceLock(lock_path)
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        QMessageBox.critical(None, "DFX Tester", str(exc))
        return 1

    try:
        while True:
            login = LoginDialog()
            if login.exec() != LoginDialog.DialogCode.Accepted:
                break

            user_info = login.get_user_info()
            window = MainWindow(user_info)
            app.setProperty("logout_requested", False)
            window.show()
            app.exec()
            if bool(app.property("logout_requested")):
                continue
            break
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
