"""Tests for the /api/models CRUD surface — register, update, delete cascade.

Covers:
  * POST /api/models/scan with user-edited rows (overrides win over detection)
  * POST /api/models emits model.registered with the caller-supplied source
  * PUT /api/models/{id} accepts new editable fields + emits model.updated
  * DELETE /api/models/{id} cascade ordering: slot.state events fire
    BEFORE model.deleted, slot TOMLs get [model].default = ""
  * DELETE with force_cascade=false returns 409 + affected_slots
  * DELETE on an unreferenced model: affected_slots=[]
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.providers as providers_mod
from hal0.api import create_app
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider

# ── Lemonade stub (PR-10) ────────────────────────────────────────────────────


@pytest.fixture
def systemctl_stub() -> Iterator[dict[str, Any]]:
    """Compatibility shim — installs a Lemonade stub provider.

    PR-10 retired systemctl dispatch; the fixture name is kept so the
    existing tests don't have to be re-signed. The returned dict
    carries a ``_lemonade`` key with the underlying Lemonade state
    dict so tests can mutate it if needed.
    """
    state: dict[str, Any] = {
        "loaded": [{"model_name": "qwen3-4b-q4_k_m"}],
        "load_calls": [],
        "unload_calls": [],
    }

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            body = json.loads(req.content.decode() or "{}")
            state["load_calls"].append(body)
            state["loaded"] = [{"model_name": body.get("model_name", "")}]
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/unload":
            body = json.loads(req.content.decode() or "{}")
            state["unload_calls"].append(body)
            state["loaded"] = []
            return httpx.Response(200, json={"status": "unloaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": state["loaded"]})
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(h),
        base_url="http://test",
    )
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider

    yield {"calls": [], "is_active_state": "active", "_lemonade": state}

    providers_mod._PROVIDERS["lemonade"] = original


@pytest.fixture(autouse=False)
def stub_await_ready() -> None:
    """No-op (PR-10): _await_ready is Lemonade-aware and short-circuits."""
    return None


# ── isolated app fixture (lifespan resolves under tmp_hal0_home) ────────────


@pytest.fixture
def crud_app(tmp_hal0_home: str) -> FastAPI:
    """An app with a model root + no slots wired by default.

    Tests that need a slot register it via a per-test fixture that writes
    its TOML before constructing the client.
    """
    extra_root = Path(tmp_hal0_home) / "crud-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{extra_root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture
def crud_client(crud_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(crud_app) as c:
        yield c


@pytest.fixture
def crud_models_root(tmp_hal0_home: str) -> Path:
    return Path(tmp_hal0_home) / "crud-models"


def _events_since(client: TestClient, since: int, type_glob: str | None = None) -> list[dict]:
    params = f"?since={since}&limit=1000"
    if type_glob:
        params += f"&type={type_glob}"
    return client.get(f"/api/events{params}").json().get("events", [])


def _max_event_id(client: TestClient) -> int:
    body = client.get("/api/events?limit=1000").json()
    return max((ev["id"] for ev in body.get("events", [])), default=0)


# ── POST /api/models/scan with rows ────────────────────────────────────────


def test_scan_with_rows_persists_user_overrides(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """User-edited rows override detection — not the other way around."""
    fpath = crud_models_root / "my-custom-model.gguf"
    fpath.write_bytes(b"\x00" * 64)

    pre = _max_event_id(crud_client)
    r = crud_client.post(
        "/api/models/scan",
        json={
            "rows": [
                {
                    "path": str(fpath),
                    "id": "user-chosen-id",
                    "name": "User Chosen Name",
                    "backends": ["vulkan"],
                    "capabilities": ["embed"],
                    "defaults": {"context_size": 8192, "n_gpu_layers": -1},
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "user-chosen-id" in body["added"]

    # Verify the persisted entry reflects the overrides, not detection
    # (which would have returned ["chat"] + [vulkan,rocm,cuda,cpu]).
    entry = crud_client.get("/api/models/user-chosen-id").json()
    assert entry["name"] == "User Chosen Name"
    assert entry["backends"] == ["vulkan"]
    assert entry["capabilities"] == ["embed"]
    assert entry["defaults"]["context_size"] == 8192
    assert entry["defaults"]["n_gpu_layers"] == -1

    # model.registered fired with source=scan.
    events = _events_since(crud_client, pre, "model.registered")
    assert any(
        ev["data"].get("id") == "user-chosen-id" and ev["data"].get("source") == "scan"
        for ev in events
    ), events


def test_scan_with_rows_falls_back_to_detection_for_missing_fields(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A row with only a path still registers using detect() defaults."""
    fpath = crud_models_root / "qwen-test.gguf"
    fpath.write_bytes(b"\x00" * 64)

    r = crud_client.post("/api/models/scan", json={"rows": [{"path": str(fpath)}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["added"]) == 1
    mid = body["added"][0]

    entry = crud_client.get(f"/api/models/{mid}").json()
    # detect() seeds GGUF backends even on an unreadable header.
    assert set(entry["backends"]) >= {"vulkan", "cpu"}


def test_scan_legacy_empty_body_still_auto_registers(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Empty body falls back to the legacy auto-scan path."""
    (crud_models_root / "qwen3-4b-instruct-q4_k_m.gguf").write_bytes(b"\x00" * 64)
    pre = _max_event_id(crud_client)
    r = crud_client.post("/api/models/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "qwen3-4b" in body["added"]
    # Auto-scan path emits model.registered too.
    events = _events_since(crud_client, pre, "model.registered")
    assert any(ev["data"].get("id") == "qwen3-4b" for ev in events), events


# ── POST /api/models (single register) ─────────────────────────────────────


def test_create_emits_registered_with_source(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The optional ``source`` body field tags the emitted event."""
    fpath = crud_models_root / "hand-registered.gguf"
    fpath.write_bytes(b"\x00" * 16)
    pre = _max_event_id(crud_client)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "hand-1",
            "path": str(fpath),
            "name": "Hand 1",
            "capabilities": ["chat"],
            "backends": ["vulkan"],
            "source": "manual",
        },
    )
    assert r.status_code == 201, r.text
    events = _events_since(crud_client, pre, "model.registered")
    assert any(
        ev["data"].get("id") == "hand-1" and ev["data"].get("source") == "manual" for ev in events
    ), events


def test_create_defaults_source_to_manual(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "h2.gguf"
    fpath.write_bytes(b"\x00")
    pre = _max_event_id(crud_client)
    crud_client.post("/api/models", json={"id": "h2", "path": str(fpath)})
    events = _events_since(crud_client, pre, "model.registered")
    assert any(
        ev["data"].get("id") == "h2" and ev["data"].get("source") == "manual" for ev in events
    ), events


# ── PUT /api/models/{id} ───────────────────────────────────────────────────


def test_update_accepts_new_editable_fields_and_emits(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "upd.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "upd", "path": str(fpath)})

    pre = _max_event_id(crud_client)
    r = crud_client.put(
        "/api/models/upd",
        json={
            "name": "Updated Name",
            "capabilities": ["chat", "embed"],
            "backends": ["vulkan", "rocm"],
            "defaults": {"context_size": 4096, "n_gpu_layers": 99},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Updated Name"
    assert set(body["capabilities"]) == {"chat", "embed"}
    assert set(body["backends"]) == {"vulkan", "rocm"}
    assert body["defaults"]["context_size"] == 4096

    events = _events_since(crud_client, pre, "model.updated")
    assert events, "expected model.updated event"
    payload = next(ev for ev in events if ev["data"].get("id") == "upd")
    changed = set(payload["data"]["changed_fields"])
    assert {"name", "capabilities", "backends", "defaults"} <= changed


def test_update_changed_fields_only_lists_actual_changes(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A PUT that re-sends the same values lists no changed_fields."""
    fpath = crud_models_root / "noop.gguf"
    fpath.write_bytes(b"\x00")
    crud_client.post(
        "/api/models",
        json={"id": "noop", "path": str(fpath), "name": "Same"},
    )
    pre = _max_event_id(crud_client)
    crud_client.put("/api/models/noop", json={"name": "Same"})
    events = _events_since(crud_client, pre, "model.updated")
    assert any(
        ev["data"].get("id") == "noop" and ev["data"]["changed_fields"] == [] for ev in events
    )


# ── DELETE cascade ─────────────────────────────────────────────────────────


@pytest.fixture
def slot_referencing_model(
    tmp_hal0_home: str,
    crud_models_root: Path,
) -> tuple[Path, str]:
    """Drop a slot TOML whose [model].default points at a known model id.

    The fixture also pre-stages the model file on disk so the model can
    be registered via POST /api/models. Returns (slot_toml_path, model_id).
    """
    slot_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slot_dir.mkdir(parents=True, exist_ok=True)
    slot_path = slot_dir / "chat.toml"
    slot_path.write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "cascade-target"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    fpath = crud_models_root / "cascade-target.gguf"
    fpath.write_bytes(b"\x00" * 16)
    return slot_path, "cascade-target"


def test_delete_force_cascade_false_returns_409_with_affected_slots(
    crud_client: TestClient,
    slot_referencing_model: tuple[Path, str],
) -> None:
    """Opt-out from cascade surfaces a 409 + the slot list for UI confirm."""
    _slot_path, mid = slot_referencing_model
    crud_client.post("/api/models", json={"id": mid, "path": "/tmp/cascade-target.gguf"})

    r = crud_client.delete(f"/api/models/{mid}?force_cascade=false")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"]["code"] == "model.in_use"
    assert "chat" in body["error"]["details"]["affected_slots"]

    # Model must still be registered after the rejection.
    assert crud_client.get(f"/api/models/{mid}").status_code == 200


def test_delete_cascade_clears_slot_default_and_emits_model_deleted_last(
    crud_app: FastAPI,
    crud_client: TestClient,
    slot_referencing_model: tuple[Path, str],
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
) -> None:
    """Cascade ordering: slot.state events fire BEFORE model.deleted.

    Drive the slot through load() so the cascade has a running referrer
    to unload. Snapshot the event ring, fire DELETE, then assert the
    final model.deleted event's id is greater than every slot.state event
    emitted by the unload — that's the contract the footer ticker relies
    on so the user sees "unloading … unloaded … model gone".
    """
    slot_path, mid = slot_referencing_model
    crud_client.post("/api/models", json={"id": mid, "path": str(slot_path)})

    # Load the slot so the cascade hits a running referrer.
    r = crud_client.post("/api/slots/chat/load")
    assert r.status_code == 200, r.text

    pre = _max_event_id(crud_client)
    r = crud_client.delete(f"/api/models/{mid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["affected_slots"] == ["chat"]

    # Slot TOML now has [model].default = "" (still parseable).
    with open(slot_path, "rb") as f:
        reloaded = tomllib.load(f)
    assert reloaded["model"]["default"] == ""

    # Event ordering: every slot.state event for 'chat' has an id less
    # than the final model.deleted id.
    new_events = _events_since(crud_client, pre)
    deleted = [ev for ev in new_events if ev["type"] == "model.deleted"]
    assert len(deleted) == 1, f"expected exactly one model.deleted, got {new_events}"
    deleted_id = deleted[0]["id"]
    slot_states = [
        ev for ev in new_events if ev["type"] == "slot.state" and ev["source"] == "slot:chat"
    ]
    assert slot_states, "expected slot.state events from the unload cascade"
    for ev in slot_states:
        assert ev["id"] < deleted_id, (
            f"slot.state id={ev['id']} should precede model.deleted id={deleted_id}"
        )

    # The model is gone from the registry list.
    listing = crud_client.get("/api/models").json()
    assert mid not in {m["id"] for m in listing["models"]}


def test_delete_unreferenced_model_emits_with_empty_affected_slots(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Deleting a model with no referrers: short-circuit, affected_slots=[]."""
    fpath = crud_models_root / "lonely.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "lonely", "path": str(fpath)})

    pre = _max_event_id(crud_client)
    r = crud_client.delete("/api/models/lonely")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["affected_slots"] == []

    events = _events_since(crud_client, pre, "model.deleted")
    assert any(
        ev["data"].get("id") == "lonely" and ev["data"]["affected_slots"] == [] for ev in events
    )


def test_delete_unknown_model_returns_404(
    crud_client: TestClient,
) -> None:
    """A typed 404 envelope, not a silent ``deleted: false``."""
    r = crud_client.delete("/api/models/never-existed")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "model.not_found"


# ── EventBus subscriber verification ───────────────────────────────────────


async def test_model_registered_reaches_live_subscriber(
    crud_app: FastAPI,
    crud_models_root: Path,
) -> None:
    """Drive a register through the route and assert a live subscriber
    receives the model.registered event off the EventBus directly.

    Bypasses the HTTP /api/events shape so the test exercises the bus
    fan-out path (which is what the footer's SSE listener consumes).
    """
    fpath = crud_models_root / "sub.gguf"
    fpath.write_bytes(b"\x00")
    with TestClient(crud_app) as client:
        bus = crud_app.state.events
        received: list[dict] = []

        async def consume() -> None:
            async with bus.subscribe() as q:
                while True:
                    ev = await asyncio.wait_for(q.get(), timeout=2.0)
                    received.append(ev)
                    if ev["type"] == "model.registered" and ev["data"].get("id") == "sub-1":
                        return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # let the subscriber register
        client.post("/api/models", json={"id": "sub-1", "path": str(fpath)})
        await asyncio.wait_for(task, timeout=2.0)

    assert any(
        ev["type"] == "model.registered" and ev["data"].get("id") == "sub-1" for ev in received
    )
