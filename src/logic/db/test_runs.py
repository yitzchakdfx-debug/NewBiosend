"""Test-run and test-result persistence."""

from __future__ import annotations

from pathlib import Path

from logic.db.connection import open_conn
from logic.models import TestRunRecord


def save_run(db_path: Path, record: TestRunRecord) -> int:
    with open_conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO test_runs "
            "(operator, part_number, serial_number, overall_passed, start_time, end_time) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (
                record.operator,
                record.part_number,
                record.serial_number,
                int(record.overall_passed),
                record.start_time.isoformat(),
                record.end_time.isoformat() if record.end_time else None,
            ),
        )
        run_id = int(cur.lastrowid)
        cur.executemany(
            "INSERT INTO test_results "
            "(run_id, test_name, value, min_val, max_val, unit, passed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            [
                (run_id, r["test_name"], r["value"], r["min"],
                 r["max"], r["unit"], int(r["passed"]))
                for r in record.results
            ],
        )
        conn.commit()
        return run_id
