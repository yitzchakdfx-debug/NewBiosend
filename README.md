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
   pip install PySide6 reportlab pypdf2 pyserial
   ```

2. Launch the application:

   ```bash
   python src/main.py
   ```

### Default Credentials (Admin)

On first run the database is auto-initialized with a default administrator account:

- **Username:** `lior`
- **Password:** `XXXXXXX`

---

## Building a Standalone Executable

A PyInstaller spec and build script are included:

```powershell
# From the repo root
.\build.ps1
```

The output `dist/DFX_Tester.exe` is self-contained. Writable data (database, logs, reports) is stored next to the `.exe`; bundled read-only assets are unpacked to `sys._MEIPASS` at runtime.

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
