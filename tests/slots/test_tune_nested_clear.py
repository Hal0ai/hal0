"""``tune: null`` clears the root [tune] table on a nested-shape slot dict."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hal0.errors import BadRequest
from hal0.slots.config_write import guard_and_normalize_tune, reconcile_and_guard_slot_config


def _nested() -> dict:
    return {
        "slot": {"name": "n", "type": "llm", "device": "gpu-vulkan", "port": 8083},
        "model": {"default": "m"},
        "tune": {"vision": "off"},
    }


def test_null_tune_clears_root_table_on_nested_base(tmp_hal0_home: str) -> None:
    out = reconcile_and_guard_slot_config("n", _nested(), {"tune": None})
    assert "tune" not in out
    assert "tune" not in out["slot"]


def test_null_tune_clears_on_flat_base(tmp_hal0_home: str) -> None:
    flat = {
        "name": "n",
        "type": "llm",
        "device": "gpu-vulkan",
        "port": 8083,
        "tune": {"vision": "off"},
    }
    assert "tune" not in reconcile_and_guard_slot_config("n", flat, {"tune": None})


def test_per_key_clear_on_nested_base_drops_empty_table(tmp_hal0_home: str) -> None:
    out = reconcile_and_guard_slot_config("n", _nested(), {"tune": {"vision": None}})
    assert "tune" not in out


@pytest.mark.parametrize("nested", [{"type": "embedding"}, {"device": "npu"}])
def test_nested_scalars_win_over_stale_root_in_tune_guard(nested: dict) -> None:
    merged = {"type": "llm", "device": "gpu-vulkan", "slot": nested, "tune": {"vision": "off"}}
    with pytest.raises(BadRequest) as exc:
        guard_and_normalize_tune(merged, {"tune": {"vision": "off"}})
    assert exc.value.code == "slot.tune_not_supported"


def test_nested_profile_controls_thinking_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(view: dict) -> SimpleNamespace:
        return SimpleNamespace(flags="--reasoning off" if view["profile"] == "pinned" else "")

    monkeypatch.setattr("hal0.providers.container.resolve_slot_profile", resolve)
    merged = {
        "type": "llm",
        "device": "gpu-vulkan",
        "profile": "chat",
        "slot": {"profile": "pinned"},
        "tune": {"enable_thinking": True},
    }
    with pytest.raises(BadRequest) as exc:
        guard_and_normalize_tune(merged, {"tune": {"enable_thinking": True}})
    assert exc.value.code == "slot.tune_pinned_by_flags"
