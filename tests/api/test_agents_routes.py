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
        {"path": "/var/lib/hal0/.hermes/runtime.json", "reason": "PermissionError: [Errno 13]"}
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
