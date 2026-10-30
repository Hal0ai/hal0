"""Boundary validation on slot-config writes (PUT config / PATCH defaults /
POST create) + configured port-range allocation.

``SlotConfig``/``ModelConfig``/``ServerConfig`` are ``extra="forbid"``, so a
typo'd key would persist a slot file that fails its next load. The routes
reject unknown keys with 400 ``validation.unknown_keys`` listing the
offending paths; the declared fields (``type``, ``default``,
``default_voice``, …) are the whole vocabulary. Known fields are derived from
the pydantic models dynamically, so schema additions (e.g. ``[server].env``)
pass automatically.
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
    """[server].env (schema-derived, not hardcoded) passes."""
    r = client.put("/api/slots/chat/config", json={"server": {"env": {"HSA_XNACK": "1"}}})
    assert r.status_code == 200, r.text
    assert _read(slot_toml)["server"] == {"env": {"HSA_XNACK": "1"}}


def test_put_config_null_deletes_a_key(slot_toml: Path, client: TestClient) -> None:
    """``null`` REMOVES a persisted key (#1372): ``reconcile_slot_updates``
    implements None-means-delete, so the drawer's "Clear" really unsets it."""
    r = client.put("/api/slots/chat/config", json={"image_pin": "ghcr.io/x/y:z"})
    assert r.status_code == 200, r.text
    assert _read(slot_toml)["image_pin"] == "ghcr.io/x/y:z"

    r = client.put("/api/slots/chat/config", json={"image_pin": None})
    assert r.status_code == 200, r.text
    on_disk = _read(slot_toml)
    assert "image_pin" not in on_disk
    # Siblings survive the removal — it is a targeted delete, not a rewrite.
    assert on_disk["model"]["default"] == "qwen3-4b"
    assert on_disk["port"] == 8081


@pytest.mark.parametrize("value", ["chatml", "", None])
def test_put_config_refuses_a_slot_chat_template(
    value: object, slot_toml: Path, client: TestClient
) -> None:
    """ADR-0029: the chat template is a fact about the model file; the slot
    has no ``chat_template`` key to set or clear, whatever the value."""
    before = slot_toml.read_text(encoding="utf-8")
    r = client.put("/api/slots/chat/config", json={"chat_template": value})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.tuning_key_denied"
    assert slot_toml.read_text(encoding="utf-8") == before


def test_put_config_refuses_server_extra_args(slot_toml: Path, client: TestClient) -> None:
    """Launch flags live on the slot's profile, never in ``[server]``."""
    r = client.put(
        "/api/slots/chat/config",
        json={"server": {"extra_args": "-b 2048", "env": {"HSA_XNACK": "1"}}},
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "slot.tuning_key_denied"
    assert err["details"]["keys"] == ["[server].extra_args"]
    assert "server" not in _read(slot_toml)


def test_put_config_declared_fields_pass(slot_toml: Path, client: TestClient) -> None:
    """Every key the drawer sends is a declared field: voice settings, the
    per-slot output-sanity opt-out, the type and the image pin."""
    for body in (
        {"default_voice": "Ryan"},
        {"output_sanity": False},
        {"type": "llm", "default": True},
        {"image_pin": "ghcr.io/hal0ai/toolbox:vulkan"},
    ):
        r = client.put("/api/slots/chat/config", json=body)
        assert r.status_code == 200, r.text
    on_disk = _read(slot_toml)
    assert on_disk["default_voice"] == "Ryan"
    assert on_disk["output_sanity"] is False
    assert on_disk["image_pin"] == "ghcr.io/hal0ai/toolbox:vulkan"


def test_put_config_refuses_keys_the_old_allow_list_tolerated(
    slot_toml: Path, client: TestClient
) -> None:
    """``lru`` (retired), ``default_language`` (never read) and a bare ``image``
    ref (now ``image_pin``) would fail the slot's next load: refused."""
    before = slot_toml.read_text(encoding="utf-8")
    for body, code in (
        ({"lru": True}, "slot.removed_key_denied"),
        ({"default_language": "Auto"}, "validation.unknown_keys"),
        ({"image": "ghcr.io/hal0ai/toolbox:vulkan"}, "validation.unknown_keys"),
    ):
        r = client.put("/api/slots/chat/config", json=body)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == code
    assert slot_toml.read_text(encoding="utf-8") == before


# ── model-table writes through PUT /config ──────────────────────────────────


def test_put_config_refuses_a_model_context_window(slot_toml: Path, client: TestClient) -> None:
    """The ``ctx_size`` alias fold is gone (ADR-0029): the window is the slot
    profile's ``ctx_size``, so a ``[model]`` write refuses it and writes nothing."""
    before = slot_toml.read_text(encoding="utf-8")
    for body in ({"ctx_size": 32768}, {"context_size": 32768}):
        r = client.put("/api/slots/chat/config", json={"model": body})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.tuning_key_denied"
    assert slot_toml.read_text(encoding="utf-8") == before


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


# ── PUT /config: the [tune] override table (slot tune overrides) ─────────────


@pytest.fixture
def embed_slot_toml(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "embed.toml"
    path.write_text(
        'name = "embed"\ntype = "embedding"\ndevice = "gpu-vulkan"\nport = 8082\n'
        '[model]\ndefault = "bge-small"\n',
        encoding="utf-8",
    )
    return path


def test_put_tune_persists_table(slot_toml: Path, client: TestClient) -> None:
    r = client.put("/api/slots/chat/config", json={"tune": {"vision": "off", "ctx_size": 8192}})
    assert r.status_code == 200, r.text
    assert _read(slot_toml)["tune"] == {"vision": "off", "ctx_size": 8192}
    # A second partial write merges key by key; a null key clears just that key.
    r = client.put("/api/slots/chat/config", json={"tune": {"vision": None, "reasoning_budget": 0}})
    assert r.status_code == 200, r.text
    assert _read(slot_toml)["tune"] == {"ctx_size": 8192, "reasoning_budget": 0}


def test_put_tune_null_clears_table(slot_toml: Path, client: TestClient) -> None:
    client.put("/api/slots/chat/config", json={"tune": {"vision": "off"}})
    assert "tune" in _read(slot_toml)
    r = client.put("/api/slots/chat/config", json={"tune": None})
    assert r.status_code == 200, r.text
    assert "tune" not in _read(slot_toml)


def test_put_tune_unknown_key_400(slot_toml: Path, client: TestClient) -> None:
    before = slot_toml.read_bytes()
    r = client.put("/api/slots/chat/config", json={"tune": {"mtp": "off"}})
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "validation.unknown_keys"
    assert err["details"]["unknown_keys"] == ["tune.mtp"]
    assert slot_toml.read_bytes() == before


@pytest.mark.parametrize(
    "tune",
    [
        {"vision": "maybe"},
        {"ctx_size": 5},
        {"reasoning_effort": "wild"},
        {"reasoning_budget": -2},
        {"speculation": "on"},
    ],
)
def test_put_tune_bad_value_400_and_file_untouched(
    slot_toml: Path, client: TestClient, tune: dict
) -> None:
    before = slot_toml.read_bytes()
    r = client.put("/api/slots/chat/config", json={"tune": tune})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation.tune_invalid"
    assert slot_toml.read_bytes() == before


def test_put_tune_not_an_object_400(slot_toml: Path, client: TestClient) -> None:
    before = slot_toml.read_bytes()
    r = client.put("/api/slots/chat/config", json={"tune": "off"})
    assert r.status_code == 400, r.text
    assert slot_toml.read_bytes() == before


def test_tune_refused_on_non_llm_slot(embed_slot_toml: Path, client: TestClient) -> None:
    before = embed_slot_toml.read_bytes()
    r = client.put("/api/slots/embed/config", json={"tune": {"vision": "off"}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.tune_not_supported"
    assert embed_slot_toml.read_bytes() == before


def test_tune_refused_on_npu_slot(slot_toml: Path, client: TestClient) -> None:
    before = slot_toml.read_bytes()
    r = client.put("/api/slots/chat/config", json={"device": "npu", "tune": {"vision": "off"}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.tune_not_supported"
    assert slot_toml.read_bytes() == before


def test_tune_clear_is_allowed_on_non_llm_slot(embed_slot_toml: Path, client: TestClient) -> None:
    r = client.put("/api/slots/embed/config", json={"tune": None})
    assert r.status_code == 200, r.text


def test_tune_thinking_refused_when_pinned(slot_toml: Path, client: TestClient) -> None:
    from hal0.config.schema import ProfileConfig
    from hal0.profiles import ProfileCatalog

    ProfileCatalog().create("pinned", ProfileConfig(flags="--reasoning off"))
    assert client.put("/api/slots/chat/config", json={"profile": "pinned"}).status_code == 200
    before = slot_toml.read_bytes()
    r = client.put("/api/slots/chat/config", json={"tune": {"enable_thinking": True}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.tune_pinned_by_flags"
    assert slot_toml.read_bytes() == before
    # The other keys are not pinned by the flag.
    assert client.put("/api/slots/chat/config", json={"tune": {"vision": "off"}}).status_code == 200


def test_tune_thinking_judged_against_the_profile_in_the_same_write(
    slot_toml: Path, client: TestClient
) -> None:
    from hal0.config.schema import ProfileConfig
    from hal0.profiles import ProfileCatalog

    ProfileCatalog().create("pinned2", ProfileConfig(flags="--reasoning off"))
    r = client.put(
        "/api/slots/chat/config", json={"profile": "pinned2", "tune": {"enable_thinking": True}}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.tune_pinned_by_flags"


def test_put_tune_per_key_nulls_remove_keys_and_empty_table(
    slot_toml: Path, client: TestClient
) -> None:
    client.put("/api/slots/chat/config", json={"tune": {"vision": "off", "ctx_size": 8192}})
    r = client.put("/api/slots/chat/config", json={"tune": {"vision": None, "ctx_size": None}})
    assert r.status_code == 200, r.text
    assert "tune" not in _read(slot_toml)
    assert "[tune]" not in slot_toml.read_text()


def test_put_tune_null_clear_never_refused(
    embed_slot_toml: Path, slot_toml: Path, client: TestClient
) -> None:
    from hal0.config.schema import ProfileConfig
    from hal0.profiles import ProfileCatalog

    # Unsupported slot (embedding): per-key nulls and tune=null are both accepted.
    assert (
        client.put("/api/slots/embed/config", json={"tune": {"enable_thinking": None}}).status_code
        == 200
    )
    assert client.put("/api/slots/embed/config", json={"tune": None}).status_code == 200
    # Pinned key (profile flags carry --reasoning): clearing it is fine.
    ProfileCatalog().create("pinned3", ProfileConfig(flags="--reasoning off"))
    client.put("/api/slots/chat/config", json={"tune": {"vision": "off"}})
    client.put("/api/slots/chat/config", json={"profile": "pinned3"})
    r = client.put(
        "/api/slots/chat/config", json={"tune": {"enable_thinking": None, "vision": None}}
    )
    assert r.status_code == 200, r.text
    assert "tune" not in _read(slot_toml)


def test_tune_speculation_ngram_needs_no_drafter(slot_toml: Path, client: TestClient) -> None:
    """The guard checks slot type/device and the thinking pin only; it never asks
    whether the model carries a drafter or MTP heads, so ngram (and auto/off) pass."""
    for mode in ("ngram", "auto", "off"):
        r = client.put("/api/slots/chat/config", json={"tune": {"speculation": mode}})
        assert r.status_code == 200, r.text
        assert _read(slot_toml)["tune"]["speculation"] == mode
