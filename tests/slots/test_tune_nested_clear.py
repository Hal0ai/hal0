"""``tune: null`` clears the root [tune] table on a nested-shape slot dict."""

from __future__ import annotations

from hal0.slots.config_write import reconcile_and_guard_slot_config


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
