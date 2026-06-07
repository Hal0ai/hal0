"""Smoke-import tests for every top-level hal0 submodule.

Catches circular-import and missing __init__.py regressions early.
Each test imports one module and asserts the module object exists.
"""

from __future__ import annotations

import importlib

import pytest

# Hardcoded list of expected top-level submodules (Phase 0 scaffold).
_EXPECTED_MODULES = [
    "hal0.slots",
    "hal0.dispatcher",
    "hal0.providers",
    "hal0.registry",
    "hal0.hardware",
    "hal0.upstreams",
    "hal0.config",
    "hal0.updater",
    "hal0.installer",
    "hal0.openwebui",
    "hal0.cli",
    "hal0.api",
]


@pytest.mark.parametrize("module_name", _EXPECTED_MODULES)
def test_module_imports(module_name: str) -> None:
    """Module can be imported without error and is not None."""
    mod = importlib.import_module(module_name)
    assert mod is not None, f"import_module('{module_name}') returned None"
