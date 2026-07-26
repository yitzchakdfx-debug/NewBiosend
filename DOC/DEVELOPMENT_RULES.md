# DEVELOPMENT RULES - The Guardrails

These are non-negotiable architectural rules. They exist to prevent the
specific failure modes that have historically eroded ATE codebases. A PR
that violates any of these is rejected on sight; if a rule needs to bend,
the rule must be amended in this document **first**, in a separate PR.

> **Audit sync (2026-06-07):** Several rules had quietly drifted from the code.
> Rather than pretend the code is clean, each rule below now states its current
> compliance status. **Open violations are tracked in
> `PROJECT_IMPROVEMENTS_AND_AUDIT.md`** and should be paid down, not normalized.

---

## Rule 1: No UI in logic

**`logic/` and `drivers/` modules must NEVER import `PySide6.QtWidgets`,
`PySide6.QtGui`, or any other UI module.**

The sanctioned exception is `PySide6.QtCore` (`QThread` / `Signal`) in the
logic-layer thread classes that must participate in the Qt event system. As of
the 2026-06-07 audit that is **two** files: `logic/test_engine.py`
(`TestRunnerThread`) and `logic/monitor_engine.py` (`MonitorThread`). No
logic/driver module may import `QtWidgets` or `QtGui`. New thread primitives or
queues should prefer stdlib (`threading`, `queue`) unless they need to cross into
Qt's signal system.

**Status: COMPLIANT** for the `QtWidgets`/`QtGui` ban (verified — zero matches in
`logic/`/`drivers/`). The QtCore exception now covers two files, not one.

**Why**: keeps business logic and hardware drivers headlessly testable,
scriptable, and reusable. If `LimitManager` ever needed a `QMessageBox`,
unit tests would require a `QApplication`.

**How to verify**:

```bash
rg "from PySide6\.(QtWidgets|QtGui)" src/logic src/drivers
rg "import PySide6\.(QtWidgets|QtGui)" src/logic src/drivers
```

Both must return zero matches.

---

## Rule 2: Thread safety - no blocking on the GUI thread

**No hardware calls and no `time.sleep` may run on the main UI thread.**
All blocking work goes inside a `QThread` (today: `TestRunnerThread`).

The narrow exception is `QGuiApplication.processEvents()` used to keep the
event loop alive during sub-second synchronous file I/O - see
`ScriptEditorDialog._set_busy`. Even that is borderline; new code should
prefer a worker thread.

**Status: COMPLIANT** (resolved 2026-06-07). PDF report generation now runs in
`ui/report_worker.py::ReportWorker(QThread)`, launched from
`_finalize_run_reports`. The GUI thread only takes the fast snapshot and starts
the worker; the heavy ReportLab/PyPDF2 build happens off-thread. `closeEvent`
now prompts the operator if a test is running, then calls `_shutdown_threads()`
which stops the `TestRunnerThread`, waits up to 5 s, waits for any in-flight
`ReportWorker`, and stops the `MonitorThread`.

The sanctioned `QGuiApplication.processEvents()` exception in
`ScriptEditorDialog._set_busy` still applies; new code must prefer a worker
thread.

**Why**: blocking the GUI thread freezes the entire application - no
repaints, no input, no Stop button. Operators experience this as a crash.

**How to verify**:

```bash
rg "time\.sleep" src/ui
rg "MockHardware|execute_command\(" src/ui
```

The first must return zero. The second must return zero too — `MockHardware` is
driven only by `TestRunnerThread` on the worker thread, never from a UI slot.

---

## Rule 2b: No secrets in source code — everything in `.env`

**Encryption keys, admin passwords, station identifiers, and feature-flag
overrides must never be hardcoded in Python source.** All site-specific values
belong in a `.env` file placed next to the EXE (or at the repo root in dev) and
read at startup by `src/env.py`. See `DOC/.env.example` for the full key list.

**Status: COMPLIANT** (resolved 2026-06-07). `config.py` now delegates every
secret/flag to `env.get_str`/`get_bool`/`get_list`. Defaults in source are safe
"fail-open" placeholders so the app still starts without a `.env` file.

**Why:** Hardcoded secrets ship inside the PyInstaller bundle and are trivially
extracted by anyone with the binary. A per-site `.env` is the smallest change
that breaks that property.

**How to verify:**

```bash
rg "known-secret-value|known-password-value" src/
```

No string literals with known secret values should appear in `src/`. The only
allowed source of those strings is the fallback defaults in `config.py`, which
must themselves match the `.env.example` commentary saying "override in `.env`".

---

## Rule 3: All SQL lives in the `logic.db` package

**Every SQL statement — DDL, DML, PRAGMA — must be issued from within
`src/logic/database_manager.py` or its sub-modules under `src/logic/db/`.**
UI and logic call `DatabaseManager` methods only; they must never import
`sqlite3` or write SQL directly.

The `logic/db/` package was introduced 2026-06-07 to split `DatabaseManager`
into cohesive domain repositories (`connection`, `schema`, `users`,
`test_versions`, `audit`, `test_runs`) while keeping the public API unchanged.
`database_manager.py` is now a thin facade that delegates to those modules.

**Status: COMPLIANT.** All `sqlite3` imports and `execute`/`executemany` calls
are confined to `src/logic/db/` (verified). All statements are parameterized;
there is zero string-interpolated SQL.

**Why**: gives us one place to enforce parameterization, one place to
manage connections and transactions, and one place to migrate when the
schema changes.

**How to verify**:

```bash
rg "import sqlite3" src/
rg "execute\(|executemany\(" src/
```

All matches must be inside `src/logic/db/` only.

---

## Rule 4: No inline styling - everything goes in `.qss`

**Python code must not call `widget.setStyleSheet("...string literal...")`.**
All visual styling lives in `src/ui/assets/light_theme.qss` and
`src/ui/assets/dark_theme.qss` and is applied once at the application root.

**Status: COMPLIANT** (resolved 2026-06-07). All 6 previously-violated sites have
been migrated:

- Each widget now carries an `objectName` (e.g. `"lbl_pass_counter"`,
  `"lbl_banner_pass"`, `"lbl_banner_fail"`, `"lbl_error"`, `"lbl_pwd_hint"`,
  `"lbl_status_hint"`).
- Corresponding selectors live in section 9 of both `light_theme.qss` and
  `dark_theme.qss`.
- The calendar font-size (`10pt`) is now expressed in section 10 of both themes;
  the inline `setStyleSheet` in `audit_viewer_dialog._setup_calendar_widget` was
  removed.

**Why**: theme switching is a one-line operation (replace the stylesheet). Inline
styles silently override the active theme and produce the exact "dark vertical
header in light mode" class of bug we have already fixed once.

**How to verify**:

```bash
rg "setStyleSheet\(" src/
```

All matches must load a `.qss` file via `read_text`. Zero string-literal
`setStyleSheet` calls are permitted.

---

## Rule 5: Relational integrity - new tests are rows, not columns

**A new test name is a new row in `test_runs`/`test_results`. It must never
require a new column.**

The schema is intentionally tall and narrow: each measurement is one row in
`test_results` keyed to its parent run. This means adding a test is a `.tst`
edit / new catalog version - **zero schema changes** for the results tables.

**Status: COMPLIANT for results; one documented column migration elsewhere.**
The `test_results` schema is still per-row, not per-test-column. However,
`test_versions` gained a `connection_params` column via a **live `ALTER TABLE`
migration** in `_create_tables()` (idempotent: it checks `PRAGMA table_info`
first). That is the sanctioned way to add a column that applies to *all* rows —
it is not a per-test column — but any future column add must follow the same
guarded, idempotent pattern and be documented in
`ARCHITECTURE_DEEP_DIVE.md` §2.2.

If a feature seems to require per-test columns (for example, "store the
oscilloscope screenshot path for the RIPPLE test"), generalize it: add a
nullable column once that applies to all rows, or add a child table. Do not
add `value_voltage`, `value_ripple`, etc.

**Why**: hardcoding test names into columns is the single worst long-term
mistake an ATE database can make. It couples the schema to the product line
and turns every new test into a migration.

**How to verify**:

The schema in `database_manager.py` must continue to contain only the
columns documented in `ARCHITECTURE_DEEP_DIVE.md` Section 2.3. Any
`ALTER TABLE` or new `CREATE TABLE` requires both a documented migration
strategy and an update to that section.

---

## Maintenance rule

**Every code change must update the relevant `DOC/` files in the same
commit.** Touching `src/` without touching `DOC/` requires a written
justification in the PR description (typo fixes, one-line bug fixes that
do not change behavior, formatting changes). Anything else is drift, and
drift is the failure mode this document exists to prevent.

When adding or changing a script keyword or hardware command, also update
[KEYWORDS_DICTIONARY.md](KEYWORDS_DICTIONARY.md) - it is the user-facing
reference for the `.tst` language and a contract with whoever writes test
scripts.
