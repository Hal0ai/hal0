"""Pure NPU conflict rule: FLM anchor vs per-op xdna slot (issue #3666)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots.npu_conflict import (
    NpuPeer,
    effective_xdna_path,
    find_npu_conflict,
    npu_role,
    roles_conflict,
    xdna_launch_env,
)
from hal0.slots.state import LIVE_COUNTERPART_STATES, SlotState
from tests.registry.gguf_fixtures import write_gguf

FLM = {"name": "flm", "device": "npu", "type": "llm", "model": {"default": "qwen3:8b"}}
SHADOW_STT = {
    "name": "flm-stt",
    "device": "npu",
    "type": "transcription",
    "model": {"default": "whisper"},
    "served_by": "flm",
}
SHADOW_EMBED = {
    "name": "flm-embed",
    "device": "npu",
    "type": "embedding",
    "model": {"default": "emb"},
    "served_by": "flm",
}


def _xdna(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "device": "npu",
        "type": "llm",
        "binary": "xdna",
        "model": {"default": "m"},
    }


def _peer(
    cfg: dict[str, Any],
    state: SlotState = SlotState.READY,
    *,
    pinned: bool = False,
    path: Any = None,
) -> NpuPeer:
    return NpuPeer(name=cfg["name"], cfg=cfg, state=state, pinned=pinned, xdna_path=path)


def test_perop_incoming_vs_live_flm_conflicts() -> None:
    peers = [
        _peer(FLM),
        _peer(SHADOW_STT, SlotState.OFFLINE),
        _peer(SHADOW_EMBED, SlotState.OFFLINE),
    ]
    c = find_npu_conflict("x", _xdna("x"), "per-op", peers, {})
    assert c is not None
    assert c.holders == ("flm",)
    assert c.holder_engine == "flm"
    assert c.unloads == ("flm", "flm-stt", "flm-embed")
    assert c.pending is False


def test_flm_incoming_vs_live_perop_conflicts() -> None:
    peers = [_peer(_xdna("a"), path="per-op"), _peer(_xdna("b"), path="per-op")]
    c = find_npu_conflict("flm", FLM, None, peers, {})
    assert c is not None
    assert c.holders == ("a", "b")
    assert c.holder_engine == "xdna"
    assert c.unloads == ("a", "b")


def test_fused_incoming_vs_flm_no_conflict() -> None:
    assert find_npu_conflict("x", _xdna("x"), "fused", [_peer(FLM)], {}) is None


def test_flm_incoming_vs_fused_only_no_conflict() -> None:
    assert find_npu_conflict("flm", FLM, None, [_peer(_xdna("a"), path="fused")], {}) is None


def test_offline_holder_no_conflict() -> None:
    assert find_npu_conflict("x", _xdna("x"), "per-op", [_peer(FLM, SlotState.OFFLINE)], {}) is None


@pytest.mark.parametrize("state", sorted(LIVE_COUNTERPART_STATES, key=str))
def test_loading_and_unloading_holder_counts_live(state: SlotState) -> None:
    assert find_npu_conflict("x", _xdna("x"), "per-op", [_peer(FLM, state)], {}) is not None


def test_pending_opposite_reservation_conflicts_with_pending_true() -> None:
    c = find_npu_conflict("x", _xdna("x"), "per-op", [], {"flm": "flm"})
    assert c is not None and c.pending is True and c.holders == ("flm",)
    c2 = find_npu_conflict("flm", FLM, None, [], {"p": "per-op"})
    assert c2 is not None and c2.pending is True and c2.holder_engine == "xdna"
    assert find_npu_conflict("x", _xdna("x"), "per-op", [], {"p": "per-op"}) is None


def test_pinned_holder_reported() -> None:
    c = find_npu_conflict("x", _xdna("x"), "per-op", [_peer(FLM, pinned=True)], {})
    assert c is not None and c.pinned is True


def test_trio_shadow_incoming_never_conflicts() -> None:
    peers = [_peer(_xdna("a"), path="per-op"), _peer(FLM)]
    assert find_npu_conflict("flm-stt", SHADOW_STT, None, peers, {}) is None


def test_roles() -> None:
    assert npu_role(FLM, None) == "flm"
    assert npu_role(_xdna("a"), "per-op") == "per-op"
    assert npu_role(_xdna("a"), "fused") is None
    assert npu_role(SHADOW_STT, None) is None
    assert npu_role({"name": "g", "device": "gpu-vulkan", "type": "llm"}, None) is None
    assert roles_conflict("flm", "per-op") and roles_conflict("per-op", "flm")
    assert not roles_conflict("flm", "flm")
    assert not roles_conflict(None, "flm")


def test_effective_path_env_zero_forces_perop_on_fused_model(tmp_path: Path) -> None:
    path = write_gguf(
        tmp_path / "m.gguf",
        "qwen35",
        uints={"qwen35.embedding_length": 1024},
        tensor_names=["blk.0.ssm_out.weight"],
        tensor_types={"blk.0.ssm_out.weight": 13},
    )
    assert effective_xdna_path({}, path) == "fused"
    assert effective_xdna_path({"GGML_XDNA_FUSED_LAYER": "0"}, path) == "per-op"


def test_effective_path_unreadable_model_is_perop(tmp_path: Path) -> None:
    assert effective_xdna_path({}, tmp_path / "missing.gguf") == "per-op"
    assert effective_xdna_path({}, None) == "per-op"
    bad = tmp_path / "x.gguf"
    bad.write_bytes(b"nope")
    assert effective_xdna_path({}, bad) == "per-op"


def test_launch_env_merge_order_matches_load_sync() -> None:
    cfg = {
        "server": {"env": {"GGML_XDNA_FUSED_LAYER": "1", "FOO": "s"}},
        "xdna": {"env": {"GGML_XDNA_FUSED_LAYER": "0"}},
    }
    env = xdna_launch_env(cfg)
    assert env["GGML_XDNA_FUSED_LAYER"] == "0"  # [xdna].env wins
    assert env["FOO"] == "s"


def test_live_and_pending_holders_together_report_pending() -> None:
    """A pending holder anywhere in the set blocks the opt-in, even beside a live one."""
    peers = [_peer(_xdna("a"), path="per-op")]
    c = find_npu_conflict("flm", FLM, None, peers, {"b": "per-op"})
    assert c is not None
    assert c.holders == ("a", "b")
    assert c.pending is True
