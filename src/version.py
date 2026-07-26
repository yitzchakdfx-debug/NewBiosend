"""Single source of truth for the application version (overridable via .env)."""
from __future__ import annotations

from env import get_str

__version__ = get_str("APP_VERSION", "0.1.0-Beta")
