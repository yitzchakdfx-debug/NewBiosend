"""Single source of truth for the application version and name (version overridable via .env)."""
from __future__ import annotations

from env import get_str

__version__ = get_str("APP_VERSION", "0.1.0-Beta")

#: The product name shown in window titles, the login screen and About.
APP_TITLE = "EM-5406 Power Supply Load Tester"
