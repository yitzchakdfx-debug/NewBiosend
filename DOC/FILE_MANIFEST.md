# FILE_MANIFEST - The Project Map

Authoritative inventory of every source artifact in the DFX_ate project. Update
this table whenever a file is added, renamed, removed, or has its responsibility
materially changed.

> Synced to the source tree on 2026-06-07 by an external code audit. Files that
> were previously undocumented are flagged **(NEW)**; files whose described
> behaviour no longer matches the code are flagged **(CHANGED)**; non-functional
> artifacts are flagged **(STALE/UNUSED)**.

## Entry point & top-level modules

| File Path                | Module | Short Description                                                                                                  |
| ------------------------ | ------ | ----------------------------------------------------------------------------------------------------------------- |
| `src/main.py`            | entry  | Sets the Windows AppUserModelID, seeds user data, acquires `SingleInstanceLock`, then runs a login → `MainWindow` loop that supports logout-and-switch-user. |
| `src/env.py`             | config | **(NEW)** `.env` loader (`load_env_once()`) + typed getters (`get_str`, `get_bool`, `get_list`). Loads once at import; degrades gracefully if `python-dotenv` is absent. |
| `src/config.py`          | config | Feature flags (`SHOW_LIVE_MONITOR`, `SHOW_SEARCH_BAR`), `UUT_TYPES` list, secrets, and station identity — all sourced from the environment via `env.py`. Zero hardcoded secrets. |
| `src/paths.py`           | config | Dev/frozen path resolution. `resource_path()` for bundled read-only files, `user_data_path()` for writable state, `user_tmp_path()` for scratch files swept on startup, `ensure_user_data_seeded()` to copy seed `.tst`/`limits.json` next to the EXE. |
| `src/version.py`         | config | Single source of truth for `__version__`; reads `APP_VERSION` from `.env` (default `"0.1.0-Beta"`). |

## `src/logic/` — Qt-free business logic (plus the two sanctioned QThreads)

| File Path                       | Short Description                                                                                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/logic/__init__.py`         | Package marker.                                                                                                                                    |
| `src/logic/models.py`           | Domain types: `TestLimit` (legacy, frozen slots), `TestStep`, `ScriptDocument` (metadata + steps), `TestResultPayload` `TypedDict`, `TestRunRecord`. **Note:** `TestOutcome`/`TestConfig` referenced by `base_driver.py` are **not** defined here. |
| `src/logic/test_engine.py`      | `TestRunnerThread(QThread)`. Script-driven runner; loops, retries, `Critical` abort, `stop_on_fail`, cooperative cancel, **pause/resume**, `Prompt` via `threading.Event`, per-result secure logging, and `report_snapshot()` for PDF/CSV. Emits 8 signals. |
| `src/logic/script_manager.py`   | `ScriptManager`; discovery, raw read/write, `load_document()` parser (→ `ScriptDocument`), and `serialize_ordered_steps()` for version archiving. Recognizes `PartNum:` header, `Critical`, `Limits`, `Target/Tol`, `Unit`, `Retry`, `Hidden`, `Group`, `Always`, and the report-mapping directives `Report`/`Quantity`/`Measurement`/`TimePoint` (cross-checked by `_validate_report_directives`). Defines `ScriptParseError`. **`serialize_ordered_steps` must write back every field the parser reads** — a field it omits is destroyed the first time an Admin saves from the limits editor; `test_script_roundtrip.py` guards this. |
| `src/logic/database_manager.py` | **(CHANGED)** `DatabaseManager`; thin facade over `logic/db/` sub-modules. Holds the class-level `_schema_ready` bootstrap flag. Public API unchanged for all callers. |
| `src/logic/roles.py`            | **(NEW)** Single source of truth for roles and permissions: `Role` (Operator/Technician/Maintenance/Admin), `ALL_ROLES`, `Capability`, and `can(role, capability)`. Every role list and permission check derives from here; fails closed on an unknown role. |
| `src/logic/report_xml.py`       | **(NEW)** CAMSTAR XML report (spec Rev.1.1 Appendix B). `build_tests_element()` generates the nested `<Tests>` subtree from the `report_*` fields on each result row; `build_camstar_tree()` renders the admin template around it; `has_report_mapping()` gates report generation (a run with no mapping raises rather than emitting a flat fallback). `group_by_report_test()` is the single source of test ordering, pinned to Appendix A and shared with the PDF generator. |
| `src/logic/report_templates.py` | **(NEW)** Admin-editable XML and PDF report templates under `data/templates/`, with defaults, validation (`validate_xml_template`, `validate_pdf_template`) and `{{placeholder}}` substitution. Malformed templates fall back to the defaults rather than losing a report. |
| `src/logic/db/__init__.py`      | Package marker for the `logic.db` repository sub-package. |
| `src/logic/db/connection.py`    | `open_conn(db_path)` — shared SQLite connection factory with `row_factory` and `PRAGMA foreign_keys = ON`. |
| `src/logic/db/schema.py`        | **(CHANGED)** `create_tables()` — DDL for all tables plus the idempotent `connection_params` `ALTER TABLE` migration, and `_seed_admin()` bootstrap. The `users.role` CHECK clause is generated from `roles.ALL_ROLES`; `_migrate_role_check()` rebuilds the table (12-step, with a timestamped `.bak` copy and `PRAGMA integrity_check`) when an existing database's CHECK predates a newly added role — `CREATE TABLE IF NOT EXISTS` would otherwise keep rejecting it forever. |
| `src/logic/db/users.py`         | User CRUD: `verify_login`, `create_user`, `delete_user`, `update_user`, `change_password`, `list_users`. |
| `src/logic/db/test_versions.py` | Test-version catalog: `add_test_version`, `list_test_versions`, `get_test_version`, `delete_test_version`, `version_exists`. |
| `src/logic/db/audit.py`         | `log_audit_action` (writes DB row + `SecureLogger` side-channel), `get_audit_logs`. |
| `src/logic/db/test_runs.py`     | `save_run(record: TestRunRecord)` — inserts one `test_runs` row + N `test_results` rows atomically. |
| `src/logic/auth_manager.py`     | `AuthManager`; thin facade over `DatabaseManager.verify_login`/`change_password`. `validate_password_strength()` is defined but **never called**. |
| ~~`src/logic/limit_manager.py`~~ | **DELETED (2026-06-07)** — was STALE/UNUSED. `LimitManager` was not imported anywhere; `limits.json` keys matched no current test names. |
| `src/logic/file_lock.py`        | `SingleInstanceLock`; **Windows:** named mutex via `CreateMutexW` (`Local\DFX_ate_singleton`) — OS releases automatically on process death, no stale-lock logic needed. **POSIX:** PID lock file with `os.kill` liveness probe. |
| `src/logic/secure_logger.py`    | **(NEW)** `SecureLogger`; Fernet-encrypted, append-only daily JSON log (`logs/sys_YYYYMMDD.dat`). Process-wide singleton via `get_secure_logger()`. Key derived from the hardcoded `LOG_ENCRYPTION_PASSWORD`. |
| `src/logic/monitor_engine.py`   | **(NEW)** `MonitorThread(QThread)`; emits simulated voltage/current readings on an interval. **Second QThread in `logic/`** (not covered by the old "only `test_engine` uses QtCore" exception). |
| `src/logic/report_generator.py` | **(CHANGED)** `ReportGenerator` + module functions; PDF (ReportLab) and CSV reports with capability-based detail, XML via `logic/report_xml.py`; auto-archives under `data/results/<UUT>/<Serial>/`. Title, subtitle, logo, header fields and results columns come from the admin PDF template. Raises `ReportsForbiddenError` for a role without `Capability.GENERATE_REPORTS`, so the Maintenance report ban is enforced in the logic layer and not only in the UI. Skipped steps render `N/A` with blank measurements, never `0`. |

## `src/drivers/` — hardware abstraction

| File Path                     | Short Description                                                                                                                                |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/drivers/__init__.py`     | Package marker.                                                                                                                                  |
| `src/drivers/base_driver.py`  | `BaseDriver(ABC)` — real abstract contract (`connect`, `disconnect`, `execute_command`, `measurement_commands` property) plus an error hierarchy (`HardwareError`, `ConnectionLostError`, `CommandTimeoutError`, `UnknownCommandError`). |
| `src/drivers/mock_hardware.py`| `MockHardware(BaseDriver)`; Qt-free simulator. `execute_command` handles `setvoltage`, `relay`, `getid`, and `setlogic` as side-effect commands; `readchannel` as a measurement command. `measurement_commands` returns `frozenset({"readchannel"})`. Raises `UnknownCommandError` for unknown commands. `connect()`/`disconnect()` called by the runner lifecycle. |

## `src/ui/` — Qt presentation layer

| File Path                              | Short Description                                                                                                                          |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `src/ui/__init__.py`                   | Package marker.                                                                                                                            |
| `src/ui/report_worker.py`              | **(NEW)** `ReportWorker(QThread)`; runs `ReportGenerator.generate_pdf_auto_archive` off the GUI thread; emits `archived(str)` or `failed(str)`. |
| `src/ui/ui_helpers.py`                 | `attach_password_visibility_toggle()` and an emoji-icon helper, shared by login/user/audit dialogs.                              |
| `src/ui/widgets/__init__.py`           | Package marker.                                                                                                                            |
| `src/ui/widgets/control_panel.py`      | **(CHANGED)** `ControlPanelWidget`; start/stop, save-log checkbox, user box (detachable via `take_user_box`), unit fields, status group, loop count, stop-on-fail. Emits `start_requested`/`stop_requested` signals; exposes `set_running_state(bool)`. Pass/fail labels use objectName QSS selectors (Rule 4 compliant). |
| `src/ui/widgets/instrument_panel.py`   | **(NEW)** `InstrumentPanelWidget`; live voltage/current readout rows, fed by `MonitorThread`.                                              |
| `src/ui/widgets/result_row_delegate.py`| **(NEW)** `ResultRowDelegate`; paints PASS/FAIL cell tints in the results table.                                                          |
| `src/ui/views/__init__.py`             | Package marker.                                                                                                                            |
| `src/ui/views/main_window.py`          | **(CHANGED)** `MainWindow`; ribbon, menu bar, capability gating via `_can(Capability...)`, pre-test flow, live monitor wiring, trace log, results table, report export, audit logging, and runner wiring. Uses `ControlPanelWidget.start_requested`/`stop_requested` intent signals and `set_running_state()`. Also hosts the Maintenance single-step run (`_on_run_single_step`, `_maintenance_step_selection`) and the report gate inside `_finalize_run_reports` — the gate must stay in that method because the parallel-run path calls it once per unit in a loop. |
| `src/ui/views/report_templates_dialog.py` | **(NEW)** `ReportTemplatesDialog`; Admin-only tabbed editor for the XML and PDF report templates. Validates both tabs before writing either, offers per-tab Check and Restore Default, and audit-logs the change. |
| ~~`src/ui/views/main_window.ui`~~      | **DELETED (2026-06-07)** — was STALE/UNUSED. Qt Designer file; UI is built entirely in Python; `loadUi`/`QUiLoader` appeared nowhere. |
| `src/ui/views/login_dialog.py`         | **(CHANGED)** `LoginDialog`; authenticates via `AuthManager`, writes a "User Logged In" audit row. Loads `style.qss` directly. No password-change flow. |
| `src/ui/views/pre_test_dialog.py`      | **(NEW)** `PreTestDialog`; collects UUT type / serial / tester name before a run.                                                          |
| `src/ui/views/select_test_dialog.py`   | **(NEW)** `SelectTestDialog`; pick a version from the DB catalog; writes its content to a temp `.tst` for execution.                       |
| ~~`src/ui/views/sequence_editor_dialog.py`~~ | **DELETED** — retired; step reorder/add/remove now lives only in the raw `.tst` editor + Version Manager import. |
| `src/ui/views/script_editor.py`        | `ScriptEditorDialog`; raw `.tst` text editor. **Only used by the Version Manager** (`_edit`), not from the main ribbon.                    |
| `src/ui/views/test_result_dialog.py`   | **(NEW)** `TestResultDialog`; full-screen PASS/FAIL banner shown at end of run for **all** roles. Inline-styled.                           |
| `src/ui/views/user_management_dialog.py`| `UserManagementDialog` + `UserEditDialog`; Admin CRUD over users/roles. Does **not** call `validate_password_strength`.                   |
| `src/ui/views/version_manager_dialog.py`| **(CHANGED)** `VersionManagerDialog` + `ImportTestMetaDialog`; Admin import/view/edit/delete/export of test versions. 4-col table (Product Name, UUT Type, Version, Date). |
| `src/ui/views/led_check_dialog.py` | **(NEW)** `LedCheckDialog` — the manual LED indication prompt as the Pass/Fail **checkboxes** spec Rev.1.1 §1.1.6.3 names explicitly. Neither box pre-ticked (no answer by reflex); mutually exclusive (no contradictory answer); OK disabled until one is ticked and the dialog cannot be dismissed, which is how §1.1.4.2's "subsequent DC Load tests will not be permitted to proceed" is enforced. `result_is_pass()` feeds the existing `submit_yesno_answer` handshake, so `test_engine.py` is unchanged. |
| `src/ui/views/limits_editor_dialog.py` | **(NEW)** `LimitsEditorDialog`; Admin-only step-parameter editor for the loaded test. One row per step; columns (indices named `COL_*`): Test (r/o), Value, Low, High, Delay (ms), Unit, Channel (`readchannel` arg), Crit (checkbox) — together covering the three parameters spec Rev.1.1 requires an Admin to adjust: duration, voltage limits, and load resistance. Value = the step's numeric setpoint when present (the `setresistance` value for the load tests), else the on/off state of a `relay`/`setlogic` command. `PromptYesNo` has no stored value (runtime answer) so it shows "—". Editing Low/High also rewrites the trailing monitoring bounds of a `Delay <ms> <min> <max>` so continuous monitoring cannot police a different window than pass/fail; clearing the limits removes monitoring and warns. Retry and Prompt text remain out of scope. Multi-command steps are supported — only the identified Value/Channel args and step-level fields are written back; all other command lines are preserved on serialize. Opens capped to the screen so buttons stay visible; maximizable via the title bar or a Maximize button. Apply writes a temp `.tst`; Save creates a new DB version. Works on a deep copy of `ScriptDocument.steps` — live document never mutated. |
| `src/ui/views/connections_dialog.py`  | **(NEW)** `ConnectionsDialog`; Admin-only dialog to configure per-instrument connection strings. One labeled `QLineEdit` per `INSTRUMENTS` entry; pre-filled from DB; Save upserts + logs audit. |
| `src/ui/views/audit_viewer_dialog.py`  | **(CHANGED)** `AuditViewerDialog`; Admin viewer for DB audit rows and decrypted daily `sys_*.dat` hardware logs (password-gated export). Window title: "Logs".       |
| `src/ui/views/connection_settings_form.py`| **(NEW)** `ConnectionSettingsForm`; edits a `PORT\|BAUD\|PARITY\|STOP_BITS` string; enumerates COM ports via `pyserial` when available.   |
| `src/ui/assets/light_theme.qss`        | Light QSS theme (applied at the `QMainWindow` root).                                                                                       |
| `src/ui/assets/dark_theme.qss`         | Dark QSS theme (applied at the `QMainWindow` root).                                                                                        |
| `src/ui/assets/style.qss`              | **(CHANGED)** Login-screen stylesheet. Contrary to the previous manifest, it **is** loaded — by `LoginDialog`.                            |
| `src/ui/assets/icons/`                 | App icon, brand logo, and theme/ribbon SVG/PNG icons (`BirdAppIcon.png`, `BirdLogo.png`, `moon.svg`, `sun.svg`, `logout.svg`, `power.svg`, …). |

## `src/data/` — seed config & writable state

| File Path                       | Kind          | Short Description                                                                                              |
| ------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------- |
| `src/data/sequence.tst`         | config (seed) | Default sequence loaded on launch for non-operator roles.                                                     |
| `src/data/demo_system.tst`      | config (seed) | Demo script exercising every keyword/command.                                                                 |
| `src/data/28VDC Power Supply Input.tst`           | config | Real-world sample sequence.                                                                          |
| `src/data/SPREOS Power Supply Main Card.tst`      | config | Real-world sample sequence.                                                                          |
| `src/data/12VDC RF Module Stabilized Output Interface.tst` | config | Real-world sample sequence.                                                                 |
| `src/data/limits.json`          | config        | **(STALE/UNUSED)** Legacy JSON limits. Keys do not match any current `.tst` test names. Still seeded by `paths.py` and bundled by the spec. |
| `src/data/database.db`          | state         | SQLite DB (runs, results, users, versions, audit). Created on first run; git-ignored.                          |
| `src/data/logs/`                | state         | Encrypted daily hardware/system logs (`sys_YYYYMMDD.dat`); git-ignored.                                        |
| `src/data/results/`             | state         | Auto-archived PDF reports under `<UUT>/<Serial>/`; git-ignored.                                                 |
| ~~`src/data/secure_system.log`~~ | state        | **DELETED (2026-06-07)** — was STALE. Older plaintext log artifact superseded by `logs/sys_*.dat`. |
| `src/data/tmp/`                 | state         | App scratch directory; created on startup and swept clean by `_sweep_tmp()` in `main.py`. Temp `.tst` files from `SelectTestDialog` land here instead of the OS `%TEMP%`. |

## Build & tooling

| File Path             | Short Description                                                                                                  |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `requirements.txt`    | Six runtime deps: `PySide6`, `reportlab`, `PyPDF2`, `cryptography`, `pyserial`, `python-dotenv`.                   |
| `DFX_Tester.spec`     | **(NEW)** PyInstaller onedir spec. Bundles assets + seed data; force-includes `cryptography`. **Excludes `serial`** even though `connection_settings_form.py` imports it (falls back to a static port list — see audit). |
| `build.ps1`           | **(NEW)** PowerShell build driver: installs deps + PyInstaller, cleans, runs the spec, prints the bundle path.    |
| `.pylintrc`           | Pylint config; whitelists PySide6 C-extensions.                                                                   |
| `.gitignore`          | Ignores build output, venvs, `*.db`/`*.lock`/`*.log`, `src/data/results/`, `*.csv/*.json/*.txt`, and `*PLAN*.md`. |

## Documentation (`DOC/`)

| File Path                       | Short Description                                                                  |
| ------------------------------- | --------------------------------------------------------------------------------- |
| `DOC/.env.example`              | Template with every supported `.env` key, dummy/safe values, and usage comments. Copy to `.env` next to the EXE (or repo root). **Do not commit `.env`**. |
| `DOC/FILE_MANIFEST.md`          | This file.                                                                         |
| `DOC/ARCHITECTURE_DEEP_DIVE.md` | Threading, data strategy, subsystems, signal map, schema, boot flow, RBAC.        |
| `DOC/SPECIFICATIONS.md`         | Pinned environment, dependencies, deployment.                                     |
| `DOC/TECH_STACK.md`             | Required engineering competencies.                                                |
| `DOC/DEVELOPMENT_RULES.md`      | Architectural guardrails (with current-reality notes).                            |
| `DOC/KEYWORDS_DICTIONARY.md`    | `.tst` script language reference.                                                 |
| `README.md`                     | Top-level project overview (note: still uses the old role name "Engineer").       |
| `PROJECT_CONTEXT.md`            | **(NEW)** Condensed team reference: what the project is, folder map, RBAC table, DB schema, .tst language, threading model, .env keys, architectural rules, and Q&A hooks. Intended to be loaded as context into Claude web for team Q&A. |

## Files referenced by old docs that do NOT exist

- `src/ui/views/change_password_dialog.py` — never present in the tree; there is no
  password-change dialog and no `must_change_pwd` flow anywhere in the code.
