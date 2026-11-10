"""An xdna slot is not FastFlowLM, for every NPU-means-FLM shortcut (ADR-0057).

``binary = "xdna"`` puts a llama-server launch on ``device = "npu"``. Each
helper below used ``device == "npu"`` to mean "the FLM process"; an xdna slot
must not be read as the FLM anchor, an FLM slot for model fit, a member of
the FLM trio's shared port group, or the trio's dispatch target.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.ports import _config_claims
from hal0.slots.activation import (
    claims_npu_anchor,
    is_xdna_slot,
    npu_anchor_config,
    npu_modality_active,
)
from hal0.slots.config_write import slot_is_flm
from hal0.slots.manager import SlotManager


def _xdna(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "xdna",
        "port": 8090,
        "device": "npu",
        "type": "llm",
        "binary": "xdna",
        "profile": "chat",
        "model": {"default": "qwen3.5-0.8b-q4_k_m"},
    }
    base.update(overrides)
    return base


def _flm(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "flm",
        "port": 8082,
        "device": "npu",
        "type": "llm",
        "profile": "flm",
        "model": {"default": "gemma3:1b"},
        "npu": {"chat": True},
    }
    base.update(overrides)
    return base


def test_is_xdna_slot_reads_flat_and_nested_shapes() -> None:
    assert is_xdna_slot(_xdna())
    assert is_xdna_slot({"slot": {"binary": "xdna"}})
    assert not is_xdna_slot(_flm())
    assert not is_xdna_slot(_flm(binary="flm"))
    assert not is_xdna_slot(None)


def test_is_xdna_slot_follows_the_loaders_slot_table_precedence() -> None:
    """``[slot]`` wins on a clash; a root binary counts when ``[slot]`` has none."""
    assert not is_xdna_slot({"binary": "xdna", "slot": {"binary": "rocm"}})
    assert is_xdna_slot({"binary": "rocm", "slot": {"binary": "xdna"}})
    assert is_xdna_slot({"binary": "xdna", "slot": {"name": "x"}})


def test_is_xdna_slot_matches_exactly_like_get_runner() -> None:
    # get_runner("XDNA") misses, so the launch would not run the xdna image;
    # the slot must not be carved out of FLM either.
    assert not is_xdna_slot(_xdna(binary="XDNA"))
    assert not is_xdna_slot(_xdna(binary=" xdna"))


def test_slot_is_flm_sees_a_root_binary_beside_a_slot_table() -> None:
    assert not slot_is_flm({"binary": "xdna", "slot": {"device": "npu", "type": "llm"}})


def test_effective_max_inflight_reads_the_slot_table_first(tmp_path: Path) -> None:
    from types import SimpleNamespace

    (tmp_path / "a.toml").write_text('binary = "rocm"\n[slot]\nbinary = "xdna"\n')
    (tmp_path / "b.toml").write_text('binary = "xdna"\n[slot]\nbinary = "rocm"\n')
    fake = SimpleNamespace(
        _cfg_cache={},
        _resolve_alias=lambda n: n,
        _key=lambda n: n,
        _config_file_for=lambda n: tmp_path / f"{n}.toml",
    )
    cap = SlotManager.effective_max_inflight.__get__(fake)
    assert cap("a") == 1
    assert cap("b") is None


def test_xdna_slot_is_not_the_flm_anchor() -> None:
    assert not claims_npu_anchor(_xdna())
    assert claims_npu_anchor(_flm())


def test_npu_anchor_config_skips_xdna_slots() -> None:
    xdna, flm = _xdna(), _flm()
    assert npu_anchor_config([xdna, flm]) is flm
    assert npu_anchor_config([xdna]) is None
    # A model-less FLM slot is still the fallback anchor; xdna never is.
    bare = _flm(model={"default": ""})
    assert npu_anchor_config([xdna, bare]) is bare


def test_npu_modality_reads_the_flm_anchor_not_an_xdna_slot() -> None:
    assert not npu_modality_active([_xdna()], "llm")
    assert npu_modality_active([_xdna(), _flm()], "llm")


def test_xdna_slot_is_not_a_container_flm_slot() -> None:
    assert not is_container_npu_cfg(_xdna())
    assert is_container_npu_cfg(_flm())


def test_xdna_slot_is_not_flm_for_model_fit() -> None:
    assert not slot_is_flm(_xdna())
    assert not slot_is_flm({"slot": {"device": "npu", "binary": "xdna"}})
    assert slot_is_flm(_flm())


def test_xdna_slot_does_not_join_the_flm_port_group(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    assert sm._derive_identity_fields("xdna", _xdna())[2] is None
    assert sm._derive_identity_fields("flm", _flm())[2] == "npu-flm-trio"


def test_port_config_claims_keep_xdna_out_of_the_flm_group(tmp_path: Path) -> None:
    (tmp_path / "xdna.toml").write_text(
        'name = "xdna"\nport = 8090\ndevice = "npu"\nbinary = "xdna"\n', encoding="utf-8"
    )
    (tmp_path / "flm.toml").write_text(
        'name = "flm"\nport = 8082\ndevice = "npu"\n', encoding="utf-8"
    )
    groups = {c.owner: c.group for c in _config_claims(tmp_path)}
    assert groups == {"slot:xdna": None, "slot:flm": "npu-flm-trio"}


# ── review follow-ups: the remaining npu-means-FLM shortcuts ────────────────


def test_launch_refuses_xdna_binary_that_dispatches_to_flm() -> None:
    from unittest.mock import patch

    import pytest

    from hal0.providers.container import _refuse_foreign_runner, _spec_provider_for
    from hal0.slots.state import RunnerFamilyMismatch

    cfg = _xdna(profile="flm")
    with patch("hal0.providers.container._profile_runtime_family", return_value="flm"):
        provider = _spec_provider_for(cfg)
        assert provider is not None  # the FLM-family profile still dispatches FLM
        with pytest.raises(RunnerFamilyMismatch):
            _refuse_foreign_runner(cfg, provider)


def test_effective_backend_of_an_xdna_slot_is_npu_not_flm() -> None:
    from hal0.slots.config_write import _cfg_effective_backend

    assert _cfg_effective_backend(_xdna()) == "npu"
    assert _cfg_effective_backend(_flm()) == "flm"


def test_flm_multiplex_tags_skip_an_xdna_slot_with_a_stale_npu_table() -> None:
    from hal0.api import _flm_multiplex_tags

    stale = _xdna(npu={"asr": True, "embed": True})
    assert _flm_multiplex_tags(stale) == []
    assert _flm_multiplex_tags(_flm(npu={"asr": True})) != []


def test_hal0_npu_alias_skips_xdna_slots() -> None:
    from hal0.normalize.resolver import SlotView, _slot_matches_name

    xdna = SlotView(name="xdna", device="npu", model_id="m", context_length=0, binary="xdna")
    flm = SlotView(name="flm", device="npu", model_id="g", context_length=0)
    assert not _slot_matches_name(xdna, "npu")
    assert _slot_matches_name(flm, "npu")
    # A slot literally named npu still answers by name.
    named = SlotView(name="npu", device="npu", model_id="m", context_length=0, binary="xdna")
    assert _slot_matches_name(named, "npu")


def test_route_to_chat_allows_xdna_and_flm_to_delegate() -> None:
    from hal0.omni_router.route_to_chat import validate_delegation

    configs = [_xdna(), _flm()]
    assert (
        validate_delegation(configs, caller_slot_name="xdna", target="flm", current_depth=0) is None
    )
    assert (
        validate_delegation(configs, caller_slot_name="flm", target="xdna", current_depth=0) is None
    )
    two_flm = [_flm(), _flm(name="flm-b")]
    refused = validate_delegation(two_flm, caller_slot_name="flm", target="flm-b", current_depth=0)
    assert refused is not None and "NPU" in refused


def test_xdna_is_never_an_npu_trio_shadow() -> None:
    from hal0.slots.npu.trio import is_npu_trio_shadow

    assert not is_npu_trio_shadow(_xdna(type="embedding"))
    assert is_npu_trio_shadow(_flm(type="embedding"))


def test_config_write_refuses_xdna_on_a_non_llm_slot() -> None:
    import pytest

    from hal0.errors import BadRequest
    from hal0.slots.config_write import guard_xdna_slot_shape

    with pytest.raises(BadRequest) as exc:
        guard_xdna_slot_shape(_xdna(type="transcription"))
    assert exc.value.code == "slot.xdna_not_supported"


def test_config_write_refuses_glu_a_in_server_env() -> None:
    import pytest

    from hal0.errors import BadRequest
    from hal0.slots.config_write import guard_xdna_slot_shape

    cfg = _xdna(server={"env": {"GGML_XDNA_PGEMM_GLU_A": "1"}})
    with pytest.raises(BadRequest) as exc:
        guard_xdna_slot_shape(cfg, {"server"})
    assert exc.value.status == 400
    assert exc.value.code == "validation.xdna_env_pinned"
    # Not an xdna slot: [server].env is not this guard's business.
    guard_xdna_slot_shape(_flm(server={"env": {"GGML_XDNA_PGEMM_GLU_A": "1"}}), {"server"})
    # An unrelated edit does not trip on a state already on disk.
    guard_xdna_slot_shape(cfg, {"model"})


def test_binary_flip_to_xdna_drops_the_stale_npu_table() -> None:
    from hal0.slots.config_write import guard_xdna_slot_shape

    cfg = _xdna(npu={"asr": True})
    guard_xdna_slot_shape(cfg, {"binary"})
    assert "npu" not in cfg


async def test_update_config_refuses_glu_a_in_server_env(tmp_hal0_home: str) -> None:
    import pytest

    from hal0.errors import BadRequest

    sm = SlotManager()
    cfg = _xdna()
    del cfg["profile"]
    await sm.create("xdna", cfg)
    with pytest.raises(BadRequest) as exc:
        await sm.update_config("xdna", {"server": {"env": {"GGML_XDNA_PGEMM_GLU_A": "1"}}})
    assert exc.value.code == "validation.xdna_env_pinned"
