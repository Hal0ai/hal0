"""Shared pytest fixtures for hal0 tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# 0.4: the memory subsystem ships disabled by default (HAL0_MEMORY_ENABLED
# gate in create_app). The bulk of the suite exercises the memory MCP, the
# /api/memory/* routes, and the Hermes memory provider with memory PRESENT,
# so default the flag on for tests. `setdefault` runs once at import — before
# any app is built — and respects an explicit override, so the dedicated gate
# test (tests/api/test_memory_gate.py) can still set it to "0" per-test.
os.environ.setdefault("HAL0_MEMORY_ENABLED", "1")

pytest_plugins = ()


@pytest.fixture(scope="function")
def app(tmp_hal0_home: str) -> FastAPI:
    """Return a fresh FastAPI app instance, filesystem-isolated under tmp_hal0_home.

    Auto-applying tmp_hal0_home means every TestClient-driven test starts
    against an empty config tree — no host /etc/hal0/slots/*.toml leaks
    into upstream registration, no host /var/lib/hal0/registry leaks into
    the model list. Tests that need to populate config should write into
    ``Path(tmp_hal0_home) / "etc" / "hal0" / ...`` before constructing
    the client.
    """
    return create_app()


@pytest.fixture(scope="function")
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with lifespan executed (so app.state singletons exist)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def tmp_hal0_home(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set HAL0_HOME to a temporary directory for filesystem isolation.

    Also opts the systemd-override renderer into the HAL0_HOME branch so
    unit-template tests write under tmp_path instead of /etc/systemd/system.
    """
    home = str(tmp_path)
    monkeypatch.setenv("HAL0_HOME", home)
    monkeypatch.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    return home
