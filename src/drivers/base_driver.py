"""Abstract hardware-driver contract and error hierarchy."""
from __future__ import annotations

from abc import ABC, abstractmethod


class HardwareError(Exception):
    """Base class for all hardware/driver faults."""


class ConnectionLostError(HardwareError):
    """Raised when the link to an instrument drops mid-session."""


class CommandTimeoutError(HardwareError):
    """Raised when an instrument does not respond in time."""


class UnknownCommandError(HardwareError):
    """Raised when a script issues a command the driver does not implement."""


class BaseDriver(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def execute_command(self, command: str, args: list[str]) -> float: ...

    @property
    @abstractmethod
    def measurement_commands(self) -> frozenset[str]: ...
