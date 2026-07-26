# TECH STACK - Skills & Paradigms

The competencies a contributor needs to be effective in this codebase.
This is not a tutorial - it is a checklist of what you should already know
(or be willing to learn before merging).

> **Audit sync (2026-06-07):** Added the cryptography, PDF, and serial
> competencies the app actually relies on, and corrected the "single QtCore
> exception" claim.

---

## 1. Modular Object-Oriented Python

DFX_ate is structured as a set of single-responsibility classes; functions
exist only as small, pure helpers. Required fluency:

- **Class design** with `__init__`, properties, private (`_name`) attributes,
  and `from __future__ import annotations` at the top of every module.
- **`@dataclass`** (and `@dataclass(frozen=True, slots=True)` where the value
  is immutable) for plain data containers - see `TestLimit`, `TestRunRecord`.
- **`TypedDict`** for cross-thread payload shapes (`TestResultPayload`).
- **`abc.ABC`** for hardware interfaces (`BaseDriver`).
- **The "Manager" pattern** as used here: one class owns one external
  resource. Active examples: `DatabaseManager` (SQLite — though it now spans
  runs, users, versions, and audit, so it is closer to a repository facade than
  a single-resource manager), `ScriptManager` (`.tst` files), `AuthManager`
  (credentials, a thin facade over `DatabaseManager`), `SecureLogger` (encrypted
  logs), and `ReportGenerator` (PDF/CSV). `LimitManager` (JSON) still exists but
  is **legacy/unused**. New external resources should follow the same pattern.

## 2. Asynchronous multi-threading with Qt

The runtime correctness of the app depends on knowing the threading rules.
Required fluency:

- **`QThread` subclassing** with overridden `run()` (two subclasses today:
  `TestRunnerThread` and `MonitorThread`) and the cooperative-cancel pattern via
  a boolean flag plus `threading.Event`s for pause and prompt synchronization.
- **`Signal` / `Slot`** declarations and connections. Understanding that
  cross-thread connections become `QueuedConnection` automatically and that
  payloads must be picklable / Qt-serializable.
- **Thread affinity**: any `QWidget` mutation must occur on the GUI thread.
  The runner therefore *emits signals* and never calls `setText`, `setValue`,
  or `addRow` directly.
- **`QApplication.processEvents()`** as a deliberate, narrow tool to keep
  short synchronous I/O responsive (used inside `ScriptEditorDialog._set_busy`).

## 3. Relational database design

Required fluency:

- **Stdlib `sqlite3`** - `Connection`, `Cursor`, `executemany`, `lastrowid`,
  context-manager commits.
- **Parameterized queries** (`?` placeholders) - **never** f-string or
  `%` SQL. The codebase has zero string-interpolated SQL and must keep it that
  way.
- **Foreign keys** with `PRAGMA foreign_keys = ON;` (SQLite does not enforce
  by default).
- **Schema evolution**: prefer adding rows over adding columns; if a column
  must be added, it requires a documented migration step.

## 4. QSS theming

The UI is themed entirely through Qt Style Sheets. Required fluency:

- **QSS selectors** including pseudo-states (`:hover`, `:disabled`,
  `:read-only`) and sub-controls (`QHeaderView::section:vertical`,
  `QTableCornerButton::section`, `QSplitter::handle:horizontal`).
- **Theme propagation**: `setStyleSheet` on `QMainWindow` cascades to all
  child widgets (including dialogs constructed with the main window as
  parent). This is why `ScriptEditorDialog` does not load its own QSS.
- **No inline styling** in Python (`widget.setStyleSheet("...")`) except for
  the small, documented exception in `ControlPanelWidget` for the pass/fail
  counter labels - flagged for future migration into `*.qss`.
- **Palette discipline**: every color used in a `.qss` should have a
  semantic role (background, surface, text, accent, danger). Avoid one-off
  hex values; reuse existing tokens.

## 5. Filesystem and configuration hygiene

- **`pathlib.Path`** for *all* I/O. No `os.path.join`, no string paths
  except at the very edge (e.g., the value returned by `QFileDialog`).
- **UTF-8 everywhere** - explicit `encoding="utf-8"` on every `read_text`
  and `write_text`. Windows defaults to a non-UTF-8 codepage; relying on
  the default is a bug.
- **Resource location** via `Path(__file__).resolve().parent...` so the
  code works identically when launched from source and when frozen by
  PyInstaller.

## 6. Cryptography & secure logging

- **`cryptography.fernet`** — symmetric authenticated encryption of one JSON
  record per line. Understanding that a Fernet key derived from a **constant**
  (`config.LOG_ENCRYPTION_PASSWORD`) provides tamper-evidence/obfuscation, not
  real confidentiality.
- **`PBKDF2HMAC`** (cryptography) for the log key, and **`hashlib.pbkdf2_hmac` +
  `secrets`** for password hashing/salting and constant-time comparison
  (`secrets.compare_digest`).

## 7. PDF / CSV reporting

- **ReportLab** — `SimpleDocTemplate`, `LongTable` (multi-page, `repeatRows`),
  `TableStyle`, `Paragraph`, and `canvas`-based watermark generation.
- **PyPDF2** — `PdfReader`/`PdfWriter`, `page.merge_page()` for the logo overlay,
  and `writer.encrypt()` for Admin-only password-protected reports.
- **`csv`** (stdlib) for the role-gated CSV export.

## 8. Serial enumeration (optional)

- **`pyserial`** — `serial.tools.list_ports.comports()` for COM discovery,
  imported defensively so the app still runs when pyserial is absent.

## 9. Tooling expectations

- **pylint** clean (per `.pylintrc`).
- **Type hints on all public signatures.** Internal helpers may omit them
  if obvious.
- **No new third-party dependencies** without explicit approval (see
  `SPECIFICATIONS.md` -> Dependencies), and keep `DFX_Tester.spec` in sync.
