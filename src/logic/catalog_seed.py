"""First start: put the product test into the empty catalog.

A fresh install had an empty catalog, so an Operator saw "No test" until an
Admin imported `biosense_test.tst` by hand. Done once, only into an empty
catalog: an Admin who deletes versions later is not overruled.
"""

from __future__ import annotations

from logic.database_manager import DatabaseManager
from paths import resource_path, user_data_path

SEED_TEST_NAME = "biosense_test"
SEED_VERSION = "V1.0"
SEED_FILE = "biosense_test.tst"


def seed_catalog_if_empty() -> bool:
    """Add `biosense_test V1.0` if the catalog has no version at all. True if added."""
    db = DatabaseManager()
    if db.list_test_versions():
        return False
    source = user_data_path(SEED_FILE)
    if not source.is_file():
        source = resource_path("data", SEED_FILE)
    if not source.is_file():
        return False
    from config import UUT_TYPES

    uut_type = UUT_TYPES[0] if UUT_TYPES else "EM-5406-00"
    db.add_test_version(
        SEED_TEST_NAME, uut_type, SEED_VERSION, source.read_text(encoding="utf-8"), "system"
    )
    try:
        db.log_audit_action(
            "Catalog seeded",
            username="system",
            details=f"{SEED_TEST_NAME} {SEED_VERSION} added on first start",
        )
    except Exception:
        pass
    return True
