"""Manual check: every `.tst` in src/data survives parse -> serialize -> parse.

`ScriptManager.serialize_ordered_steps` is what the limits editor and the
version manager write back to `test_versions`. Any step-level field the parser
understands but the serializer omits is silently destroyed the first time an
Admin saves — this script catches that regression.

Run:  venv\\Scripts\\python.exe test_script_roundtrip.py
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from logic.script_manager import ScriptManager  # noqa: E402


def check(sm: ScriptManager, path: Path, scratch: Path) -> list[str]:
    """Return a list of human-readable differences (empty means clean)."""
    doc1 = sm.load_document(path)
    scratch.write_text(
        sm.serialize_ordered_steps(doc1.steps, metadata=doc1.metadata),
        encoding="utf-8",
    )
    doc2 = sm.load_document(scratch)

    problems: list[str] = []
    if len(doc1.steps) != len(doc2.steps):
        return [f"step count {len(doc1.steps)} -> {len(doc2.steps)}"]

    for before, after in zip(doc1.steps, doc2.steps):
        fields_before, fields_after = asdict(before), asdict(after)
        for key, value in fields_before.items():
            if value != fields_after[key]:
                problems.append(
                    f"step {before.name!r}: {key} {value!r} -> {fields_after[key]!r}"
                )
    if doc1.metadata != doc2.metadata:
        problems.append(f"metadata {doc1.metadata} -> {doc2.metadata}")
    return problems


def main() -> int:
    scripts = sorted((SRC / "data").glob("*.tst"))
    if not scripts:
        print("no .tst files found under src/data")
        return 1

    sm = ScriptManager()
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "roundtrip.tst"
        for path in scripts:
            problems = check(sm, path, scratch)
            if problems:
                failed += 1
                print(f"FAIL {path.name}")
                for line in problems:
                    print(f"       {line}")
            else:
                print(f"ok   {path.name}")

    print()
    print(f"{failed} of {len(scripts)} scripts lost data" if failed else "all scripts round-trip cleanly")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
