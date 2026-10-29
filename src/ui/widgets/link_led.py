"""Toolbar indicator: is there communication with the electronic load?"""

from __future__ import annotations

import re
import socket
import struct
import subprocess
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

#: Text shown for each state. Colours live in the .qss (`[state="..."]`).
_TEXT = {
    "ok": "Load connected",
    "reconnecting": "Load reconnecting...",
    "down": "No connection to load",
    # Ping answers but the control port does not: seen after a LAN cable was
    # pulled mid-run (2026-10-29); only a power cycle of the 3300G cleared it.
    "port": "Load not answering - switch the 3300G off and on",
    "unknown": "Load: not checked",
    "sim": "Simulator",
}


def host_from_visa(resource: str) -> str:
    """IP/host of a `TCPIP0::192.168.16.123::4001::SOCKET` resource, else ""."""
    match = re.match(r"^\s*TCPIP\d*::([^:]+)::", resource or "", re.IGNORECASE)
    return match.group(1) if match else ""


def port_from_visa(resource: str) -> int | None:
    """Port of a `TCPIP0::host::4001::SOCKET` resource, else None."""
    match = re.match(r"^\s*TCPIP\d*::[^:]+::(\d+)::SOCKET", resource or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


class LinkLed(QWidget):
    """A coloured dot and a short label: ok / reconnecting / down / unknown / sim."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._dot = QLabel()
        self._dot.setObjectName("link_led_dot")
        self._text = QLabel()
        self._text.setObjectName("link_led_text")
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self._state = ""
        self.set_state("unknown")

    def state(self) -> str:
        return self._state

    def set_state(self, state: str, detail: str = "") -> None:
        state = state if state in _TEXT else "unknown"
        self._state = state
        self._text.setText(_TEXT[state])
        tip = f"{detail}\n" if detail else ""
        self.setToolTip(f"{tip}Last update: {datetime.now():%H:%M:%S}")
        for widget in (self._dot, self._text):
            # A property change only restyles after a re-polish.
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class PingProbe(QObject):
    """Checks the load on a background thread; `result(state)` arrives in the GUI thread.

    State is "ok", "port" (ping answers, control port does not) or "down".
    Ping alone said "connected" while the 3300G's control port was dead after a
    pulled cable, so with a port the probe also opens a TCP connection and
    closes it at once with RST (SO_LINGER 0), which frees the instrument's
    single connection slot in ~0.1 s. The caller only probes between runs and
    when no dialog (e.g. the channel scan) may be using the port.
    """

    result = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False

    def probe(self, host: str, port: int | None = None) -> None:
        if self._busy or not host:
            return
        self._busy = True
        threading.Thread(target=self._run, args=(host, port), daemon=True).start()

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
        except OSError:
            return False
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        sock.close()
        return True

    def _run(self, host: str, port: int | None) -> None:
        state = "down"
        try:
            if self._ping(host):
                state = "ok" if port is None or self._port_open(host, port) else "port"
        finally:
            self._busy = False
        self.result.emit(state)

    @staticmethod
    def _ping(host: str) -> bool:
        ok = False
        try:
            windows = sys.platform.startswith("win")
            cmd = ["ping", "-n" if windows else "-c", "1", "-w" if windows else "-W", "1000" if windows else "1", host]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if windows else 0
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=flags)
            # Windows can exit 0 for "Destination host unreachable"; a real reply has a TTL.
            ok = done.returncode == 0 and "ttl=" in done.stdout.lower()
        except Exception:
            ok = False
        return ok
