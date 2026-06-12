"""HTTP tests for ``PUT /api/agents/{agent_id}/personas/{persona_id}``.

Pins the persona-update contract the dashboard's ``PersonaEditModal``
calls. Mutable fields persist to the persona TOML; ``id`` is immutable;
``budget`` round-trips untouched; validation rejects a bad
``default_policy``. Personas store is redirected to tmp_path so the test
runner never writes to /var/lib/hal0.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.agents import personas as personas_mod
from hal0.api.agents import personas as personas_route


@pytest.fixture
def personas_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "personas"
    root.mkdir()
    monkeypatch.setattr(personas_mod, "PERSONAS_ROOT", root)
    monkeypatch.setitem(personas_route._AGENT_PERSONAS_ROOTS, "hermes", root)
    yield root


@pytest.fixture
def seeded(personas_root: Path) -> Path:
    personas_mod.seed_default_personas(agent_id="hermes-agent", root=personas_root)
    return personas_root


def test_update_persists_mutable_fields(client: TestClient, seeded: Path) -> None:
    r = client.put(
        "/api/agents/hermes/personas/hermes",
        json={
            "display_name": "Hermes Prime",
            "summary": "updated summary",
            "system_prompt": "You are Hermes Prime.",
            "tools_allowed": ["memory.*", "slot.read.*"],
            "preferred_model": "qwen3-coder",
            "approval": {"default_policy": "auto-approve"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "hermes"
    assert body["display_name"] == "Hermes Prime"
    assert body["summary"] == "updated summary"
    assert body["system_prompt"] == "You are Hermes Prime."
    assert body["tools_allowed"] == ["memory.*", "slot.read.*"]
    assert body["preferred_model"] == "qwen3-coder"
    assert body["approval"]["default_policy"] == "auto-approve"

    # Persisted to disk — reload proves the round-trip.
    reloaded = personas_mod.load_persona("hermes", root=seeded)
    assert reloaded.display_name == "Hermes Prime"
    assert reloaded.approval.default_policy == "auto-approve"


def test_update_is_partial(client: TestClient, seeded: Path) -> None:
    """Omitted fields are left unchanged."""
    before = personas_mod.load_persona("hermes", root=seeded)
    r = client.put("/api/agents/hermes/personas/hermes", json={"summary": "only summary"})
    assert r.status_code == 200, r.text
    after = personas_mod.load_persona("hermes", root=seeded)
    assert after.summary == "only summary"
    # System prompt untouched.
    assert after.system_prompt == before.system_prompt
    assert after.tools_allowed == before.tools_allowed


def test_update_cannot_change_id(client: TestClient, seeded: Path) -> None:
    """An ``id`` in the body is ignored — the path/filename is authoritative."""
    r = client.put(
        "/api/agents/hermes/personas/hermes",
        json={"display_name": "X"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "hermes"
    # No stray persona file was created.
    assert {p.stem for p in seeded.glob("*.toml")} == {"hermes", "coder"}


def test_update_preserves_budget(client: TestClient, seeded: Path) -> None:
    """Budget round-trips untouched (it has its own /budget route)."""
    before = personas_mod.load_persona("hermes", root=seeded)
    client.put("/api/agents/hermes/personas/hermes", json={"summary": "s"})
    after = personas_mod.load_persona("hermes", root=seeded)
    assert after.budget == before.budget


def test_update_rejects_bad_default_policy(client: TestClient, seeded: Path) -> None:
    r = client.put(
        "/api/agents/hermes/personas/hermes",
        json={"approval": {"default_policy": "whenever"}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "persona.invalid"


def test_update_unknown_persona_404(client: TestClient, seeded: Path) -> None:
    r = client.put("/api/agents/hermes/personas/ghost", json={"summary": "s"})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "persona.not_found"


def test_update_unknown_agent_404(client: TestClient, seeded: Path) -> None:
    r = client.put("/api/agents/nobody/personas/hermes", json={"summary": "s"})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "agent.unknown"
