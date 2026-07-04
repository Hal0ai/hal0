"""Boundary validation on slot-config writes (PUT config / PATCH defaults /
POST create) + configured port-range allocation.

``SlotConfig``/``ModelConfig``/``ServerConfig`` are ``extra="allow"``, so a
typo'd key used to persist silently and the intended setting never took
effect. The routes now reject unknown keys with 400
``validation.unknown_keys`` listing the offending paths, while all
documented legacy aliases (``[model].ctx_size``, string ``image``, …) and
actively-read extras (``type``, ``default``, ``lru``, ``default_voice``)
keep working. Known fields are derived from the pydantic models
dynamically, so schema additions (e.g. ``[server].env``) pass automatically.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api.routes.slots import _next_free_slot_port


@pytest.fixture
def slot_toml(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "chat.toml"
    path.write_text(
        "\n".join(
            [
                'name = "chat"',
                'type = "llm"',
                'device = "gpu-vulkan"',
                "enabled = true",
                "port = 8081",
                "[model]",
                'default = "qwen3-4b"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


# ── PUT /config ──────────────────────────────────────────────────────────────


def test_put_config_unknown_top_level_key_400(slot_toml: Path, client: TestClient) -> None:
    r = client.put("/api/slots/chat/config", json={"enabeld": False})
    assert r.status_code == 400, r.text
    body = r.json()["error"]
    assert body["code"] == "validation.unknown_keys"
    assert body["details"]["unknown_keys"] == ["enabeld"]
    # Nothing persisted.
    assert "enabeld" not in _read(slot_toml)


def test_put_config_unknown_model_key_400_with_path(slot_toml: Path, client: TestClient) -> None:
    r = client.put("/api/slots/chat/config", json={"model": {"ctx_sizee": 8192}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["unknown_keys"] == ["model.ctx_sizee"]


def test_put_config_valid_keys_and_dynamic_server_fields_pass(
    slot_toml: Path, client: TestClient
) -> None:
    """[server].env (schema-derived, not hardcoded) + extra_args pass."""
    r = client.put(
        "/api/slots/chat/config",
        json={"server": {"extra_args": "-b 2048", "env": {"HSA_XNACK": "1"}}},
    )
    assert r.status_code == 200, r.text
    on_disk = _read(slot_toml)
    assert on_disk["server"]["extra_args"] == "-b 2048"
    assert on_disk["server"]["env"] == {"HSA_XNACK": "1"}


def test_put_config_tolerated_extras_pass(slot_toml: Path, client: TestClient) -> None:
    """default_voice (voice settings) and string image override keep working."""
    r = client.put("/api/slots/chat/config", json={"default_voice": "Ryan"})
    assert r.status_code == 200, r.text
    r = client.put("/api/slots/chat/config", json={"image": "ghcr.io/hal0ai/toolbox:vulkan"})
    assert r.status_code == 200, r.text
    on_disk = _read(slot_toml)
    assert on_disk["default_voice"] == "Ryan"
    assert on_disk["image"] == "ghcr.io/hal0ai/toolbox:vulkan"


# ── PATCH /defaults ──────────────────────────────────────────────────────────


def test_patch_defaults_ctx_size_alias_still_accepted(slot_toml: Path, client: TestClient) -> None:
    r = client.patch("/api/slots/chat/defaults", json={"ctx_size": 32768})
    assert r.status_code == 200, r.text
    model = _read(slot_toml)["model"]
    # Folded to the canonical key; exactly one survives on disk (#585).
    assert model["context_size"] == 32768
    assert "ctx_size" not in model
    assert model["default"] == "qwen3-4b", "sibling [model] keys must survive"


def test_patch_defaults_unknown_key_400(slot_toml: Path, client: TestClient) -> None:
    r = client.patch("/api/slots/chat/defaults", json={"contxt_size": 32768})
    assert r.status_code == 400, r.text
    body = r.json()["error"]
    assert body["code"] == "validation.unknown_keys"
    assert body["details"]["unknown_keys"] == ["model.contxt_size"]


# ── POST create ──────────────────────────────────────────────────────────────


def test_create_unknown_key_400_writes_nothing(tmp_hal0_home: str, client: TestClient) -> None:
    r = client.post(
        "/api/slots",
        json={"name": "extra", "type": "llm", "modle": "qwen3-4b"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["unknown_keys"] == ["modle"]
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "extra.toml").exists()


def test_create_flat_body_valid_keys_201(slot_toml: Path, client: TestClient) -> None:
    r = client.post(
        "/api/slots",
        json={
            "name": "extra",
            "type": "llm",
            "runtime": "container",
            "device": "gpu-vulkan",
            "model": "qwen3-4b",
            "default": False,
        },
    )
    assert r.status_code == 201, r.text
    # Auto-assigned port skips chat's 8081.
    assert r.json()["port"] == 8082


# ── port-range allocation ────────────────────────────────────────────────────


def test_next_free_slot_port_pool_capped_below_comfyui(tmp_hal0_home: str) -> None:
    """The auto-allocation pool deliberately ends at 8099 (#1036).

    The pool default is capped below ComfyUI's 8188 so a freshly
    auto-created slot never squats on the image port; per-slot ``port``
    validation still allows explicit values up to 8200. Occupying the
    whole default pool therefore exhausts it rather than spilling into
    the 8100+ range — a wider pool must be opted into via
    ``[slots].port_range_end`` (covered by the hal0.toml-range test).
    """
    from hal0.api.middleware.error_codes import BadRequest

    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    for port in range(8081, 8100):  # occupy the whole default 8081-8099 pool
        (root / f"s{port}.toml").write_text(f'name = "s{port}"\nport = {port}\n', encoding="utf-8")
    with pytest.raises(BadRequest) as exc:
        _next_free_slot_port()
    assert exc.value.code == "slot.no_free_port"


def test_next_free_slot_port_honors_hal0_toml_range(tmp_hal0_home: str) -> None:
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[slots]\nport_range_start = 8150\nport_range_end = 8152\n",
        encoding="utf-8",
    )
    (etc / "slots").mkdir(exist_ok=True)
    (etc / "slots" / "a.toml").write_text('name = "a"\nport = 8150\n', encoding="utf-8")
    assert _next_free_slot_port() == 8151


def test_next_free_slot_port_exhausted_configured_range_raises(
    tmp_hal0_home: str,
) -> None:
    from hal0.api.middleware.error_codes import BadRequest

    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    (etc / "slots").mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[slots]\nport_range_start = 8150\nport_range_end = 8151\n",
        encoding="utf-8",
    )
    for port in (8150, 8151):
        (etc / "slots" / f"s{port}.toml").write_text(
            f'name = "s{port}"\nport = {port}\n', encoding="utf-8"
        )
    with pytest.raises(BadRequest) as exc_info:
        _next_free_slot_port()
    assert exc_info.value.code == "slot.no_free_port"
