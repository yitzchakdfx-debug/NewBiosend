# -*- mode: python ; coding: utf-8 -*-
"""Protected build: same as EM5406_Tester.spec, with our code compiled (no bytecode).

Run tools/build_protected.py first (Cython), then:
    venv\Scripts\python.exe -m PyInstaller --noconfirm EM5406_Tester_protected.spec
Everything else (data, excludes, both EXEs) comes from EM5406_Tester.spec.
"""
from pathlib import Path

PROTECTED_BUILD = True
exec(compile((Path(SPECPATH) / "EM5406_Tester.spec").read_text(encoding="utf-8"), "EM5406_Tester.spec", "exec"))
