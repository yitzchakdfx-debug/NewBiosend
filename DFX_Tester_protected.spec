# -*- mode: python ; coding: utf-8 -*-
"""Protected build: same as DFX_Tester.spec, with our code compiled (no bytecode).

Run tools/build_protected.py first (Cython), then:
    venv\Scripts\python.exe -m PyInstaller --noconfirm DFX_Tester_protected.spec
Everything else (data, excludes, both EXEs) comes from DFX_Tester.spec.
"""
from pathlib import Path

PROTECTED_BUILD = True
exec(compile((Path(SPECPATH) / "DFX_Tester.spec").read_text(encoding="utf-8"), "DFX_Tester.spec", "exec"))
