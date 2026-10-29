"""Free the 3300G's LAN port after a cable pull, without a power cycle.

The mainframe's LAN controller (Metavert, 2007 firmware) accepts ONE TCP
session and never notices that the PC side is gone. Pull the cable mid-session
and Windows drops its socket at once, but the RST never reaches the device:
after the replug it keeps the dead session and ignores every new connect to
port 4001 — until a power cycle.

A connect bound to the SAME local port as the dead session gets through:
the device takes the SYN as belonging to that session's address pair, drops
it and accepts. Measured on the station (2026-10-29): plain connect timed out,
the same-port connect succeeded, and after closing it a normal connect worked.

So every session's local port is remembered (in memory and on disk, so an app
restart can still free it), and a failed connect first "kicks" from that port.
Best-effort throughout: nothing here raises.
"""

from __future__ import annotations

import json
import re
import socket
import struct
import threading
import time

_LOCK = threading.Lock()
_SESSIONS: dict[str, int] = {}
_LOADED = False
_KICK_TIMEOUT_S = 3.0
_SETTLE_S = 0.5


def tcp_target(resource: str) -> tuple[str, int] | None:
    """(host, port) of a `TCPIP0::host::port::SOCKET` VISA resource, else None."""
    match = re.match(r"^\s*TCPIP\d*::([^:]+)::(\d+)::SOCKET", resource or "", re.IGNORECASE)
    return (match.group(1), int(match.group(2))) if match else None


def _store_path():
    from paths import user_data_path

    return user_data_path("load_link.json")


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _SESSIONS.update({str(k): int(v) for k, v in data.items()})
    except Exception:
        pass


def remember_session(host: str, port: int, local_port: int) -> None:
    """Record the local port of the session just opened to host:port."""
    key = f"{host}:{port}"
    with _LOCK:
        _load()
        if _SESSIONS.get(key) == local_port:
            return
        _SESSIONS[key] = int(local_port)
        try:
            path = _store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_SESSIONS), encoding="utf-8")
        except Exception:
            pass


def remembered_port(host: str, port: int) -> int | None:
    with _LOCK:
        _load()
        return _SESSIONS.get(f"{host}:{port}")


def remember_from_socket(host: str, port: int, sock: socket.socket) -> None:
    try:
        remember_session(host, port, sock.getsockname()[1])
    except Exception:
        pass


def _rst_close(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def kick_stale_session(host: str, port: int) -> bool:
    """Connect from the remembered local port, then RST-close. True if attempted.

    "Attempted" rather than "succeeded": the caller should retry its normal
    connect either way, as the device may drop the stale session even when
    this connect itself does not complete.
    """
    local_port = remembered_port(host, port)
    if not local_port:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", local_port))
        sock.settimeout(_KICK_TIMEOUT_S)
        sock.connect((host, port))
    except OSError:
        pass
    finally:
        _rst_close(sock)
    time.sleep(_SETTLE_S)
    return True
