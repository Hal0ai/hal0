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
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.providers.container import ContainerProvider

# ── Container-provider stub ──────────────────────────────────────────────────


@pytest.fixture
def container_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub out ContainerProvider's systemd/podman surface.

    SlotManager dispatches every slot lifecycle call through the
    container provider (write + start the ``hal0-slot@<name>`` unit).
    Tests can't touch systemd, so the stub keeps an in-memory "active
    units" set: ``load_sync`` marks the slot active, ``unload_sync``
    clears it, and ``is_active`` answers from the set — which keeps
    ``status()``'s drift reconciler honest (a loaded slot stays READY,
    an unloaded one reads OFFLINE).
    """
    state: dict[str, Any] = {
        "active": set(),
        "load_calls": [],
        "unload_calls": [],
    }

    def load_sync(
        self: ContainerProvider,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> None:
        state["load_calls"].append({"cfg": dict(slot_cfg), "model_info": dict(model_info)})
        state["active"].add(slot_cfg.get("name"))

    def unload_sync(self: ContainerProvider, slot_cfg: dict[str, Any]) -> None:
        state["unload_calls"].append(dict(slot_cfg))
        state["active"].discard(slot_cfg.get("name"))

    def is_active(self: ContainerProvider, slot_name: str) -> bool:
        return slot_name in state["active"]

    async def health(
        self: ContainerProvider, port: int, slot_cfg: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"ok": True, "status": 200}

    async def wait_ready(self: ContainerProvider, port: int) -> None:
        return None

    monkeypatch.setattr(ContainerProvider, "load_sync", load_sync)
    monkeypatch.setattr(ContainerProvider, "unload_sync", unload_sync)
    monkeypatch.setattr(ContainerProvider, "is_active", is_active)
    monkeypatch.setattr(ContainerProvider, "health", health)
    monkeypatch.setattr(ContainerProvider, "wait_ready", wait_ready)

    return state


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


# ── Deliverable 1: stamp semantics — a profile change must NOT re-materialize ──


def test_put_profile_change_does_not_rematerialize_extra_args(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """spec-flags-ownership §3: the server never re-materializes flags from a
    profile on save — the client copies explicitly. A PUT that changes
    ``defaults.profile`` while re-sending the SAME ``extra_args`` must leave
    that text verbatim (proof the server did not resolve the profile and
    overwrite the tune)."""
    fpath = crud_models_root / "stamp.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={
            "id": "stamp",
            "path": str(fpath),
            "defaults": {"profile": "old-profile", "extra_args": "-b 1024 -fa on"},
        },
    )
    # Point the pointer at a DIFFERENT profile name but keep the same text.
    # A re-materializing server would try to resolve 'ghost-profile' and either
    # error or replace extra_args; the correct server keeps the text as sent.
    r = crud_client.put(
        "/api/models/stamp",
        json={"defaults": {"profile": "ghost-profile", "extra_args": "-b 1024 -fa on"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["defaults"]["profile"] == "ghost-profile"
    assert body["defaults"]["extra_args"] == "-b 1024 -fa on"


# ── Deliverable 2: preferred_runner is writable + validated ────────────────────


def test_put_preferred_runner_valid_key_persists(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "pr.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "pr", "path": str(fpath)})
    r = crud_client.put("/api/models/pr", json={"preferred_runner": "cpu"})
    assert r.status_code == 200, r.text
    assert r.json()["preferred_runner"] == "cpu"


def test_put_preferred_runner_unknown_key_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "pr2.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "pr2", "path": str(fpath)})
    r = crud_client.put("/api/models/pr2", json={"preferred_runner": "does-not-exist"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.unknown_runner"


def test_put_preferred_runner_null_clears(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "pr3.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={"id": "pr3", "path": str(fpath), "preferred_runner": "cpu"},
    )
    r = crud_client.put("/api/models/pr3", json={"preferred_runner": None})
    assert r.status_code == 200, r.text
    assert r.json()["preferred_runner"] is None


# ── Deliverable 4: O10 guard — bare double-quoted JSON eaten by the shell ──────


def test_put_bare_double_quoted_json_extra_args_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A JSON value whose double quotes the shell would strip is rejected with
    the actionable single-quote message (spec §3 JSON-token integrity)."""
    fpath = crud_models_root / "o10a.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "o10a", "path": str(fpath)})
    r = crud_client.put(
        "/api/models/o10a",
        json={"defaults": {"extra_args": '--chat-template-kwargs {"enable_thinking":false}'}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.extra_args_json_quoting"


def test_put_single_quoted_json_extra_args_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The correctly single-quoted JSON value survives shlex-splitting and is
    accepted unchanged."""
    fpath = crud_models_root / "o10b.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "o10b", "path": str(fpath)})
    good = "--chat-template-kwargs '{\"enable_thinking\":false}'"
    r = crud_client.put("/api/models/o10b", json={"defaults": {"extra_args": good}})
    assert r.status_code == 200, r.text
    assert r.json()["defaults"]["extra_args"] == good


# ── Deliverable 1: create screens too, + dry-run /validate ────────────────────


def test_create_screens_managed_extra_args(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """POST create screens defaults.extra_args like PUT does — a smuggled
    managed flag fails the write instead of persisting a row that rebinds a
    slot at launch."""
    fpath = crud_models_root / "cs1.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "cs1",
            "path": str(fpath),
            "defaults": {"extra_args": "--flash-attn on --port 9"},
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"
    # And nothing was written.
    assert crud_client.get("/api/models/cs1").status_code == 404


def test_create_screens_unknown_preferred_runner(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "cs2.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={"id": "cs2", "path": str(fpath), "preferred_runner": "does-not-exist"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.unknown_runner"


def test_validate_accepts_clean_body(crud_client: TestClient) -> None:
    r = crud_client.post(
        "/api/models/validate",
        json={"defaults": {"extra_args": "-b 8192 --flash-attn on"}, "preferred_runner": "cpu"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_validate_rejects_managed_extra_args_without_writing(crud_client: TestClient) -> None:
    """/validate is a dry run: it screens with the same envelope as create/PUT
    and never touches the registry (no id required, nothing persisted)."""
    r = crud_client.post(
        "/api/models/validate",
        json={"id": "should-not-persist", "defaults": {"extra_args": "--model /etc/passwd"}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"
    assert crud_client.get("/api/models/should-not-persist").status_code == 404


# ── Deliverable 3: duplicate-model endpoint ────────────────────────────────────


def test_duplicate_creates_new_row_sharing_weights(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A duplicate is a new registry id pointing at the SAME path, copying
    metadata/capabilities/backends. Hand-registered sources carry no
    model_file rows, so files_refcounted is 0 (nothing to refcount)."""
    fpath = crud_models_root / "dup-src.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={
            "id": "dup-src",
            "path": str(fpath),
            "name": "Source",
            "capabilities": ["chat"],
            "backends": ["vulkan", "rocm"],
        },
    )
    pre = _max_event_id(crud_client)
    r = crud_client.post("/api/models/dup-src/duplicate", json={"new_id": "dup-copy"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "dup-copy"
    assert body["path"] == str(fpath)  # SAME weights, no byte copy
    assert body["capabilities"] == ["chat"]
    assert set(body["backends"]) == {"vulkan", "rocm"}
    assert body["duplicated_from"] == "dup-src"
    assert body["files_refcounted"] == 0

    # Both rows are independently retrievable.
    assert crud_client.get("/api/models/dup-src").status_code == 200
    assert crud_client.get("/api/models/dup-copy").status_code == 200

    events = _events_since(crud_client, pre, "model.registered")
    assert any(
        ev["data"].get("id") == "dup-copy" and ev["data"].get("source") == "duplicate"
        for ev in events
    ), events


def test_duplicate_with_profile_stamps_flags_into_defaults(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The optional ``profile`` materializes that profile's flags into the new
    row's defaults (copy-not-layer). The source is untouched."""
    from hal0.profiles import ProfileCatalog

    fpath = crud_models_root / "dup-prof.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "dup-prof", "path": str(fpath)})

    resolved = ProfileCatalog().resolve("cpu-llm")
    r = crud_client.post(
        "/api/models/dup-prof/duplicate",
        json={"new_id": "dup-prof-cpu", "profile": "cpu-llm"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["defaults"]["profile"] == "cpu-llm"
    assert body["defaults"]["extra_args"] == resolved.flags

    # Source keeps no stamped defaults — it was never mutated.
    src = crud_client.get("/api/models/dup-prof").json()
    assert not (src.get("defaults") or {}).get("extra_args")


def test_duplicate_unknown_profile_404(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "dup-badprof.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "dup-badprof", "path": str(fpath)})
    r = crud_client.post(
        "/api/models/dup-badprof/duplicate",
        json={"new_id": "dup-badprof-x", "profile": "no-such-profile"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "profiles.not_found"


def test_duplicate_unknown_source_404(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/ghost-src/duplicate", json={"new_id": "whatever"})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "model.not_found"


def test_duplicate_conflicting_new_id_409(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "dup-c.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "dup-c", "path": str(fpath)})
    crud_client.post("/api/models", json={"id": "dup-c-taken", "path": str(fpath)})
    r = crud_client.post("/api/models/dup-c/duplicate", json={"new_id": "dup-c-taken"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "model.already_exists"


def test_duplicate_same_id_400(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "dup-self.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "dup-self", "path": str(fpath)})
    r = crud_client.post("/api/models/dup-self/duplicate", json={"new_id": "dup-self"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.invalid"


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
    container_stub: dict[str, Any],
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


def test_delete_reaps_pull_job_snapshot(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Deleting a model garbage-collects its durable pull-job snapshot (#MR-8)."""
    from hal0.registry.pull import pull_job_file

    fpath = crud_models_root / "reaped.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post("/api/models", json={"id": "reaped", "path": str(fpath)})
    assert r.status_code == 201, r.text

    snap = pull_job_file("reaped")
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text('{"model_id": "reaped", "state": "completed"}', encoding="utf-8")
    assert snap.exists()

    r = crud_client.delete("/api/models/reaped")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert not snap.exists()


def test_delete_succeeds_when_snapshot_absent(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The snapshot-GC is fail-soft: an already-absent file never breaks delete."""
    from hal0.registry.pull import pull_job_file

    fpath = crud_models_root / "no-snap.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post("/api/models", json={"id": "no-snap", "path": str(fpath)})
    assert r.status_code == 201, r.text

    assert not pull_job_file("no-snap").exists()

    r = crud_client.delete("/api/models/no-snap")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True


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
