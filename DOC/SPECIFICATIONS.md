# SPECIFICATIONS - Environment & Versions

The pinned operating envelope for DFX_ate. Anything outside these bounds is
unsupported until this document is updated.

> **Audit sync (2026-06-07):** The dependency surface and deployment sections
> were corrected. The app depends on four third-party packages beyond PySide6,
> and PyInstaller packaging is now wired into the repo.

## Runtime

| Concern        | Specification                                              | Notes                                                              |
| -------------- | ---------------------------------------------------------- | ------------------------------------------------------------------ |
| Python         | **3.10 or newer** (developed/tested on 3.12)               | `@dataclass(slots=True)` requires 3.10; `str.removeprefix` requires 3.9; PEP 604 unions are annotation-only (`from __future__ import annotations`). |
| GUI framework  | **PySide6 >= 6.6.0**                                       | Qt6, LGPL. Uses QtCore/QtGui/QtWidgets (+ QtSvg for icons).         |
| Database       | **SQLite 3** (stdlib `sqlite3`)                            | `data/database.db`; `PRAGMA foreign_keys = ON;`; five tables.       |
| PDF reporting  | **ReportLab >= 4.0** + **PyPDF2 >= 3.0**                   | ReportLab builds the document; PyPDF2 stamps the logo watermark and encrypts Admin reports. |
| Encryption     | **cryptography >= 41.0** (`Fernet`, PBKDF2HMAC)            | Encrypted daily system logs (`data/logs/sys_*.dat`).               |
| Serial (opt.)  | **pyserial >= 3.5**                                        | COM-port enumeration in the Connection Settings form. Imported defensively (`try/except`); falls back to a static `COM1..COM8` list if missing. |
| Config         | **`python-dotenv` >= 1.0.0** + stdlib env/JSON             | `.env` next to the EXE (or repo root in dev) is the single source of truth for secrets + feature flags. Falls back to safe defaults if absent (see `DOC/.env.example`). `data/limits.json` is **legacy/unused**. |
| Test scripts   | **Plain text** (`.tst`, UTF-8)                             | Structured keyword grammar — see `KEYWORDS_DICTIONARY.md`.          |
| Filesystem API | **`pathlib.Path`** for all I/O                            | String paths only at the edges (`QFileDialog`, `tempfile.mkstemp`). |

## OS target

| Concern         | Specification                                            |
| --------------- | ------------------------------------------------------- |
| Primary OS      | **Windows 10** and **Windows 11**, desktop only.         |
| Display         | 1280 x 800 minimum; main window opens at 1200 x 800 (clamped to the available screen on scaled displays). |
| Filesystem      | NTFS (separators handled by `pathlib`).                 |
| Other OSes      | Not supported. The single-instance lock (`file_lock.py`) now uses a named Windows mutex (`CreateMutexW` + `ERROR_ALREADY_EXISTS`) on Windows, with the POSIX PID-file path used only on non-Windows platforms. |

## Dependencies

The full runtime dependency surface lives in `requirements.txt`:

```text
PySide6>=6.6.0
reportlab>=4.0.0
PyPDF2>=3.0.0
cryptography>=41.0.0
pyserial>=3.5
python-dotenv>=1.0.0
```

ReportLab pulls in **Pillow** transitively (used by `reportlab.lib.utils` for the
logo image); the PyInstaller spec deliberately does **not** exclude PIL for this
reason. Everything else (`sqlite3`, `json`, `pathlib`, `dataclasses`, `typing`,
`abc`, `secrets`, `hashlib`, `threading`, `random`, `time`, `datetime`,
`tempfile`, `ctypes`) is standard library. There is no ORM and no test framework.

Adding any new runtime dependency requires (1) a pinned `>=` entry in
`requirements.txt`, (2) a PR justification, (3) an update to this document, and
(4) a check of `DFX_Tester.spec`'s `excludes`/`hiddenimports`.

### Configuration via `.env`

`src/env.py` loads a `.env` file once at import time (called from `config.py`).
Search order:

1. Next to the EXE (frozen) or `src/` directory (dev) — `_install_root() / ".env"`.
2. Parent of the above (repo root in dev).
3. `find_dotenv(usecwd=True)` fallback.

All keys are optional; the app starts with safe built-in defaults if `.env` is
absent. **Never commit `.env`** — it contains site-specific secrets. See
`DOC/.env.example` for the full key list. `python-dotenv` is listed in
`requirements.txt` and added to `hiddenimports` in `DFX_Tester.spec`.

## Tooling (development time)

| Tool        | Purpose                                              | Config              |
| ----------- | ---------------------------------------------------- | ------------------- |
| pylint      | Static analysis                                      | `.pylintrc`         |
| venv        | Isolated interpreter (`.venv/` or `venv/` at root)   | not in version control |
| PyInstaller | Packaging (installed on demand by `build.ps1`)       | `DFX_Tester.spec`   |

## Deployment

DFX_ate ships as a **PyInstaller onedir** Windows bundle. The toolchain is wired
into the repo:

- **`DFX_Tester.spec`** — onedir build of `src/main.py`. Bundles `ui/assets/`
  (icons + all three `.qss`) and the seed data (`limits.json`, `sequence.tst`,
  `demo_system.tst`) under paths that mirror `src/`, so `resource_path(...)`
  resolves identically in dev and frozen. Force-includes `cryptography`
  submodules and `dotenv` (lazy imports PyInstaller can miss). UPX is off and
  `console=False`. Note: `.env` is **not** bundled — it is runtime config
  placed next to the EXE by the site administrator.
- **`build.ps1`** — installs `requirements.txt` + PyInstaller, cleans
  `build/`/`dist/`, runs the spec, and verifies `dist/DFX_Tester/DFX_Tester.exe`.

**Onedir, not onefile**, on purpose: onefile self-extracts to `%TEMP%` on every
launch and trips heuristic AV (e.g. Avast/AVG `IDP.HELU.*`).

At runtime, writable state (`database.db`, `logs/`, `results/`, `app.lock`) is
created in a `data/` folder next to the EXE; bundled read-only assets resolve via
`sys._MEIPASS`. See `src/paths.py`.

> **Known spec defect (see audit):** `DFX_Tester.spec` lists `serial` under
> `excludes` with the note "unused in current code", but
> `connection_settings_form.py` imports `serial.tools.list_ports`. In a frozen
> build, COM-port enumeration will silently fall back to the static port list.

### PyArmor (planned, not wired)

Source obfuscation of `src/logic/` and `src/drivers/` with PyArmor before the
PyInstaller step is still planned but not implemented. Note that PyArmor would
**not** mitigate the hardcoded-secret weakness (the keys are derived at runtime
from constants in `config.py`); it only raises the bar on casual inspection.
