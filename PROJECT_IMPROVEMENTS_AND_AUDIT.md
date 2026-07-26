# PROJECT IMPROVEMENTS & AUDIT — DFX_ate

**Auditor role:** external senior code auditor / software architect (first look).
**Date:** 2026-06-07
**Scope:** `src/` (entry, `logic/`, `drivers/`, `ui/`), build artifacts, and the
`DOC/` reference set. `venv/` is excluded. ~6,000 lines of Python across 38
source files.
**Method:** full read of every source file; targeted `rg` verification of every
claim below. Findings are evidence-based, not assumed.

> The companion task — re-syncing the `DOC/` folder with the code — has already
> been done; this report is the separate, critical assessment requested. Nothing
> here has been applied to the application; these are recommendations.

---

## 0. Executive summary

DFX_ate is a capable, feature-rich PySide6 ATE front-end. The threading
discipline around the test runner is genuinely good, the SQL is uniformly
parameterized, and the script grammar/parser is clean and well-validated. The
project has clearly outgrown its original design, and that growth has introduced
real risk in four areas:

1. **Security theater around secrets.** Log encryption and Admin-PDF passwords
   are hardcoded constants in `config.py`; the password-strength validator is
   never called; the seeded admin password is weak and committed.
2. **A god-object `MainWindow`** (~1,235 lines) tightly coupled to widget
   internals, report internals, and the database — the dominant architectural
   debt.
3. **Stability gaps under failure**: a Windows-incorrect single-instance lock, a
   broken-on-import dead `BaseDriver`, no driver connection lifecycle, and
   synchronous PDF generation on the GUI thread.
4. **Drift and dead code**: `LimitManager`, `base_driver.py`, `main_window.ui`,
   `validate_password_strength`, `MockHardware.connect()` are all unused or
   broken; the build spec excludes a module the app imports.

Priority order for remediation is in [§6](#6-prioritized-remediation-plan).

Severity legend: 🔴 critical · 🟠 high · 🟡 medium · 🟢 low/cleanup.

---

## 1. OOP & Class Design

### 1.1 🟠 `MainWindow` is a god object

**Location & Description.** `src/ui/views/main_window.py` (~1,235 lines, one
class). It is simultaneously: layout builder, theme manager, role/permission
gate, pre-test flow controller, runner lifecycle owner, trace-log formatter,
results-table renderer, report exporter, audit logger, and temp-file janitor. It
also reaches *into* other objects' internals — it both reads and **mutates**
`ControlPanelWidget` widgets (`self.control_panel.edit_part_number.setReadOnly(...)`,
`...chk_save_log.setVisible(...)`, `...spin_loops.value()`), and calls the
"private" `ReportGenerator._resolved_archive_paths(...)` from
`export_results_csv`/`export_results_pdf` ([main_window.py:313](src/ui/views/main_window.py#L313)).

**Architectural Risk.** Every new feature lands here, so merge conflicts and
regressions concentrate in one file. The tight coupling to `ControlPanelWidget`'s
public attribute names means the widget cannot be restyled or refactored without
touching the window. Reaching into `ReportGenerator` privates means the report
module cannot change its archive-path strategy without breaking the window.
Untestable without a full `QApplication`.

**Proposed Resolution.** Extract cohesive controllers and a presenter, and give
`ControlPanelWidget` an intention-revealing API instead of exposing raw widgets:

```python
# control_panel.py — expose behavior, not widgets
class ControlPanelWidget(QWidget):
    def set_unit_fields_locked(self, locked: bool) -> None: ...
    def loop_count(self) -> int:
        return self.spin_loops.value() if self.chk_loops.isChecked() else 1
    def run_options(self) -> RunOptions:           # dataclass, not 6 getters
        return RunOptions(self.loop_count(), self.chk_stop_on_fail.isChecked())

# split MainWindow responsibilities
class RunController:      # owns TestRunnerThread lifecycle + signal wiring
class ReportController:   # owns export/archive (talks to ReportGenerator's public API)
class RolePolicy:         # single source of truth for what each role may see/do
```

`ReportGenerator` should expose a public `suggested_export_path(meta, role, ext)`
so callers stop using `_resolved_archive_paths`.

---

### 1.2 ✅ ADDRESSED (2026-06-07) — `DatabaseManager` is a fat multi-domain repository

**Location & Description.** `src/logic/database_manager.py` (511 lines) owns four
unrelated domains: test runs/results, user/RBAC, the test-version catalog, and
the audit trail. `DEVELOPMENT_RULES.md` Rule 3 ("all SQL in one file") actively
pushes toward this shape, so it is a *deliberate* tension rather than an
accident.

**Architectural Risk.** SRP violation: a change to audit logging recompiles the
same module that owns authentication. The class is hard to reason about as one
unit, and `_create_tables()` couples five schemas into one bootstrap.

**Proposed Resolution.** Keep "all SQL in the data layer" but split by aggregate
behind a thin connection provider, so each repository is small and Rule 3 still
holds at the *package* level:

```python
class _Db:                       # owns _connect(), PRAGMA, path
    ...
class RunRepository(_Db): ...    # save_run, history queries
class UserRepository(_Db): ...   # verify_login, create/update/delete users
class VersionRepository(_Db): ...
class AuditRepository(_Db): ...
```

Amend Rule 3 to "all SQL lives in the `logic.db` package" so the split is legal.

---

### 1.3 ✅ ADDRESSED (2026-06-07) — `BaseDriver` is dead and import-broken; the driver abstraction is fiction

**Location & Description.** `src/drivers/base_driver.py:8` does
`from logic.models import TestOutcome` at module top level. **`TestOutcome` does
not exist** in `logic/models.py` (verified), so importing this module raises
`ImportError`. It is imported by nothing (verified), so it never executes — it is
pure dead weight. Its interface (`connect`/`disconnect`/`execute_test(config) ->
(TestOutcome, str, dict)`) does **not** match the real driver,
`MockHardware.execute_command(name, args) -> float`. `MockHardware` does not
subclass `BaseDriver`.

**Architectural Risk.** The README states the repo is a "Core Baseline
Framework" from which hardware-specific versions are derived. The single most
important seam for that — the driver contract — is broken and misleading. A new
engineer wiring a real instrument would start from a class that cannot be
imported and whose shape contradicts the runner.

**Proposed Resolution.** Make the abstraction real and have `MockHardware`
implement it:

```python
# drivers/base_driver.py
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

class MockHardware(BaseDriver): ...
```

`TestRunnerThread` should take a `BaseDriver` in its constructor (dependency
injection) instead of hardcoding `MockHardware()`, which also makes the runner
unit-testable with a stub driver.

---

### 1.4 ✅ ADDRESSED (2026-06-07) — Dead / unused members that should be removed or wired

- `logic/limit_manager.py` (`LimitManager`) — imported nowhere; `limits.json`
  keys match no current test names.
- `src/ui/views/main_window.ui` — Qt Designer file; never loaded (`loadUi`/
  `QUiLoader` appear nowhere).
- `AuthManager.validate_password_strength()` — defined, never called (see
  [§4.3](#43--password-strength-validator-is-never-invoked)).
- `MockHardware.connect()` / `disconnect()` — never called; there is no
  connection lifecycle in the run path.
- `data/secure_system.log` — stale plaintext artifact superseded by
  `logs/sys_*.dat`.

**Risk.** Dead code is read as live by maintainers and rots into traps (1.3 is
the worst case). **Resolution.** Delete, or — for `LimitManager`/`connect()` —
wire them in deliberately and document the intent.

---

## 2. Code Duplication & Module Boundaries

### 2.1 ✅ ADDRESSED (2026-06-07) — Filename/path sanitization duplicated

**Location.** `report_generator.sanitize_path_segment()`
([report_generator.py:28](src/logic/report_generator.py#L28)) and the inline
`_sanitize()` nested in `MainWindow._default_export_basename()`
([main_window.py:269](src/ui/views/main_window.py#L269)) implement the same
regex pair (`\s+`→`_`, strip `\/:*?"<>|`).

**Risk.** Two copies drift; export filenames and archive folders can diverge.
**Resolution.** Keep the one in `report_generator`, export it, and import it in
`main_window`.

### 2.2 ✅ ADDRESSED (2026-06-07) — Version-name validation duplicated three times

**Location.** The `invalid_chars = r'\/:*?"<>|'` check appears in
`sequence_editor_dialog._on_save`, `version_manager_dialog._update_from_tst`, and
`version_manager_dialog._edit`. The "is this version unique?" + "non-empty?"
dance is likewise repeated.

**Risk.** Inconsistent validation rules across entry points; easy to fix one and
miss another. **Resolution.** A single helper, e.g.
`ScriptManager.validate_version_name(name) -> str | None`, reused by all callers.

### 2.3 ✅ ADDRESSED (2026-06-07) — Decrypted-log rendering duplicated

**Location.** `audit_viewer_dialog._decrypt_hardware_day` inlines the exact
record-to-line formatting that also lives in
`audit_viewer_dialog._format_records` ([audit_viewer_dialog.py:375](src/ui/views/audit_viewer_dialog.py#L375)).
`_format_records` is used by `_load_manual_file` only; `_decrypt_hardware_day`
re-implements it.

**Risk.** Two formatters for one concept. **Resolution.** Call `_format_records`
from `_decrypt_hardware_day`.

### 2.4 ✅ ADDRESSED (2026-06-07) — Role normalization scattered

`str(...).strip().title()` to normalize a role appears in `MainWindow`,
`DatabaseManager._validate_role`, and `ReportGenerator`. Centralize as a `Role`
enum / `normalize_role()` helper so "Technician" vs "technician" vs a typo is
handled in exactly one place.

### 2.5 🟢 Boundary smell: logic depends on a presentation-flavored constant

`config.py` mixes pure feature flags (`SHOW_LIVE_MONITOR`) with secrets
(`LOG_ENCRYPTION_PASSWORD`, `ADMIN_REPORT_PASSWORD`) and report identity
(`TESTER_SERIAL_NUMBER`). Logic modules (`secure_logger`, `report_generator`)
import from a module whose name implies UI configuration. Split into
`config.py` (flags) and a secrets/secure-config source (see [§4.1](#41--hardcoded-secrets-for-encryption-and-pdf-protection)).

---

## 3. Multithreading & Stability

### 3.1 ✅ ADDRESSED (2026-06-07) — PDF report generation blocks the GUI thread

**Location.** `MainWindow.on_tests_finished` → `_finalize_run_reports` →
`ReportGenerator.generate_pdf_auto_archive` ([main_window.py:744](src/ui/views/main_window.py#L744)).
This runs ReportLab document build + PyPDF2 page-merge watermark + (for Admin)
encryption, **synchronously on the GUI thread**, right after every run.

**Architectural Risk.** Directly against the project's headline rule ("the GUI
thread never blocks"). For a long multi-loop run the results table is large and
the PDF build is non-trivial; the window will freeze for the duration. This is
exactly the "operators experience this as a crash" failure `DEVELOPMENT_RULES.md`
Rule 2 warns about.

**Proposed Resolution.** Move report generation to a short-lived `QThread`/worker
(or `QThreadPool` task) and re-enable the UI on its `finished` signal:

```python
class _ReportWorker(QThread):
    done = Signal(object)        # Path or Exception
    def __init__(self, meta, rows, role): ...
    def run(self):
        try: self.done.emit(ReportGenerator().generate_pdf_auto_archive(...))
        except Exception as e: self.done.emit(e)
```

### 3.2 ✅ ADDRESSED (2026-06-07) — `closeEvent` does not stop a running test thread

**Location.** `MainWindow.closeEvent` ([main_window.py:1105](src/ui/views/main_window.py#L1105))
stops `monitor_thread` but never the active `TestRunnerThread`.

**Architectural Risk.** Closing (or being closed by app exit) mid-run leaves the
worker alive, still calling `time.sleep`/`msleep` and emitting queued signals at
a window being torn down (the logout path even sets `WA_DeleteOnClose`). Possible
"QThread: Destroyed while thread is still running" aborts and use-after-free of
C++ widget objects. Partial run state may also be saved at an unexpected moment.

**Proposed Resolution.** In `closeEvent`: if a run is active, `stop()` it and
`wait(timeout)`; optionally prompt the operator to confirm aborting a live test.

```python
def closeEvent(self, event):
    if self.test_thread and self.test_thread.isRunning():
        self.test_thread.stop()
        if not self.test_thread.wait(5000):
            ...  # last-resort handling / warn
    ...
```

### 3.3 ✅ ADDRESSED (2026-06-07) — Runner reference dropped without `wait()`/`deleteLater()`

**Location.** `on_tests_finished` sets `self.test_thread = None`
([main_window.py:1215](src/ui/views/main_window.py#L1215)) immediately after the
`finished` slot, and reads thread internals (`th.report_snapshot()`,
`th.resume_pause()`) from the GUI thread in the same handler.

**Risk.** `finished` is emitted *inside* `run()`'s `finally:` before `run()`
returns, so the underlying QThread may not be fully finished when the last Python
reference is dropped → GC → `QThread` destructor races the still-unwinding
thread. Reading `_run_record` post-hoc is currently safe only because there is no
concurrent writer.

**Resolution.** Connect `finished` to `deleteLater`, keep the reference until
then, and prefer passing the final snapshot *through* a signal payload rather
than reaching back into the thread object after completion.

### 3.4 ✅ ADDRESSED (2026-06-07) — `DatabaseManager()` constructed constantly; full DDL each time

**Location.** `MainWindow` builds a new `DatabaseManager()` for nearly every
action — `log_audit_action` on login/start/finish/logout, `SelectTestDialog`,
`SequenceEditorDialog`, etc. Each construction runs `_create_tables()`:
`CREATE TABLE IF NOT EXISTS ×5`, a `PRAGMA table_info` probe, a conditional
`ALTER TABLE`, and the admin `INSERT OR IGNORE` ([database_manager.py:32](src/logic/database_manager.py#L32)).

**Risk.** Wasteful (repeated DDL + a PBKDF2-salted insert attempt on every
object), and it muddies "when is the schema created?" There is also a
thread-affinity subtlety: the object is built on the GUI thread but
`TestRunnerThread` calls `save_run` on the worker thread — currently safe only
because every method opens its own connection via `_connect()`.

**Resolution.** Construct one `DatabaseManager` at app start, inject it where
needed, and split bootstrap (`ensure_schema()` run once) from normal use. Keep
the per-call connection pattern (it is what makes cross-thread use safe), but
stop re-running DDL.

### 3.5 🟢 Prompt/stop event handling is correct — note it stays that way

The `Prompt` handshake (`_prompt_event.clear()` *before* `emit`, `wait()` after;
`stop()` sets the event) has no lost-wakeup bug, and `_pause_event` defaults to
*set*. This is good — call it out so a future refactor preserves the ordering.
One minor note: pause is honored only at step boundaries, not between commands
inside a long step; document that as intended or tighten it.

---

## 4. Security & Memory / Hardware Operations

### 4.1 ✅ ADDRESSED (2026-06-07) — Hardcoded secrets for encryption and PDF protection

**Location.** `config.py` previously contained hardcoded log and report password
constants. The Fernet key for the "encrypted" system logs is derived
(PBKDF2, fixed salt `b"DFX-ATE-SALT-v1"`) from that secret
([secure_logger.py:18](src/logic/secure_logger.py#L18)); Admin PDF encryption
uses the other constant ([report_generator.py:391](src/logic/report_generator.py#L391)).
The Audit Viewer also gates TXT export by comparing the typed password to
`LOG_ENCRYPTION_PASSWORD` in plaintext ([audit_viewer_dialog.py:292](src/ui/views/audit_viewer_dialog.py#L292)).

**Architectural Risk.** Anyone with the source or the unpacked bundle can decrypt
every system log and open every Admin report. The salt is also fixed, so the
derived key is identical on every install — logs are cross-install decryptable.
This is **obfuscation, not encryption**, and the docs/README present it as a
security feature. PyArmor (planned) does not fix it.

**Proposed Resolution.** Derive keys from a real secret the attacker does not
ship with the binary: a machine-bound key (Windows DPAPI via `ctypes`/`pywin32`),
or an operator-supplied passphrase entered at unlock time, or an OS keyring
entry. At minimum, use a **per-install random salt** stored alongside the data
dir, and never compare secrets with `!=` (use `secrets.compare_digest`). Treat
the current scheme as tamper-evidence only and stop describing it as
confidentiality.

### 4.2 🚫 WON'T FIX (by design) — Weak, committed default admin password; no forced rotation

> **Factory-floor decision (2026-06-07):** Shared stations with rotating shifts
> intentionally use simple passwords (e.g. "123") with no forced rotation or
> strength enforcement. RBAC is considered sufficient to protect test modifications.
> The default admin credential is now sourced from `.env` (4.1 fix), which is the
> appropriate mechanism to change it per-site. Password policy enforcement is out
> of scope.

**Location.** `DatabaseManager._ensure_initial_admin` previously seeded Admin
with a committed default password ([database_manager.py:116](src/logic/database_manager.py#L116)).
There is **no `must_change_pwd` column or flow** anywhere (verified) — the
ARCHITECTURE doc previously described a change-password lifecycle that does not
exist.

**Architectural Risk.** Every deployed station ships with a known admin
credential and no mechanism that forces a change. On a production floor this is a
full RBAC bypass.

**Proposed Resolution.** Generate a random temporary password at seed time, print
it once to a first-run console/file, and add a genuine first-login forced-change
flow (the column + a `ChangePasswordDialog`, which the docs already assumed
existed). Enforce strength on that change (see §4.3).

### 4.3 🚫 WON'T FIX (by design) — Password-strength validator is never invoked

> **Factory-floor decision (2026-06-07):** Same rationale as 4.2 — no password
> policy enforcement. The validator remains in the codebase but is not wired.

**Location.** `AuthManager.validate_password_strength()` exists
([auth_manager.py:22](src/logic/auth_manager.py#L22)) but is called from
nowhere. `UserEditDialog._submit` ([user_management_dialog.py:96](src/ui/views/user_management_dialog.py#L96))
creates/updates users after only a non-empty + confirmation check.

**Architectural Risk.** There is effectively no password policy — a one-character
password is accepted for any user, including Admins.

**Proposed Resolution.** Call `validate_password_strength` in `create_user`/
`update_user` (server-side, in `DatabaseManager`/`AuthManager`, not just the
dialog) and surface the returned message; reject on failure.

### 4.4 ✅ ADDRESSED (2026-06-07) — Single-instance lock uses POSIX `os.kill(pid, 0)` on Windows

**Location.** `SingleInstanceLock._is_pid_alive` ([file_lock.py:18](src/logic/file_lock.py#L18))
calls `os.kill(pid, 0)` to decide whether a stale lock's owner is alive.

**Architectural Risk.** On Windows (the documented **primary OS**), `os.kill`
does not implement the POSIX "signal 0 = liveness probe" semantics. Python maps
`sig == 0` to `CTRL_C_EVENT` and routes it through `GenerateConsoleCtrlEvent`
rather than performing a no-op existence check. The practical effects range from
"the probe raises and every existing lock is treated as stale" (defeating the
single-instance guarantee and letting a second instance delete the lock and
start) to delivering an unexpected console control event. Either way the
guarantee the lock exists to provide is unreliable on the target platform.

**Proposed Resolution.** Use a Windows-correct liveness check (e.g.
`OpenProcess`/`WaitForSingleObject` via `ctypes`, or `psutil.pid_exists`), or —
simpler and robust — hold an exclusive OS handle for the lifetime of the process
(keep the `O_EXCL` file open and never close it until exit; on Windows also
consider a named mutex via `ctypes.windll.kernel32.CreateMutexW` + check
`ERROR_ALREADY_EXISTS`). Guard the POSIX path behind `os.name != "nt"`.

### 4.5 ✅ ADDRESSED (2026-06-07) — No real hardware connection lifecycle / failure recovery

**Location.** The run path calls `MockHardware.execute_command` directly and
never opens/closes a session. `connect()`/`disconnect()` exist but are unused.
There is no timeout, no retry-on-I/O-error (distinct from the test-level
`Retry`), and no concept of "instrument disconnected mid-run."

**Architectural Risk.** The whole point of the framework is to derive
real-hardware variants. With a real driver, a dropped USB/serial link or a
GPIB timeout would surface as an arbitrary exception caught generically in
`_run_step` and recorded as a plain FAIL — indistinguishable from a real
out-of-spec measurement, with no reconnect attempt and no safe-state teardown
(e.g. forcing supplies to 0 V on abort).

**Proposed Resolution.** Bake a lifecycle into `BaseDriver`: `connect()` before
the sequence and `disconnect()`/safe-state in the runner's `finally:`. Define a
`HardwareError` hierarchy (`ConnectionLost`, `Timeout`) so the runner can
distinguish *measurement failed* from *instrument failed*, log them differently,
and optionally attempt one reconnect before aborting the run.

### 4.6 🟢 Input validation gaps in the mock command layer

**Location.** `MockHardware.execute_command` does `int(args[0])` for
`readchannel` with no arity/range check; side-effect commands ignore their args
entirely ([mock_hardware.py:44](src/drivers/mock_hardware.py#L44)). `setvoltage`
accepts and discards its voltage.

**Risk.** Low today (errors are caught and marked FAIL), but a real driver copied
from this template would inherit the "trust the args" habit. **Resolution.**
Validate arity/types/ranges at the driver boundary and raise typed errors; the
parser already validates *keywords* but not *command arguments*.

### 4.7 ✅ ADDRESSED (2026-06-07) — Operator temp-file lifecycle leaks on crash

`SelectTestDialog` writes the chosen catalog version to a `tempfile.mkstemp`
`.tst`; `MainWindow` cleans it on next load / logout / close. On a hard crash the
temp file is orphaned in `%TEMP%`. Low impact; consider a `data/tmp/` you sweep
on startup, or `tempfile` with a registered `atexit` cleanup.

---

## 5. Documentation Drift (corrected during this engagement)

For completeness — these were the concrete code/doc mismatches found and fixed in
`DOC/` as part of Task 1:

| # | Doc claim (before) | Code reality |
| - | ------------------ | ------------ |
| D1 | Roles are Operator / **Engineer** / Admin | Operator / **Technician** / Admin (`CHECK` constraint + UI) |
| D2 | `must_change_pwd` column + `ChangePasswordDialog` boot gate | Neither exists anywhere |
| D3 | Only dependency is PySide6 | Also reportlab, PyPDF2, cryptography, pyserial (+ Pillow transitively) |
| D4 | Schema = `test_runs` + `test_results` (+ `users`) | Also `test_versions` and `audit_logs`; `connection_params` added via `ALTER TABLE` |
| D5 | Signal map of 8 runner signals | Missing `loop_started`; `MonitorThread.values_updated` undocumented |
| D6 | "Only `test_engine.py` uses QtCore" | `monitor_engine.py` is a second QtCore/QThread user |
| D7 | `style.qss` "not loaded by the running app" | Loaded by `LoginDialog` |
| D8 | Manifest lists `change_password_dialog.py`, omits ~15 real files | File does not exist; 15 files (config, paths, version, secure_logger, monitor_engine, report_generator, ui_helpers, instrument_panel, result_row_delegate, pre_test/select_test/sequence_editor/test_result/version_manager/audit_viewer/connection_settings dialogs) were undocumented |
| D9 | Rule 4 "one inline-style exception" | 6+ inline-style sites |
| D10 | Deployment "not wired into the repo yet" | `DFX_Tester.spec` + `build.ps1` are wired (PyArmor still not) |

The README (outside `DOC/`, currently modified in the working tree) still uses
the old role name **"Engineer"** and shows a redacted default password — worth
aligning to "Technician" / `.env` default-admin semantics in a follow-up.

---

## 6. Prioritized remediation plan

**🔴 Do first (correctness / security):**
1. ✅ §1.3 Fix or delete `base_driver.py` and make `MockHardware` implement the real
   contract. **Done — real ABC + error hierarchy + `MockHardware(BaseDriver)`.**
2. ✅ §4.4 Replace the Windows-incorrect `os.kill(pid, 0)` liveness check.
   **Done — named Windows mutex.**
3. ✅ §3.2 Stop the running `TestRunnerThread` in `closeEvent`.
   **Done — `_shutdown_threads()` + operator prompt.**
4. ✅ §4.1 Remove hardcoded secrets; move to `.env` via `env.py`.
   **Done.** §4.2 and §4.3 are Won't Fix (by design).

**🟠 Do next (stability / policy):**
5. ✅ §3.1 Move PDF generation off the GUI thread.
   **Done — `ReportWorker(QThread)` in `ui/report_worker.py`.**
6. 🚫 §4.3 Actually call `validate_password_strength`. **Won't Fix (by design).**
7. ✅ §4.5 Add a driver connection lifecycle + typed hardware errors.
   **Done — `connect()`/`disconnect()` lifecycle in runner; `HardwareError` hierarchy.**
8. §1.1 Begin decomposing `MainWindow` (start by giving `ControlPanelWidget` an
   intent API). **(Open — future work.)**

**🟡 / 🟢 Cleanup (debt reduction) — all addressed 2026-06-07:**
9. ✅ §3.4 Single injected `DatabaseManager`; bootstrap once. **Done — `_schema_ready` class flag.**
10. ✅ §2.x De-duplicate sanitization, version-name validation, log formatting, role
    normalization. **Done — `sanitize_path_segment` reused; `ScriptManager.validate_version_name`;
    `normalize_role()`; `_format_records` called from `_decrypt_hardware_day`.**
11. ✅ Migrate the inline `setStyleSheet` sites into the `.qss` themes.
    **Done — all 6 sites replaced with objectName selectors + QSS rules. Rule 4: COMPLIANT.**
12. ✅ §1.4 Delete dead code. **Done — `limit_manager.py`, `main_window.ui`, `secure_system.log` deleted;
    `"serial"` removed from spec excludes.**
13. ✅ Fix `DFX_Tester.spec`. **Done (see 12).**
14. ✅ §3.3 `deleteLater` for `TestRunnerThread`. **Done.**
15. ✅ §4.7 Temp-file lifecycle. **Done — `user_tmp_path()` + `_sweep_tmp()` + `SelectTestDialog` updated.**
16. ✅ §1.1 `ControlPanelWidget` intent API. **Done — `start_requested`/`stop_requested` signals,
    `set_running_state()` method, `ReportGenerator.suggested_export_path()` public.**
17. ✅ §1.2 `DatabaseManager` repository split. **Done — `logic/db/` package with `connection`,
    `schema`, `users`, `test_versions`, `audit`, `test_runs`; `database_manager.py` is a thin façade.**

---

## 7. What is already good (keep it)

- **Threading core**: cooperative cancel, the `Prompt` handshake, and the
  pause/stop event wiring are correct and race-free.
- **SQL hygiene**: 100% parameterized, single data-layer module, `PRAGMA
  foreign_keys = ON`, atomic `save_run` transaction.
- **Script parser**: line-precise `ScriptParseError`, mutually-exclusive
  `Limits`/`Target-Tol`, retry-only-reports-final semantics — clean and tested by
  construction.
- **Packaging judgment**: onedir-not-onefile to dodge AV heuristics, with
  `cryptography` hidden-imports forced — shows real deployment awareness.
- **Path abstraction** (`paths.py`): dev/frozen parity via `resource_path` /
  `user_data_path` is exactly right for a PyInstaller app.
- **RBAC plumbing & audit trail**: present, consistently logged, and fail-safe
  (audit writes never crash the flow).
