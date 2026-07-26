# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DFX Tester — Windows GUI build (onedir).

Produces ``dist/DFX_Tester/`` containing ``DFX_Tester.exe`` plus the Qt
and Python DLLs it loads at startup. Read-only data (icons, QSS, seed
.tst, limits.json) is bundled alongside the EXE; writable state
(database, logs, results, app.lock) is created in a ``data/`` folder
next to the EXE on first run — see ``src/paths.py``.

**Onedir (not onefile) on purpose**: ``--onefile`` extracts the whole
bundle to ``%TEMP%`` on every launch, which trips heuristic antivirus
detections (e.g. Avast ``IDP.HELU.*``). Onedir + no UPX is the
boring/safe layout that AV engines treat as a normal application.

Big unused PySide6 sub-modules are excluded to keep the bundle small —
this app only uses QtCore / QtGui / QtWidgets / QtSvg (for icons).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
SRC = SPEC_DIR / "src"

# Bundled read-only resources. The first element is the absolute source
# path; the second is the destination path relative to the bundle root
# (i.e. sys._MEIPASS at runtime). The layout mirrors src/ so that
# resource_path("ui", "assets", "icons", "BirdAppIcon.png") resolves
# identically in dev and in the frozen build.
datas = [
    (str(SRC / "ui" / "assets" / "icons"), "ui/assets/icons"),
    (str(SRC / "ui" / "assets" / "dark_theme.qss"), "ui/assets"),
    (str(SRC / "ui" / "assets" / "light_theme.qss"), "ui/assets"),
    (str(SRC / "ui" / "assets" / "style.qss"), "ui/assets"),
    (str(SRC / "data" / "sequence.tst"), "data"),
    (str(SRC / "data" / "demo_system.tst"), "data"),
]

# Force-include cryptography backends — they are lazy imports that
# PyInstaller's static analysis sometimes misses, and the secure log
# Fernet path will blow up at runtime without them.
hiddenimports = collect_submodules("cryptography") + ["dotenv"]

# PyVISA + its pure-python backend are imported lazily inside the Prodigit
# driver (`import pyvisa` only when the real-hardware path runs), so the
# static analysis misses them. Without these the frozen app cannot talk to
# the load on a machine that has HARDWARE_BACKEND=prodigit.
hiddenimports += collect_submodules("pyvisa")
hiddenimports += collect_submodules("pyvisa_py")

excludes = [
    # Heavy PySide6 modules this app does not use. Excluding the Python
    # binding also prevents PyInstaller from pulling the matching Qt6
    # DLLs into the bundle.
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # Other unused stdlib / 3rd-party trees
    "tkinter",
    "unittest.mock",
    "pydoc_data",
    "pytest",
    "IPython",
    "jedi",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    # "serial" removed — connection_settings_form.py imports pyserial for COM-port enumeration
    # NOTE: do NOT exclude PIL — reportlab.lib.utils imports it at load time.
]


a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir layout — binaries live next to the EXE
    name="DFX_Tester",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # off on purpose: UPX-packed bins trip AV heuristics (e.g. IDP.HELU.*)
    console=False,
    disable_windowed_traceback=False,
    icon=str(SRC / "ui" / "assets" / "icons" / "DFXAppIcon.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DFX_Tester",
)
