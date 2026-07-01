"""Tests for #770 — canonical slot creation and model-update paths.

Covers:
  (a) POST /api/backends/npu/load creates a slot with ALL canonical
      fields: device=npu, type=llm, runtime=container, profile=flm.
  (b) PUT /api/install/slots/{name}/model succeeds for an existing slot
      and updates model.default while preserving sibling fields.
  (c) PUT /api/install/slots/{name}/model returns a typed 404 error when
      the slot TOML does not exist.
  (d) Existing seeded slot TOMLs written before either change are not
      disturbed (field set is read-then-write, not synthesised from
      scratch).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.slots.state import SlotState

# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, content: str) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(content, encoding="utf-8")
    return path


class _FakeSlot:
    """Minimal Slot stand-in for SlotManager mock returns."""

    def __init__(self, name: str, port: int, model_id: str) -> None:
        self.name = name
        self.port = port
        self.model_id = model_id
        self.state = SlotState.OFFLINE
        self.backend = "flm"
        self.metadata: dict = {}


# ── (a) POST /api/backends/npu/load canonical fields ─────────────────────────


def test_npu_load_creates_slot_with_all_canonical_fields(
    tmp_hal0_home: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """npu/load must write device, type, runtime, profile to the slot TOML.

    This test FAILS before the fix because the old cfg dict omitted those
    fields; after the fix every canonical field appears on disk.
    """
    created_cfg: dict = {}

    sm = client.app.state.slot_manager

    # Patch status to raise (slot does not exist yet) and create to capture cfg.
    from hal0.slots.state import SlotConfigError

    async def _fake_status(name: str) -> _FakeSlot:
        raise SlotConfigError(f"slot {name!r} not found")

    async def _fake_create(name: str, cfg: dict) -> _FakeSlot:
        created_cfg.update(cfg)
        return _FakeSlot(name, cfg.get("port", 8088), cfg.get("model", {}).get("default", ""))

    async def _fake_load(name: str, model_id: str | None = None) -> _FakeSlot:
        return _FakeSlot(name, 8088, model_id or "")

    async def _fake_list() -> list[_FakeSlot]:
        return []

    monkeypatch.setattr(sm, "status", _fake_status)
    monkeypatch.setattr(sm, "create", _fake_create)
    monkeypatch.setattr(sm, "load", _fake_load)
    monkeypatch.setattr(sm, "list", _fake_list)

    r = client.post("/api/backends/npu/load", json={"model_id": "lfm2:1.2b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["model_id"] == "lfm2:1.2b"

    # Canonical fields must all be present in the cfg that was created.
    assert created_cfg.get("device") == "npu", (
        f"expected device='npu', got {created_cfg.get('device')!r}"
    )
    assert created_cfg.get("type") == "llm", f"expected type='llm', got {created_cfg.get('type')!r}"
    assert created_cfg.get("runtime") == "container", (
        f"expected runtime='container', got {created_cfg.get('runtime')!r}"
    )
    assert created_cfg.get("profile") == "flm", (
        f"expected profile='flm', got {created_cfg.get('profile')!r}"
    )


def test_npu_load_idempotent_returns_existing_slot(
    tmp_hal0_home: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call with the same model_id must reuse the existing slot
    and NOT call create again."""
    sm = client.app.state.slot_manager

    existing = _FakeSlot("npu-lfm2-1-2b", 8088, "lfm2:1.2b")

    async def _fake_status(name: str) -> _FakeSlot:
        return existing

    create_calls: list[str] = []

    async def _fake_create(name: str, cfg: dict) -> _FakeSlot:
        create_calls.append(name)
        return existing

    async def _fake_load(name: str, model_id: str | None = None) -> _FakeSlot:
        return existing

    monkeypatch.setattr(sm, "status", _fake_status)
    monkeypatch.setattr(sm, "create", _fake_create)
    monkeypatch.setattr(sm, "load", _fake_load)

    r = client.post("/api/backends/npu/load", json={"model_id": "lfm2:1.2b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is False
    assert not create_calls, "create must not be called for an existing slot"


# ── (b) PUT /api/install/slots/{name}/model — success ────────────────────────


def test_install_slot_model_updates_model_default(
    tmp_hal0_home: str,
    client: TestClient,
) -> None:
    """PUT /api/install/slots/npu/model rewrites model.default in the TOML.

    The existing canonical fields (device, type, runtime, profile) must
    survive the rewrite — only model.default changes.
    """
    slot_toml = _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        "\n".join(
            [
                'name = "npu"',
                "port = 8088",
                'device = "npu"',
                'type = "llm"',
                'runtime = "container"',
                'profile = "flm"',
                "[model]",
                'default = "gemma4-it-e2b-FLM"',
                "context_size = 16384",
                "",
            ]
        ),
    )

    r = client.put(
        "/api/install/slots/npu/model",
        json={"model_id": "lfm2-1-2b-FLM"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slot"] == "npu"
    assert body["model_id"] == "lfm2-1-2b-FLM"

    with slot_toml.open("rb") as fh:
        cfg = tomllib.load(fh)

    # model.default updated
    assert cfg["model"]["default"] == "lfm2-1-2b-FLM"
    # sibling key preserved
    assert cfg["model"]["context_size"] == 16384
    # canonical fields untouched
    assert cfg["device"] == "npu"
    assert cfg["type"] == "llm"
    assert cfg["runtime"] == "container"
    assert cfg["profile"] == "flm"


def test_install_slot_model_preserves_all_existing_top_level_fields(
    tmp_hal0_home: str,
    client: TestClient,
) -> None:
    """All top-level fields in the existing TOML survive a model update."""
    slot_toml = _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        "\n".join(
            [
                'name = "agent"',
                "port = 8081",
                'type = "llm"',
                'device = "gpu-vulkan"',
                'runtime = "container"',
                'profile = "vulkan"',
                "enabled = true",
                "[model]",
                'default = "qwen3.5-9b"',
                "context_size = 65536",
                "",
            ]
        ),
    )

    r = client.put(
        "/api/install/slots/agent/model",
        json={"model_id": "qwen3.6-27b"},
    )
    assert r.status_code == 200, r.text

    with slot_toml.open("rb") as fh:
        cfg = tomllib.load(fh)

    assert cfg["model"]["default"] == "qwen3.6-27b"
    assert cfg["model"]["context_size"] == 65536
    assert cfg["port"] == 8081
    assert cfg["device"] == "gpu-vulkan"
    assert cfg["runtime"] == "container"
    assert cfg["profile"] == "vulkan"
    assert cfg["enabled"] is True


# ── (c) PUT /api/install/slots/{name}/model — missing slot → typed 404 ───────


def test_install_slot_model_returns_typed_404_for_missing_slot(
    tmp_hal0_home: str,
    client: TestClient,
) -> None:
    """A request for a slot whose TOML does not exist returns a typed 404.

    This test FAILS before the fix because the endpoint didn't exist.
    After the fix the endpoint exists and returns install.slot_toml_not_found.
    """
    r = client.put(
        "/api/install/slots/nonexistent/model",
        json={"model_id": "some-model"},
    )
    assert r.status_code == 404, r.text
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "install.slot_toml_not_found"
    assert "nonexistent" in body["error"]["message"]


def test_install_slot_model_missing_body_field_returns_400(
    tmp_hal0_home: str,
    client: TestClient,
) -> None:
    """Missing model_id in body returns 400, not 500."""
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        'name = "npu"\nport = 8088\n[model]\ndefault = "x"\n',
    )
    r = client.put("/api/install/slots/npu/model", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "install.model_id_required"


# ── (d) seeded slot TOMLs are not disturbed ───────────────────────────────────


def test_seeded_npu_toml_fields_survive_model_update(
    tmp_hal0_home: str,
    client: TestClient,
) -> None:
    """The seeded npu.toml shape (all canonical fields) is fully preserved
    after a PUT /install/slots/npu/model call — no fields are dropped or
    rewritten to stale defaults."""
    slot_toml = _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        # Mirrors installer/etc-hal0/slots/npu.toml exactly
        "\n".join(
            [
                "# NPU LLM slot",
                'name = "npu"',
                "port = 8088",
                'device = "npu"',
                'runtime = "container"',
                'profile = "flm"',
                'type = "llm"',
                'role = "utility"',
                "[model]",
                'default = "gemma4-it-e2b-FLM"',
                "context_size = 16384",
                "",
            ]
        ),
    )

    r = client.put(
        "/api/install/slots/npu/model",
        json={"model_id": "lfm2-1-2b-FLM"},
    )
    assert r.status_code == 200, r.text

    with slot_toml.open("rb") as fh:
        cfg = tomllib.load(fh)

    # Only model.default changed
    assert cfg["model"]["default"] == "lfm2-1-2b-FLM"
    assert cfg["model"]["context_size"] == 16384
    # All other canonical fields intact
    assert cfg["name"] == "npu"
    assert cfg["port"] == 8088
    assert cfg["device"] == "npu"
    assert cfg["runtime"] == "container"
    assert cfg["profile"] == "flm"
    assert cfg["type"] == "llm"
    assert cfg["role"] == "utility"
