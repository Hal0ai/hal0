"""Shared pytest fixtures for hal0 tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

pytest_plugins = ()


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt every test out of the v1.0 auth-on-by-default posture.

    As of security review §36 (2026-05-21), ``auth_enabled()`` defaults
    to True when ``HAL0_AUTH_ENABLED`` is unset. The pre-v1 test suite
    was written against an open-by-default API surface — propagating
    the new default into every fixture would require minting an admin
    token in ~449 places. The cheap, contained alternative is to make
    every test start with ``HAL0_AUTH_DISABLED=1`` and let the small
    set of auth-focused tests flip it off again via their own
    ``monkeypatch.delenv`` + ``monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")``
    setup.

    Autouse so import-time fixture choice is implicit; individual
    auth-aware tests (test_auth_middleware, test_auth_writer_scope,
    test_no_public_paths, test_password_auth, test_auth_routes) start
    by deleting ``HAL0_AUTH_DISABLED`` and setting ``HAL0_AUTH_ENABLED=1``
    so they exercise the real production gate.
    """
    monkeypatch.setenv("HAL0_AUTH_DISABLED", "1")


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
