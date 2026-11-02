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

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
SRC = SPEC_DIR / "src"

# Protected build (DFX_Tester_protected.spec sets PROTECTED_BUILD): the scripts
# and modules come from build/protected (made by tools/build_protected.py) and
# every module of ours ships as a compiled .pyd instead of bytecode in the PYZ.
PROTECTED = bool(globals().get("PROTECTED_BUILD", False))
CODE = SPEC_DIR / "build" / "protected" / "py" if PROTECTED else SRC
COMPILED = SPEC_DIR / "build" / "protected" / "pyd"

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
    # Ship ONLY the spec-conformant script. It is `_DEFAULT_SCRIPT_NAME` in
    # main_window.py, so the app opens it on a fresh install -- yet the bundle
    # used to carry `sequence.tst` (a 5 V demo) and `demo_system.tst` instead and
    # omit this one, leaving a new station with no runnable test. Extra scripts
    # are a hazard on a production floor: an operator can pick one whose limits
    # look right but whose load and durations are not (see the deleted
    # biosend_test_TEMP.tst, which "passed" a 300 W / 30 min burn-in in 30 s at
    # ~6 W). Admins can still import other versions through the Version Manager.
    (str(SRC / "data" / "biosense_test.tst"), "data"),
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
# USB to the load (2026-10-29): pyvisa-py imports these lazily too. pyserial for
# a USB link that shows up as a COM port; pyusb + libusb_package (which carries
# the libusb DLL) for USBTMC.
hiddenimports += collect_submodules("serial") + collect_submodules("usb") + ["libusb_package"]
datas += collect_data_files("libusb_package")
binaries = collect_dynamic_libs("libusb_package")

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
    [str(CODE / "main.py")],
    pathex=[str(CODE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)



def _protect(analysis):
    """Drop our bytecode from the PYZ and ship the compiled .pyd in its place.

    The analysis still ran on the sources, so every third-party import our
    code makes was found. Package __init__ files stay (docstrings only).
    Stops the build if any module of ours has no compiled counterpart.
    """
    code = CODE.resolve()
    ours = {
        p.resolve().relative_to(code).with_suffix("").as_posix()
        for p in code.rglob("*.py")
        if p.name != "__init__.py" and "data" not in p.relative_to(code).parts
    } - {"main", "xml_service"}  # the 3-line entry stubs
    compiled = {}
    for pyd in COMPILED.rglob("*.pyd"):
        rel = pyd.relative_to(COMPILED)
        compiled[(rel.parent / rel.name.split(".")[0]).as_posix()] = (rel.as_posix(), str(pyd))
    lacking = sorted(ours - set(compiled))
    if lacking:
        raise SystemExit(f"Protected build: not compiled: {lacking}")

    def is_ours(entry):
        try:
            rel = Path(entry[1]).resolve().relative_to(code)
        except (ValueError, OSError, TypeError):
            return False
        return rel.with_suffix("").as_posix() in ours

    analysis.pure = [e for e in analysis.pure if not is_ours(e)]
    for dest, src in compiled.values():
        analysis.binaries.append((dest, src, "EXTENSION"))


if PROTECTED:
    _protect(a)
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

# The XML transfer service (customer POD): a second, windowless program in the
# same folder, sharing `_internal`. It must run without the GUI (and keeps
# running when the GUI is closed), so it is its own EXE rather than a thread.
# No Qt: it only copies files, so its analysis excludes PySide6 entirely.
svc = Analysis(
    [str(CODE / "xml_service.py")],
    pathex=[str(CODE)],
    binaries=[],
    datas=[],
    hiddenimports=["dotenv"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes + ["PySide6", "shiboken6", "reportlab", "PIL", "pyvisa", "pyvisa_py", "serial", "usb"],
    noarchive=False,
)
if PROTECTED:
    _protect(svc)
svc_pyz = PYZ(svc.pure)
svc_exe = EXE(
    svc_pyz,
    svc.scripts,
    [],
    exclude_binaries=True,
    name="DFX_XmlTransfer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(SRC / "ui" / "assets" / "icons" / "DFXAppIcon.png"),
)

coll = COLLECT(
    exe,
    svc_exe,
    a.binaries,
    a.datas,
    svc.binaries,
    svc.datas,
    strip=False,
    upx=False,
    name="DFX_Tester",
)
