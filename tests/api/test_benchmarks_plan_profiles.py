"""``GET /api/benchmarks/plan`` plans over models carrying their slot's profile (ADR-0029)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.bench.test_planner_slot_profiles import PROFILE_FLAGS, _registry, _suite
from tests.bench.test_planner_slot_profiles import box as box  # re-exported fixture


@pytest.mark.usefixtures("box")
def test_api_plan_cells_carry_the_profile_flags(
    isolated_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /api/benchmarks/plan`` plans over the same attached models."""
    from hal0.api.routes import benchmarks as routes

    monkeypatch.setattr(routes, "fetch_registry_models", lambda api: _registry())
    monkeypatch.setattr(routes, "load_suites", lambda d: {"roster": _suite()})
    captured: list[Any] = []
    real_plan = routes.plan

    def spy(suite, models, store):
        cells = real_plan(suite, models, store)
        captured.extend(cells)
        return cells

    monkeypatch.setattr(routes, "plan", spy)

    resp = isolated_client.get("/api/benchmarks/plan", params={"suite": "roster"})

    assert resp.status_code == 200, resp.text
    cell = next(c for c in captured if c.model_id == "m1")
    assert cell.identity.config.argv == PROFILE_FLAGS.split()
    assert cell.identity.config.ctx == 65536
    cells = resp.json()["cells"]
    assert any(row["model"] == "m1" and row["cell_key"] == cell.cell_key for row in cells)


@pytest.mark.usefixtures("box")
def test_planner_attaches_effective_tune(box) -> None:
    """A slot's [tune] override is what the cell is measured under."""
    from hal0.bench.planner import attach_slot_profiles

    slot = box / "slots" / "chat.toml"
    slot.write_text(slot.read_text() + '[tune]\nvision = "auto"\nenable_thinking = false\n')
    prof = next(m for m in attach_slot_profiles(_registry()) if m["id"] == "m1")["profile"]
    assert prof["vision"] == "auto"  # profile says "off"
    assert prof["enable_thinking"] is False
    assert prof["tune"]["sources"]["vision"] == "slot"
