"""Toolbar indicator: is there communication with the electronic load?"""

from __future__ import annotations

import re
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
    "unknown": "Load: not checked",
    "sim": "Simulator",
}


def host_from_visa(resource: str) -> str:
    """IP/host of a `TCPIP0::192.168.16.123::4001::SOCKET` resource, else ""."""
    match = re.match(r"^\s*TCPIP\d*::([^:]+)::", resource or "", re.IGNORECASE)
    return match.group(1) if match else ""


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
    """Pings a host on a background thread; `result(bool)` arrives in the GUI thread.

    ICMP only: the 3300G accepts a single TCP connection, and a probe that
    opened one between runs could block the channel scan or the next run.
    """

    result = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False

    def probe(self, host: str) -> None:
        if self._busy or not host:
            return
        self._busy = True
        threading.Thread(target=self._run, args=(host,), daemon=True).start()

    def _run(self, host: str) -> None:
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
        finally:
            self._busy = False
        self.result.emit(ok)
