# DFX Tester — Component ATE

**Automated Test Equipment Framework for Component Validation**

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![PySide6](https://img.shields.io/badge/UI-PySide6-green.svg)
![SQLite](https://img.shields.io/badge/Database-SQLite-lightgrey.svg)

## About the Project

The **DFX Tester** is an advanced software framework for managing and executing automated tests on electronic components (ATE). The system supports dynamic script-based test sequences (`.tst` files), real-time hardware trace monitoring, professional engineering PDF reporting, and built-in role-based security.

> **Architecture Note:** This repository is the **Core Baseline Framework** — a generic, modular foundation. Custom versions and branches are derived from it to meet specific hardware requirements, communication protocols, and client demands.

---

## User Management & Permissions (RBAC)

To ensure operational safety on the production floor, the system uses strict Role-Based Access Control (RBAC).

| Feature / Action                |   Operator    | Engineer | Admin |
| :------------------------------ | :-----------: | :------: | :---: |
| Run Test Sequences              |      Yes      |   Yes    |  Yes  |
| View Results (Pass/Fail)        |      Yes      |   Yes    |  Yes  |
| Add\Remove Test From Scripts    |      No       |   Yes    |  Yes  |
| View Technical Limits (Min/Max) |      No       |    No    |  Yes  |
| Edit Test Scripts (.tst)        |      No       |    No    |  Yes  |
| Export Full Detailed Reports    | No (Filtered) |    No    |  Yes  |
| Manage Connection Parameters    |      No       |    No    |  Yes  |
| User Management                 |      No       |    No    |  Yes  |

### Role Breakdown

- **Operator:** Simplified UI for production line use. Hardware trace log and Min/Max limits are hidden. End-of-run shows a large PASS/FAIL banner only.
- **Engineer:** Like Operator but Can add, remove and change the order of tests.
- **Admin:** All Engineer privileges plus user management, connection parameter editing, and report archiving controls.

---

## Key Features

- **Script-Driven Execution:** Run dynamic test sequences from `.tst` files without touching source code.
- **Loop-Aware Reporting:** Multi-loop test runs are grouped per loop in PDF reports.
- **PDF Logo Branding:** Company logo (BirdLogo.png) is stamped on every page of generated PDF reports.
- **Connection Parameters per Sequence:** Each test version stores its serial connection settings (`PORT|BAUD|PARITY|STOP_BITS`). Editable by Admins via the Version Manager.
- **Dynamic Hardware Trace Log:** Real-time command log with filtering for in-session debugging (hidden from Operators).
- **Role-Aware UI:** Trace log, test list actions, and save-log checkbox are shown or hidden based on the logged-in role.
- **End-of-Run Result Dialog:** Full-screen PASS/FAIL modal shown at sequence completion.
- **Security & Integrity:** PBKDF2-HMAC-SHA256 password hashing, forced password resets for new users, single-instance file locking.
- **PyInstaller / Frozen App Support:** `paths.py` resolves bundled resources (`sys._MEIPASS`) and writable user data (next to the `.exe`) transparently in both dev and packaged modes.

---

## Tech Stack

- **Core Logic:** Python 3.x
- **GUI / Frontend:** PySide6 (Qt for Python)
- **Database:** SQLite 3 (with live schema migration for new columns)
- **PDF Engine:** ReportLab + PyPDF2 (watermark stamping)
- **Serial Communication:** pyserial (optional, used for connection param enumeration)
- **Security:** `hashlib`, `secrets`, `ctypes` (Windows PID locking)
- **Styling:** Qt Style Sheets (QSS) — Dark and Light themes
- **Packaging:** PyInstaller (`DFX_Tester.spec` + `build.ps1`)

---

## Setup and Installation

1. Install required dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Launch the application (use the project venv's interpreter, so `python-dotenv`
   and PySide6 resolve):

   ```powershell
   .\venv\Scripts\python.exe src\main.py
   ```

### Default Credentials (Admin)

On first run the database is auto-initialized with a default administrator account:

- **Username:** `admin`
- **Password:** `admin`

These two values come from `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` in
`.env`. They only **create** that admin: `_seed_admin` runs at every start-up with
`INSERT OR IGNORE`, so if no user by that name exists it is added, and an existing
one is never changed. Two consequences:

- Changing the **password** in `.env` later has no effect on an existing user —
  change it in the app, under **Admin > User Management**.
- Changing the **username** in `.env` adds a *second* admin at the next start;
  the old one stays until it is deleted in User Management.

---

## Configuring a Deployed Station

A packaged build is a folder, not a lone `.exe`. `build.ps1` copies `.env` **next
to** `DFX_Tester.exe`, and the app resolves it from there (`paths._install_root()`),
so a deployed station stays configurable:

```
dist\DFX_Tester\
├── .env                 <- editable config (shipped by build.ps1)
├── DFX_Tester.exe
├── _internal\           <- runtime; do not edit
└── data\                <- created on first launch: DB, reports, logs
```

**What to change where:**

| Setting | Where | Applies |
|---|---|---|
| Users, passwords, roles | In-app: **Admin > User Management** | immediately |
| Simulator ↔ Prodigit | In-app: **Admin > Connections** | next launch |
| Electronic load address | In-app: **Admin > Connections** | next launch |
| Reports folder / XML folder (may be a network share) | In-app: **Admin > Connections > Reports** | next report |
| Test limits, durations, resistance | In-app: **Admin > Limits Editor** | next run |
| Report templates (PDF/XML) | In-app: **Admin > Report Templates** | next report |
| Everything else (secrets, VISA tuning) | `.env` beside the `.exe` | next launch |

The Connections dialog writes to the database, which **overrides** `.env`. An
untouched station therefore behaves exactly as its `.env` says; once an admin saves
a backend or load address, that value wins. Only fields you actually change are
written, and setting a field back to *Use .env default* (or clearing the address)
removes the override — so `.env` never gets shadowed by simply opening the dialog.
The backend is resolved once at startup, so a change applies on the next launch:
the 3300G accepts a single TCP connection, and the shutdown load-off safety path must reach the same instrument the run used. `HARDWARE_BACKEND` accepts
`prodigit`/`visa`/`usb` (real) or `mock`/`simulator`/`demo` (simulated) — anything
else aborts startup with a message naming the legal values, rather than silently
guessing.

> **Note on secrets.** `.env` holds `LOG_ENCRYPTION_PASSWORD` and
> `ADMIN_REPORT_PASSWORD` in cleartext beside the `.exe`. This is a deliberate
> trade-off for shared factory-floor stations (see `CLAUDE.md`): anyone with file
> access to the station can read the audit-log encryption key. Restrict access to
> the install folder accordingly.

---

## Building a Standalone Executable

A PyInstaller spec and build script are included:

```powershell
# From the repo root
.\build.ps1
```

The output is the **folder** `dist/DFX_Tester/` (onedir, not a single file — onefile builds trip antivirus heuristics; see the comment in `build.ps1`). Ship the whole folder. Writable data (database, logs, reports) is stored next to the `.exe`; bundled read-only assets are unpacked to `sys._MEIPASS` at runtime. See **Configuring a Deployed Station** above for what an admin can change on an installed station.

---

## Directory Structure

```
src/
  logic/          — Core logic: test engine, DB manager, report generator, limits, scripts
  ui/
    views/        — PySide6 dialogs and windows (main window, login, version manager, …)
    widgets/      — Reusable widgets (control panel, instrument panel, row delegate)
    assets/       — QSS themes (dark_theme.qss, light_theme.qss) and icons
  data/           — Test script files (.tst) and local database
  paths.py        — Path resolution for dev and frozen (PyInstaller) environments
  main.py         — Application entry point
DFX_Tester.spec   — PyInstaller build spec
build.ps1         — PowerShell build script
```

---

## Database Schema Notes

The `test_versions` table includes a `connection_params` column (added via live `ALTER TABLE` migration on first run after upgrade). Format: `PORT|BAUD|PARITY|STOP_BITS` (e.g. `COM3|115200|N|1`). Empty string means "use defaults".
