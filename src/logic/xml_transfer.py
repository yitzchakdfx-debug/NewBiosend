"""XML transfer to the customer's MES (CAMSTAR) folder: the logic shared by the
background service (`xml_service.py`) and the GUI that monitors it.

Flow (customer POD "XML Transfer Service"):

1. A finished report's XML is put in the local ``Pending`` folder (`enqueue`).
2. A transfer cycle copies each pending file to the MES folder, reads the copy
   back and compares it with the local file (SHA-256).
3. Only a verified file is moved to the local ``Transferred`` folder; anything
   else stays in ``Pending`` and is retried on the next cycle.
4. Every attempt is appended to a transfer log that is never edited.

Pending and Transferred are always on this PC, so a report is never lost when
the network is down. The service and the GUI are separate processes; they talk
through small files in ``data/xml_transfer``: ``status.json`` (written by the
service) and ``kick`` (written by the GUI to ask for a cycle now).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from paths import user_data_path

#: Customer requirement: one transfer cycle every 60 seconds.
CYCLE_SECONDS = 60
#: A status older than this means the service is not running.
STALE_AFTER_SECONDS = 90

RESULT_OK = "Successful"
RESULT_FAILED = "Failed"
STATUS_PENDING = "Pending"
STATUS_TRANSFERRED = "Transferred"

#: The single warning the customer specified (POD test 2), word for word.
MES_UNAVAILABLE_TEXT = (
    "MES connection unavailable. XML reports were saved locally and will be "
    "transferred automatically after communication is restored."
)


# --------------------------------------------------------------------- paths
def xml_root() -> Path:
    return user_data_path("results", "XML")


def pending_dir() -> Path:
    return xml_root() / "Pending"


def transferred_dir() -> Path:
    return xml_root() / "Transferred"


def state_dir() -> Path:
    return user_data_path("xml_transfer")


def status_path() -> Path:
    return state_dir() / "status.json"


def kick_path() -> Path:
    return state_dir() / "kick"


def stop_path() -> Path:
    return state_dir() / "stop"


def log_db_path() -> Path:
    return state_dir() / "transfer_log.db"


def ensure_dirs() -> None:
    for folder in (pending_dir(), transferred_dir(), state_dir()):
        folder.mkdir(parents=True, exist_ok=True)


def mes_dir() -> Path | None:
    """The configured MES folder, or None when none is set (see `report_paths.mes_dir`)."""
    from logic.report_paths import mes_dir as _mes_dir

    return _mes_dir()


# ------------------------------------------------------------------- helpers
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _free_name(folder: Path, name: str) -> Path:
    """`folder/name`, or `name_2`, `name_3`... if taken."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists():
        target = folder / f"{stem}_{n}{suffix}"
        n += 1
    return target


def enqueue(xml_file: Path) -> Path:
    """Put a copy of a finished XML report in Pending; returns the pending path.

    A name already used (same unit retested in the same minute) gets `_2`, so a
    report never overwrites another one waiting or already sent.
    """
    ensure_dirs()
    name = Path(xml_file).name
    target = pending_dir() / name
    if target.exists() or (transferred_dir() / name).exists():
        stem, suffix = Path(name).stem, Path(name).suffix
        n = 2
        while (pending_dir() / f"{stem}_{n}{suffix}").exists() or (
            transferred_dir() / f"{stem}_{n}{suffix}"
        ).exists():
            n += 1
        target = pending_dir() / f"{stem}_{n}{suffix}"
    tmp = target.with_name(target.name + ".part")
    shutil.copyfile(xml_file, tmp)
    os.replace(tmp, target)
    return target


def pending_files() -> list[Path]:
    folder = pending_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".xml")


def file_status(name: str) -> str:
    """Where the file is now: Pending, Transferred, or "" if in neither."""
    if (pending_dir() / name).is_file():
        return STATUS_PENDING
    if (transferred_dir() / name).is_file():
        return STATUS_TRANSFERRED
    return ""


def check_access(folder: Path | None) -> tuple[bool, str]:
    """Write, read back and delete a dummy file in the MES folder.

    A ping is not enough (customer POD): the folder must really accept files.
    Returns (available, reason); the reason is empty when available.
    """
    if folder is None:
        return False, "No MES folder is set (Connections > Instrument Setup)."
    probe = Path(folder) / f".dfx_mes_check_{uuid.uuid4().hex}.tmp"
    token = f"DFX MES access check {uuid.uuid4().hex}".encode("ascii")
    try:
        if not Path(folder).is_dir():
            return False, f"MES folder not reachable: {folder}"
        probe.write_bytes(token)
        if probe.read_bytes() != token:
            return False, f"MES folder returned different data: {folder}"
    except OSError as exc:
        return False, f"MES folder not usable ({folder}): {exc}"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if probe.exists():
        return False, f"MES folder does not allow deleting files: {folder}"
    return True, ""


# ---------------------------------------------------------------- the log
class TransferLog:
    """Append-only record of every transfer attempt (customer POD test 3).

    Kept in its own SQLite file so the service and the GUI (two processes)
    never contend for the main database. There is no update or delete here on
    purpose: the log is read-only from the GUI.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else log_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS xml_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    result TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=10)

    def add(self, filename: str, result: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO xml_transfers (timestamp, filename, result, detail) VALUES (?, ?, ?, ?)",
                (_now_iso(), filename, result, detail),
            )

    def entries(self, limit: int = 2000) -> list[dict]:
        """Newest first."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, filename, result, detail FROM xml_transfers "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]


# -------------------------------------------------------------- the cycle
@dataclass
class CycleResult:
    started: str
    finished: str = ""
    mes_folder: str = ""
    available: bool = False
    reason: str = ""
    transferred: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _transfer_one(src: Path, folder: Path) -> str:
    """Copy one pending file to the MES folder and verify it. "" = verified, else why not."""
    local_hash = _sha256(src)
    final = folder / src.name
    if final.exists():
        # Already there (e.g. the previous cycle copied it and stopped before
        # moving it locally): same content counts as sent, a different file is
        # never overwritten.
        if _sha256(final) == local_hash:
            return ""
        return "A different file with the same name already exists in the MES folder."
    part = folder / f".{src.name}.part"
    shutil.copyfile(src, part)
    os.replace(part, final)
    if _sha256(final) != local_hash:
        return "The copy in the MES folder does not match the local report."
    return ""


def _move_to_transferred(src: Path) -> None:
    ensure_dirs()
    target = transferred_dir() / src.name
    if target.exists():
        if _sha256(target) == _sha256(src):
            src.unlink()
            return
        target = _free_name(transferred_dir(), src.name)
    os.replace(src, target)


def run_cycle(log: TransferLog, folder: Path | None) -> CycleResult:
    """One transfer cycle to `folder` (None = no MES folder set): check access,
    then send and verify every pending file."""
    ensure_dirs()
    result = CycleResult(started=_now_iso(), mes_folder=str(folder or ""))
    result.available, result.reason = check_access(folder)
    for src in pending_files():
        if not result.available:
            log.add(src.name, RESULT_FAILED, result.reason)
            result.failed.append(src.name)
            continue
        try:
            problem = _transfer_one(src, folder)
        except OSError as exc:
            problem = f"Copy to the MES folder failed: {exc}"
        if problem:
            log.add(src.name, RESULT_FAILED, problem)
            result.failed.append(src.name)
            continue
        try:
            _move_to_transferred(src)
        except OSError as exc:
            # Verified in MES but still in Pending: the next cycle sees the
            # identical file there and finishes the move.
            log.add(src.name, RESULT_FAILED, f"Sent and verified, but not moved to Transferred: {exc}")
            result.failed.append(src.name)
            continue
        log.add(src.name, RESULT_OK, f"Verified in {folder}")
        result.transferred.append(src.name)
    result.finished = _now_iso()
    return result


# ------------------------------------------------------- service <-> GUI
def write_status(status: dict) -> bool:
    """Atomic write, so the GUI never reads half a file. Never raises.

    On Windows the replace is refused while the GUI has the file open for a
    read, so it is retried briefly; a status that still cannot be written is
    simply written on the next heartbeat. Returns False if it was skipped.
    """
    try:
        ensure_dirs()
        tmp = status_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    except OSError:
        return False
    for _ in range(20):
        try:
            os.replace(tmp, status_path())
            return True
        except PermissionError:
            time.sleep(0.05)
        except OSError:
            return False
    return False


def read_status() -> dict:
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def service_running(status: dict | None = None) -> bool:
    """True when the service wrote a heartbeat recently."""
    status = read_status() if status is None else status
    if status.get("service") != "Running":
        return False
    try:
        beat = float(status.get("heartbeat", 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - beat) < STALE_AFTER_SECONDS


def start_service() -> bool:
    """Start the service if it is not running. Returns True if a start was attempted.

    Backup for the Task Scheduler entry: the GUI calls this at startup and when
    it sees the service has stopped. A second copy exits at once (mutex), so a
    start while one is already running is harmless.
    DFX_XML_SERVICE_AUTOSTART=0 turns this off (automated tests).
    """
    import subprocess
    import sys

    from paths import is_frozen

    if os.environ.get("DFX_XML_SERVICE_AUTOSTART", "1").strip() == "0":
        return False
    if service_running():
        return False
    if is_frozen():
        exe = Path(sys.executable).resolve().parent / "DFX_XmlTransfer.exe"
        if not exe.is_file():
            return False
        command = [str(exe)]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / "xml_service.py")]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            command,
            cwd=str(Path(command[-1]).parent),
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return False
    return True


def request_cycle() -> float:
    """Ask the service for a cycle now; returns the request time (epoch seconds)."""
    ensure_dirs()
    stamp = time.time()
    kick_path().write_text(str(stamp), encoding="ascii")
    return stamp
