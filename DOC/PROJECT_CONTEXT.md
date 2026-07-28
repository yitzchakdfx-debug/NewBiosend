# DFX_ate — Project Context Reference

> Paste this file into Claude web as context. It covers what the project is,
> where everything lives, and how the key systems work.
> Last synced: 2026-06-07.

---

## What this project is

**DFX_ate** is a Windows desktop ATE (Automated Test Equipment) front-end built
with PySide6. It runs keyword-driven `.tst` test scripts against hardware
(currently `MockHardware`; real drivers slot in via `BaseDriver`), records results
in SQLite, generates PDF/CSV reports, and provides role-gated access for factory
floor operators.

- Entry point: `src/main.py`
- All source lives under `src/`
- Config is in `.env` at the repo root (git-ignored); `src/env.py` loads it
- Secrets/flags are never hardcoded — always in `.env` → `src/config.py`

---

## Tech stack

| Concern | Library / tool |
|---|---|
| UI | PySide6 (Qt6) |
| DB | SQLite via stdlib `sqlite3` |
| PDF | ReportLab + PyPDF2 |
| Encryption | `cryptography` (Fernet / PBKDF2) |
| COM ports | `pyserial` (optional; graceful fallback) |
| Config | `python-dotenv` |
| Build | PyInstaller onedir (`DFX_Tester.spec`) |
| Python | 3.11+ |

---

## Folder map

```
src/
  main.py              # entry point: lock → login loop → MainWindow
  env.py               # .env loader (load_env_once, get_str/bool/list)
  config.py            # all app constants — reads from env.py
  paths.py             # resource_path / user_data_path / user_tmp_path
  version.py           # __version__ from .env APP_VERSION

  logic/               # Qt-free business logic (+ 2 sanctioned QThreads)
    test_engine.py     # TestRunnerThread — runs .tst sequences
    script_manager.py  # ScriptManager — parse/read/write .tst files
    database_manager.py# DatabaseManager — thin facade over logic/db/
    db/                # SQL repository sub-package
      connection.py    # open_conn() factory
      schema.py        # CREATE TABLE + migrations + admin seed
      users.py         # user CRUD + verify_login
      test_versions.py # version catalog CRUD
      audit.py         # audit log write + read
      test_runs.py     # save_run (test_runs + test_results)
    auth_manager.py    # AuthManager — thin wrapper over DatabaseManager
    models.py          # TestStep, ScriptDocument, TestRunRecord, normalize_role()
    report_generator.py# ReportGenerator — PDF + CSV, role-gated detail
    secure_logger.py   # SecureLogger — Fernet-encrypted daily .dat logs
    monitor_engine.py  # MonitorThread — emits simulated V/A readings
    file_lock.py       # SingleInstanceLock (Windows: named mutex)

  drivers/
    base_driver.py     # BaseDriver (ABC) + HardwareError hierarchy
    mock_hardware.py   # MockHardware(BaseDriver) — Qt-free simulator

  ui/
    report_worker.py   # ReportWorker(QThread) — PDF build off GUI thread
    ui_helpers.py      # attach_password_visibility_toggle + icon helper
    widgets/
      control_panel.py # ControlPanelWidget — start/stop/status/loops rail
      instrument_panel.py  # InstrumentPanelWidget — live V/A readout
      result_row_delegate.py # PASS/FAIL cell tints
    views/
      main_window.py       # MainWindow — composition root (~1,200 lines)
      login_dialog.py      # LoginDialog
      pre_test_dialog.py   # UUT type / serial / tester name before run
      select_test_dialog.py# Operator picks from DB version catalog
      sequence_editor_dialog.py # Reorder/add/remove steps, save version
      script_editor.py     # Raw .tst text editor
      version_manager_dialog.py # Admin: import/view/edit/delete versions
      audit_viewer_dialog.py    # Admin: DB audit rows + decrypt .dat logs
      user_management_dialog.py # Admin: CRUD users/roles
      test_result_dialog.py     # Big PASS/FAIL banner at end of run
      connection_settings_form.py # PORT|BAUD|PARITY|STOP_BITS editor
    assets/
      light_theme.qss / dark_theme.qss  # full app QSS themes
      style.qss                         # login screen only

  data/                # writable state (git-ignored)
    database.db        # SQLite
    logs/              # encrypted sys_YYYYMMDD.dat files
    results/           # archived PDFs under <UUT>/<Serial>/
    tmp/               # scratch .tst files, swept on startup
```

---

## Roles (RBAC)

Four roles — **Operator / Technician / Maintenance / Admin** — defined once in
`logic/roles.py`. `ALL_ROLES` feeds both the DB `CHECK` constraint and the
user-management combo box; the DB CHECK is widened by `_migrate_role_check()`
when a role is added.

**Gate on a `Capability`, never on a role name.** `MainWindow._can(Capability.X)`
is the only permission predicate in the UI; `logic/report_generator.py` enforces
report permission independently so the ban cannot be bypassed by a background
worker.

| Capability | Operator | Technician | Maintenance | Admin |
|---|---|---|---|---|
| `RUN_SEQUENCE` | ✓ | ✓ | ✓ | ✓ |
| `RUN_SINGLE_STEP` | ✗ | ✗ | ✓ | ✓ |
| `SELECT_STEPS` | ✗ | ✓ | ✓ | ✓ |
| `GENERATE_REPORTS` / `ARCHIVE_RUN` | ✓ | ✓ | **✗** | ✓ |
| `VIEW_MEASURED_DETAIL` / `VIEW_TRACE_LOG` | ✗ | ✗ | ✓ | ✓ |
| `EDIT_LIMITS` / `EDIT_TEMPLATES` | ✗ | ✗ | ✗ | ✓ |
| `MANAGE_VERSIONS` / `MANAGE_USERS` | ✗ | ✗ | ✗ | ✓ |
| `VIEW_AUDIT_LOG` / `EDIT_CONNECTIONS` | ✗ | ✗ | ✗ | ✓ |

Maintenance is **not** a superset of Technician. Per spec Rev.1.1 §1.1.4.3 it
gains diagnostic access (single-step execution, measured detail, trace) but
loses report generation and run archiving, so bench troubleshooting cannot
leave behind something that looks like a production record. A maintenance run
also sets `production_flow=False`, which skips the 24 V-presence and polarity
station gates — they would abort a diagnostic and inject rows the user did not
ask for.

DB stores full Min/Max always — visibility gating is visual only.

---

## Default script and report conformance (Rev.1.1)

`biosend_test.tst` is the **default** script (`main_window._DEFAULT_SCRIPT_NAME`,
seeded via `paths._SEED_FILES`). It is the only sequence that satisfies the
spec: CR mode, 5.76 Ω / 2.0 Ω, load off before the polarity check, monitored
delays, `Critical` measurement steps, and the `Report` / `Quantity` /
`Measurement` / `TimePoint` directives. `Automatic Power Supply Test.tst` was
deleted — its name implied it was the official sequence while it ran 12.5 Ω,
never selected CR, and produced non-conformant XML.

Three consequences worth knowing:

* **The CAMSTAR XML has no fallback.** `write_xml_report` raises
  `XmlMappingMissingError` when a script declares no `Report` directives,
  instead of silently emitting a flat document CAMSTAR rejects. Only
  `biosend_test.tst` carries them today; every other `.tst` will raise. The
  error names the script and lists the directives to add. `ReportWorker` reports
  this on a dedicated `xml_failed` signal so a missing MES file is never
  mistaken for a successful export — the PDF still archives.
* **The PDF follows Appendix A §2.1.3.** `_appendix_a_sections` renders one
  section per `Report` name — Result, Expected Value, Load Mode, and measured
  values labelled t1/t2/t3 — ahead of the full step table. The static text lives
  in the PDF template under `test_sections` so admins can edit it (§1.1.4.4
  Note 2). Test order is pinned to the spec's 1-4; unknown tests follow.
* **Every role gets measurements.** `write_pdf_report` no longer consults
  `VIEW_MEASURED_DETAIL`; Appendix A requires measured values with no role
  qualifier, and Operator/Technician lack that capability, so production PDFs
  used to archive names and PASS/FAIL only. The capability still gates the live
  UI table and trace log.

Unbounded limits render blank rather than `nan`: steps the spec records but does
not bound (Current, Power, Resistance) carry NaN bounds from the engine, and
`_fmt_num` maps non-finite values to `""`.

## Polarity gate fails closed (spec §1.1.7.1)

The polarity station gate is a **voltage readback**, not a boolean flag.
`PRODIGIT_QUERY_POLARITY` defaults to a voltage query, and
`ProdigitVisaDriver._read_polarity` returns the signed reading in volts.

Two rules keep it from passing a unit it never measured:

* **The driver raises rather than guesses.** An empty response, a non-numeric
  reply, or a non-finite value raises `HardwareError`. It previously returned
  `1.0` (PASS) in each of those cases, so a silent instrument cleared the gate.
  `_check_polarity` turns any exception into a FAIL.
* **The engine requires output to be present.** The verdict is
  `reading >= _polarity_floor_v()`, not `reading >= 0`. The floor is
  `_POLARITY_FLOOR_FRACTION` (10 %) of the configured input target — 2.4 V for a
  24 V product — so `0.00 V` from a dead UUT and a lead sitting on the noise
  floor now fail. The floor scales with `InputTarget` / `INPUT_CONNECTED_TARGET_V`
  instead of being hardcoded.

No upper bound is applied: §1.1.7.1 is a wiring check, and accuracy against
22.8–25.2 V belongs to the Low Load and Burn-In tests. The row is recorded as a
real measurement (`is_measurement=True`, unit `V`), so Appendix A §2.1.3.2 gets
its *Measured Output Value* and Appendix B gets
`<MeasuredOutput><Voltage><Value>`. `MockHardware` returns 24.0 V so mock runs
exercise the same units and threshold.

The engine row carries `report_test` / `report_quantity` — unlike the other
fixture checks, Polarity Check is a *spec* test and must reach the XML.

**Ordering: LED first, polarity second.** The gate used to run before the
scripted sequence, alongside the 24 V input check, so polarity was measured
*before* the operator was asked about the LED. Spec §1.1.4.2 makes the manual
LED check the first test — "the subsequent DC Load tests will not be permitted
to proceed" until it is answered — and Appendix A numbers LED as Test 1 and
Polarity as Test 2. The gate therefore fires from inside the step loop, right
after the LED step passes and before any load is applied.
`_is_led_step` identifies it by its `PromptYesNo` (so a rename or translation
still gates), falling back to the Appendix B `Report` name. If the selected
steps contain no LED check — a partial Maintenance selection, or a product whose
script has none — the gate runs up front instead, because polarity must still be
verified before a load is switched on.

The operator answers through `ui/views/led_check_dialog.py`, which presents the
**Pass / Fail checkboxes** spec §1.1.6.3 names verbatim ("check the 'Pass'
Checkbox … check the 'Fail' Checkbox"). Neither box is pre-ticked, the two are
mutually exclusive, OK stays disabled until one is chosen, and the dialog cannot
be dismissed — that is how §1.1.4.2's "the subsequent DC Load tests will not be
permitted to proceed" is enforced at the GUI. Other `PromptYesNo` steps still
use a plain Yes/No box; the checkbox requirement is specific to the LED test.
`MainWindow._is_led_prompt` routes between the two on the prompt text.

`_check_polarity` performs its own `loadoff` + 3 s settle (`_POLARITY_SETTLE_MS`)
before reading, as §1.1.7.1 requires. It does not rely on a script step for
that: the gate now runs at a point the script does not control. The script
accordingly has **no** polarity steps at all — neither the measurement (which
produced a duplicate row with a laxer `Limits 0 1000` that passes a dead unit,
and two `<Test>` nodes where Appendix B expects one) nor a setup step (which
would now run at the wrong point and waste a second settle).

---

## The 3300G accepts ONE TCP connection (design constraint)

Measured on the station: a second simultaneous connection is refused with
`could not connect: -1073807339`. Everything that touches the instrument — the
live monitor, the pre-test scan, the test runner, the exit-time load shutdown —
competes for a single slot. Most "cannot connect" reports trace back to this,
not to the network.

**Holding the slot.** `MonitorThread` owns the connection on the main screen and
releases it in `pause()` before a scan or run. That handshake used to race: the
poll loop could be inside `_connect_driver()` when `pause()` ran and reopen the
link straight after, leaving `_paused=True` with the driver still held and every
other component locked out. Now `pause()` sets the flag *before* taking the
lock, and `_connect_driver` re-checks it while holding the lock, discarding what
it just opened if paused. `resume()` clears the flag first, or its own connect
would be discarded.

**Releasing the slot.** A session that ends without the device being told — a
yanked cable, a killed process — leaves the 3300G holding the slot until its own
timeout expires, which is minutes. That is why restarting the app does not help
and only waiting does. `_tune_socket` sets `SO_LINGER` (timeout 0) so a close
sends RST rather than FIN, plus `SO_KEEPALIVE`. Measured: after a hard
`taskkill /F` on a process holding the connection, the next connect succeeded in
**0.33 s** instead of minutes. Both settings are best-effort — they reach
pyvisa-py internals that do not exist under NI-VISA — and a failure to tune
never fails the connection.

`_VISAContext` also carries the `ResourceManager` so `disconnect()` can close it;
`connect()` builds one per call and previously leaked every one.

**Diagnosing it.** `_connect_error_message` turns `-1073807339` into an
explanation naming the likely holders (a second copy of the app, an orphaned
process, a bench tool, a session the instrument has not timed out yet) and notes
that it clears on its own within a couple of minutes. The bare code cost three
debugging rounds. A second copy of the app is prevented by
`SingleInstanceLock` (Windows named mutex, `logic/file_lock.py`).

## No silent demo-mode fallback

A failed `connect()` used to swap the driver for `MockHardware` and carry on.
That is why reconnecting the instrument's cable never helped: the real driver
was gone for the rest of the session, so every later read came from a simulator
that answers a healthy 24 V for **every** slot — and the run still archived as
production data.

Now:

* `TestRunnerThread._retry_connect` retries `connect()` for
  `_CONNECT_RETRY_BUDGET_S` (8 s, 1 s apart), which covers an operator plugging
  the cable back in, then aborts the run with an explanation. Verified: recovers
  in ~3 s when the link returns, gives up in ~8 s when the instrument is absent.
* The parallel-run path raises a modal instead of substituting a mock.
* The pre-test scan reports "nothing detected" rather than mock-detecting all
  four channels.

Demo mode remains available deliberately via `HARDWARE_BACKEND=mock`. It is
never an automatic consolation prize during a production run.

`monitor_engine` had the same fallback and it mattered more than it looked: the
live monitor owns the hardware on the main screen, so unplugging the instrument
turned it into a simulator **permanently** — `_connect_driver` ran only at thread
start and on `resume()`, and `_read_values` swallowed every error and returned
`0.0, 0.0` forever. Replugging the cable could not recover, and neither could
leaving and re-entering the screen. Now the monitor connection is self-healing:

* no MockHardware fallback — a failed connect leaves the driver `None`,
* a failed read discards the driver instead of polling a dead handle,
* `run()` reconnects every `_MONITOR_RECONNECT_INTERVAL_MS` (3 s) while
  disconnected, throttled so a failing attempt's timeout cannot stall the loop,
* `connection_restored` reports recovery in the trace pane.

Test runs were never affected in the same way: each run builds a fresh driver via
`create_driver()`, and `_io_with_resilience` retries per I/O call.

## Pre-test channel scan: fast and honest

Two independent problems, both fixed in `_ScanThread.run`:

* **Speed.** The scan reads every slot expecting most to be empty, so a failed
  read is the normal case, not a fault. It inherited
  `_io_with_resilience`, spending the reconnect budget *per empty slot* — up to
  20 s for one UUT among four channels — and each resource reopen could disturb
  the channel that did have a UUT. `create_driver(reconnect_on_error=False)`
  now disables retry for the scan only; test runs keep full resilience.
  Measured: 5.01 s → instant per empty channel, ~15 s saved per scan.
* **False positives.** `activate_slot` fires `CHAN <n>` and cannot verify it
  took effect, so a mainframe that ignores a selection for an absent module
  keeps answering with the previously selected channel — one UUT appears on two
  channels. Slots whose readings match within `_SLOT_ECHO_TOLERANCE_V` (0.02 V)
  are treated as that echo and collapsed to the first; genuinely different
  supplies differ by more, an echo is bit-identical.

Detection stays advisory: the operator can still tick an undetected channel
(spec §1.1.5.3 asks for exclusion, and this remains a deliberate deviation).

## The load is always commanded off (safety)

A UUT must never be left dissipating test power. Four layers, because each one
alone has a hole:

1. **The script's `Always` teardown** (`:Cleanup` / `loadoff` in
   `biosend_test.tst`) runs even after a critical abort or a user stop. But most
   `.tst` files in `data/` declare no `Always` step at all.
2. **The engine's `finally`.** `_run_single_unit` wraps the whole per-unit run
   and calls `_force_load_off` on the way out. This is the layer that matters:
   the method has early returns for slot activation, input connection and
   polarity, plus the normal path, and an exception can escape any of them. The
   shutdown does not depend on which branch was taken or on script content.
3. **`ProdigitVisaDriver.disconnect`** sends `LOAD OFF` before closing the VISA
   resource. Closing a socket does not stop a 3316G — it holds its last
   commanded state — and once closed there is no way left to command it.
   Skipped while `_reconnecting`, so a reconnect probe's teardown does not fight
   the reconnect it belongs to.
4. **App exit.** `_release_load_on_exit` (from `_shutdown_threads`) opens a
   short-timeout connection purely to send `loadoff`, covering a session that
   closed without a run or after its thread had gone.

**What software cannot do:** switch off a load it cannot reach. Pulling the
load's LAN cable aborts the test (see monitoring below) but leaves it drawing
current, and `loadoff` then fails. `_force_load_off` therefore emits
`load_shutdown_failed`, which `MainWindow._on_load_shutdown_failed` raises as a
**critical** modal telling the operator to switch off at the front panel or
disconnect the UUT. It used to be a trace-log line — which is precisely how a
unit sits under 300 W with nobody noticing. The driver's `_io_with_resilience`
reconnect budget means a cable restored before teardown still results in a
successful, confirmed shutdown.

## Continuous monitoring aborts on a lost instrument (§1.1.7.3)

`Delay <ms> <lo> <hi>` polls the voltage every 300 ms for the whole wait. Two
distinct failures end the run, and the trace log tells them apart:

* `_MonitorOutOfRange` — the UUT left 22.8–25.2 V. The offending reading is
  recorded as the step's measurement.
* `_MonitorReadFailed` — the *instrument* stopped answering. Raised only after
  `_io_with_resilience` has spent its full reconnect budget
  (`_RECONNECT_BUDGET_S`, retrying and reopening the VISA resource), so it means
  the link is genuinely down, not blipping.

The read failure used to be swallowed (`except Exception: v = None`), which
skipped the range check: pulling the instrument's cable two minutes into a
burn-in left the remaining 28 minutes unmonitored while the run still passed.
The spec requires monitoring for the entire duration, so an unmonitored
remainder cannot be certified — the step now fails.

---

## Database schema (5 tables)

```
test_runs       id, operator, part_number, serial_number,
                overall_passed, start_time, end_time

test_results    id, run_id FK→test_runs, test_name, value,
                min_val, max_val, unit, passed

users           id, username (UNIQUE NOCASE), password_hash, salt,
                role CHECK(Operator/Technician/Admin),
                employee_id, created_at, updated_at

test_versions   id, test_name, uut_type, version_name, test_content,
                connection_params (PORT|BAUD|PARITY|STOP_BITS),
                created_at, created_by
                UNIQUE(test_name, version_name)

audit_logs      id, timestamp, username, employee_id, action, details
```

- Passwords: PBKDF2-HMAC-SHA256, 200k iterations, 16-byte random salt per user
- All queries parameterized — zero string-interpolated SQL
- `DatabaseManager._schema_ready` class flag: DDL runs once per process

---

## .tst script language (key syntax)

```
# comment
PartNum: ABC-123      # fills UI part-number field

: Step Name           # opens a new step
Critical              # abort whole run if this step fails finally
Limits 4.5 5.5        # pass if last measurement in [4.5, 5.5]
Target 5.0 Tol 10     # equivalent: Limits 4.5 5.5 (resolved at parse)
Unit V
Retry 2               # run up to 3 times; report only final attempt

readchannel 1         # hardware command → MockHardware.execute_command
Delay 500             # runner-side sleep, never reaches hardware
Log "message"         # emit trace line
Prompt "operator msg" # park runner until operator clicks OK
```

`ScriptManager.validate_version_name(name)` — call before saving a version name.

---

## Threading model

```
GUI thread          MainWindow (PySide6 event loop)
Worker thread 1     TestRunnerThread  — runs the .tst sequence
Worker thread 2     MonitorThread     — emits V/A every 500 ms
Short-lived         ReportWorker      — builds PDF after a run
```

- Cross-thread: Qt signals (auto QueuedConnection)
- Cancel: cooperative boolean flag + `threading.Event` (wakes prompt/pause)
- Pause: `_pause_event` cleared = paused; set = running (checked at step boundaries)
- Shutdown: `MainWindow._shutdown_threads()` — called from both `closeEvent` and `logout`

---

## Key signals (TestRunnerThread → MainWindow)

`log_msg(str)`, `test_result(str, dict)`, `loop_started(int, int)`,
`progress_total(int)`, `progress_test(int)`, `current_test(str)`,
`prompt_request(str)`, `script_log(str)`, `finished` (built-in)

---

## .env keys

```
LOG_ENCRYPTION_PASSWORD   # Fernet key source for daily .dat logs
ADMIN_REPORT_PASSWORD     # PDF encryption password for Admin reports
DEFAULT_ADMIN_USERNAME    # seeded only on first DB creation
DEFAULT_ADMIN_PASSWORD    # seeded only on first DB creation
DEFAULT_ADMIN_EMPLOYEE_ID
APP_VERSION               # shown in window title
TESTER_SERIAL_NUMBER      # printed on reports
SHOW_LIVE_MONITOR         # true/false
SHOW_SEARCH_BAR           # true/false
UUT_TYPES                 # comma-separated list for PreTest combo
TEST_LOAD_MODEL           # <TestLoad><Model> in the CAMSTAR XML (3316G)
INPUT_CONNECTED_TARGET_V  # 24 V presence check target
INPUT_CONNECTED_TOLERANCE_PCT
LOAD_RESISTANCE_100W_OHM  # 5.76 — fallback only; the .tst wins
LOAD_RESISTANCE_300W_OHM  # 2.0  — fallback only; the .tst wins
LOAD_RESISTANCE_50W_OHM   # 12.5 — legacy scripts still named "50W"
```

---

## Spec Rev.1.1 (2026-07-15) — where each requirement lives

| Requirement | Implementation |
|---|---|
| §1.1.4.3 Maintenance role, single-scenario execution, **no reports** | `logic/roles.py`; `MainWindow._on_run_single_step`; gates in `_finalize_run_reports`, `ReportGenerator`, and `TestRunnerThread(persist_runs=…)` |
| §1.1.4.4 Note 1 — admin-updatable XML template | `logic/report_templates.py` + `ui/views/report_templates_dialog.py` |
| §1.1.4.4 Note 2 — admin-updatable PDF template, changes reflected in the report | same; consumed by `write_pdf_report`, which calls `read_pdf_template()` per report so a save applies to the next report with no restart. Verified by mutating each field with a unique token and inspecting the rendered flowables: `title`, `subtitle`, `header_fields`, `detail_columns` and `test_sections` all propagate. `summary_columns` is **CSV-only** and deliberately does not — the PDF always uses `detail_columns` because Appendix A requires the measured values regardless of the operator's role |
| §1.1.4.5 failed test → later tests **N/A** | `skipped` flag carried through `BatchUnitReport.rows()`; `N/A` in PDF/CSV and empty `<Value/>` in XML |
| §1.1.4.5 "proceed only if the previous test has passed" | `TestRunnerThread.__init__`: `self._stop_on_fail = stop_on_fail or production_flow`. A production run **always** halts on the first failure — the caller's flag can add stopping, never remove it. Previously it was honoured as given and the "Stop on fail" checkbox ships unticked, so any step lacking the `Critical` keyword continued after a failure. The checkbox now only matters in Maintenance, where continuing past a failure is a legitimate diagnostic |
| §1.1.4.3 no configuration privileges for production roles | A role with `SELECT_STEPS` can untick steps, and deselected steps are filtered before execution so they leave no `N/A` row. `BatchUnitReport.step_coverage()` records `"17 / 20 (partial)"` into `meta["steps_executed"]`, surfaced as the PDF's "Tests Executed" header field and the `{{steps_executed}}` XML placeholder, so a reduced run can never read as a full pass. Omitted from the PDF when the run was full |
| §1.1.7.1 Polarity = voltage readback, load OFF | the engine's `_check_polarity` gate, which issues its own `loadoff` and waits `_POLARITY_SETTLE_MS` (3 s) immediately before reading — not a script step, so the load-off and settle cannot be reordered away from the measurement |
| §1.1.7.2 Low Load 100 W @ **5.76 Ω**, monitored for the full minute | `:Low Load 100W Setup` `setresistance 5.76`; `Delay 60000 22.8 25.2` |
| §1.1.7.3 Burn-In @ **2.0 Ω**, monitored across 30 min, t1/t2/t3 | `:Burn-In 300W Setup` `setresistance 2.0`; `Delay <ms> 22.8 25.2` on each voltage step |
| §1.1.7.2/3 Note 2 — adjustable duration, limits, resistance | `ui/views/limits_editor_dialog.py` (Delay / Low / High / Value columns) |
| Appendix A — Resistance recorded for Low Load and all Burn-In points | `Resistance Measurement` steps using `getresistance` |
| Appendix B — exact nested XML | `logic/report_xml.py` + the `Report`/`Quantity`/`Measurement`/`TimePoint` directives |
| Appendix A test numbering (LED 1, Polarity 2, Low Load 3, Burn-In 4) | `report_xml.group_by_report_test()` — one spec-pinned ordering shared by the XML and the PDF. Deliberately not derived from row order: the Polarity row comes from an engine gate, not a script step, so its recorded position depends on where that gate fires |

---

## Shutdown must never depend on an operator answer

Closing the app while a `PromptYesNo` was pending used to leave the process
alive with an invisible window, holding the single-instance lock (so it would
not restart) and, in a hardware run, the instrument's one TCP slot. Two causes,
both fixed:

1. **A set/clear race.** `_execute_command` cleared the prompt event *before*
   waiting. If `stop()` set it during the gap between the check and the clear,
   the clear discarded the only wake-up the runner would ever get. Both prompt
   branches now re-check `_stop_requested` *after* clearing.
2. **An unbounded wait.** `Event.wait()` with no timeout cannot notice a stop
   flag if the ordering goes against it. `_wait_for_operator` polls in
   `_OPERATOR_PROMPT_POLL_S` (0.2 s) slices instead, so the flag is honoured
   within one slice however the race falls. Prompts still wait indefinitely for
   the operator — that is intended; a technician may take minutes over the LED
   check.

`main._exit_now` is the backstop: `sys.exit` only unwinds the main thread and
the interpreter then waits for every non-daemon thread, so one stuck QThread
strands the process. It runs after `closeEvent` has already stopped the threads,
released the load and flushed the reports, so nothing unfinished is cut short.
The Windows single-instance mutex is released by the OS on process death, so
skipping teardown does not leak it.

## Architectural rules (short form)

1. **No UI in logic** — `logic/` and `drivers/` never import `QtWidgets`/`QtGui`
2. **No blocking on GUI thread** — hardware, sleep, PDF → worker threads
3. **No secrets in source** — everything in `.env`
4. **All SQL in `logic/db/`** — no `sqlite3` imports anywhere else
5. **No inline styles** — use objectName + `.qss` selectors; never `setStyleSheet("...")`
6. **Roles are Operator / Technician / Maintenance / Admin** — not "Engineer";
   gate on a `Capability` from `logic/roles.py`, never on a role-name compare
7. **`serialize_ordered_steps` must round-trip every parsed step field** — an
   omitted field is destroyed on the first limits-editor save (this once deleted
   the `Always` teardown, leaving the load energized after a failure). Guarded
   by `test_script_roundtrip.py`
8. **Simple passwords are intentional** — factory-floor shared stations; no policy enforcement

---

## Common Q&A hooks

- **Where are reports saved?** `data/results/<UUT_type>/<Serial_number>/`
- **Where are encrypted logs?** `data/logs/sys_YYYYMMDD.dat`
- **Where is the DB?** `data/database.db` (next to the EXE in prod)
- **How to add a new test step type?** Add keyword to `ScriptManager.load_document` parser + `KEYWORDS_DICTIONARY.md`
- **How to add a real hardware driver?** Subclass `BaseDriver`, implement `connect/disconnect/execute_command/measurement_commands`; pass instance to `TestRunnerThread(driver=...)`
- **How to add a new .env key?** Add to `config.py` via `env.get_str/bool/list`, add to `DOC/.env.example`
- **Password hashing algo?** PBKDF2-HMAC-SHA256, 200k iterations, 16-byte salt, stored as BLOB
- **Single instance enforcement?** Named Windows mutex `Local\DFX_ate_singleton` via `ctypes`
