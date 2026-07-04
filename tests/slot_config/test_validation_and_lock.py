"""Unit tests for the slot-config write helpers added in the guarded-writes
wave: ``unknown_slot_config_keys`` (boundary validation),
``fold_ctx_size_alias`` (the ONE #585 fold), and ``slot_write_lock``
(coarse cross-process lock for all slots/*.toml writes).
"""

from __future__ import annotations

from pathlib import Path

from hal0.config.locking import lock_path_for
from hal0.slot_config import (
    fold_ctx_size_alias,
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
            "backend": "vulkan",  # deprecated but declared field
            "provider": "llama-server",
            "runtime": "container",
            "enabled": True,
            "default": True,
            "lru": True,
            "profile": "vulkan",
            "model": {"default": "m", "context_size": 8192, "extra": {"anything": 1}},
            "server": {"extra_args": "-b 2048"},
            "npu": {"asr": True, "embed": False},
            "image": {"default_steps": 20},
        }
        assert unknown_slot_config_keys(payload) == []

    def test_typo_top_level_flagged(self) -> None:
        assert unknown_slot_config_keys({"enabeld": True}) == ["enabeld"]

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

    def test_legacy_ctx_size_alias_tolerated(self) -> None:
        assert unknown_slot_config_keys({"model": {"ctx_size": 8192}}) == []

    def test_string_image_override_tolerated(self) -> None:
        assert unknown_slot_config_keys({"image": "ghcr.io/x/y:z"}) == []

    def test_extra_tables_pass_verbatim(self) -> None:
        assert unknown_slot_config_keys({"extra": {"whatever": {"nested": 1}}}) == []

    def test_nested_slot_table_checked_against_top_vocabulary(self) -> None:
        assert unknown_slot_config_keys({"slot": {"port": 8081, "prot": 1}}) == ["slot.prot"]

    def test_fields_derived_dynamically_from_schema(self) -> None:
        """Every declared ServerConfig field passes without a hardcoded list —
        this is what keeps schema additions like [server].env in sync."""
        from hal0.config.schema import ServerConfig

        payload = {"server": dict.fromkeys(ServerConfig.model_fields, None)}
        assert unknown_slot_config_keys(payload) == []


# ── fold_ctx_size_alias ──────────────────────────────────────────────────────


class TestFoldCtxSizeAlias:
    def test_folds_and_drops_alias(self) -> None:
        cfg = {"model": {"ctx_size": 4096, "context_size": 2048, "default": "m"}}
        fold_ctx_size_alias(cfg)
        assert cfg["model"] == {"context_size": 4096, "default": "m"}

    def test_noop_without_alias(self) -> None:
        cfg = {"model": {"context_size": 2048}}
        fold_ctx_size_alias(cfg)
        assert cfg == {"model": {"context_size": 2048}}

    def test_copy_safe_on_shared_model_subdict(self) -> None:
        """The 'before' snapshot sharing the [model] object is never mutated."""
        shared = {"ctx_size": 4096}
        before = {"model": shared}
        cfg = {"model": shared}
        fold_ctx_size_alias(cfg)
        assert before["model"] == {"ctx_size": 4096}, "shared sub-dict must not mutate"
        assert cfg["model"] == {"context_size": 4096}


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
