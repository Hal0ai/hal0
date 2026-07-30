"""[slots] policy wiring — max_slots creation gate + configurable port pool.

Both keys are read from the LIVE ``app.state.hal0_config`` on every
POST /api/slots (apply-class ``immediate``): the max_slots gate rejects
creation once the on-disk slot count reaches the budget (seeded slots
count), and the port auto-allocator draws from
``[slots].port_range_start/end`` instead of the old hardcoded 8081-8099.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes.slots import _next_free_slot_port, _normalize_create_body


def _seed_slot(home: str, name: str, port: int) -> None:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                f"port = {port}",
                'device = "gpu-vulkan"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    # Instantiate after tmp_hal0_home is in place (same rationale as
    # test_slots_routes.isolated_app).
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


# ── port pool ────────────────────────────────────────────────────────────────


def test_next_free_port_honours_configured_range(tmp_hal0_home: str) -> None:
    _seed_slot(tmp_hal0_home, "a", 8150)
    assert _next_free_slot_port(8150, 8155) == 8151


def test_normalize_create_body_allocates_from_configured_pool(tmp_hal0_home: str) -> None:
    out = _normalize_create_body({"name": "x", "model": "m"}, port_start=8190, port_end=8195)
    assert out["port"] == 8190
    assert out["model"] == {"default": "m"}


def test_normalize_create_body_keeps_explicit_port(tmp_hal0_home: str) -> None:
    out = _normalize_create_body({"name": "x", "port": 8188}, port_start=8081, port_end=8099)
    assert out["port"] == 8188


# ── max_slots creation gate ──────────────────────────────────────────────────


def test_create_slot_rejects_when_max_slots_reached(
    tmp_hal0_home: str,
    isolated_app: FastAPI,
    isolated_client: TestClient,
) -> None:
    _seed_slot(tmp_hal0_home, "existing", 8081)
    # Live-config read: mutate app.state (what PUT /api/settings updates)
    # rather than the on-disk TOML — the gate must see it without restart.
    isolated_app.state.hal0_config.slots.max_slots = 1
    r = isolated_client.post(
        "/api/slots",
        json={"name": "second", "model": "qwen3-4b-q4_k_m", "device": "gpu-vulkan"},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "slot.capacity_exhausted"
    assert err["details"]["max_slots"] == 1
    assert err["details"]["existing_slots"] == 1


def test_create_slot_allows_when_under_budget(
    tmp_hal0_home: str,
    isolated_app: FastAPI,
    isolated_client: TestClient,
) -> None:
    _seed_slot(tmp_hal0_home, "existing", 8081)
    isolated_app.state.hal0_config.slots.max_slots = 5
    r = isolated_client.post(
        "/api/slots",
        json={"name": "second", "model": "qwen3-4b-q4_k_m", "device": "gpu-vulkan"},
    )
    assert r.status_code == 201, r.text
    # Auto-allocated from the default pool, skipping the seeded 8081.
    assert r.json()["port"] == 8082


def test_capacity_endpoint_reports_slot_budget(
    tmp_hal0_home: str,
    isolated_app: FastAPI,
    isolated_client: TestClient,
) -> None:
    _seed_slot(tmp_hal0_home, "existing", 8081)
    isolated_app.state.hal0_config.slots.max_slots = 4
    r = isolated_client.get("/api/slots/capacity")
    assert r.status_code == 200, r.text
    budget = r.json()["slot_budget"]
    assert budget == {"used_slots": 1, "max_slots": 4}
