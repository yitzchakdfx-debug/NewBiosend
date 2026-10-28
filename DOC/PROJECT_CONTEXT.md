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
    file_lock.py       # SingleInstanceLock (Windows: named mutex)

  drivers/
    base_driver.py     # BaseDriver (ABC) + HardwareError hierarchy
    mock_hardware.py   # MockHardware(BaseDriver) — Qt-free simulator

  ui/
    report_worker.py   # ReportWorker(QThread) — PDF build off GUI thread
    ui_helpers.py      # attach_password_visibility_toggle + icon helper
    widgets/
      control_panel.py # ControlPanelWidget — start/stop/status/loops rail
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

Three roles, as in spec Rev.1.1 §1.1.4.3 — **Operator / Maintenance / Admin** —
defined once in `logic/roles.py`. Operator is the spec's "Technician (Operator)":
the database stores `Operator` and the UI and PDF show `role_label()` →
"Technician (Operator)" (`ROLE_LABELS`). A separate Technician role (which could
untick tests) existed until 2026-10-18; `schema._merge_retired_roles()` converts
such users to `Operator` before the CHECK rebuild, and `coerce_role()` still
accepts the old name and the label. `ALL_ROLES` feeds both the DB `CHECK` constraint and the
user-management combo box; the DB CHECK is widened by `_migrate_role_check()`
when a role is added.

**Gate on a `Capability`, never on a role name.** `MainWindow._can(Capability.X)`
is the only permission predicate in the UI; `logic/report_generator.py` enforces
report permission independently so the ban cannot be bypassed by a background
worker.

| Capability | Operator ("Technician (Operator)") | Maintenance | Admin |
|---|---|---|---|
| `RUN_SEQUENCE` | ✓ | ✓ | ✓ |
| `RUN_SINGLE_STEP` | ✗ | ✓ | ✓ |
| `SELECT_STEPS` | ✗ | ✓ | ✓ |
| `GENERATE_REPORTS` / `ARCHIVE_RUN` | ✓ | **✗** | ✓ |
| `VIEW_MEASURED_DETAIL` / `VIEW_TRACE_LOG` | ✗ | ✓ | ✓ |
| `EDIT_LIMITS` / `EDIT_TEMPLATES` | ✗ | ✗ | ✓ |
| `MANAGE_VERSIONS` / `MANAGE_USERS` | ✗ | ✗ | ✓ |
| `VIEW_AUDIT_LOG` / `EDIT_CONNECTIONS` | ✗ | ✗ | ✓ |

Maintenance is **not** a superset of Operator. Per spec Rev.1.1 §1.1.4.3 it
gains diagnostic access (single-step execution, measured detail, trace) but
loses report generation and run archiving, so bench troubleshooting cannot
leave behind something that looks like a production record. A maintenance run
also sets `production_flow=False`, which skips the 24 V-presence and polarity
station gates — they would abort a diagnostic and inject rows the user did not
ask for.

DB stores full Min/Max always — visibility gating is visual only.

---

## Default script and report conformance (Rev.1.1)

`biosense_test.tst` is the **default** script (`main_window._DEFAULT_SCRIPT_NAME`,
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
  `biosense_test.tst` carries them today; every other `.tst` will raise. The
  error names the script and lists the directives to add. `ReportWorker` reports
  this on a dedicated `xml_failed` signal so a missing MES file is never
  mistaken for a successful export — the PDF still archives.
* **The PDF follows Appendix A §2.1.3.** `_appendix_a_sections` renders one
  section per `Report` name — Result, Expected Value, Load Mode, and measured
  values labelled t1/t2/t3 — ahead of the full step table. The static text lives
  in the PDF template under `test_sections` so admins can edit it (§1.1.4.4
  Note 2). Test order is pinned to the spec's 1-4; unknown tests follow.
* **"All Recorded Steps" has one row per measurement point** (2026-10-18),
  like the on-screen results table: `_merge_point_rows` joins a point's
  V/I/P/R steps (same loop, `report_test`, `Measurement` index) into one row
  named "Burn-In / Continuous Load Test  t2 (15 min)", values in the Value
  column (widened to 1.45 in), Min/Max and verdict from the limited quantity.
  Single-reading points (polarity) and steps without a `Quantity` keep their
  row. PDF only — the CSV still has one row per step. Loop headers are now
  derived from the rows' `loop` (run_meta never carried `loop_count`, so
  they were never printed).
* **On-screen and report text uses a plain hyphen `-`** (2026-10-18), not the
  em/en dash (`—`/`–`), at the user's request — including the "empty cell"
  marker in the limits editor (`_locked_dash`; `_get_text` treats `-` as empty).
  Comments and docstrings are unaffected.
* **Every role gets measurements.** `write_pdf_report` no longer consults
  `VIEW_MEASURED_DETAIL`; Appendix A requires measured values with no role
  qualifier, and Operator lacks that capability, so production PDFs
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
`is_led_step` identifies it by its `PromptYesNo` (so a rename or translation
still gates), falling back to the Appendix B `Report` name. If the selected
steps contain no LED check — a partial Maintenance selection, or a product whose
script has none — the gate runs up front instead, because polarity must still be
verified before a load is switched on.

**The LED is answered before the run, in the pre-test dialog** (since
2026-10-15). `BatchPreTestDialog` shows an **"LED ON"** checkbox per unit when the
selected steps include an LED step. None is pre-ticked. Pressing OK with an
active unit's LED unticked asks, per unit, "really off, or did you forget?":

* **Forgot** — back to the dialog to tick it.
* **Really off** — `MainWindow._report_led_failure` records that unit's FAIL
  report: an unstarted `TestRunnerThread.record_led_failure()` builds the LED row
  as FAIL plus N/A for every later test (the same rows a run would produce, via
  `_backfill_skipped`) without touching the hardware, then
  `_finalize_run_reports` archives the PDF/XML — so Maintenance still gets no
  report. The unit's serial is cleared so a replacement can be scanned into the
  same channel, and the dialog stays open.

The run starts only once every unit in the batch has a lit LED. Each `BatchUnit`
carries the answer as `led_verdict`; when the runner reaches the LED step's
`PromptYesNo` it uses that answer instead of prompting, so the step still runs —
LED stays Test 1 on the report and the polarity gate still fires right after it.

Deviation to disclose: spec §1.1.6.3 names separate "Pass" and "Fail"
checkboxes. The dialog has one checkbox, with Fail confirmed explicitly through
the "really off" prompt.

`ui/views/led_check_dialog.py` (the Pass/Fail checkbox dialog) remains as the
fallback when `led_verdict` is None — a Maintenance single-step run has no
pre-test dialog. `MainWindow._is_led_prompt` routes LED prompts to it and other
`PromptYesNo` steps to a plain Yes/No box.

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
pre-test scan, the test runner, the exit-time load shutdown — competes for a
single slot. Most "cannot connect" reports trace back to this,
not to the network.

**Holding the slot.** Nothing holds the connection while the station is idle.
The idle live monitor (`MonitorThread` + the right-hand "Power Supply" panel,
which showed slot 1 only) was removed on 2026-10-15; live readings now come from
the test runner's own connection during a run (see *Live readings pane*). That
also removed the monitor's pause/resume handshake before every scan and run,
which had previously raced and locked the rest of the app out of the instrument.

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

Demo mode remains available deliberately via `HARDWARE_BACKEND=mock` (also
`simulator`/`demo`; real hardware is `prodigit`/`visa`/`usb`, and any other
value is rejected at startup by `config.py`). `drivers.factory.is_simulated()`
is the single source every driver consumer consults, so they cannot disagree
and contend for the 3300G's one TCP slot. It is resolved once per
process and cached: a backend change applies on the next launch, which is what
the Connections dialog promises. It is
never an automatic consolation prize during a production run.

Each test run builds a fresh driver via `create_driver()`, and
`_io_with_resilience` retries per I/O call.

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

**Selections are confirmed with `CHAN?`** (2026-10-18). Measured on the
station: after `CHAN n` the mainframe answers `CHAN?` with `n` immediately, every
time. The scan now asks `driver.selected_slot()` after each selection; a
confirmed slot is trusted even when its voltage equals another slot's, a slot
whose selection did not take is skipped, and only unconfirmed slots (instruments
without `CHAN?`, `PRODIGIT_QUERY_SLOT` empty) fall back to the identical-voltage
echo rule. The rule had hidden real channels: one supply wired to two loads read
24.373 V and 24.374 V, and the second channel was dropped (3 of 4 detected).

**Run commands land on the right channel** (2026-10-19). A 4-unit run left CH3
at the previous run's 28.8 ohm through a 10 W step, and an earlier one showed
loads 1/2/4 unloaded. The engine selects `CHAN n` before every command under the
shared lock, but the driver's `_io_with_resilience` retried a failed command
after a reconnect *without* re-selecting, so it (and the resistance read-back
that "confirmed" it) went to whatever channel the mainframe had. Now:
`_reselect_slot` sends `CHAN n` again after every reconnect and reports it via
`driver.on_reconnect` (the run log shows "Lost the connection to the electronic
load - reconnected and selected CHn again"). `setresistance` no longer "trusts
the write" when its `CR:HIGH?` read-back fails: that counts as a failed attempt,
and after `_RESISTANCE_VERIFY_ATTEMPTS` the step fails ("Resistance not
confirmed on CHn"). That trust is how CH3's stale value passed unnoticed.

A `CHAN?` check before every state change (`setmode`/`setresistance`/`loadon`/
`loadoff`) was tried the same day and removed: measured from the log, the
10 W -> 20 W transition went from 2-3 s to 9-15 s, with a reconnect during the
Setup steps in every run — the 3300G does not reliably answer `CHAN?` while a
load is changing state, and each unanswered query cost a timeout plus a
reconnect for all units. Do not reintroduce it in the run path.

**Every setup is checked against Ohm's law** (2026-10-19). At 13:06 CH2 drew the
previous run's 20 W through a 10 W step with no drop and no error: the setup's
`CR:HIGH?` read-back answered the new 57.6 ohm, and a minute later the same
query answered 28.8 — most likely a missed `CHAN 2`, so the setting and its
read-back both went to another channel. A read-only probe of the four channels
afterwards found them identical (CR mode, `LEV?` 1, `RANG?` 1, `CR:HIGH?` 57.6,
`CR:LOW?` 60000 — a second, near-open level that would explain this morning's
0 W loads if it were active). So instead of asking the instrument what it thinks
is set, `TestRunnerThread._verify_load` measures what the channel does: after
`loadon` in a step that ran `setresistance`, it waits `_LOAD_VERIFY_SETTLE_S`
(0.3 s), reads V and I, and accepts I within `_LOAD_VERIFY_TOL` (15 %) of V/R
(floor `_LOAD_VERIFY_MIN_A`). A mismatch is measured once more (V and I are two
reads, and at 100/300 W the voltage can still be moving), then the setup is sent
again and logged ("CH2 draws 21.2 W ... expected ~10.7 W ... sending the setup
again"); a second mismatch fails the step with `HardwareError`. A sagging supply
is not misread as a load fault — its current still matches its voltage — and
fails on the voltage as before. Cost: ~1 s per run, setups only.
`PRODIGIT_SET_LEVEL` (empty by default, e.g. `LEV 1`) is written before each
resistance if set; it is untested as a write on the station.

**Shorter stalls when the link drops** (2026-10-19). With the reconnect now
logged, a 4-unit BENCH run showed the 3300G dropping the connection 3-4 times,
each stalling *every* unit (one shared connection) for 6-11 s — seen as a slow
move to the next step and a slow Stop. The old silent version had the same
stalls (the 7 s setups at 11:41 and 11:59 are when CH3 kept 28.8 ohm).
`PRODIGIT_VISA_TIMEOUT_MS` stays 5000 (2500 was tried and reverted: a slow but
valid reply then counted as a drop and forced a reconnect), and a parallel run polls the
voltage every 500 ms and samples live A/W every 2 s instead of 300 ms / 1 s,
about 40 % fewer commands during the waits. A single-unit run is unchanged.

**A link lost mid-test produces no report** (2026-10-28). When the reconnect
budget runs out, `ProdigitVisaDriver.link_lost_count` goes up. The engine notes
it at each unit's start (`_unit_link_mark`); if it moved, `_save_record` skips
the database and `_run_single_unit` marks the report `aborted_link_lost` — every
exit path of `_run_single_unit_inner` is covered, as with Stop. The window
creates no PDF/XML for it, does not count it in the batch, shows "NO COMM" in
red on its progress row, and replaces the PASS/FAIL banner with "Load
communication lost … No report was saved for: …". The exit load-off and its red
"LOAD MAY STILL BE ON" warning are unchanged. A drop the reconnect recovers from
is reported normally. The link is shared, so every unit running when it was lost
loses its report.

**Load-link indicator** (2026-10-28, `ui/widgets/link_led.py`). A dot and a label
in the toolbar, right of the active script: green "Load connected", orange
"Load reconnecting...", red "No connection to load", grey "Load: not checked"
(USB / no address) or "Simulator". Between runs `MainWindow._probe_link` pings
the host parsed from `resolved_visa_resource()` every 5 s on a background thread
(`PingProbe`) — ICMP only, never a TCP connection, because the 3300G accepts one
and the scan or next run needs it. During a run the ping pauses and the driver
reports the real link: `ProdigitVisaDriver.on_link_state` -> "reconnecting" on
the first failed I/O, "ok" once a retry succeeds, "down" when the reconnect
budget runs out; the engine forwards it as `TestRunnerThread.link_state`.
Colours are `[state="..."]` selectors in both `.qss` themes.

Detection stays advisory: the operator can still tick an undetected channel
(spec §1.1.5.3 asks for exclusion, and this remains a deliberate deviation).

## The load is always commanded off (safety)

A UUT must never be left dissipating test power. Four layers, because each one
alone has a hole:

1. **The script's `Always` teardown** (`:Cleanup` / `loadoff` in
   `biosense_test.tst`) runs even after a critical abort or a user stop. But most
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

**Every load-off selects its channel first** (2026-10-15). The 3300G applies
`LOAD OFF` to the channel last selected with `CHAN n`. `_force_load_off` used to
send it without selecting, so in a parallel run it hit whichever unit had just
polled its voltage: reproduced on the simulator, a unit failing early switched
off *another* unit's load mid burn-in — which then passed a 300 W test it never
ran — while its own load stayed on. It now calls `activate_slot` and `loadoff`
inside the same `hw_lock`, like `_hw_execute`. `_release_load_on_exit` likewise
selects and switches off every channel in `LOAD_SERIALS` instead of one.

**Every engine and exit load-off is read back** (2026-10-18). A station run with
three units logged "Load off confirmed" for each while all three loads stayed on:
a write over TCP "succeeds" even when the instrument has stopped processing
commands (the run also lost the instrument mid-burn-in, `VI_ERROR_TMO`). A probe
on the station confirmed `CHAN n` takes effect immediately and `LOAD OFF` works
on a responsive instrument, so the defect was trusting the write.
`ProdigitVisaDriver.load_off_verified()` sends `LOAD OFF`, reads `LOAD?`, retries
up to 3 times and raises if the load still reports on or the instrument does not
answer; `_force_load_off` uses it (logging `LOAD? -> 0`) and otherwise emits
`load_shutdown_failed`, and `_release_load_on_exit` verifies every channel and
shows a critical warning before the window closes. Verified on the station on an
empty channel (0.22 s). Root cause of the lost instrument is still open.

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

**The wait is timed by the wall clock** (`time.monotonic()`, since 2026-10-15),
not by counting polls. Counting added a fixed 300 ms per poll and ignored the
time each poll's instrument queries took, so every delay overran: measured on
the simulator, a 3 s delay took 4.7 s; on the station a "30 min" burn-in ran an
estimated 10-20 % long (more in a parallel run, where all units queue on one
connection), and the t=5/15/30 labels on the report were wrong. Each poll now
sleeps only what remains until its 300 ms slot and the loop exits at the
deadline (3 s delay: 3.2 s including thread start-up). If a poll overruns its
slot the next one starts immediately; if the loop falls more than a slot behind
(a slow reconnect) the cadence restarts from now instead of firing a burst. The
live-readings sample is timed the same way. Not covered: the current, power and
resistance steps *between* burn-in delays still add a few seconds per point.

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
                role CHECK(Operator/Maintenance/Admin),
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
`prompt_request(str)`, `script_log(str)`, `live_reading(dict)`, `finished` (built-in)

### Test list: one row per spec test

The left-hand list shows the spec's four tests (Appendix A, Tests 1-4), not the
script's 20 steps (2026-10-18). `logic/test_groups.build_test_groups` derives
the groups from the script itself: `Report X` steps form test X, a `Hidden` step
without `Report` (a Setup) joins the test after it, `Always` steps are never
listed and always run, and a visible step without `Report` (older scripts) is
its own row. The engine-run Polarity Check gets a synthetic row after the LED
test; it is fixed (not uncheckable) because production always runs that gate.
Each row has a detail line — CR Ω from the Setup's `setresistance`, time points
from `TimePoint`, expected text from the PDF template — so it follows edits.

Selection is by whole test only. `_selected_test_names()` expands the ticked
tests into their steps plus the teardown, so the engine receives exactly what it
did before; a test can no longer lose its Setup (which ran measurements with the
load never switched on) and Cleanup can no longer be unticked. Burn-In's
t1/t2/t3 are deliberately not separate rows: the waits are cumulative, so
skipping one would shorten the burn-in and mislabel later time points. Maintenance
"Run Step" runs a whole test; for the Polarity row it passes
`polarity_check=True`, which makes a `production_flow=False` run perform
`_check_polarity`. Rows are measured by `WrapItemDelegate` so wrapped detail
lines are not cut.

The LED row stays uncheckable by roles holding `SELECT_STEPS` (Maintenance,
Admin) — a deliberate decision of 2026-10-18. Since the roles merge the Operator
cannot untick any row. A run without it skips the
pre-test "LED ON" column, omits LED from the report, and is disclosed as a
partial run. Do not lock it without revisiting that decision.

### Per-unit progress rows

The control panel's status group has one row per UUT in the run (1-4, rebuilt
by `ControlPanelWidget.show_unit_progress`). Each row is a header line —
`CH<n>` (serial in the tooltip), the current step's label and a clock of real time since that step
started — over a thin, text-less progress bar. The label is the report test
and point, as in the results table (`MainWindow._step_label`, 2026-10-19):
`Low Load t1`, `Burn-In t2` (a point's V/I/P/R steps share it), `Low Load
setup` for a hidden step without `Report`, `LED`. Steps the script does not
place (the engine's polarity/input checks, cleanup, scripts without `Report`)
fall back to `short_step_name` (`Polarity`, `Input 24V`, `V 300W 15m`). The
full step name is in the tooltip.
A unit that fails (`unit_finished` with `passed` false, not an operator Stop)
gets `UnitProgressRow.mark_failed()` from `MainWindow._on_unit_finished`: a full
red bar and a red bold "FAIL" next to its channel (dynamic property
`failed="true"`, styled in both `.qss` themes), while the other units keep
running (2026-10-28). The name is deliberately *not*
drawn inside the bar: there the fill covered it and it was unreadable on the
station, and fixed label widths cut "CH1" to "CH". When the step has
`Delay` commands the clock also shows their total (`MainWindow._step_delay_s`),
because the time in a step's name is the report time point, not its duration —
`… - 300W - 15min` waits 10 minutes. Each runner's `current_test` /
`progress_test` drive its own unit's row; the engine also announces its
`Input Connection Check` and `Polarity Check` gates. The rows freeze ("Done") at
the end of the run (bar full) and stay until the next one. They replaced the shared
"Current" field and single "Test" bar, which in a parallel run showed whichever
unit reported last.

**The Total bar is weighted by expected time** (2026-10-15), not by step count.
Each step weighs its `Delay` total (`TestRunnerThread.step_delay_s`) plus
`_STEP_BASE_S` (2 s), the polarity gate its 3 s settle; `_emit_total` reports
time done over the unit's total and is also called during a `Delay`, so the bar
moves through the long waits. Counting steps showed 36 % one minute into the
~32-minute script (7 of 19 steps done, but the four waits hold ~31 minutes);
now that point reads 4 %, the end of the 15-minute wait ~51 %.

In a parallel run each unit's `progress_total` goes to its own
`UnitProgressRow.set_total`, and `ControlPanelWidget._refresh_total` shows the
slowest unit still running (2026-10-19). Every unit used to write the bar
directly, so it flickered between their values (2 % / 3 % / 2 %); a unit that
has ended (`on_step("")`) no longer holds the bar back.

The control panel's **Unit** group (UUT type, Part number, Serial number) and the
**Status > Unit** row are hidden (2026-10-28): in a parallel run they showed one
unit only, while the CH1-CH4 rows, results headers and live table show all of
them. The widgets remain as data holders — part number, UUT type and serial
still feed the run meta, reports and export names. When a script declares no
`PartNum` (which a run requires), `_reload_script_into_list` shows the Unit
group again with the part number editable.

### Results table: why a unit failed

The Result column says what the unit did and what it had to meet (2026-10-18),
for every role — the Value/Min/Max columns are hidden for some, and the reason
used to live only in the Messages tab. Examples: `FAIL - 14.97 V (OK 22.8-25.2
V), at 0.3 s`; `FAIL - no reading - instrument not responding (30.1 s) (OK
22.8-25.2 V)`; `FAIL - answered Fail` (LED). The engine attaches the reason as
the payload's optional `note` (`_fail_note`, set in `_run_step` from
`_MonitorOutOfRange` / `_MonitorReadFailed` / a command error). Steps not
performed after a failure carry `skipped` and show grey **N/A** instead of a red
FAIL — matching the PDF/XML — and no longer count in the FAIL counter. Failing
rows grow to wrap the text.

**One row per measurement point** (2026-10-18). The table used to show one row
per quantity — V, I, P, R at every point, 19 rows per unit, each with a green
PASS although only voltage has limits. Steps carrying a `Quantity` now merge by
(unit, loop, `report_test`, `Measurement` index) into one row, the way Appendix A lays
a point out: `Low Load Stability Test  t1 (1 min) | 25.62 V · 0.436 A · 11.18 W ·
57.6 Ω | PASS`. The verdict and Min/Max come from the limited quantity (voltage);
a failing component gives the row its failure text; an all-skipped point is
N/A. The engine includes `_report_fields(step)` in every emitted result for
this; reports and CSV are unchanged. Rows are located by their cell item, not
row number, because parallel sections insert rows above them, and row heights
re-flow when the table resizes. Steps without a `Quantity` (input check, LED,
polarity, older scripts) keep one row each. The loop number is part of the key
(the engine sends `loop` with each result), so with **Loops** > 1 every pass gets
its own rows instead of merging into loop 1's; `_backfill_skipped` likewise
looks only at the current loop when deciding which steps still need an N/A row.

**Impossible readings are instrument faults, not UUT values.** On the station
the 3300G answered a voltage query with `9999` 47 s into the 100 W wait; it was
recorded as a 9999 V failure of the UUT. `_hw_execute` now validates V/I/P reads
(`_VALIDATED_READS`): a value at or above `_INVALID_READING_SENTINEL` (9990), or
a voltage above `_MAX_PLAUSIBLE_VOLTS` (100 V), is re-read up to twice; if it
persists, `InvalidReadingError` fails the step with no value and the note
"invalid reading from the instrument (9999) — check the load's front panel".
A plausible out-of-range value (e.g. 30 V) is still a normal UUT failure. Open
question: three station runs hit an instrument problem 30–50 s into the 100 W
wait (twice lost comms, once 9999) — watch the load's front panel then.

The driver's comms-recovery budget (`_RECONNECT_BUDGET_S`) is 15 s, up from 5 s,
which equalled one query timeout: twice on the station a single late reply
failed a burn-in although the instrument answered again seconds later. Not
unbounded, because continuous monitoring cannot tolerate a long gap.

### Live readings pane

The bottom of the main window is a `QTabWidget`: **Live Readings** (every role)
and **Messages** (the trace log, `VIEW_TRACE_LOG` only). The readings table has
one **column** per UUT in the current run — 1 to 4, from the batch dialog — each
headed by its load channel and serial (`CH2` / `SN-0002`), and one row each for
Voltage [V], Current [A] and Power [W]. It is a run-time view only; values stay
at their last reading after the run. There is no idle readout: the former
right-hand "Power Supply" panel and its `MonitorThread` were removed. Values are
drawn at 15pt bold (`QTableWidget#live_readings_table` in both `.qss` themes)
so they read from a distance.

`live_reading` carries `{slot, serial}` plus any of `V`/`A`/`W`, and is partial
by design (the UI merges). It reuses the runner's connection — the 3300G accepts
only one — so no new connection is opened:

* every `measvoltage`/`meascurrent`/`measpower` a script step executes is
  forwarded;
* a monitored `Delay` forwards each 300 ms voltage poll (500 ms,
  `_SHARED_POLL_S`, when units share the connection in a parallel run), and
  every `_LIVE_READING_INTERVAL_MS` (1 s; 2 s shared) also samples current and power (and voltage
  on an unmonitored delay), **after** the monitoring check so it never delays a
  trip. These samples are display-only and never fail the step. The first one
  is taken one interval into the delay (right after LOAD ON a load that has not
  come up reads 0 W); a failed one is reported once per delay ("Live readings
  unavailable for CHn") and retried every `_LIVE_RETRY_S` (5 s) — it used to stop
  sampling silently, leaving stale values on screen.
* every `_LIVE_LOG_INTERVAL_S` (5 s) each unit's latest V/A/W are written to the
  encrypted log under category `live`, so a run can be checked afterwards for
  what each load actually drew.

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

## Operator Stop produces no report

A run the operator stops with **Stop** is neither saved nor reported (decided
2026-10-15). Before this, the interrupted step was recorded as FAIL and the
rest N/A, the run went into `test_runs`, and a PDF/XML was archived with nothing
saying it had been stopped — indistinguishable from a faulty supply, in CAMSTAR
too. Spec §1.1.4.5 asks for a report when a *test fails*; a Stop is not one.

* A unit counts as stopped only if Stop arrived before its tests finished.
  `_run_single_unit_inner` captures `self._stop_requested` *before* the `Always`
  teardown, so a Stop during teardown keeps a complete result and its report.
* A stopped unit skips `_save_record` and returns a `BatchUnitReport` with
  `aborted_by_operator=True` (`overall_result="STOPPED"`). The rows stay on the
  object for the on-screen table only.
* `_finalize_run_reports` checks that flag first — ahead of the Maintenance and
  "Save as log" gates — and also clears `_last_run_meta`, so the Results menu
  cannot export the stopped run manually.
* Instead of the PASS/FAIL banner the operator sees "Run stopped by operator. No
  report was saved for: …". In a parallel run, units that finished before the
  Stop keep their reports. The audit log records "Test run stopped by operator"
  with the serials, and the batch PASS/FAIL counter ignores stopped units.
* Load safety is unchanged: teardown, `_force_load_off` and the
  "LOAD MAY STILL BE ON" alert all still run.

**A run that tested no unit produces no report either.** If the runner ends
with no unit report at all (connection failed after the retry budget, script
failed to load, Stop before the first unit), `_finalize_run_reports` returns
without archiving and clears `_last_run_meta`. This used to write an empty PDF
under `results/unknown/unknown/` and raise a spurious "XML not generated"
modal. `on_tests_finished` shows "Run did not start … No report was generated"
instead of a FAIL banner (an Operator cannot see the Messages tab holding the
reason), and audits "Test run ended before any unit was tested".

**Stop works after a unit has already finished** (2026-10-18). Parallel
threads used to be `deleteLater`'d on their own `finished`; when one unit failed
early, Stop walked `_parallel_threads`, called `isRunning()` on the deleted
thread, raised RuntimeError and never reached the units still running — Stop did
nothing (seen on the station). Threads are now deleted together at the end of
`_on_parallel_thread_finished`, and `MainWindow._is_running()` (shiboken6
validity check) guards every Stop / shutdown / close check.

**The exit load-off always runs** (2026-10-18). Closing the app once raised in
`_shutdown_threads` on a report worker that had finished and been deleted but
was still in `_report_workers` (it was removed by a lambda slot, which could
miss), and the close then skipped `_release_load_on_exit`. Workers are now pruned
by a bound method (`_prune_report_workers`), checked with `_is_running`, and
`_shutdown_threads` runs the exit load-off in a `finally`.

`should_generate_report` is a different, older flag: it is set on the up-front
polarity failure path but `_finalize_run_reports` never reads it, so that path
still archives a report despite logging "Skipping report". Left as is pending a
decision; `aborted_by_operator` is deliberately separate so as not to change it.

## Shutdown must never depend on an operator answer

**Closing the window exits the event loop explicitly** (2026-10-15).
`closeEvent` ends with `app.exit(0)` instead of relying on Qt's
quit-on-last-window-closed. That check runs only at the instant the main window
closes: a runner stopped by the close finishes a moment later and used to open
its result dialog ("Run stopped", PASS/FAIL), which made Qt cancel the pending
quit — and closing that dialog (it has a parent) never re-checks. The process
then idled in `app.exec()` with every window hidden, holding the
single-instance lock, so the next launch said "Application is already running".
Diagnosed on the station with `py-spy dump` (main thread idle at
`main.py: app.exec()`, all top-level windows hidden). `_closing` also suppresses
the result dialogs once the window has agreed to close. Logout is unaffected:
`exec()` returns and `main()` reopens the login dialog as before.

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
6. **Roles are Operator / Maintenance / Admin** (Operator shown as "Technician
   (Operator)") — not "Engineer";
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
