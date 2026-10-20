"""δ-harness: v0.3 persona-activate round-trip.

Exercises the persona swap through the real API app:

1. ``GET /api/agents/hermes/personas`` reflects the seeded persona TOMLs.
2. ``POST /api/agents/hermes/personas/{id}/activate`` writes ``active.txt``
   and reports whether the hot-reload nudge succeeded (``reloaded``).
3. The post-activate persona detail report shows the new ``active`` flag.

The reload nudge's method name and payload are pinned by the unit tests in
``tests/api/test_agents_personas.py``; no Hermes runs here, so this suite
pins only that activation persists and answers either way.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.agents import personas as personas_mod
from hal0.api.agents import personas as personas_route


@pytest.fixture
def seeded_personas_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the personas store to a tmp dir + seed the default
    hermes + hal0-brain personas + the active pointer (= hermes)."""
    root = tmp_path / "personas"
    root.mkdir()
    monkeypatch.setattr(personas_mod, "PERSONAS_ROOT", root)
    monkeypatch.setitem(personas_route._AGENT_PERSONAS_ROOTS, "hermes", root)
    personas_mod.seed_default_personas(agent_id="hermes-agent", root=root)
    yield root


def test_persona_list_reflects_seeded_personas(
    harness_client: TestClient,
    seeded_personas_root: Path,
) -> None:
    r = harness_client.get("/api/agents/hermes/personas")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["active"] == "hermes"
    ids = sorted(row["id"] for row in body["personas"])
    assert ids == ["hal0-brain", "hermes"]


def test_persona_activate_writes_active_txt(
    harness_client: TestClient,
    seeded_personas_root: Path,
) -> None:
    """The activate POST persists ``active.txt`` to the persona root."""
    # Hermes is already active per seed. Switch to hal0-brain.
    r = harness_client.post(
        "/api/agents/hermes/personas/hal0-brain/activate",
        json={"reload": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] == "hal0-brain"
    assert body["previous"] == "hermes"
    assert body["reloaded"] is False  # because we passed reload=false

    # On-disk active.txt must reflect the swap.
    active_file = seeded_personas_root / "active.txt"
    assert active_file.exists()
    assert active_file.read_text().strip() == "hal0-brain"


def test_persona_activate_with_reload_calls_hermes(
    harness_client: TestClient,
    seeded_personas_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reload=true`` (the default) still persists the swap when no hermes answers."""
    r = harness_client.post(
        "/api/agents/hermes/personas/hal0-brain/activate",
        json={"reload": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] == "hal0-brain"
    # ``reloaded`` reflects whether the nudge succeeded; the network helper
    # is pinned by the unit tests, so either outcome is accepted here.
    assert "reloaded" in body


def test_persona_activate_unknown_persona_returns_404(
    harness_client: TestClient,
    seeded_personas_root: Path,
) -> None:
    r = harness_client.post(
        "/api/agents/hermes/personas/nonexistent/activate",
        json={"reload": False},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "persona.not_found"


def test_persona_activate_unknown_agent_returns_404(
    harness_client: TestClient,
    seeded_personas_root: Path,
) -> None:
    r = harness_client.post(
        "/api/agents/pi-coder/personas/hal0-brain/activate",
        json={"reload": False},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


def test_persona_detail_after_activate_shows_new_active(
    harness_client: TestClient,
    seeded_personas_root: Path,
) -> None:
    """Round-trip: activate hal0-brain → GET hal0-brain → ``active=true``."""
    r = harness_client.post(
        "/api/agents/hermes/personas/hal0-brain/activate",
        json={"reload": False},
    )
    assert r.status_code == 200

    r = harness_client.get("/api/agents/hermes/personas/hal0-brain")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "hal0-brain"
    assert body["active"] is True

    # And the previously-active hermes is now inactive.
    r = harness_client.get("/api/agents/hermes/personas/hermes")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "hermes"
    assert body["active"] is False
