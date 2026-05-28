"""HTTP tests for ``/api/agents/{agent_id}/personas`` (PR-4, v0.3).

Pins the route shape the dashboard persona picker (PR-8) and any future
CLI/automation consumers depend on. Each test points the personas store
at a tmp_path via monkeypatching :data:`hal0.agents.personas.PERSONAS_ROOT`
+ the API module's registry so we don't write to ``/var/lib/hal0`` from
the test runner.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.agents import personas as personas_mod
from hal0.api.agents import personas as personas_route


@pytest.fixture
def personas_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Redirect the personas store to a tmp dir for the whole test.

    Both the module-level :data:`PERSONAS_ROOT` (used by helpers like
    :func:`hermes_reload`) and the route module's agent registry are
    rewired so the API handlers resolve agent_id="hermes" against the
    tmp dir.
    """
    root = tmp_path / "personas"
    root.mkdir()
    monkeypatch.setattr(personas_mod, "PERSONAS_ROOT", root)
    monkeypatch.setitem(personas_route._AGENT_PERSONAS_ROOTS, "hermes", root)
    yield root


@pytest.fixture
def seeded_personas(personas_root: Path) -> Path:
    """Seed the hermes + coder personas + the active pointer (= hermes)."""
    personas_mod.seed_default_personas(agent_id="hermes-agent", root=personas_root)
    return personas_root


# ── list ────────────────────────────────────────────────────────────────────


def test_list_returns_seeded_personas(client: TestClient, seeded_personas: Path) -> None:
    r = client.get("/api/agents/hermes/personas")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["active"] == "hermes"
    ids = sorted(row["id"] for row in body["personas"])
    assert ids == ["coder", "hermes"]
    hermes_row = next(row for row in body["personas"] if row["id"] == "hermes")
    assert hermes_row["active"] is True
    assert hermes_row["display_name"] == "Hermes"
    assert "summary" in hermes_row
    coder_row = next(row for row in body["personas"] if row["id"] == "coder")
    assert coder_row["active"] is False


def test_list_empty_when_store_empty(client: TestClient, personas_root: Path) -> None:
    r = client.get("/api/agents/hermes/personas")
    assert r.status_code == 200
    body = r.json()
    assert body["personas"] == []
    assert body["active"] is None


def test_list_unknown_agent_returns_404(client: TestClient, personas_root: Path) -> None:
    r = client.get("/api/agents/pi-coder/personas")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


# ── detail ──────────────────────────────────────────────────────────────────


def test_detail_returns_parsed_and_raw_toml(client: TestClient, seeded_personas: Path) -> None:
    r = client.get("/api/agents/hermes/personas/hermes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "hermes"
    assert body["display_name"] == "Hermes"
    assert body["active"] is True
    assert body["system_prompt"].startswith("You are Hermes")
    assert isinstance(body["tools_allowed"], list)
    assert body["approval"]["default_policy"] == "ask"
    assert "memory.read.*" in body["approval"]["auto_approve"]
    # Raw TOML is the source-of-truth body the editor surface needs.
    assert "[persona]" in body["raw_toml"]
    assert 'id = "hermes"' in body["raw_toml"]


def test_detail_inactive_persona_reports_active_false(
    client: TestClient, seeded_personas: Path
) -> None:
    r = client.get("/api/agents/hermes/personas/coder")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "coder"
    assert body["active"] is False


def test_detail_unknown_persona_returns_404(client: TestClient, seeded_personas: Path) -> None:
    r = client.get("/api/agents/hermes/personas/ghost")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "persona.not_found"


def test_detail_unknown_agent_returns_404(client: TestClient, personas_root: Path) -> None:
    r = client.get("/api/agents/pi-coder/personas/anything")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


def test_detail_malformed_toml_returns_400(client: TestClient, personas_root: Path) -> None:
    (personas_root / "broken.toml").write_text(
        "[persona]\nid = 'broken'\ndisplay_name = unterminated", encoding="utf-8"
    )
    r = client.get("/api/agents/hermes/personas/broken")
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "persona.malformed"
    # The path-leak guard rewrites ``/path: …`` → ``<persona>: …``.
    msg = body["error"]["message"]
    assert str(personas_root) not in msg


@pytest.mark.parametrize(
    "toml_body, expected_substring",
    [
        # Missing [persona].id
        (
            '[persona]\ndisplay_name = "Bad"\n',
            "id is required",
        ),
        # Invalid default_policy
        (
            '[persona]\nid = "bad-policy"\ndisplay_name = "Bad"\n'
            '[persona.approval]\ndefault_policy = "yolo"\n',
            "default_policy",
        ),
    ],
)
def test_detail_invalid_persona_returns_400(
    client: TestClient,
    personas_root: Path,
    toml_body: str,
    expected_substring: str,
) -> None:
    # Filename stem must align with [persona].id for the id-mismatch
    # branch not to fire first; we deliberately exercise the missing-id
    # + bad-policy errors instead.
    persona_file = "bad-policy" if "bad-policy" in toml_body else "noid"
    (personas_root / f"{persona_file}.toml").write_text(toml_body, encoding="utf-8")
    r = client.get(f"/api/agents/hermes/personas/{persona_file}")
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "persona.malformed"
    assert expected_substring in body["error"]["message"]


# ── activate ────────────────────────────────────────────────────────────────


def test_activate_switches_active_and_invokes_hot_reload(
    client: TestClient,
    seeded_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default body (no ``reload`` key) triggers the hot-reload helper."""
    calls: list[dict[str, Any]] = []

    def _fake_reload(**kwargs: Any) -> tuple[bool, str | None]:
        calls.append(kwargs)
        return (True, None)

    monkeypatch.setattr(personas_mod, "hermes_reload", _fake_reload)

    r = client.post("/api/agents/hermes/personas/coder/activate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["active"] == "coder"
    assert body["previous"] == "hermes"
    assert body["reloaded"] is True
    assert body["reload_error"] is None
    # The pointer file actually moved.
    assert personas_mod.get_active(root=seeded_personas) == "coder"
    # And the hot-reload nudge actually fired once.
    assert len(calls) == 1


def test_activate_reload_false_skips_hot_reload(
    client: TestClient,
    seeded_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reload=false`` writes active.txt but DOESN'T call hermes_reload."""
    calls: list[dict[str, Any]] = []

    def _fake_reload(**kwargs: Any) -> tuple[bool, str | None]:
        calls.append(kwargs)
        return (True, None)

    monkeypatch.setattr(personas_mod, "hermes_reload", _fake_reload)

    r = client.post(
        "/api/agents/hermes/personas/coder/activate",
        json={"reload": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] == "coder"
    assert body["previous"] == "hermes"
    assert body["reloaded"] is False
    assert body["reload_error"] is None
    # Pointer moved …
    assert personas_mod.get_active(root=seeded_personas) == "coder"
    # … but hermes_reload was NEVER called.
    assert calls == []


def test_activate_reports_failed_hot_reload(
    client: TestClient,
    seeded_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed hot-reload is non-fatal; response carries ``reloaded=false``."""
    monkeypatch.setattr(personas_mod, "hermes_reload", lambda **_: (False, "connection refused"))

    r = client.post("/api/agents/hermes/personas/coder/activate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == "coder"
    assert body["reloaded"] is False
    assert body["reload_error"] == "connection refused"
    assert personas_mod.get_active(root=seeded_personas) == "coder"


def test_activate_unknown_persona_returns_404(
    client: TestClient,
    seeded_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(personas_mod, "hermes_reload", lambda **_: (True, None))
    r = client.post("/api/agents/hermes/personas/ghost/activate", json={})
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "persona.not_found"
    # Active pointer must NOT have moved.
    assert personas_mod.get_active(root=seeded_personas) == "hermes"


def test_activate_unknown_agent_returns_404(
    client: TestClient,
    personas_root: Path,
) -> None:
    r = client.post("/api/agents/pi-coder/personas/whatever/activate", json={})
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


def test_activate_no_body_defaults_to_reload_true(
    client: TestClient,
    seeded_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSTing with no body still triggers the hot-reload nudge."""
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        personas_mod,
        "hermes_reload",
        lambda **kwargs: calls.append(kwargs) or (True, None),
    )

    r = client.post("/api/agents/hermes/personas/coder/activate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reloaded"] is True
    assert len(calls) == 1


def test_activate_reload_false_unknown_persona_404(
    client: TestClient,
    seeded_personas: Path,
) -> None:
    """The reload=false branch also 404s on missing persona."""
    r = client.post(
        "/api/agents/hermes/personas/ghost/activate",
        json={"reload": False},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "persona.not_found"
    assert personas_mod.get_active(root=seeded_personas) == "hermes"
