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
