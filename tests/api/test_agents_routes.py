"""Integration tests for ``/api/agents/*`` enum + skills surface.

Covers the two read-only catalogues the dashboard's Agent surface
consumes:

  - ``GET /api/agents/persona-enums``  — PersonaEditModal (#226)
  - ``GET /api/agents/skills``         — Agent > Skills tab  (#227)

Both routes are stateless veneers over :mod:`hal0.agents.persona`, so
the test pins the response shape + a couple of guard-rail invariants
(non-empty payload, no duplicate ids) rather than the full enum
membership — that lives in the module itself and would just churn
the test on every catalogue add.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import agents as agents_routes


def _build_app() -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(agents_routes.router, prefix="/api/agents", tags=["agents"])
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_app()) as c:
        yield c


# ── /api/agents/persona-enums ────────────────────────────────────────────


def test_persona_enums_shape(client: TestClient) -> None:
    res = client.get("/api/agents/persona-enums")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"tones", "tools"}

    tones = body["tones"]
    tools = body["tools"]
    assert isinstance(tones, list) and tones, "tones must be non-empty"
    assert isinstance(tools, list) and tools, "tools must be non-empty"

    # Every entry carries id/label; tones have desc, tools have cap.
    for t in tones:
        assert {"id", "label", "desc"} <= set(t.keys())
        assert t["id"] and t["label"]
    for t in tools:
        assert {"id", "label", "cap"} <= set(t.keys())
        assert t["id"] and t["label"] and t["cap"]


def test_persona_enums_ids_unique(client: TestClient) -> None:
    body = client.get("/api/agents/persona-enums").json()
    tone_ids = [t["id"] for t in body["tones"]]
    tool_ids = [t["id"] for t in body["tools"]]
    assert len(tone_ids) == len(set(tone_ids)), "duplicate tone ids"
    assert len(tool_ids) == len(set(tool_ids)), "duplicate tool ids"


def test_persona_enums_operator_default_present(client: TestClient) -> None:
    # The modal seeds tone="operator" — guard so we don't accidentally
    # rename it out from under the UI.
    tone_ids = [t["id"] for t in client.get("/api/agents/persona-enums").json()["tones"]]
    assert "operator" in tone_ids


# ── /api/agents/skills ───────────────────────────────────────────────────


def test_skills_catalog_shape(client: TestClient) -> None:
    res = client.get("/api/agents/skills")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"skills", "count"}
    skills = body["skills"]
    assert isinstance(skills, list) and skills
    assert body["count"] == len(skills)

    expected_keys = {"name", "cap", "policy", "src"}
    for s in skills:
        assert expected_keys <= set(s.keys())
        assert s["policy"] in {"always", "remember", "auto", "deny"}


def test_skills_catalog_names_unique(client: TestClient) -> None:
    skills = client.get("/api/agents/skills").json()["skills"]
    names = [s["name"] for s in skills]
    assert len(names) == len(set(names)), "duplicate skill names"


# ── DELETE /api/agents/{name} — O16 uninstall surface ────────────────────────


def test_delete_agent_partial_removal_returns_207_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O16: a partial uninstall (root-owned entries the service can't remove)
    surfaces a typed 207 with the residual — never a bare 500."""
    from hal0.agents import AgentUninstallIncompleteError

    residual = [
        {"path": "/var/lib/hal0/.hermes/provision.json", "reason": "PermissionError: [Errno 13]"}
    ]

    class _FakeMgr:
        def uninstall(self, name: str) -> bool:
            raise AgentUninstallIncompleteError(name, residual=residual, had_artifacts=True)

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())

    res = client.delete("/api/agents/hermes")
    assert res.status_code == 207
    body = res.json()
    assert body["error"]["code"] == "agent.uninstall_incomplete"
    details = body["error"]["details"]
    assert details["removed"] is True
    assert details["status"] == "partially_uninstalled"
    assert details["residual"] == residual


def test_delete_agent_clean_uninstall_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean uninstall keeps the 200 ``uninstalled`` / ``not_installed`` shape."""

    class _FakeMgr:
        def uninstall(self, name: str) -> bool:
            return True

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())

    res = client.delete("/api/agents/hermes")
    assert res.status_code == 200
    assert res.json() == {"name": "hermes", "status": "uninstalled"}


# ── cli-kind agents are local-only on the wire (pi daemon-home bug) ──────


def test_install_refuses_cli_kind_agent(client: TestClient) -> None:
    """The daemon runs as a system user whose home is /var/lib/hal0 — a
    cli-kind agent (pi) provisions the invoking user's home + the global
    npm prefix, so the wire path must refuse with the CLI hint instead of
    silently provisioning the wrong HOME."""
    res = client.post("/api/agents/install", json={"name": "pi"})
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "agent.cli_kind_local_only"
    assert "hal0 agent install pi" in body["error"]["message"]


def test_uninstall_refuses_cli_kind_agent(client: TestClient) -> None:
    res = client.delete("/api/agents/pi")
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "agent.cli_kind_local_only"
    assert "hal0 agent uninstall pi" in body["error"]["message"]


# ── POST /api/agents/hermes/reconcile-upstreams ──────────────────────────────


def test_reconcile_upstreams_route_returns_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []
    report = {"registered": ["hermes"], "removed": [], "skipped": [], "profiles_without_key": []}

    async def fake(registry: object, **_: object) -> dict[str, object]:
        seen.append(registry)
        return report

    class _Mgr:
        def installed_names(self) -> list[str]:
            return ["hermes"]

    monkeypatch.setattr(agents_routes, "_manager", lambda: _Mgr())
    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", fake)
    # Codex P2: the route sweeps keys first, like boot and the periodic pass.
    monkeypatch.setattr("hal0.agents.hermes_api.ensure_profile_keys", lambda home: ["later"])
    client.app.state.upstreams = sentinel = object()  # type: ignore[attr-defined]

    res = client.post("/api/agents/hermes/reconcile-upstreams")
    assert res.status_code == 200
    assert res.json() == {"ok": True, **report, "keys_written": ["later"], "profiles_wired": []}
    assert seen == [sentinel]


def test_reconcile_upstreams_404_when_not_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Mgr:
        def installed_names(self) -> list[str]:
            return []

    monkeypatch.setattr(agents_routes, "_manager", lambda: _Mgr())
    res = client.post("/api/agents/hermes/reconcile-upstreams")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "agent.not_installed"


# ── Codex P2: install/uninstall re-mirror the Hermes upstreams at once ────────


def _spy_sync(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def fake(registry: object, **kw: object) -> dict[str, object]:
        calls.append({"registry": registry, **kw})
        return {"registered": [], "removed": ["hermes"], "keys_written": []}

    monkeypatch.setattr("hal0.agents.hermes_upstreams.sync_hermes_upstreams", fake)
    return calls


def test_uninstall_hermes_reconciles_upstreams_immediately(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeMgr:
        def uninstall(self, name: str) -> bool:
            return True

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())
    calls = _spy_sync(monkeypatch)
    client.app.state.upstreams = reg = object()  # type: ignore[attr-defined]
    client.app.state.upstream_models = cache = {}  # type: ignore[attr-defined]
    res = client.delete("/api/agents/hermes")
    assert res.status_code == 200
    assert calls == [{"registry": reg, "model_cache": cache}]


def test_uninstall_hermes_reconciles_even_when_partial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.agents.manager import AgentUninstallIncompleteError

    class _FakeMgr:
        def uninstall(self, name: str) -> bool:
            raise AgentUninstallIncompleteError(
                "hermes", had_artifacts=True, residual=[{"path": "y", "reason": "root-owned"}]
            )

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())
    calls = _spy_sync(monkeypatch)
    client.app.state.upstreams = object()  # type: ignore[attr-defined]
    res = client.delete("/api/agents/hermes")
    assert res.status_code == 207
    assert len(calls) == 1


def test_uninstall_other_agent_does_not_reconcile(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeMgr:
        def uninstall(self, name: str) -> bool:
            return True

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())
    calls = _spy_sync(monkeypatch)
    assert client.delete("/api/agents/pi").status_code == 400  # cli-kind: refused
    assert calls == []


def test_bundled_name_guard_matches_bundled_agents() -> None:
    """The routes' literal allowlist (the path-injection barrier) is BUNDLED_AGENTS."""
    import ast
    import inspect
    import re

    from hal0.agents.manager import BUNDLED_AGENTS

    src = inspect.getsource(agents_routes._bundled_name)
    literal = re.search(r"if name not in (\(.*?\)):", src)
    assert literal is not None
    assert ast.literal_eval(literal.group(1)) == BUNDLED_AGENTS
    for name in BUNDLED_AGENTS:
        assert agents_routes._bundled_name(name) == name


@pytest.mark.parametrize("path", ["/api/agents/../etc", "/api/agents/nope"])
def test_unknown_agent_name_is_refused_before_any_file_access(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    class _Boom:
        def uninstall(self, name: str) -> bool:
            raise AssertionError("manager must not be reached")

    monkeypatch.setattr(agents_routes, "_manager", lambda: _Boom())
    res = client.delete(path)
    assert res.status_code == 404
    res = client.post("/api/agents/install", json={"name": "nope"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "agent.unknown"


def test_install_hermes_reconciles_upstreams_immediately(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Rec:
        def as_dict(self) -> dict[str, object]:
            return {"name": "hermes"}

    class _FakeMgr:
        def install(self, name: str, switch: bool = False) -> _Rec:
            return _Rec()

    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeMgr())
    monkeypatch.setattr(agents_routes, "agent_kind", lambda name: "daemon")
    calls = _spy_sync(monkeypatch)
    client.app.state.upstreams = object()  # type: ignore[attr-defined]
    res = client.post("/api/agents/install", json={"name": "hermes"})
    assert res.status_code == 200, res.text
    assert len(calls) == 1
