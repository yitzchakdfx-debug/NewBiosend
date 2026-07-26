"""Single-instance guard: named mutex on Windows, PID lock file elsewhere."""
from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Raised when another live instance owns the lock."""


_ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "Local\\DFX_ate_singleton"  # session-scoped (Local\ not Global\)


class SingleInstanceLock:
    def __init__(self, lock_path: Path, mutex_name: str = _MUTEX_NAME) -> None:
        self._lock_path = Path(lock_path)
        self._mutex_name = mutex_name
        self._fd: int | None = None
        self._mutex_handle = None

    def _acquire_windows(self) -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        last_error = kernel32.GetLastError()
        if not handle:
            return  # fail open: never block startup on a mutex API error
        self._mutex_handle = handle
        if last_error == _ERROR_ALREADY_EXISTS:
            raise AlreadyRunningError("Application is already running.")

    def _acquire_posix(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return
            except FileExistsError:
                try:
                    pid = int(self._lock_path.read_text("ascii").strip())
                    os.kill(pid, 0)
                    raise AlreadyRunningError("Application is already running.")
                except (OSError, ValueError):
                    try:
                        self._lock_path.unlink()
                        continue
                    except OSError:
                        raise AlreadyRunningError("Application is already running.")

    def acquire(self) -> None:
        self._acquire_windows() if sys.platform == "win32" else self._acquire_posix()

    def release(self) -> None:
        if self._mutex_handle is not None:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            try:
                self._lock_path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
