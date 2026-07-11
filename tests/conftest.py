"""Shared pytest fixtures for hal0 tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# [memory].enabled defaults to True in the schema, so the bulk of the suite
# — which exercises the memory MCP, the /api/memory/* routes, and the
# Hermes memory provider with memory PRESENT — gets it for free via the
# `app`/`client` fixtures' `tmp_hal0_home` isolation, no env var needed. The
# dedicated gate test (tests/api/test_memory_gate.py) writes memory.enabled
# = false explicitly per-test to cover the off path.

pytest_plugins = ()


@pytest.fixture(autouse=True)
def _no_static_slot_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the lifespan's static slot-TOML seeding (flm/tts/rerank/
    utility/img/agent/brain — #1218) for the whole suite by default.

    That hook exists so `hal0 update` converges an existing box; every
    other test's contract (documented on ``app`` below) is an EMPTY
    config tree on TestClient boot — first_run flags, slot-capacity
    math, and install/apply model-pick logic all assume zero slots
    pre-exist. tests/api/test_startup_slot_seed.py overrides this
    fixture (same name) to exercise the real behavior.
    """
    import hal0.install.static_seeds as static_seeds_mod

    monkeypatch.setattr(static_seeds_mod, "seed_static_slots", lambda **_kw: [])


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
