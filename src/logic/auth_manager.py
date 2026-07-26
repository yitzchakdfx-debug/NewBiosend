"""Authentication facade over DatabaseManager."""

from __future__ import annotations


from logic.database_manager import DatabaseManager


class AuthManager:
    """Login and password operations with lightweight validation."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self._db = db or DatabaseManager()

    def login(self, username: str, password: str) -> dict | None:
        return self._db.verify_login(username, password)

    def change_password(self, username: str, new_password: str) -> None:
        self._db.change_password(username, new_password)

    @staticmethod
    def validate_password_strength(password: str) -> str | None:
        if len(password) < 8:
            return "Password must be at least 8 characters."
        if not any(ch.isalpha() for ch in password):
            return "Password must include at least one letter."
        if not any(ch.isdigit() for ch in password):
            return "Password must include at least one digit."
        return None
