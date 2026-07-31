"""Per-type default MODEL marker — chokepoint invariants + route + slot wiring.

Covers the model-layer default introduced for the CreateSlotModal "Set as
default" checkbox:

  * :func:`hal0.services.models_service.set_model_type_default` — the ONE place
    the single-holder-per-type invariant is enforced: promote demotes the
    current holder of the same type, cross-type holders are untouched, remove
    leaves the type with no default, and it is idempotent.
  * ``default`` round-trips through the SQLite registry (extra-blob storage).
  * ``POST /api/models/{id}/default`` promote / clear / 404.
  * ``POST /api/slots`` with ``default: true`` promotes the slot's MODEL as its
    type default via the same chokepoint, and a promote failure (unregistered
    model) does NOT break slot creation.
  * Duplicating a default model does not clone the flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.registry.model import Model
from hal0.registry.store import ModelNotFound, ModelRegistry
from hal0.services import models_service as svc

# ── service-layer chokepoint (no app needed) ─────────────────────────────────


@pytest.fixture
def registry(tmp_path: Path) -> ModelRegistry:
    reg = ModelRegistry(registry_dir=tmp_path)

    def _m(mid: str, caps: list[str]) -> Model:
        return Model(id=mid, path=f"/models/{mid}.gguf", capabilities=caps)

    # Two llm-type models, one embedding-type model.
    reg.add(_m("llm-a", ["chat"]))
    reg.add(_m("llm-b", ["chat"]))
    reg.add(_m("embed-a", ["embed"]))
    return reg


def test_promote_sets_default_and_type(registry: ModelRegistry) -> None:
    result = svc.set_model_type_default(registry, "llm-a", default=True)
    assert result["default"] is True
    assert result["type"] == "llm"
    assert result["changed"] is True
    assert registry.get("llm-a").default is True


def test_promote_demotes_current_holder_of_same_type(registry: ModelRegistry) -> None:
    svc.set_model_type_default(registry, "llm-a", default=True)
    result = svc.set_model_type_default(registry, "llm-b", default=True)
    # Single-holder invariant: promoting llm-b demotes llm-a.
    assert result["demoted"] == ["llm-a"]
    assert registry.get("llm-b").default is True
    assert registry.get("llm-a").default is False


def test_promote_does_not_touch_other_types(registry: ModelRegistry) -> None:
    svc.set_model_type_default(registry, "embed-a", default=True)
    svc.set_model_type_default(registry, "llm-a", default=True)
    # embed-a is a different type — the llm promotion leaves it alone.
    assert registry.get("embed-a").default is True
    assert registry.get("llm-a").default is True


def test_promote_is_idempotent(registry: ModelRegistry) -> None:
    svc.set_model_type_default(registry, "llm-a", default=True)
    result = svc.set_model_type_default(registry, "llm-a", default=True)
    assert result["changed"] is False
    assert result["demoted"] == []
    assert registry.get("llm-a").default is True


def test_remove_leaves_type_without_a_default(registry: ModelRegistry) -> None:
    svc.set_model_type_default(registry, "llm-a", default=True)
    result = svc.set_model_type_default(registry, "llm-a", default=False)
    assert result["default"] is False
    assert result["changed"] is True
    assert registry.get("llm-a").default is False
    # No promotion happened — the type now has no default holder.
    assert all(not m.default for m in registry.list())


def test_remove_on_non_default_is_noop(registry: ModelRegistry) -> None:
    result = svc.set_model_type_default(registry, "llm-a", default=False)
    assert result["changed"] is False


def test_promote_unknown_model_raises(registry: ModelRegistry) -> None:
    with pytest.raises(ModelNotFound):
        svc.set_model_type_default(registry, "nope", default=True)


def test_promote_self_heals_a_double_holder(registry: ModelRegistry) -> None:
    # Force two llm defaults onto disk directly (bypassing the chokepoint),
    # then re-promote one: the stray peer must be demoted.
    registry.update("llm-a", {"default": True})
    registry.update("llm-b", {"default": True})
    result = svc.set_model_type_default(registry, "llm-a", default=True)
    assert result["demoted"] == ["llm-b"]
    assert registry.get("llm-b").default is False
    assert registry.get("llm-a").default is True


def test_default_round_trips_through_registry(registry: ModelRegistry) -> None:
    registry.update("llm-a", {"default": True})
    # Fresh instance re-reads from SQLite (extra-blob storage).
    reg2 = ModelRegistry(registry_dir=registry.registry_dir)
    assert reg2.get("llm-a").default is True
    assert reg2.get("llm-b").default is False


# ── route + slot-create wiring (full app) ────────────────────────────────────


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    root = Path(tmp_hal0_home) / "models"
    root.mkdir(parents=True, exist_ok=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, tmp_hal0_home: str, mid: str, caps: list[str]) -> None:
    fpath = Path(tmp_hal0_home) / "models" / f"{mid}.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = client.post(
        "/api/models",
        json={"id": mid, "path": str(fpath), "capabilities": caps, "backends": ["vulkan"]},
    )
    assert r.status_code == 201, r.text


def test_route_promote_and_demote(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "route-a", ["chat"])
    _register(client, tmp_hal0_home, "route-b", ["chat"])

    r = client.post("/api/models/route-a/default")
    assert r.status_code == 200, r.text
    assert r.json()["default"] is True

    r = client.post("/api/models/route-b/default", json={"default": True})
    assert r.status_code == 200, r.text
    assert r.json()["demoted"] == ["route-a"]

    assert client.get("/api/models/route-a").json()["default"] is False
    assert client.get("/api/models/route-b").json()["default"] is True


def test_route_clear(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "clr-a", ["chat"])
    client.post("/api/models/clr-a/default")
    r = client.post("/api/models/clr-a/default", json={"default": False})
    assert r.status_code == 200, r.text
    assert r.json()["default"] is False
    assert client.get("/api/models/clr-a").json()["default"] is False


def test_route_unknown_model_404(client: TestClient) -> None:
    r = client.post("/api/models/ghost/default")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "model.not_found"


def test_list_models_surfaces_default_flag(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "surf-a", ["chat"])
    client.post("/api/models/surf-a/default")
    rows = client.get("/api/models").json()["models"]
    row = next(m for m in rows if m["id"] == "surf-a")
    assert row["default"] is True


def test_create_slot_with_default_promotes_model(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "slot-model", ["chat"])
    r = client.post(
        "/api/slots",
        json={"name": "myslot", "type": "llm", "model": "slot-model", "default": True},
    )
    assert r.status_code == 201, r.text
    promo = r.json().get("default_promotion")
    assert promo and promo["promoted"] is True
    assert promo["type"] == "llm"
    # The MODEL is now its type's default.
    assert client.get("/api/models/slot-model").json()["default"] is True
    # The POST body's `default` is the MODEL marker and never lands on the slot
    # as such. `myslot` IS the type's default here, but only because it is the
    # FIRST llm slot on disk (SlotManager.create's first-of-type auto-default),
    # not because the body said so. A SECOND slot of the type proves it: it gets
    # the same body flag and still carries no slot-level default marker.
    r2 = client.post(
        "/api/slots",
        json={"name": "myslot2", "type": "llm", "model": "slot-model", "default": True},
    )
    assert r2.status_code == 201, r2.text
    cfg2 = client.get("/api/slots/myslot2/config").json()
    assert "default" not in cfg2


def test_create_slot_default_false_does_not_promote(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "slot-model-2", ["chat"])
    r = client.post(
        "/api/slots",
        json={"name": "slot2", "type": "llm", "model": "slot-model-2"},
    )
    assert r.status_code == 201, r.text
    assert "default_promotion" not in r.json()
    assert client.get("/api/models/slot-model-2").json()["default"] is False


def test_create_slot_promote_failure_does_not_break_create(
    client: TestClient, tmp_hal0_home: str
) -> None:
    # Bind a model id that is NOT registered — the slot must still be created,
    # with a soft-failed promotion report rather than a 500.
    r = client.post(
        "/api/slots",
        json={"name": "slot3", "type": "llm", "model": "unregistered-model", "default": True},
    )
    assert r.status_code == 201, r.text
    promo = r.json().get("default_promotion")
    assert promo and promo["promoted"] is False
    # Slot really exists.
    assert client.get("/api/slots/slot3").status_code == 200


def test_duplicate_does_not_clone_default_flag(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "dup-src", ["chat"])
    client.post("/api/models/dup-src/default")
    r = client.post("/api/models/dup-src/duplicate", json={"new_id": "dup-copy"})
    assert r.status_code == 201, r.text
    assert r.json().get("default") is False
    assert client.get("/api/models/dup-copy").json()["default"] is False
    # The source keeps its default — single holder still holds.
    assert client.get("/api/models/dup-src").json()["default"] is True
