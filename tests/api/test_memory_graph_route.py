"""Integration tests for ``/api/memory/graph/*``.

Builds a bare FastAPI app + a stub wrapper so we don't have to spin
up a full hal0 lifespan or a real Cognee install. Pins the contract
:mod:`hal0.cli.memory_graph_commands` + the dashboard Memory tab
depend on.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes


class StubWrapper:
    """Minimal duck-type matching :class:`CogneeWrapper.graph_*`."""

    def __init__(self, *, enabled: bool = False, route: str = "upstream") -> None:
        self.enabled = enabled
        self.route = route
        self.set_calls: list[tuple[bool, str | None]] = []

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "route": self.route,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled: bool, route: str | None = None) -> None:
        # Mirror the real CogneeWrapper #451 guard: enabling an unwired
        # route (primary/agent — no v0.3 resolver) is rejected and the
        # gate stays off.
        from hal0.memory.cognee_wrapper import GraphRouteUnsupportedError

        if enabled and (route or self.route) in {"primary", "agent"}:
            raise GraphRouteUnsupportedError(
                f"graph route {route or self.route!r} is not yet supported (lands v0.4)"
            )
        self.set_calls.append((enabled, route))
        self.enabled = enabled
        if route is not None:
            self.route = route


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point loader at a tmp HAL0_HOME with a minimal hal0.toml."""
    home = tmp_path / "hal0-home"
    etc = home / "etc"
    etc.mkdir(parents=True)
    (home / "var-lib").mkdir(parents=True)
    (etc / "hal0.toml").write_text(
        tomli_w.dumps(
            {
                "meta": {"schema_version": 1},
            }
        )
    )
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home


@pytest.fixture
def stub_wrapper() -> StubWrapper:
    return StubWrapper()


def _build_app(stub: StubWrapper) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_wrapper = stub
    return app


@pytest.fixture
def client(stub_wrapper: StubWrapper, hal0_home: Path) -> Iterator[TestClient]:
    app = _build_app(stub_wrapper)
    with TestClient(app) as c:
        yield c


def test_graph_status_default_returns_off(client: TestClient, stub_wrapper: StubWrapper) -> None:
    r = client.get("/api/memory/graph/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["route"] == "upstream"
    assert body["upstream"] is None
    assert body["builds_ok"] == 0


def test_put_enable_with_primary_route_rejected(
    client: TestClient, stub_wrapper: StubWrapper, hal0_home: Path
) -> None:
    # Issue #451 — v0.3 has no route resolver for primary/agent (lands
    # v0.4). Enabling either must fail fast (422) and never flip the gate.
    r = client.put(
        "/api/memory/graph",
        json={"enabled": True, "route": "primary"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "config.memory_graph_route_unsupported"
    # Wrapper was NOT flipped on.
    assert stub_wrapper.set_calls == []
    assert stub_wrapper.enabled is False


def test_put_enable_with_agent_route_rejected(
    client: TestClient, stub_wrapper: StubWrapper, hal0_home: Path
) -> None:
    r = client.put(
        "/api/memory/graph",
        json={"enabled": True, "route": "agent"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "config.memory_graph_route_unsupported"
    assert stub_wrapper.set_calls == []
    assert stub_wrapper.enabled is False


def test_put_enable_upstream_without_model_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    r = client.put(
        "/api/memory/graph",
        json={"enabled": True, "route": "upstream"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "config.memory_graph_invalid"


def test_put_enable_upstream_with_provider_and_model(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    r = client.put(
        "/api/memory/graph",
        json={
            "enabled": True,
            "route": "upstream",
            "upstream": {
                "provider": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["upstream"]["model"] == "anthropic/claude-3.5-sonnet"


def test_put_disable_idempotent(client: TestClient, stub_wrapper: StubWrapper) -> None:
    r = client.put("/api/memory/graph", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_put_bad_route_returns_400(client: TestClient, stub_wrapper: StubWrapper) -> None:
    r = client.put("/api/memory/graph", json={"route": "bogus"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "config.memory_graph_invalid"


def test_status_unavailable_when_no_wrapper(
    hal0_home: Path,
) -> None:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_wrapper = None
    with TestClient(app) as c:
        r = c.get("/api/memory/graph/status")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "memory.unavailable"
