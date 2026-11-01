"""DFX XML Transfer service: sends finished XML reports to the MES folder.

Runs as its own windowless program (`DFX_XmlTransfer.exe`), separate from the
GUI, so reports keep moving when the GUI is closed and after a restart. Windows
starts it at logon (Task Scheduler, see `install_xml_service_task.cmd`); the GUI
also starts it if it finds it is not running.

One loop, one cycle at a time: a cycle every 60 seconds, or at once when the GUI
asks (the `kick` file). Cycles therefore never overlap. Only one copy of the
service runs per installation folder (named mutex). The logic itself is in `logic.xml_transfer`.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from logic.file_lock import AlreadyRunningError, SingleInstanceLock  # noqa: E402
from paths import user_data_path  # noqa: E402

HEARTBEAT_SECONDS = 5


def mutex_name() -> str:
    """One service per installation folder, not per PC.

    Keyed by the data folder, so two installs on one PC (e.g. a test copy next
    to the production folder) each get their own service instead of the first
    one silently blocking the other.
    """
    import hashlib

    key = str(user_data_path()).lower().encode("utf-8")
    return "Local\\DFX_xml_transfer_" + hashlib.sha1(key).hexdigest()[:12]


def _log_line(message: str) -> None:
    """Append to data/xml_transfer/service.log (the service has no window)."""
    try:
        path = user_data_path("xml_transfer", "service.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except OSError:
        pass


def _cycle_seconds() -> float:
    """60 s (customer requirement). DFX_XML_CYCLE_SECONDS overrides it for automated tests only."""
    from logic.xml_transfer import CYCLE_SECONDS

    try:
        return max(1.0, float(os.environ.get("DFX_XML_CYCLE_SECONDS", CYCLE_SECONDS)))
    except ValueError:
        return float(CYCLE_SECONDS)


def serve() -> int:
    from logic import xml_transfer as xt

    xt.ensure_dirs()
    log = xt.TransferLog()
    interval = _cycle_seconds()
    status: dict = {
        "service": "Running",
        "pid": os.getpid(),
        "started": datetime.now().isoformat(timespec="seconds"),
        "cycle_seconds": interval,
        "cycle_count": 0,
    }
    next_cycle = 0.0  # first cycle at once: also picks up what a restart left in Pending
    last_beat = 0.0
    _log_line(f"service started (pid {os.getpid()}, cycle {interval:g} s)")
    while True:
        now = time.time()
        if xt.stop_path().exists():
            try:
                xt.stop_path().unlink()
            except OSError:
                pass
            status.update(service="Stopped", heartbeat=time.time())
            xt.write_status(status)
            _log_line("service stopped (stop request)")
            return 0
        kicked = xt.kick_path().exists()
        if kicked or now >= next_cycle:
            if kicked:
                try:
                    xt.kick_path().unlink()
                except OSError:
                    pass
            status.update(cycle_running=True, cycle_started_epoch=now, heartbeat=now)
            xt.write_status(status)
            try:
                result = xt.run_cycle(log, xt.mes_dir())
                status.update(
                    mes="Available" if result.available else "Unavailable",
                    mes_reason=result.reason,
                    mes_folder=result.mes_folder,
                    mes_checked=result.finished,
                    last_cycle_start=result.started,
                    last_cycle_end=result.finished,
                    last_transferred=len(result.transferred),
                    last_failed=len(result.failed),
                    last_error="",
                )
            except Exception as exc:  # never let one bad cycle stop the service
                status.update(last_error=str(exc), last_cycle_end=datetime.now().isoformat(timespec="seconds"))
                _log_line("cycle error: " + "".join(traceback.format_exception(exc)).strip())
            end = time.time()
            next_cycle = end + interval
            status.update(
                cycle_running=False,
                cycle_count=int(status.get("cycle_count", 0)) + 1,
                cycle_end_epoch=end,
                next_cycle=(datetime.now() + timedelta(seconds=interval)).isoformat(timespec="seconds"),
                pending=len(xt.pending_files()),
                transferred=sum(1 for p in xt.transferred_dir().glob("*.xml")),
                heartbeat=end,
            )
            xt.write_status(status)
            last_beat = end
        elif now - last_beat >= HEARTBEAT_SECONDS:
            status["heartbeat"] = now
            xt.write_status(status)
            last_beat = now
        time.sleep(0.5)


def main() -> int:
    lock = SingleInstanceLock(user_data_path("xml_transfer", "service.lock"), mutex_name())
    try:
        lock.acquire()
    except AlreadyRunningError:
        return 0  # one is already running: nothing to do
    try:
        try:
            return serve()
        except Exception as exc:
            # Typically a bad .env (config validates on import). No window to
            # show it in, so it goes to the log and the GUI shows "Stopped".
            _log_line("service failed: " + "".join(traceback.format_exception(exc)).strip())
            return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
