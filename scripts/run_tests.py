#!/usr/bin/env python3
"""Run every Python test suite in the repository.

Each package pins ``pythonpath = ["."]`` in its own ``pyproject.toml``, so a
suite only collects correctly when pytest is invoked from that package's
directory.  Running ``pytest`` from the repository root silently mis-resolves
``app`` and ``dp_core`` imports.  This script invokes each suite from its own
root with the interpreter that is running this file.

Usage:
    python scripts/run_tests.py            # all suites
    python scripts/run_tests.py dp-core    # one suite by name
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SUITES: dict[str, Path] = {
    "dp-core": ROOT / "packages" / "dp-core",
    "query-ir": ROOT / "packages" / "query-ir",
    "dp-audit": ROOT / "packages" / "dp-audit",
    "analytics-worker": ROOT / "services" / "analytics-worker",
}


def run_suite(name: str, directory: Path) -> tuple[str, int]:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", flush=True)
    if not directory.is_dir():
        print(f"missing directory: {directory}", file=sys.stderr)
        return (name, 1)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=directory,
    )
    return (name, completed.returncode)


def main() -> int:
    requested = sys.argv[1:]
    unknown = [name for name in requested if name not in SUITES]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(SUITES)}", file=sys.stderr)
        return 2

    selected = requested or list(SUITES)
    results = [run_suite(name, SUITES[name]) for name in selected]

    print(f"\n{'=' * 70}\nsummary\n{'=' * 70}")
    for name, code in results:
        print(f"  {'PASS' if code == 0 else 'FAIL'}  {name}")

    return 0 if all(code == 0 for _, code in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
