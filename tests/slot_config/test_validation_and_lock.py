"""Unit tests for the slot-config write helpers: ``unknown_slot_config_keys``
(boundary validation), ``reject_slot_tuning_keys`` (the ADR-0029 partition)
and ``slot_write_lock`` (coarse cross-process lock for all slots/*.toml
writes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.locking import lock_path_for
from hal0.errors import BadRequest
from hal0.slot_config import (
    reject_slot_tuning_keys,
    slot_write_lock,
    unknown_slot_config_keys,
)

# ── unknown_slot_config_keys ─────────────────────────────────────────────────


class TestUnknownKeys:
    def test_clean_payload_passes(self) -> None:
        payload = {
            "name": "chat",
            "port": 8081,
            "type": "llm",
            "device": "gpu-vulkan",
            "provider": "llama-server",
            "runtime": "container",
            "default": True,
            "profile": "agent",
            "model": {"default": "m"},
            "server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"}},
            "npu": {"asr": True, "embed": False},
            "image": {"default_steps": 20},
        }
        assert unknown_slot_config_keys(payload) == []

    def test_typo_top_level_flagged(self) -> None:
        assert unknown_slot_config_keys({"enabeld": True}) == ["enabeld"]

    def test_removed_enabled_key_is_flagged(self) -> None:
        """#1369: ``enabled`` is no longer a declared field, so the boundary
        must flag it rather than let ``extra="allow"`` persist inert debris.
        The route layer intercepts it first with a migration hint
        (``reject_removed_slot_keys``); this is the backstop."""
        assert unknown_slot_config_keys({"enabled": True}) == ["enabled"]

    def test_typo_in_subtables_flagged_with_paths(self) -> None:
        payload = {
            "model": {"ctx_sizee": 1},
            "server": {"extra_argz": ""},
            "npu": {"asrr": True},
        }
        assert unknown_slot_config_keys(payload) == [
            "model.ctx_sizee",
            "npu.asrr",
            "server.extra_argz",
        ]

    def test_model_table_knows_only_default(self) -> None:
        """ADR-0029: ``[model]`` names the model and nothing else, so even a
        provider ``extra`` table under it is unknown."""
        assert unknown_slot_config_keys({"model": {"default": "m", "extra": {"a": 1}}}) == [
            "model.extra"
        ]

    def test_string_image_override_is_unknown(self) -> None:
        """The bare container-image override is gone (``image_pin``); ``image``
        is the image-generation settings table."""
        assert unknown_slot_config_keys({"image": "ghcr.io/x/y:z"}) == ["image"]

    def test_there_is_no_extra_passthrough(self) -> None:
        assert unknown_slot_config_keys({"extra": {"whatever": {"nested": 1}}}) == ["extra"]

    def test_nested_slot_table_checked_against_top_vocabulary(self) -> None:
        assert unknown_slot_config_keys({"slot": {"port": 8081, "prot": 1}}) == ["slot.prot"]

    def test_fields_derived_dynamically_from_schema(self) -> None:
        """Every declared ServerConfig field passes without a hardcoded list —
        this is what keeps schema additions like [server].env in sync."""
        from hal0.config.schema import ServerConfig

        payload = {"server": dict.fromkeys(ServerConfig.model_fields, None)}
        assert unknown_slot_config_keys(payload) == []

    def test_tuning_keys_are_unknown_at_the_generic_boundary(self) -> None:
        """No tuning key is a declared slot field any more (ADR-0029), so the
        generic boundary alone flags them as typos. The specific
        ``reject_slot_tuning_keys`` check (below) runs first at the route
        layer so the operator gets the "belongs on a profile" message."""
        payload = {
            "model": {"ctx_size": 1, "context_size": 2},
            "server": {"extra_args": ""},
            "mtp": True,
            "parallel": 2,
            "workers": 1,
            "chat_template": "chatml",
        }
        assert unknown_slot_config_keys(payload) == [
            "chat_template",
            "model.context_size",
            "model.ctx_size",
            "mtp",
            "parallel",
            "server.extra_args",
            "workers",
        ]


# ── reject_slot_tuning_keys (ADR-0029 §5) ────────────────────────────────────


class TestRejectSlotTuningKeys:
    def test_clean_payload_passes(self) -> None:
        reject_slot_tuning_keys(
            {
                "name": "chat",
                "port": 8081,
                "device": "gpu-rocm",
                "profile": "agent",
                "model": {"default": "m"},
                "server": {"env": {"A": "1"}},
            }
        )

    @pytest.mark.parametrize(
        ("payload", "key"),
        [
            ({"mtp": True}, "mtp"),
            ({"enable_thinking": False}, "enable_thinking"),
            ({"vision": False}, "vision"),
            ({"parallel": 4}, "parallel"),
            ({"chat_template": "chatml"}, "chat_template"),
            ({"workers": 2}, "workers"),
            ({"ctx_size": 8192}, "ctx_size"),
            ({"model": {"context_size": 8192}}, "[model].context_size"),
            ({"model": {"ctx_size": 8192}}, "[model].ctx_size"),
            ({"model": {"n_gpu_layers": 40}}, "[model].n_gpu_layers"),
            ({"model": {"rope_freq_base": 1e6}}, "[model].rope_freq_base"),
            ({"server": {"extra_args": "-b 2048"}}, "[server].extra_args"),
            ({"slot": {"parallel": 2}}, "parallel"),
        ],
    )
    def test_each_tuning_key_is_rejected(self, payload: dict, key: str) -> None:
        with pytest.raises(BadRequest) as exc:
            reject_slot_tuning_keys(payload)
        assert exc.value.code == "slot.tuning_key_denied"
        assert exc.value.details["keys"] == [key]
        assert "profile" in exc.value.message

    def test_multiple_offenders_all_listed(self) -> None:
        with pytest.raises(BadRequest) as exc:
            reject_slot_tuning_keys(
                {"vision": True, "mtp": None, "model": {"default": "m", "context_size": 1}}
            )
        assert set(exc.value.details["keys"]) == {"vision", "mtp", "[model].context_size"}

    def test_null_value_still_rejected(self) -> None:
        """The key itself is the violation, not its value: an explicit null
        (the old "reset to Auto" shape) is refused too."""
        with pytest.raises(BadRequest):
            reject_slot_tuning_keys({"mtp": None})


# ── slot_write_lock ──────────────────────────────────────────────────────────


class TestSlotWriteLock:
    def test_creates_one_coarse_lock_for_the_slots_dir(self, tmp_path: Path) -> None:
        slots_dir = tmp_path / "slots"
        with slot_write_lock(slots_dir):
            assert lock_path_for(slots_dir).exists()

    def test_reentrant_within_thread(self, tmp_path: Path) -> None:
        slots_dir = tmp_path / "slots"
        with slot_write_lock(slots_dir), slot_write_lock(slots_dir):
            pass  # nested acquire must not self-deadlock

    def test_defaults_to_configured_slots_dir(self, tmp_hal0_home: str) -> None:
        from hal0.config import paths

        with slot_write_lock():
            assert lock_path_for(paths.slots_config_dir()).exists()
