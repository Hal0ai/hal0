"""Tests for typed-bodies inc-3: in-handler pydantic validation on the
COMMAND bodies in ``routes/models.py`` + ``routes/slots.py`` where a
missing/wrong-type key used to silently degrade into a bad downstream call
instead of a clean 400.

Every case here asserts the SAME contract the untyped sites already had:

  * a valid body still works (200/201/202 as before).
  * a missing/wrong-type key returns **400** with the hal0 typed envelope
    (``{"error": {"code", "message", "details"}}``) — NEVER FastAPI's 422
    ``{"detail": [...]}`` shape, since these routes stay hand-parsed
    ``await request.json()`` handlers, not FastAPI body-params.
  * the LEFT-LOOSE sites (create_slot / update_slot_config /
    update_slot_defaults / create_model / update_model / add-from-path /
    inspect / validate / scan) are unchanged by this lane — a couple of
    smoke checks pin that.

Anti-silent-degradation cases (the actual point of this lane): a
non-string ``model_id`` on slot load/swap used to either tunnel into
``SlotManager`` (load: silently proceeds, ships a bad value toward the
180s container health timeout) or crash inside
``is_resolvable()``'s ``.endswith()`` call (swap: an unhandled
AttributeError). Both now fail fast with a clean 400 instead.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

_INVALID_JSON_BODY = b"this is not json {{{"


# ── slots.py fixtures ─────────────────────────────────────────────────────────
#
# Mirrors test_slots_routes.py's container_stub/slot_root — kept local here
# (pytest fixtures aren't shared across test modules without a conftest
# entry, and the house convention in this test suite is per-file fixtures).


@pytest.fixture
def container_stub() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"active": set(), "load_calls": []}
    provider = MagicMock()

    def _load_sync(cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        state["load_calls"].append({"cfg": dict(cfg), "model_info": dict(model_info)})
        state["active"].add(str(cfg.get("name", "")))

    def _unload_sync(cfg: dict[str, Any]) -> None:
        state["active"].discard(str(cfg.get("name", "")))

    provider.load_sync = MagicMock(side_effect=_load_sync)
    provider.unload_sync = MagicMock(side_effect=_unload_sync)
    provider.wait_ready = AsyncMock(return_value=None)
    provider.is_active = MagicMock(side_effect=lambda name: name in state["active"])
    provider.health = AsyncMock(side_effect=lambda port: {"ok": True, "status": "healthy"})
    provider.running_image = MagicMock(return_value=None)
    provider.running_argv = MagicMock(return_value=None)
    provider.expected_argv = MagicMock(return_value=None)
    provider.image_present = MagicMock(return_value=True)

    with patch("hal0.providers.container.container_provider", return_value=provider):
        yield state


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Write a chat.toml slot (matches test_slots_routes.py's fixture shape)."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                'runtime = "container"',
                'profile = "vulkan-radv"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def slots_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


# ── models.py fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def models_app(tmp_hal0_home: str) -> FastAPI:
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
def models_client(models_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(models_app) as c:
        yield c


def _register_model(client: TestClient, tmp_hal0_home: str, mid: str) -> None:
    fpath = Path(tmp_hal0_home) / "models" / f"{mid}.gguf"
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(b"\x00" * 16)
    r = client.post("/api/models", json={"id": mid, "path": str(fpath)})
    assert r.status_code == 201, r.text


# ══════════════════════════════════════════════════════════════════════════
# slots.py — POST /{name}/rename
# ══════════════════════════════════════════════════════════════════════════


def test_rename_valid_body_works(slot_root: Path, slots_client: TestClient) -> None:
    r = slots_client.post("/api/slots/chat/rename", json={"new_name": "chat2"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "chat2"


def test_rename_missing_name_returns_400_typed_envelope(
    slot_root: Path, slots_client: TestClient
) -> None:
    r = slots_client.post("/api/slots/chat/rename", json={})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "slot.name_required"


def test_rename_wrong_type_new_name_returns_400_not_422(
    slot_root: Path, slots_client: TestClient
) -> None:
    """A non-string new_name must clean-400, not FastAPI-422."""
    r = slots_client.post("/api/slots/chat/rename", json={"new_name": 123})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body, f"expected hal0 envelope not FastAPI 422 shape: {body}"
    assert body["error"]["code"] == "slot.name_required"


def test_rename_invalid_json_returns_400_request_invalid_json(
    slot_root: Path, slots_client: TestClient
) -> None:
    r = slots_client.post(
        "/api/slots/chat/rename",
        content=_INVALID_JSON_BODY,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "request.invalid_json"


# ══════════════════════════════════════════════════════════════════════════
# slots.py — POST /{name}/load
# ══════════════════════════════════════════════════════════════════════════


def test_load_valid_body_works(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register_model(slots_client, tmp_hal0_home, "chat-model")
    r = slots_client.post("/api/slots/chat/load", json={"model_id": "chat-model"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "ready"
    assert container_stub["load_calls"]


def test_load_no_body_falls_back_to_default(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
) -> None:
    """Unchanged behavior: no body at all still loads the TOML default."""
    r = slots_client.post("/api/slots/chat/load")
    assert r.status_code == 200, r.text


def test_load_invalid_json_body_still_falls_back(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
) -> None:
    """Unchanged behavior: malformed JSON is swallowed, not 400'd, here —
    load's contract has always been "any body-read failure means no
    override", unlike rename/swap which 400 on bad JSON."""
    r = slots_client.post(
        "/api/slots/chat/load",
        content=_INVALID_JSON_BODY,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200, r.text


def test_load_non_string_model_id_returns_clean_400(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
) -> None:
    """Anti-silent-degradation: a non-string model_id (e.g. an int) used to
    slip past the old truthy ``if model_id:`` guard and tunnel into
    SlotManager.load — now it 400s before ever touching the slot."""
    r = slots_client.post("/api/slots/chat/load", json={"model_id": 123})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "slot.invalid_model_id"
    # The slot must be untouched — no load attempt happened.
    assert not container_stub["load_calls"]


def test_load_falsy_int_zero_model_id_returns_clean_400(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
) -> None:
    """The sharpest edge of the old bug: ``model_id: 0`` is falsy, so the
    old ``if model_id:`` guard skipped validation AND treated it as "no
    override" only by accident of truthiness — a differently-falsy wrong
    type (e.g. ``False``) would too. Pin that it's now rejected outright
    rather than silently coerced."""
    r = slots_client.post("/api/slots/chat/load", json={"model_id": 0})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.invalid_model_id"


# ══════════════════════════════════════════════════════════════════════════
# slots.py — POST /{name}/swap
# ══════════════════════════════════════════════════════════════════════════


def test_swap_valid_body_works(
    slot_root: Path,
    container_stub: dict[str, Any],
    slots_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    _register_model(slots_client, tmp_hal0_home, "swap-model")
    slots_client.post("/api/slots/chat/load")
    r = slots_client.post("/api/slots/chat/swap", json={"model_id": "swap-model"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "chat"


def test_swap_missing_model_id_returns_400_swap_missing_model(
    slot_root: Path, container_stub: dict[str, Any], slots_client: TestClient
) -> None:
    r = slots_client.post("/api/slots/chat/swap", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "swap.missing_model"


def test_swap_non_string_model_id_returns_clean_400_same_code(
    slot_root: Path, container_stub: dict[str, Any], slots_client: TestClient
) -> None:
    """Anti-silent-degradation: a truthy non-string model_id (e.g. an int)
    used to sail past the ``if not model_id:`` guard and crash inside
    ``is_resolvable()``'s ``.endswith()`` call. Now it 400s with the SAME
    ``swap.missing_model`` code the empty-body case already uses — not a
    500, and not a new/different code."""
    r = slots_client.post("/api/slots/chat/swap", json={"model_id": 123})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "swap.missing_model"


# ══════════════════════════════════════════════════════════════════════════
# slots.py — left-loose sites unchanged (SlotConfig config-write trio)
# ══════════════════════════════════════════════════════════════════════════


def test_create_slot_unknown_key_still_400s_via_existing_boundary(
    slots_client: TestClient,
) -> None:
    """create_slot is left loose — _reject_unknown_config_keys is still the
    boundary, untouched by this lane."""
    r = slots_client.post(
        "/api/slots",
        json={"name": "loose1", "model": {"ctx_sizee": 4096}},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.unknown_keys"


def test_update_slot_config_unknown_key_still_400s(
    slot_root: Path, slots_client: TestClient
) -> None:
    r = slots_client.put("/api/slots/chat/config", json={"enabeld": True})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.unknown_keys"


def test_update_slot_defaults_unknown_key_still_400s(
    slot_root: Path, slots_client: TestClient
) -> None:
    r = slots_client.patch("/api/slots/chat/defaults", json={"ctx_sizee": 4096})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.unknown_keys"


# ══════════════════════════════════════════════════════════════════════════
# models.py — POST /scan/preview
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def preview_client(tmp_hal0_home: str) -> Iterator[tuple[TestClient, Path]]:
    extra_root = Path(tmp_hal0_home) / "preview-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[models]\nroots = []\nauto_scan_on_start = false\n", encoding="utf-8"
    )
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c, extra_root


def test_scan_preview_valid_body_works(
    preview_client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, root = preview_client
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: [])
    (root / "qwen3-4b-q4_k_m.gguf").write_bytes(b"\x00" * 64)
    r = client.post("/api/models/scan/preview", json={"paths": [str(root)]})
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1


def test_scan_preview_empty_paths_returns_400_unchanged(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Pin the pre-existing behavior/code (no explicit code = validation.invalid)."""
    client, _ = preview_client
    r = client.post("/api/models/scan/preview", json={"paths": []})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.invalid"


def test_scan_preview_non_list_paths_returns_400_not_422(
    preview_client: tuple[TestClient, Path],
) -> None:
    client, _ = preview_client
    r = client.post("/api/models/scan/preview", json={"paths": "not-a-list"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body, f"expected hal0 envelope not FastAPI 422 shape: {body}"


def test_scan_preview_non_string_path_elements_return_clean_400(
    preview_client: tuple[TestClient, Path],
) -> None:
    """Anti-silent-degradation: the old code only checked `isinstance(raw_paths,
    list)`, never each element's type — `{"paths": [1, 2]}` used to sail
    through toward Path()/detect() calls on an int. Now it 400s here."""
    client, _ = preview_client
    r = client.post("/api/models/scan/preview", json={"paths": [1, 2]})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "validation.invalid"


# ══════════════════════════════════════════════════════════════════════════
# models.py — POST /{model_id}/default
# ══════════════════════════════════════════════════════════════════════════


def test_set_default_valid_body_works(models_client: TestClient, tmp_hal0_home: str) -> None:
    _register_model(models_client, tmp_hal0_home, "def-a")
    r = models_client.post("/api/models/def-a/default", json={"default": True})
    assert r.status_code == 200, r.text
    assert r.json()["default"] is True


def test_set_default_bare_post_defaults_true_unchanged(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    _register_model(models_client, tmp_hal0_home, "def-b")
    r = models_client.post("/api/models/def-b/default")
    assert r.status_code == 200, r.text
    assert r.json()["default"] is True


def test_set_default_string_false_keeps_legacy_truthy_coercion(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    """Deliberately-preserved quirk: {"default": "false"} is a non-empty
    string, so the legacy ``bool("false")`` is True — same today. Typing
    this field as pydantic ``bool`` would have FLIPPED this result (pydantic
    parses the string "false" as False), so `default` is typed `Any` on
    purpose. This test pins the (surprising but unchanged) result."""
    _register_model(models_client, tmp_hal0_home, "def-c")
    r = models_client.post("/api/models/def-c/default", json={"default": "false"})
    assert r.status_code == 200, r.text
    assert r.json()["default"] is True


def test_set_default_unknown_model_still_404(models_client: TestClient) -> None:
    r = models_client.post("/api/models/ghost/default")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "model.not_found"


def test_set_default_invalid_json_returns_400(models_client: TestClient) -> None:
    r = models_client.post(
        "/api/models/anything/default",
        content=_INVALID_JSON_BODY,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400, r.text
    assert "error" in r.json()


# ══════════════════════════════════════════════════════════════════════════
# models.py — POST /{model_id}/duplicate
# ══════════════════════════════════════════════════════════════════════════


def test_duplicate_valid_body_works(models_client: TestClient, tmp_hal0_home: str) -> None:
    _register_model(models_client, tmp_hal0_home, "dup-src")
    r = models_client.post("/api/models/dup-src/duplicate", json={"new_id": "dup-copy"})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "dup-copy"


def test_duplicate_missing_new_id_returns_400_not_422(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    _register_model(models_client, tmp_hal0_home, "dup-src2")
    r = models_client.post("/api/models/dup-src2/duplicate", json={})
    assert r.status_code == 400, r.text
    body = r.json()
    assert "error" in body, f"expected hal0 envelope not FastAPI 422 shape: {body}"
    assert body["error"]["code"] == "validation.invalid"


def test_duplicate_wrong_type_new_id_returns_400_not_422(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    _register_model(models_client, tmp_hal0_home, "dup-src3")
    r = models_client.post("/api/models/dup-src3/duplicate", json={"new_id": 123})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.invalid"


def test_duplicate_wrong_type_profile_returns_400(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    _register_model(models_client, tmp_hal0_home, "dup-src4")
    r = models_client.post(
        "/api/models/dup-src4/duplicate", json={"new_id": "dup-copy4", "profile": 123}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.invalid"


def test_duplicate_both_new_id_and_profile_wrong_reports_new_id_first(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    """Pin the old sequential-if check order: new_id was validated before
    profile, so a body with both wrong reports the new_id error."""
    _register_model(models_client, tmp_hal0_home, "dup-src5")
    r = models_client.post("/api/models/dup-src5/duplicate", json={"new_id": 1, "profile": 2})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    assert "new_id" in _json.dumps(body["error"].get("details", {}))


# ══════════════════════════════════════════════════════════════════════════
# models.py — left-loose sites unchanged
# ══════════════════════════════════════════════════════════════════════════


def test_create_model_left_loose_uses_model_pydantic_directly(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    """create_model is left loose: ``Model(**body)`` IS the validation."""
    fpath = Path(tmp_hal0_home) / "models" / "loose-create.gguf"
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(b"\x00" * 16)
    r = models_client.post("/api/models", json={"id": "loose-create", "path": str(fpath)})
    assert r.status_code == 201, r.text
    # Bad shape still 400s — through Model(**body), not a new typed model.
    r2 = models_client.post("/api/models", json={"id": 123, "path": str(fpath)})
    assert r2.status_code == 400, r2.text


def test_update_model_left_loose_via_registry_update(
    models_client: TestClient, tmp_hal0_home: str
) -> None:
    _register_model(models_client, tmp_hal0_home, "loose-upd")
    r = models_client.put("/api/models/loose-upd", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
