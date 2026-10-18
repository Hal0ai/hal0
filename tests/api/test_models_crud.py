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
from tests.fixtures.slot_probe import probe_slot_name
from tests.registry.gguf_fixtures import write_gguf

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

    def is_active(self: ContainerProvider, slot: Any) -> bool:
        # Production passes the slot CONFIG (#1417); this stub keys by name.
        return probe_slot_name(slot) in state["active"]

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
                    "chat_template": "minicpm5-1b",
                    "jinja": False,
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
    assert entry["chat_template"] == "minicpm5-1b"
    assert entry["jinja"] is False
    assert "defaults" not in entry

    # model.registered fired with source=scan.
    events = _events_since(crud_client, pre, "model.registered")
    assert any(
        ev["data"].get("id") == "user-chosen-id" and ev["data"].get("source") == "scan"
        for ev in events
    ), events


def test_scan_with_rows_skips_a_row_carrying_a_defaults_table(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A scan row's file facts are top-level (ADR-0029 §6): a ``defaults`` table
    skips that row with the error code as the reason instead of being silently
    dropped."""
    fpath = crud_models_root / "tuned-row.gguf"
    fpath.write_bytes(b"\x00" * 64)
    r = crud_client.post(
        "/api/models/scan",
        json={
            "rows": [
                {
                    "path": str(fpath),
                    "id": "tuned-row",
                    "defaults": {"chat_template": "chatml"},
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tuned-row" not in body["added"]
    assert any(sk["reason"] == "model.defaults_removed" for sk in body["skipped"]), body["skipped"]
    assert crud_client.get("/api/models/tuned-row").status_code == 404


def test_scan_with_rows_skips_a_row_carrying_a_tune_key(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "tuned-flat-row.gguf"
    fpath.write_bytes(b"\x00" * 64)
    r = crud_client.post(
        "/api/models/scan",
        json={"rows": [{"path": str(fpath), "id": "tuned-flat-row", "context_size": 8192}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tuned-flat-row" not in body["added"]
    assert any(sk["reason"] == "model.tune_key_removed" for sk in body["skipped"]), body["skipped"]
    assert crud_client.get("/api/models/tuned-flat-row").status_code == 404


def test_scan_with_rows_skips_a_row_carrying_a_bad_fact(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "bad-fact-row.gguf"
    fpath.write_bytes(b"\x00" * 64)
    r = crud_client.post(
        "/api/models/scan",
        json={"rows": [{"path": str(fpath), "id": "bad-fact-row", "mtp": "maybe"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "bad-fact-row" not in body["added"]
    assert any(sk["reason"] == "model.fact_invalid" for sk in body["skipped"]), body["skipped"]
    assert crud_client.get("/api/models/bad-fact-row").status_code == 404


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


def test_create_derives_provider_from_backends_when_absent(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Task 3 fix: POST /api/models with ``backends`` but no ``provider``
    must not mint a permanently ``provider=None`` row — the create path
    derives it the same way the PUT/import/seed paths do."""
    fpath = crud_models_root / "kokoro-voice.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={"id": "kokoro-voice", "path": str(fpath), "backends": ["kokoro"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "kokoro"

    row = crud_client.get("/api/models/kokoro-voice").json()
    assert row["provider"] == "kokoro"


def test_create_explicit_provider_wins_over_backends(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """An explicit ``provider`` in the create body is never overridden by
    the tag-derived guess, even when the tags disagree."""
    fpath = crud_models_root / "explicit-provider.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "explicit-provider",
            "path": str(fpath),
            "backends": ["kokoro"],
            "provider": "llama-server",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "llama-server"


# ── PUT /api/models/{id} ───────────────────────────────────────────────────


def test_update_accepts_new_editable_fields_and_emits(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Task 3: ``backends`` is retired from the PUT write path — sent
    alongside real editable fields, it is silently ignored (not rejected,
    not applied) while ``name``/``capabilities``/the file facts still save and
    still surface in ``changed_fields``."""
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
            "chat_template": "minicpm5-1b",
            "jinja": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Updated Name"
    assert set(body["capabilities"]) == {"chat", "embed"}
    assert body["backends"] == []  # never had any; the PUT's backends is dead
    assert body["chat_template"] == "minicpm5-1b"
    assert body["jinja"] is False
    assert "defaults" not in body

    events = _events_since(crud_client, pre, "model.updated")
    assert events, "expected model.updated event"
    payload = next(ev for ev in events if ev["data"].get("id") == "upd")
    changed = set(payload["data"]["changed_fields"])
    assert {"name", "capabilities", "chat_template", "jinja"} <= changed
    assert "backends" not in changed


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


# ── ADR-0029 §6: a model row holds file facts only, top-level ─────────────────

_FACTS = {
    "chat_template": "minicpm5-1b",
    "tokenizer_repo": "Qwen/Qwen3-4B",
    "mtp": True,
    "jinja": False,
}

#: Every key that used to tune a model and is now a profile tune (or gone).
_TUNE_KEYS = [
    ("extra_args", "-b 1024 -fa on"),
    ("context_size", 8192),
    ("profile", "agent"),
    ("enable_thinking", True),
    ("vision", False),
    ("rope_freq_base", 10000.0),
    ("n_gpu_layers", 99),
    ("preferred_runner", "vulkan"),
]

#: The two spellings of the removed nested table: with content, and a bare null.
_DEFAULTS_BODIES = [
    pytest.param({"chat_template": "chatml"}, id="table"),
    pytest.param(None, id="null"),
]


def test_get_returns_the_facts_top_level_and_no_defaults_key(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "facts-top.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post("/api/models", json={"id": "facts-top", "path": str(fpath), **_FACTS})
    assert r.status_code == 201, r.text
    got = crud_client.get("/api/models/facts-top").json()
    for key, value in _FACTS.items():
        assert got[key] == value, key
    assert "defaults" not in got
    listed = next(
        m for m in crud_client.get("/api/models").json()["models"] if m["id"] == "facts-top"
    )
    assert listed["chat_template"] == "minicpm5-1b"
    assert "defaults" not in listed


def test_get_unset_facts_are_top_level_nulls(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "facts-unset.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "facts-unset", "path": str(fpath)})
    got = crud_client.get("/api/models/facts-unset").json()
    assert got["chat_template"] is None
    assert got["mtp"] is None
    assert got["jinja"] is None
    assert "defaults" not in got


@pytest.mark.parametrize("defaults", _DEFAULTS_BODIES)
def test_put_defaults_key_is_422_and_writes_nothing(
    crud_client: TestClient,
    crud_models_root: Path,
    defaults: object,
) -> None:
    """The nested ``defaults`` table is gone: any ``defaults`` key (a table or a
    bare null) on a PUT is a 422 ``model.defaults_removed`` and the stored row
    is untouched."""
    fpath = crud_models_root / "rm-defaults.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={"id": "rm-defaults", "path": str(fpath), "chat_template": "minicpm5-1b"},
    )
    r = crud_client.put("/api/models/rm-defaults", json={"defaults": defaults})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.defaults_removed"
    assert err["details"]["keys"] == (sorted(defaults) if defaults else [])
    assert err["details"]["facts"] == ["chat_template", "tokenizer_repo", "mtp", "jinja"]
    got = crud_client.get("/api/models/rm-defaults").json()
    assert got["chat_template"] == "minicpm5-1b"
    assert (got["tokenizer_repo"], got["mtp"], got["jinja"]) == (None, None, None)


@pytest.mark.parametrize(("key", "value"), _TUNE_KEYS)
def test_put_top_level_tune_key_is_422_and_writes_nothing(
    crud_client: TestClient,
    crud_models_root: Path,
    key: str,
    value: object,
) -> None:
    """A tune no longer lives on the model: a top-level tune key on PUT is a 422
    that names the key, and the stored row is untouched."""
    fpath = crud_models_root / f"rm-{key}.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={"id": f"rm-{key}", "path": str(fpath), "chat_template": "minicpm5-1b"},
    )
    r = crud_client.put(f"/api/models/rm-{key}", json={key: value})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.tune_key_removed"
    assert key in err["message"]
    assert err["details"]["keys"] == [key]
    got = crud_client.get(f"/api/models/rm-{key}").json()
    assert got["chat_template"] == "minicpm5-1b"
    assert key not in got or got[key] != value


def test_put_tune_key_alongside_a_fact_writes_neither(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The screen runs before the write: a body mixing a valid fact with a
    tuning key is refused whole — the fact is not half-applied."""
    fpath = crud_models_root / "rm-mixed.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "rm-mixed", "path": str(fpath)})
    r = crud_client.put("/api/models/rm-mixed", json={"jinja": False, "extra_args": "-b 1024"})
    assert r.status_code == 422, r.text
    assert crud_client.get("/api/models/rm-mixed").json()["jinja"] is None


@pytest.mark.parametrize("defaults", _DEFAULTS_BODIES)
def test_create_with_defaults_key_is_422_and_writes_nothing(
    crud_client: TestClient,
    crud_models_root: Path,
    defaults: object,
) -> None:
    """POST create screens the body like PUT does — one screen, three paths."""
    fpath = crud_models_root / "cs1.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={"id": "cs1", "path": str(fpath), "defaults": defaults},
    )
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.defaults_removed"
    assert crud_client.get("/api/models/cs1").status_code == 404


def test_create_with_defaults_keys_lists_the_nested_keys(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "cs2.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "cs2",
            "path": str(fpath),
            "defaults": {"extra_args": "--flash-attn on", "context_size": 8192},
        },
    )
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.defaults_removed"
    assert err["details"]["keys"] == ["context_size", "extra_args"]
    assert crud_client.get("/api/models/cs2").status_code == 404


@pytest.mark.parametrize(("key", "value"), _TUNE_KEYS)
def test_create_with_top_level_tune_key_is_422_and_writes_nothing(
    crud_client: TestClient,
    crud_models_root: Path,
    key: str,
    value: object,
) -> None:
    fpath = crud_models_root / f"cs-{key}.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post("/api/models", json={"id": f"cs-{key}", "path": str(fpath), key: value})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.tune_key_removed"
    assert crud_client.get(f"/api/models/cs-{key}").status_code == 404


def test_validate_accepts_facts_only_body(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/validate", json=dict(_FACTS))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


@pytest.mark.parametrize("defaults", _DEFAULTS_BODIES)
def test_validate_rejects_defaults_key_without_writing(
    crud_client: TestClient,
    defaults: object,
) -> None:
    """/validate is a dry run: it screens with the same envelope as create/PUT
    and never touches the registry (no id required, nothing persisted)."""
    r = crud_client.post(
        "/api/models/validate",
        json={"id": "should-not-persist", "defaults": defaults},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.defaults_removed"
    assert crud_client.get("/api/models/should-not-persist").status_code == 404


def test_validate_rejects_a_tune_key_without_writing(crud_client: TestClient) -> None:
    r = crud_client.post(
        "/api/models/validate",
        json={"id": "should-not-persist", "extra_args": "--model /etc/passwd"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.tune_key_removed"
    assert crud_client.get("/api/models/should-not-persist").status_code == 404


def test_validate_reports_removed_context_size_inline(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/validate", json={"context_size": 8192})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.tune_key_removed"


def test_seed_profile_route_is_gone(crud_client: TestClient, crud_models_root: Path) -> None:
    """``POST /api/models/{id}/seed-profile`` was deleted with the copy-on-stamp
    machinery: a model no longer takes a profile's flags."""
    fpath = crud_models_root / "seedgone.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "seedgone", "path": str(fpath)})
    r = crud_client.post("/api/models/seedgone/seed-profile", json={"profile": "chat"})
    assert r.status_code in (404, 405), r.text
    assert crud_client.get("/api/models/seedgone").json()["chat_template"] is None


# ── #1413: PUT keeps the facts it does not name; null clears one ─────────────


def test_put_single_fact_preserves_the_other_facts(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A body naming only ``jinja`` must not reset the facts the client never
    sent (#1413)."""
    fpath = crud_models_root / "m1413.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "m1413", "path": str(fpath)})
    seed = crud_client.put(
        "/api/models/m1413",
        json={"chat_template": "minicpm5-1b", "mtp": True, "jinja": False},
    )
    assert seed.status_code == 200, seed.text

    r = crud_client.put("/api/models/m1413", json={"jinja": True})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["jinja"] is True
    assert got["chat_template"] == "minicpm5-1b"
    assert got["mtp"] is True
    # And the persisted row agrees (not just the response echo).
    stored = crud_client.get("/api/models/m1413").json()
    assert (stored["chat_template"], stored["mtp"], stored["jinja"]) == ("minicpm5-1b", True, True)
    assert "defaults" not in stored


def test_put_explicit_null_clears_only_that_fact(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """`null` remains the DELETE verb for one fact — the tri-state contract the
    drawer relies on to clear it (#1413)."""
    fpath = crud_models_root / "m1413b.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "m1413b", "path": str(fpath)})
    crud_client.put(
        "/api/models/m1413b",
        json={"chat_template": "minicpm5-1b", "mtp": True},
    )
    r = crud_client.put("/api/models/m1413b", json={"mtp": None})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["mtp"] is None
    assert got["chat_template"] == "minicpm5-1b"
    stored = crud_client.get("/api/models/m1413b").json()
    assert stored["mtp"] is None
    assert stored["chat_template"] == "minicpm5-1b"


# ── #1414: a malformed fact is a 400, never a 500 ─────────────────────────────


@pytest.mark.parametrize("bad", ["abc", "", [], {}])
def test_put_unparseable_fact_is_400_not_500(
    crud_client: TestClient,
    crud_models_root: Path,
    bad: object,
) -> None:
    """Client garbage in a fact must never escape as a 5xx with a raw pydantic
    traceback (#1414a)."""
    fpath = crud_models_root / "fact-bad.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "factbad", "path": str(fpath)})
    r = crud_client.put("/api/models/factbad", json={"mtp": bad})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.fact_invalid"
    assert err["details"]["field"] == "mtp"
    assert crud_client.get("/api/models/factbad").json()["mtp"] is None


def test_validate_reports_unparseable_fact_inline(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/validate", json={"jinja": "abc"})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "model.fact_invalid"
    assert err["details"]["field"] == "jinja"


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


# ── #1393: server-side vision↔mmproj backstop ─────────────────────────────────
#
# #1380/PR #1392 fixed the model drawer client-side; the wire stayed open. These
# lock the invariant at the shared write screen so curl/CLI/MCP inherit it.


def test_create_vision_without_mmproj_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """POST create refuses a row that advertises ``vision`` with no projector —
    the launch path would have no ``--mmproj`` to load (#1393)."""
    fpath = crud_models_root / "vis1.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={"id": "vis1", "path": str(fpath), "capabilities": ["chat", "vision"]},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.vision_requires_mmproj"
    assert crud_client.get("/api/models/vis1").status_code == 404


def test_create_vision_with_mmproj_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The legitimate shape — vision + an on-disk projector — still writes."""
    fpath = crud_models_root / "vis2.gguf"
    fpath.write_bytes(b"\x00" * 16)
    mm = crud_models_root / "mmproj-vis2-F16.gguf"
    mm.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "vis2",
            "path": str(fpath),
            "capabilities": ["chat", "vision"],
            "mmproj": str(mm),
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["mmproj"] == str(mm)


def test_put_adding_vision_to_projectorless_row_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A sparse PUT that adds ``vision`` without mentioning ``mmproj`` is
    screened against the STORED projector, not against the absent key (#1393)."""
    fpath = crud_models_root / "vis3.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "vis3", "path": str(fpath)})
    r = crud_client.put("/api/models/vis3", json={"capabilities": ["chat", "vision"]})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.vision_requires_mmproj"
    assert "vision" not in crud_client.get("/api/models/vis3").json()["capabilities"]


def test_put_clearing_mmproj_on_vision_row_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """The mirror image: nulling the projector on a row that still advertises
    vision is the same broken state, reached from the other side."""
    fpath = crud_models_root / "vis4.gguf"
    fpath.write_bytes(b"\x00" * 16)
    mm = crud_models_root / "mmproj-vis4-F16.gguf"
    mm.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={
            "id": "vis4",
            "path": str(fpath),
            "capabilities": ["chat", "vision"],
            "mmproj": str(mm),
        },
    )
    r = crud_client.put("/api/models/vis4", json={"mmproj": None})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.vision_requires_mmproj"


def test_put_adding_vision_alongside_mmproj_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Both halves in ONE sparse PUT satisfy the merged view — the guard reads
    body-over-stored, not body-alone."""
    fpath = crud_models_root / "vis5.gguf"
    fpath.write_bytes(b"\x00" * 16)
    mm = crud_models_root / "mmproj-vis5-F16.gguf"
    mm.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "vis5", "path": str(fpath)})
    r = crud_client.put(
        "/api/models/vis5",
        json={"capabilities": ["chat", "vision"], "mmproj": str(mm)},
    )
    assert r.status_code == 200, r.text
    assert "vision" in r.json()["capabilities"]


def test_put_facts_only_save_on_valid_vision_row_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A save that touches neither field must not re-litigate the invariant —
    otherwise every unrelated edit on a vision row pays for the check."""
    fpath = crud_models_root / "vis6.gguf"
    fpath.write_bytes(b"\x00" * 16)
    mm = crud_models_root / "mmproj-vis6-F16.gguf"
    mm.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={
            "id": "vis6",
            "path": str(fpath),
            "capabilities": ["chat", "vision"],
            "mmproj": str(mm),
        },
    )
    r = crud_client.put("/api/models/vis6", json={"jinja": False})
    assert r.status_code == 200, r.text


def test_put_unrelated_field_on_preexisting_broken_row_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A row already in the broken state (written before the guard existed)
    stays editable: the screen fires only when the write TOUCHES capabilities
    or mmproj, so renaming it is not blocked (#1393's pre-existing-rows note)."""
    from hal0.registry.model import Model

    fpath = crud_models_root / "vis7.gguf"
    fpath.write_bytes(b"\x00" * 16)
    # Bypass the route to plant the legacy shape the guard would now refuse.
    crud_client.app.state.model_registry.add(
        Model(id="vis7", name="vis7", path=str(fpath), capabilities=["chat", "vision"])
    )
    r = crud_client.put("/api/models/vis7", json={"name": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"


def test_validate_rejects_vision_without_mmproj(crud_client: TestClient) -> None:
    """The dry-run screen carries the same envelope so the drawer renders one
    error path (and nothing is persisted)."""
    r = crud_client.post(
        "/api/models/validate",
        json={"id": "vis-dry", "capabilities": ["vision"]},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.vision_requires_mmproj"
    assert crud_client.get("/api/models/vis-dry").status_code == 404


# ── unfiled: an mmproj path that does not exist is accepted today ─────────────


def test_create_with_nonexistent_mmproj_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """``mmproj`` is a host path handed verbatim to the runner as ``--mmproj``.
    A path that isn't there persists happily today and only fails at launch."""
    fpath = crud_models_root / "vis8.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={
            "id": "vis8",
            "path": str(fpath),
            "capabilities": ["chat", "vision"],
            "mmproj": str(crud_models_root / "nope-mmproj.gguf"),
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.mmproj_not_found"
    assert crud_client.get("/api/models/vis8").status_code == 404


def test_put_with_mmproj_pointing_at_a_directory_rejected(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """A directory is not a projector sidecar — same envelope."""
    fpath = crud_models_root / "vis9.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "vis9", "path": str(fpath)})
    r = crud_client.put(
        "/api/models/vis9",
        json={"capabilities": ["chat", "vision"], "mmproj": str(crud_models_root)},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model.mmproj_not_found"


def test_put_clearing_mmproj_and_vision_together_accepted(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """Dropping BOTH halves is the sanctioned way off a vision row — no
    existence check runs on a null projector."""
    fpath = crud_models_root / "vis10.gguf"
    fpath.write_bytes(b"\x00" * 16)
    mm = crud_models_root / "mmproj-vis10-F16.gguf"
    mm.write_bytes(b"\x00" * 16)
    crud_client.post(
        "/api/models",
        json={
            "id": "vis10",
            "path": str(fpath),
            "capabilities": ["chat", "vision"],
            "mmproj": str(mm),
        },
    )
    r = crud_client.put(
        "/api/models/vis10",
        json={"capabilities": ["chat"], "mmproj": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mmproj"] is None


# ── Task 1: Model.provider explicit engine identity ─────────────────────────


def test_update_model_provider_valid_and_invalid(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """PUT accepts a RUNTIME_FAMILIES member into ``provider``, it survives
    the extra-JSON round trip, and a non-member is rejected with
    ``model.provider_invalid`` (screened before the row is touched)."""
    fpath = crud_models_root / "prov1.gguf"
    fpath.write_bytes(b"\x00" * 16)
    crud_client.post("/api/models", json={"id": "prov1", "path": str(fpath)})

    r = crud_client.put("/api/models/prov1", json={"provider": "kokoro"})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "kokoro"

    r2 = crud_client.get("/api/models/prov1")
    assert r2.json()["provider"] == "kokoro"  # survives the extra-JSON round trip

    bad = crud_client.put("/api/models/prov1", json={"provider": "whispercpp"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "model.provider_invalid"


def test_put_explicit_null_provider_rederives_from_backends_2199(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    """#2199: a PUT body carrying ``{"provider": null}`` must converge on the
    same derive-from-backends path as an omitted key, not persist a
    permanent ``provider=None`` row. Round-trip through an explicit override
    first so the assertion can't pass by coincidence (the row already being
    at the derived value)."""
    fpath = crud_models_root / "null-provider.gguf"
    fpath.write_bytes(b"\x00" * 16)
    r = crud_client.post(
        "/api/models",
        json={"id": "null-provider", "path": str(fpath), "backends": ["kokoro"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "kokoro"  # create-time derivation (Task 3)

    # Override to a different explicit provider first.
    r = crud_client.put("/api/models/null-provider", json={"provider": "llama-server"})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "llama-server"

    # An explicit null re-derives from the stored backends tags — it must
    # neither store None nor leave the prior override in place.
    r = crud_client.put("/api/models/null-provider", json={"provider": None})
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "kokoro"

    row = crud_client.get("/api/models/null-provider").json()
    assert row["provider"] == "kokoro"


# ── speculative-decoding drafter pairing (ADR-0031) ─────────────────────────


def _target_gguf(root: Path, name: str = "tgt.gguf") -> Path:
    return write_gguf(
        root / name,
        "qwen3",
        uints={"qwen3.embedding_length": 4096, "qwen3.block_count": 36},
    )


def _drafter_gguf(root: Path, name: str = "tgt-dflash.gguf", *, width: int = 4096) -> Path:
    return write_gguf(
        root / name,
        "dflash",
        uints={"dflash.embedding_length": width, "dflash.block_size": 8},
        int_arrays={"dflash.target_layers": [2, 19, 35]},
    )


def _register(client: TestClient, root: Path, model_id: str = "tgt") -> Path:
    fpath = _target_gguf(root, f"{model_id}.gguf")
    r = client.post("/api/models", json={"id": model_id, "path": str(fpath)})
    assert r.status_code == 201, r.text
    return fpath


def test_put_draft_pairs_and_classifies(crud_client: TestClient, crud_models_root: Path) -> None:
    _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    r = crud_client.put("/api/models/tgt", json={"draft": str(draft)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"] == str(draft)
    assert body["draft_kind"] == "dflash"
    meta = body["metadata"]["draft"]
    assert meta["variant"] == "dflash"
    assert meta["block_size"] == 8
    assert meta["size_bytes"] == draft.stat().st_size
    stored = crud_client.get("/api/models/tgt").json()
    assert (stored["draft"], stored["draft_kind"]) == (str(draft), "dflash")


def test_put_draft_mismatch_422(crud_client: TestClient, crud_models_root: Path) -> None:
    _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root, width=2048)
    r = crud_client.put("/api/models/tgt", json={"draft": str(draft)})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.draft_mismatch"
    assert crud_client.get("/api/models/tgt").json()["draft"] is None


def test_put_draft_that_is_not_a_drafter_422(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    _register(crud_client, crud_models_root)
    clip = write_gguf(crud_models_root / "clip.gguf", "clip")
    r = crud_client.put("/api/models/tgt", json={"draft": str(clip)})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.draft_not_a_drafter"


def test_put_mtp_draft_refused_when_target_mtp_is_false(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    _register(crud_client, crud_models_root)
    assert crud_client.put("/api/models/tgt", json={"mtp": False}).status_code == 200
    mtp = write_gguf(
        crud_models_root / "tgt-mtp.gguf",
        "qwen3",
        uints={"qwen3.nextn_predict_layers": 1},
        tensor_names=["blk.36.nextn.eh_proj.weight"],
    )
    r = crud_client.put("/api/models/tgt", json={"draft": str(mtp)})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "model.draft_mismatch"
    assert "target mtp fact is false" in err["details"]["reasons"]


def test_put_draft_not_found_422(crud_client: TestClient, crud_models_root: Path) -> None:
    _register(crud_client, crud_models_root)
    r = crud_client.put("/api/models/tgt", json={"draft": str(crud_models_root / "nope.gguf")})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.draft_not_found"


def test_put_draft_kind_is_readonly_422(crud_client: TestClient, crud_models_root: Path) -> None:
    _register(crud_client, crud_models_root)
    r = crud_client.put("/api/models/tgt", json={"draft_kind": "mtp"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "model.draft_kind_readonly"


def test_put_draft_null_unpairs_and_keeps_file(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    r = crud_client.put("/api/models/tgt", json={"draft": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft"] is None
    assert body["draft_kind"] is None
    assert "draft" not in body["metadata"]
    assert draft.is_file()


def test_put_metadata_keeps_draft_metadata(crud_client: TestClient, crud_models_root: Path) -> None:
    _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    r = crud_client.put("/api/models/tgt", json={"metadata": {"note": "x"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["note"] == "x"
    assert body["metadata"]["draft"]["variant"] == "dflash"
    assert body["draft"] == str(draft)


def test_delete_draft_route_removes_file_and_clears_fact(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    size = draft.stat().st_size
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    r = crud_client.delete("/api/models/tgt/draft")
    assert r.status_code == 200, r.text
    assert r.json() == {"model_id": "tgt", "removed": str(draft), "bytes_freed": size}
    assert not draft.exists()
    row = crud_client.get("/api/models/tgt").json()
    assert row["draft"] is None
    assert row["draft_kind"] is None
    assert "draft" not in row["metadata"]
    # nothing paired any more -> 404
    assert crud_client.delete("/api/models/tgt/draft").status_code == 404


def test_delete_draft_keeps_a_file_another_model_still_pairs(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    _register(crud_client, crud_models_root, "tgt")
    _register(crud_client, crud_models_root, "tgt2")
    draft = _drafter_gguf(crud_models_root)
    for mid in ("tgt", "tgt2"):
        assert crud_client.put(f"/api/models/{mid}", json={"draft": str(draft)}).status_code == 200
    r = crud_client.delete("/api/models/tgt/draft")
    assert r.status_code == 200, r.text
    assert r.json()["bytes_freed"] == 0
    assert draft.is_file()
    assert crud_client.get("/api/models/tgt").json()["draft"] is None
    assert crud_client.get("/api/models/tgt2").json()["draft"] == str(draft)


def test_delete_model_cascade_removes_draft(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    fpath = _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    r = crud_client.delete("/api/models/tgt")
    assert r.status_code == 200, r.text
    removed = {f["path"] for f in r.json()["files_removed"]}
    assert removed == {str(fpath), str(draft)}
    assert not draft.exists()


# ── DELETE removes files by default (ADR-0026) ─────────────────────────────


def test_delete_removes_files_by_default_and_reports_them(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "sub" / "doomed.gguf"
    fpath.parent.mkdir()
    fpath.write_bytes(b"\x00" * 32)
    assert (
        crud_client.post("/api/models", json={"id": "doomed", "path": str(fpath)}).status_code
        == 201
    )

    r = crud_client.delete("/api/models/doomed")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["affected_slots"] == []
    assert body["files_removed"] == [{"path": str(fpath), "size_bytes": 32}]
    assert body["bytes_freed"] == 32
    assert body["files_kept"] == []
    assert body["tombstoned"] == []
    assert not fpath.exists()
    assert not fpath.parent.exists(), "emptied parent dir is removed"
    assert crud_models_root.is_dir()


def test_delete_keep_files_tombstones_and_scan_does_not_readd(
    crud_client: TestClient,
    crud_models_root: Path,
) -> None:
    fpath = crud_models_root / "kept.gguf"
    fpath.write_bytes(b"\x00" * 32)
    assert (
        crud_client.post("/api/models", json={"id": "kept", "path": str(fpath)}).status_code == 201
    )

    r = crud_client.delete("/api/models/kept?delete_files=false")

    assert r.status_code == 200, r.text
    body = r.json()
    assert fpath.exists()
    assert body["files_kept"] == [{"path": str(fpath), "reason": "kept_by_request"}]
    assert body["tombstoned"] == [str(fpath.resolve())]

    scan = crud_client.post("/api/models/scan", json={})
    assert scan.status_code == 200, scan.text
    assert scan.json()["added"] == []
    assert crud_client.get("/api/models/kept").status_code == 404

    # An explicit add-from-path brings it back and clears the tombstone.
    r = crud_client.post("/api/models/add-from-path", json={"path": str(fpath), "id": "kept"})
    assert r.status_code in (200, 201), r.text
    from hal0.registry import tombstones

    assert tombstones.load() == set()


def test_delete_never_removes_files_outside_the_roots(
    crud_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    outside = Path(tmp_hal0_home) / "not-a-root" / "precious.gguf"
    outside.parent.mkdir()
    outside.write_bytes(b"\x00" * 8)
    crud_client.post("/api/models", json={"id": "precious", "path": str(outside)})

    body = crud_client.delete("/api/models/precious").json()

    assert outside.exists()
    assert body["deleted"] is True
    assert body["files_kept"] == [{"path": str(outside), "reason": "outside_roots"}]


# ── startup auto-scan prunes ghost rows (ADR-0026) ─────────────────────────


def _boot_with_ghosts(tmp_hal0_home: str, *, mounted: bool = True) -> tuple[Path, Any]:
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    root = Path(tmp_hal0_home) / "boot-models"
    root.mkdir()
    if mounted:
        # A non-empty root reads as mounted; an empty one as an unmounted share.
        (root / "README").write_text("models live here\n", encoding="utf-8")
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    (etc / "slots").mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{root}"]\nauto_scan_on_start = true\n', encoding="utf-8"
    )
    (etc / "slots" / "chat.toml").write_text(
        'name = "chat"\nport = 8081\ndevice = "gpu-vulkan"\nprovider = "llama-server"\n'
        '[model]\ndefault = "ghost-referenced"\n',
        encoding="utf-8",
    )
    reg = ModelRegistry()
    reg.add(Model(id="ghost", path=str(root / "gone.gguf")))
    reg.add(Model(id="ghost-referenced", path=str(root / "also-gone.gguf")))
    reg.add(Model(id="npu-model", path=str(root / "flm-gone"), metadata={"runtime": "flm"}))
    return root, reg


def test_startup_scan_prunes_missing_rows_but_protects_referenced(tmp_hal0_home: str) -> None:
    _root, reg = _boot_with_ghosts(tmp_hal0_home)

    with TestClient(create_app()):
        pass

    assert not reg.has("ghost")
    assert reg.has("ghost-referenced"), "slot-referenced row is protected"
    assert reg.has("npu-model"), "FLM rows are never pruned"


def test_startup_scan_skips_prune_when_references_incomplete(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.api as api_mod

    _root, reg = _boot_with_ghosts(tmp_hal0_home)
    monkeypatch.setattr(api_mod, "_collect_referenced_model_ids", lambda: (set(), False))

    with TestClient(create_app()):
        pass

    assert reg.has("ghost"), "no prune with an incomplete protected set"


def test_startup_scan_does_not_prune_under_an_empty_root(tmp_hal0_home: str) -> None:
    """An empty root looks like an unmounted share: its rows are not pruned."""
    _root, reg = _boot_with_ghosts(tmp_hal0_home, mounted=False)

    with TestClient(create_app()):
        pass

    assert reg.has("ghost")


def test_unpaired_drafter_is_tombstoned_and_not_repaired(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    from hal0.config.schema import ModelsConfig
    from hal0.registry.discover import scan_and_register

    fpath = _register(crud_client, crud_models_root)
    draft = _drafter_gguf(crud_models_root)
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    r = crud_client.put("/api/models/tgt", json={"draft": None})
    assert r.status_code == 200, r.text

    registry = crud_client.app.state.model_registry
    cfg = ModelsConfig(roots=[str(crud_models_root)])
    result = scan_and_register(registry, cfg)
    assert result["paired_drafts"] == []
    assert registry.get("tgt").draft is None
    assert fpath.exists() and draft.exists()

    # Pairing it again on purpose lifts the tombstone.
    assert crud_client.put("/api/models/tgt", json={"draft": str(draft)}).status_code == 200
    assert registry.get("tgt").draft == str(draft)


def test_unpair_does_not_tombstone_a_drafter_another_model_pairs(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    from hal0.registry import tombstones

    _register(crud_client, crud_models_root, "tgt")
    _register(crud_client, crud_models_root, "tgt2")
    shared = _drafter_gguf(crud_models_root)
    for mid in ("tgt", "tgt2"):
        assert crud_client.put(f"/api/models/{mid}", json={"draft": str(shared)}).status_code == 200

    assert crud_client.put("/api/models/tgt", json={"draft": None}).status_code == 200
    assert not tombstones.is_tombstoned(shared.resolve(), tombstones.load())
    assert crud_client.get("/api/models/tgt2").json()["draft"] == str(shared)

    # The last pairing going away does tombstone it.
    assert crud_client.put("/api/models/tgt2", json={"draft": None}).status_code == 200
    assert tombstones.is_tombstoned(shared.resolve(), tombstones.load())
