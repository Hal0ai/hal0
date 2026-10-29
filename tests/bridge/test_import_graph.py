"""The bridge package sits below the API package in the import graph (ADR-0043).

``hal0.api`` imports ``hal0.bridge.service`` lazily, when the bridge boots. If a
``hal0.bridge`` module imported anything under ``hal0.api`` at module level, even
a helper module such as ``hal0.api._redact``, Python would run the whole
``hal0.api`` package ``__init__`` first: a cycle waiting for the first eager import.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_BRIDGE_MODULES = (
    "hal0.bridge.service",
    "hal0.bridge.capabilities",
    "hal0.bridge.skills",
    "hal0.bridge.watcher",
    "hal0.bridge.caller",
)


@pytest.mark.parametrize("module", _BRIDGE_MODULES)
def test_bridge_module_import_does_not_load_the_api_package(module: str) -> None:
    # A clean interpreter: this process's sys.modules already holds hal0.api.
    snippet = (
        "import sys\n"
        f"import {module}\n"
        "print(repr(sorted(m for m in sys.modules if m == 'hal0.api' or m.startswith('hal0.api.'))))\n"
    )
    proc = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    last = [line for line in proc.stdout.splitlines() if line.strip()][-1]
    assert last == "[]", f"importing {module} loaded the API package: {last}"
