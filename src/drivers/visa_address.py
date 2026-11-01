"""Turn what an operator types as the load address into a VISA resource string.

pyvisa wants `TCPIP0::192.168.16.123::4001::SOCKET`. Only the IP changes from
station to station, so a plain IP (or IP:port) is accepted and completed here;
`COM3` becomes `ASRL3::INSTR`. Anything else - a full VISA string, a USB
resource - is returned unchanged, so existing settings keep working.
"""

from __future__ import annotations

import re

#: Port of the 3300G's LAN controller (overridable: PRODIGIT_LAN_PORT in .env).
DEFAULT_LAN_PORT = 4001

_IPV4 = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?::(\d{1,5}))?")


def _lan_port() -> int:
    try:
        from config import PRODIGIT_LAN_PORT

        return int(str(PRODIGIT_LAN_PORT).strip() or DEFAULT_LAN_PORT)
    except Exception:
        return DEFAULT_LAN_PORT


def normalize_resource(text: str) -> str:
    """`192.168.16.123` -> `TCPIP0::192.168.16.123::4001::SOCKET`; `COM3` -> `ASRL3::INSTR`."""
    value = (text or "").strip()
    com = re.fullmatch(r"COM(\d+)", value, re.IGNORECASE)
    if com:
        return f"ASRL{com.group(1)}::INSTR"
    ip = _IPV4.fullmatch(value)
    if ip and all(0 <= int(part) <= 255 for part in ip.groups()[:4]):
        host = ".".join(str(int(part)) for part in ip.groups()[:4])
        port = int(ip.group(5)) if ip.group(5) else _lan_port()
        if 0 < port < 65536:
            return f"TCPIP0::{host}::{port}::SOCKET"
    return value
