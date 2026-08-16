"""Shared fixtures and environment setup for analytics worker tests."""

import os
import sys
from pathlib import Path

# Settings are constructed when app modules are imported during collection.
os.environ.setdefault("ENCRYPTION_KEY", "test-key-is-set-by-individual-tests")
os.environ.setdefault("WORKER_TOKEN", "test-worker-token")

# Ensure dp-core and query-ir packages are importable.
# In production these are installed via pyproject.toml, but in dev/test
# the packages directory needs to be on sys.path.
_packages_root = Path(__file__).resolve().parents[2] / "packages"
for package_dir in ("dp-core", "query-ir"):
    pkg_path = str(_packages_root / package_dir)
    if pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)
