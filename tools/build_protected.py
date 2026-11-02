"""Protected build, step 1: compile our Python modules to native code (Cython).

A normal PyInstaller build keeps our code as Python bytecode inside the EXE,
which free tools can extract and decompile. This step turns every one of our
modules into a compiled extension (.pyd), so the protected build
(`DFX_Tester_protected.spec`) ships no bytecode of ours at all. The source in
`src/` is not changed; everything happens in `build/protected/`.

Output:
    build/protected/py/   sources as built: main.py and xml_service.py become
                          3-line stubs, their code moves to dfx_main.py and
                          dfx_xml_service.py (so it is compiled too)
    build/protected/pyd/  the compiled .pyd files, same package layout
    build/protected/run/  a runnable tree (stubs, __init__.py, .pyd, assets,
                          data) used to test the compiled code before packaging

Usage (from the repo root, needs Cython and the MSVC Build Tools):
    venv\\Scripts\\python.exe tools\\build_protected.py
Then:
    venv\\Scripts\\python.exe -m PyInstaller DFX_Tester_protected.spec
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "build" / "protected"
PY, PYD, RUN = OUT / "py", OUT / "pyd", OUT / "run"

#: Never copied into the build: run-time state and bench-only scripts.
_IGNORE = shutil.ignore_patterns(
    "__pycache__", "results", "logs", "tmp", "xml_transfer", "*.db", "*.lock",
    "*.bak*", "BENCH_ONLY_*", "*.pyc",
)

MAIN_STUB = '''"""Entry point of the protected build; the application is in dfx_main (compiled)."""
from dfx_main import _exit_now, main

_exit_now(main())
'''

SERVICE_STUB = '''"""Entry point of the protected build; the service is in dfx_xml_service (compiled)."""
import sys

from dfx_xml_service import main

sys.exit(main())
'''


def compiled_modules() -> list[Path]:
    """Every module of ours to compile: all .py except package __init__ and the stubs."""
    return sorted(
        p for p in PY.rglob("*.py")
        if p.name != "__init__.py"
        and p.relative_to(PY).as_posix() not in ("main.py", "xml_service.py")
        and "data" not in p.relative_to(PY).parts
    )


def prepare_sources() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SRC, PY, ignore=_IGNORE)
    (PY / "main.py").rename(PY / "dfx_main.py")
    (PY / "xml_service.py").rename(PY / "dfx_xml_service.py")
    (PY / "main.py").write_text(MAIN_STUB, encoding="utf-8")
    (PY / "xml_service.py").write_text(SERVICE_STUB, encoding="utf-8")


def compile_modules(modules: list[Path]) -> None:
    """Cython + MSVC, through setuptools, into PYD (one process; stops on any error)."""
    setup = OUT / "_setup.py"
    names = [".".join(m.relative_to(PY).with_suffix("").parts) for m in modules]
    # Relative paths, run from OUT: absolute ones made the object-file paths
    # longer than Windows' 260 characters and the linker failed.
    sources = [m.relative_to(OUT).as_posix() for m in modules]
    # Annotations are documentation here, never runtime type checks: with
    # annotation_typing on, Cython would reject e.g. an int passed where a
    # float is annotated, or None where a str is.
    directives = {
        "language_level": 3,
        "annotation_typing": False,
        "binding": True,
        "always_allow_keywords": True,
        "embedsignature": False,
    }
    setup.write_text(
        "from setuptools import setup, Extension\n"
        "from Cython.Build import cythonize\n"
        f"mods = {list(zip(names, sources))!r}\n"
        "setup(\n"
        "    script_args=['build_ext', '--build-lib', 'pyd', '--build-temp', 't'],\n"
        "    ext_modules=cythonize(\n"
        "        [Extension(n, [s]) for n, s in mods],\n"
        "        build_dir='c',\n"
        f"        compiler_directives={directives!r},\n"
        "        quiet=True,\n"
        "    ),\n"
        ")\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(setup)], cwd=str(OUT),
        stdout=subprocess.DEVNULL if "-q" in sys.argv else None,
    )
    if result.returncode != 0:
        raise SystemExit("Compilation failed - nothing was packaged.")
    missing = [n for n in names if not list(PYD.glob(n.replace(".", "/") + ".*.pyd"))]
    if missing:
        raise SystemExit(f"Not compiled: {missing}")


def make_run_tree(modules: list[Path]) -> None:
    """Sources minus the compiled .py, plus the .pyd: what the EXE will contain."""
    shutil.copytree(PY, RUN)
    for m in modules:
        (RUN / m.relative_to(PY)).unlink()
    for pyd in PYD.rglob("*.pyd"):
        target = RUN / pyd.relative_to(PYD)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pyd, target)


def main() -> int:
    prepare_sources()
    modules = compiled_modules()
    print(f"compiling {len(modules)} modules ...")
    compile_modules(modules)
    make_run_tree(modules)
    left = [p.relative_to(RUN).as_posix() for p in RUN.rglob("*.py")
            if "data" not in p.relative_to(RUN).parts]
    print(f"done: {len(list(PYD.rglob('*.pyd')))} .pyd; plain .py left (stubs + __init__): {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
