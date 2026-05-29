"""HTTP tests for ``/api/agents/{agent_id}/personas/{persona_id}/budget``.

Pins the REST shape the dashboard editor + the V1 OpenRouter provider
depend on. Wires the personas + ledger roots at a tmp_path so the test
runner doesn't write to ``/var/lib/hal0/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.agents import personas as personas_mod
from hal0.agents.budget import ledger_for
from hal0.api.agents import budget as budget_route
from hal0.api.agents import personas as personas_route


@pytest.fixture
def state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Redirect personas store + ledger root to a tmp dir.

    Layout under tmp_path mirrors the production tree exactly:

        tmp_path/hermes-agent/personas/<id>.toml
        tmp_path/hermes-agent/personas/<id>/spend.jsonl
    """
    agent_root = tmp_path / "hermes-agent"
    personas_dir = agent_root / "personas"
    personas_dir.mkdir(parents=True)
    monkeypatch.setattr(personas_mod, "PERSONAS_ROOT", personas_dir)
    monkeypatch.setitem(personas_route._AGENT_PERSONAS_ROOTS, "hermes", personas_dir)
    monkeypatch.setitem(budget_route._AGENT_PERSONAS_ROOTS, "hermes", personas_dir)
    monkeypatch.setitem(budget_route._AGENT_LEDGER_ROOTS, "hermes", tmp_path)
    yield tmp_path


@pytest.fixture
def seeded(state_root: Path) -> Path:
    """Seed the hermes + coder personas at the redirected root."""
    personas_dir = state_root / "hermes-agent" / "personas"
    personas_mod.seed_default_personas(agent_id="hermes-agent", root=personas_dir)
    return state_root


# ── GET ─────────────────────────────────────────────────────────────────────


def test_get_returns_empty_budget_for_seeded_persona(client: TestClient, seeded: Path) -> None:
    r = client.get("/api/agents/hermes/personas/hermes/budget")
    assert r.status_code == 200, r.text
    body = r.json()
    # Seeded persona has an empty budget — only hard_cap shows up.
    assert body["budget"] == {"hard_cap": True}
    assert body["spend"] == {"today_usd": 0.0, "mtd_usd": 0.0, "lifetime_usd": 0.0}
    assert body["remaining"] == {}


def test_get_unknown_agent_returns_404(client: TestClient, state_root: Path) -> None:
    r = client.get("/api/agents/pi-coder/personas/anything/budget")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"


def test_get_unknown_persona_returns_404(client: TestClient, seeded: Path) -> None:
    r = client.get("/api/agents/hermes/personas/ghost/budget")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "persona.not_found"


# ── PUT ─────────────────────────────────────────────────────────────────────


def test_put_sets_budget_and_returns_updated_state(client: TestClient, seeded: Path) -> None:
    payload = {
        "daily_usd": 2.50,
        "monthly_usd": 25.00,
        "lifetime_usd": 250.00,
        "per_call_max_usd": 0.10,
        "hard_cap": True,
    }
    r = client.put("/api/agents/hermes/personas/hermes/budget", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["budget"]["daily_usd"] == 2.50
    assert body["budget"]["monthly_usd"] == 25.00
    assert body["budget"]["lifetime_usd"] == 250.00
    assert body["budget"]["per_call_max_usd"] == 0.10
    assert body["budget"]["hard_cap"] is True
    # Persisted on disk.
    personas_dir = seeded / "hermes-agent" / "personas"
    loaded = personas_mod.load_persona("hermes", root=personas_dir)
    assert loaded.budget.daily_usd == 2.50
    # Other persona fields untouched.
    assert loaded.display_name == "Hermes"
    assert loaded.system_prompt.startswith("You are Hermes")
    # Remaining headroom snapshot is consistent.
    assert body["remaining"]["daily_usd"] == pytest.approx(2.50)


def test_put_with_warn_only_persists_hard_cap_false(client: TestClient, seeded: Path) -> None:
    payload = {"daily_usd": 1.0, "hard_cap": False}
    r = client.put("/api/agents/hermes/personas/hermes/budget", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["budget"]["hard_cap"] is False


def test_put_invalid_estimate_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.put(
        "/api/agents/hermes/personas/hermes/budget",
        json={"daily_usd": -5.0},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid"


def test_put_unknown_persona_returns_404(client: TestClient, seeded: Path) -> None:
    r = client.put("/api/agents/hermes/personas/ghost/budget", json={"daily_usd": 1.0})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "persona.not_found"


def test_put_unknown_agent_returns_404(client: TestClient, state_root: Path) -> None:
    r = client.put("/api/agents/pi-coder/personas/x/budget", json={"daily_usd": 1.0})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"


def test_put_non_object_body_returns_400(client: TestClient, seeded: Path) -> None:
    """A JSON array body is malformed shape — 400, not 500."""
    r = client.put(
        "/api/agents/hermes/personas/hermes/budget",
        json=[1, 2, 3],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid_body"


# ── POST /check ─────────────────────────────────────────────────────────────


def test_check_with_no_budget_allows_any_estimate(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/check",
        json={"estimated_cost_usd": 5.0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowed"] is True
    assert body["reason"] is None


def test_check_blocks_when_estimate_exceeds_per_call(client: TestClient, seeded: Path) -> None:
    client.put(
        "/api/agents/hermes/personas/hermes/budget",
        json={"per_call_max_usd": 0.05},
    )
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/check",
        json={"estimated_cost_usd": 0.10},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is False
    assert "per-call cap" in body["reason"]


def test_check_invalid_estimate_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/check",
        json={"estimated_cost_usd": -1.0},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid_estimate"


def test_check_missing_body_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.post("/api/agents/hermes/personas/hermes/budget/check", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid_estimate"


def test_check_unknown_persona_returns_404(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/ghost/budget/check",
        json={"estimated_cost_usd": 0.01},
    )
    assert r.status_code == 404


# ── POST /charge ────────────────────────────────────────────────────────────


def test_charge_records_to_ledger(client: TestClient, seeded: Path) -> None:
    # Set a daily cap so we can verify remaining headroom shrinks.
    client.put(
        "/api/agents/hermes/personas/hermes/budget",
        json={"daily_usd": 10.0},
    )
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/charge",
        json={
            "surface": "openrouter",
            "model": "anthropic/claude-3.7-sonnet",
            "cost_usd": 0.42,
            "request_id": "req-1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True
    assert body["row"]["model"] == "anthropic/claude-3.7-sonnet"
    assert body["spend"]["today_usd"] == pytest.approx(0.42)
    assert body["spend"]["lifetime_usd"] == pytest.approx(0.42)
    assert body["remaining"]["daily_usd"] == pytest.approx(9.58)

    # Ledger file actually exists on disk + carries the row.
    ledger = ledger_for("hermes", "hermes", root=seeded)
    rows = ledger.iter_rows()
    assert len(rows) == 1
    assert rows[0].cost_usd == pytest.approx(0.42)
    assert rows[0].request_id == "req-1"


def test_charge_missing_required_field_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/charge",
        json={"surface": "openrouter", "model": "m", "cost_usd": 0.01},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid_charge"


def test_charge_negative_cost_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/charge",
        json={
            "surface": "openrouter",
            "model": "m",
            "cost_usd": -0.01,
            "request_id": "r",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "budget.invalid_charge"


def test_charge_empty_surface_returns_400(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/hermes/budget/charge",
        json={"surface": "", "model": "m", "cost_usd": 0.01, "request_id": "r"},
    )
    assert r.status_code == 400


def test_charge_unknown_persona_returns_404(client: TestClient, seeded: Path) -> None:
    r = client.post(
        "/api/agents/hermes/personas/ghost/budget/charge",
        json={
            "surface": "openrouter",
            "model": "m",
            "cost_usd": 0.01,
            "request_id": "r",
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "persona.not_found"


def test_charge_then_check_reflects_new_spend(client: TestClient, seeded: Path) -> None:
    client.put(
        "/api/agents/hermes/personas/hermes/budget",
        json={"daily_usd": 1.0},
    )
    # Charge 0.6 — should still allow another 0.3 but block 0.5.
    client.post(
        "/api/agents/hermes/personas/hermes/budget/charge",
        json={
            "surface": "openrouter",
            "model": "m",
            "cost_usd": 0.6,
            "request_id": "r1",
        },
    )
    ok = client.post(
        "/api/agents/hermes/personas/hermes/budget/check",
        json={"estimated_cost_usd": 0.3},
    )
    assert ok.status_code == 200
    assert ok.json()["allowed"] is True

    block = client.post(
        "/api/agents/hermes/personas/hermes/budget/check",
        json={"estimated_cost_usd": 0.5},
    )
    assert block.status_code == 200
    blocked = block.json()
    assert blocked["allowed"] is False
    assert "daily cap" in blocked["reason"]
