"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import ctypes


from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from logic.file_lock import AlreadyRunningError, SingleInstanceLock
from paths import ensure_user_data_seeded, resource_path, user_data_path, user_tmp_path

# NOT imported here: anything that reaches `config`, which validates `.env` at
# import time and raises on a bad value (e.g. HARDWARE_BACKEND=simulatorr). At
# module scope that exception escapes before QApplication exists, so a windowed
# EXE would die silently with no message and no console to print to. `main()`
# imports these after the app object exists and shows the error in a dialog.


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
    _sweep_tmp()

    app = QApplication(sys.argv)

    # Deferred so a configuration error can be shown rather than killing a
    # windowless EXE in silence. `config` validates `.env` on import.
    try:
        from logic.report_templates import ensure_templates_seeded
        from ui.views.login_dialog import LoginDialog
        from ui.views.main_window import MainWindow

        ensure_templates_seeded()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "DFX Tester — configuration error",
            f"The application cannot start:\n\n{exc}",
        )
        return 1

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


def _exit_now(code: int) -> None:
    """Terminate the process, even if a non-daemon thread is still blocked.

    `sys.exit` only unwinds the main thread; the interpreter then waits for
    every non-daemon thread before the process actually dies. A runner thread
    stuck on an operator prompt therefore kept the whole application alive and
    invisible after the window closed — still holding the single-instance lock
    (so the app would not start again) and, in a hardware run, the
    instrument's one TCP slot.

    The blocking waits are fixed at the source in `test_engine`, so this is a
    backstop rather than the cure. It runs only after the normal shutdown has
    completed — `MainWindow.closeEvent` has already stopped the threads,
    released the load and flushed the reports — so nothing is cut short here
    that was not already finished or already hung.
    """
    # Best-effort only, and it must never be what stops the exit. In a windowed
    # PyInstaller build (`console=False`) there is no console attached, so
    # `sys.stdout`/`sys.stderr` are None and calling .flush() on them raised
    # AttributeError *after* the window had closed -- the user saw an "Unhandled
    # exception in script" box on every clean exit. Dev runs have real streams,
    # so this only ever failed in the packaged app.
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    # os._exit skips interpreter teardown, which is exactly the point: teardown
    # is what blocks on the stuck thread.
    os._exit(code)


if __name__ == "__main__":
    _exit_now(main())
