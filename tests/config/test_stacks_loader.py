"""Unit tests for the stacks.toml loader/saver.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_loader.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.loader import ConfigParseError, load_stacks_config, save_stacks_config
from hal0.config.schema import SEED_STACKS, StackConfig, StacksConfig, StackSlotEntry


class TestLoadStacksConfig:
    def test_missing_file_returns_seed_catalog(self, tmp_path: Path) -> None:
        # Absent stacks.toml → the built-in seed catalog (SEED_STACKS), so a
        # fresh install ships a usable Stacks page (PR-6). Mirrors
        # load_profiles_config's seed fallback.
        cfg = load_stacks_config(path=tmp_path / "nonexistent.toml")
        assert cfg.stack == SEED_STACKS
        assert set(cfg.stack) == {"saber", "forge", "pi"}

    def test_round_trip_save_then_load(self, tmp_path: Path) -> None:
        target = tmp_path / "stacks.toml"
        cfg = StacksConfig(
            stack={
                "mine": StackConfig(
                    name="Mine",
                    description="a custom stack",
                    slots=[StackSlotEntry(slot="agent", model="chadrock-35b-ace-saber")],
                )
            }
        )
        save_stacks_config(cfg, path=target)
        assert target.exists()
        loaded = load_stacks_config(path=target)
        assert loaded.stack["mine"] == cfg.stack["mine"]
        assert set(loaded.stack) == {"mine", *SEED_STACKS}

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_bytes(b"[stack\nbad toml <<<")
        with pytest.raises(ConfigParseError):
            load_stacks_config(path=p)

    def test_unknown_field_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_bytes(b'[stack.x]\nname = "X"\nnot_a_field = "surprise"\n')
        with pytest.raises(ConfigParseError):
            load_stacks_config(path=p)


_STALE_FORGE = """
[stack.forge]
name = "Forge"
description = "written by an older release"

[[stack.forge.slots]]
slot = "agent"
model = "old-coder"
device = "gpu-rocm"
profile = "rocm"

[[stack.forge.slots]]
slot = "utility"
model = "old-draft"
device = "gpu-vulkan"
profile = "vulkan"
"""


class TestSeedStacksAreVirtual:
    """Seed stacks overlay from code on every load and are never persisted."""

    def test_stale_seed_copy_on_disk_loads_as_the_code_seed(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_text(_STALE_FORGE, encoding="utf-8")
        forge = load_stacks_config(path=p).stack["forge"]
        assert forge == SEED_STACKS["forge"]
        assert {row.profile for row in forge.slots}.isdisjoint({"rocm", "vulkan", "rocm-moe"})

    def test_invalid_stale_seed_copy_does_not_block_the_catalog(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_text('[stack.pi]\nname = "Pi"\nnot_a_field = "gone"\n', encoding="utf-8")
        assert load_stacks_config(path=p).stack["pi"] == SEED_STACKS["pi"]

    def test_seeds_come_first_then_custom_stacks(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_text(_STALE_FORGE + '\n[stack.zzz]\nname = "Z"\n', encoding="utf-8")
        assert list(load_stacks_config(path=p).stack) == [*SEED_STACKS, "zzz"]

    def test_custom_stack_survives_next_to_a_stale_seed(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_text(_STALE_FORGE + '\n[stack.mine]\nname = "Mine"\n', encoding="utf-8")
        loaded = load_stacks_config(path=p)
        assert loaded.stack["mine"].name == "Mine"
        assert loaded.stack["forge"] == SEED_STACKS["forge"]

    def test_loaded_seed_is_a_copy_not_the_module_global(self, tmp_path: Path) -> None:
        cfg = load_stacks_config(path=tmp_path / "absent.toml")
        assert cfg.stack["forge"] is not SEED_STACKS["forge"]
        cfg.stack["forge"].name = "Mutated"
        assert SEED_STACKS["forge"].name != "Mutated"

    def test_save_drops_seed_slugs_and_cleans_a_stale_file(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_text(_STALE_FORGE + '\n[stack.mine]\nname = "Mine"\n', encoding="utf-8")
        cfg = load_stacks_config(path=p)  # carries all seeds plus "mine"
        save_stacks_config(cfg, path=p)
        on_disk = tomllib.loads(p.read_text(encoding="utf-8"))["stack"]
        assert set(on_disk) == {"mine"}
        assert "rocm" not in p.read_text(encoding="utf-8")

    def test_save_of_only_seeds_leaves_no_seed_on_disk(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        save_stacks_config(load_stacks_config(path=tmp_path / "absent.toml"), path=p)
        assert not tomllib.loads(p.read_text(encoding="utf-8")).get("stack")
        assert load_stacks_config(path=p).stack == SEED_STACKS
