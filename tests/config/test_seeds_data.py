"""Tests for hal0.config.seeds — the shipped-TOML seed-data loader (P3-schema).

Covers spec-p3-schema.final.md Part A: the data files under
``hal0/config/data/`` load and validate correctly, and the schema.py
module-level names (``SEED_PROFILES`` etc.) expose exactly what the loader
returns.

Targeted file run:
    uv run python -m pytest tests/config/test_seeds_data.py -q
"""

from __future__ import annotations

from hal0.config import seeds
from hal0.config.schema import (
    FAMILY_DEFAULTS,
    PROFILE_BENCH,
    SEED_PROFILES,
    SEED_STACKS,
    ProfileConfig,
    StackConfig,
)

# The seeded catalog (ADR-0029 decision 4): one profile per runtime family, the
# generic llama-server workloads, and the canonical seeds the shipped slots
# name (agent / utility / coding / brain). There are no demoted seeds: the
# retired ``dense`` / ``moe`` / ``thinking`` / ``chat-long-context`` tunes
# are published as community addon envelopes instead.
KEEP_NAMES = {
    "chat",
    "agent",
    "utility",
    "coding",
    "cpu-chat",
    "embedding",
    "reranking",
    "brain",
    "flm",
    "kokoro",
    "moonshine",
    "qwen3-tts",
    "comfyui",
}
_EXPECTED_PROFILE_COUNT = len(KEEP_NAMES)
_EXPECTED_STACK_SLUGS = {"saber", "forge", "pi"}


class TestSeedProfilesToml:
    def test_loads_expected_count(self) -> None:
        profiles = seeds.seed_profiles()
        assert len(profiles) == _EXPECTED_PROFILE_COUNT

    def test_seed_set_is_minimal_core(self) -> None:
        assert set(SEED_PROFILES) == KEEP_NAMES

    def test_every_entry_validates(self) -> None:
        for name, raw in seeds.seed_profiles().items():
            ProfileConfig.model_validate(raw), name  # raises on failure


class TestSeedProfilesAreProfileCentric:
    """ADR-0029: a seed is a complete tune in the new shape."""

    def test_no_seed_carries_a_retired_field(self) -> None:
        retired = {"device_class", "backend", "quant", "image"}
        for name, raw in seeds.seed_profiles().items():
            assert not retired & set(raw), name

    def test_no_seed_carries_a_model_fact_flag(self) -> None:
        for name, raw in seeds.seed_profiles().items():
            tokens = str(raw.get("flags", "")).split()
            assert "--jinja" not in tokens, name
            assert "--no-mmproj" not in tokens, name
            assert not any(t.startswith("--chat-template") for t in tokens), name

    def test_shipped_slot_seeds_carry_their_windows(self) -> None:
        profiles = seeds.seed_profiles()
        assert profiles["agent"]["ctx_size"] == 65536
        assert profiles["utility"]["ctx_size"] == 65536
        assert profiles["brain"]["ctx_size"] == 65536
        assert profiles["embedding"]["ctx_size"] == 4096
        assert profiles["reranking"]["ctx_size"] == 4096
        assert profiles["flm"]["ctx_size"] == 16384
        assert "ctx_size" not in profiles["coding"]

    def test_vision_off_where_the_old_tune_said_no_mmproj(self) -> None:
        profiles = seeds.seed_profiles()
        for name in ("agent", "coding", "brain"):
            assert profiles[name]["vision"] == "off", name

    def test_demoted_seed_set_is_gone(self) -> None:
        import hal0.config.schema as schema_mod

        assert not hasattr(schema_mod, "LEGACY_SEED_PROFILES")
        assert not hasattr(seeds, "legacy_seed_profiles")


class TestPruneLeavesInfrastructureResolvable:
    """Everything the install path derives must still be a *seed*."""

    def test_device_defaults_are_seeds(self) -> None:
        from hal0.config.schema import DEVICE_DEFAULT_PROFILES

        assert set(DEVICE_DEFAULT_PROFILES.values()) <= set(SEED_PROFILES)

    def test_derive_profile_targets_are_seeds(self) -> None:
        from hal0.install.profile_derive import derive_profile

        for cap, dev in [
            ("embed", "gpu-rocm"),
            ("rerank", "gpu-rocm"),
            ("tts", "cpu"),
            ("chat", "gpu-rocm"),
            ("chat", "cpu"),
            ("chat", "npu"),
            ("coder", "gpu-rocm"),
        ]:
            assert derive_profile(cap, dev) in SEED_PROFILES, (cap, dev)


class TestSeedStacksToml:
    def test_loads_expected_slugs(self) -> None:
        stacks = seeds.seed_stacks()
        assert set(stacks.keys()) == _EXPECTED_STACK_SLUGS

    def test_every_entry_is_a_valid_stack_config(self) -> None:
        for slug, stack in seeds.seed_stacks().items():
            assert isinstance(stack, StackConfig), slug

    def test_embed_rerank_marker_expands_on_the_rocm_slot(self) -> None:
        """The ``@embed_rerank`` TOML marker must expand into the shared
        embed+rerank capability pair on exactly the slot that carried it."""
        stacks = seeds.seed_stacks()
        saber = stacks["saber"]
        agent_slot = next(s for s in saber.slots if s.slot == "agent")
        children = {row.child for row in agent_slot.capabilities}
        assert children == {"embed", "rerank"}
        embed_row = next(r for r in agent_slot.capabilities if r.child == "embed")
        assert embed_row.model == "qwen3-embedding-0-6b-q8-0"
        assert embed_row.device == "gpu-rocm"
        # The sibling slot never carried the marker -> no capabilities.
        utility_slot = next(s for s in saber.slots if s.slot == "utility")
        assert utility_slot.capabilities == []


class TestProfileBenchToml:
    def test_keys_are_a_subset_of_profile_names(self) -> None:
        profile_names = set(seeds.seed_profiles().keys())
        bench_names = set(seeds.profile_bench().keys())
        assert bench_names <= profile_names

    def test_values_shape(self) -> None:
        bench = seeds.profile_bench()
        assert bench["chat"] == {"tps": 52.8}
        assert bench["kokoro"] == {"rtf": 0.18}
        assert bench["qwen3-tts"] == {"rtf": 0.48}


class TestFamilyDefaultsToml:
    def test_family_defaults_empty_for_1_0(self) -> None:
        """Per spec §1.2: family_defaults.toml data cleared for 1.0."""
        fam = seeds.family_defaults()
        assert fam == {}


class TestSchemaShims:
    """The schema.py module-level names must be exactly what seeds.* returns,
    so `from hal0.config.schema import SEED_PROFILES` (etc.) stays a drop-in."""

    def test_seed_profiles_shim_matches_loader(self) -> None:
        assert seeds.seed_profiles() == SEED_PROFILES

    def test_seed_stacks_shim_matches_loader(self) -> None:
        assert seeds.seed_stacks() == SEED_STACKS

    def test_profile_bench_shim_matches_loader(self) -> None:
        assert seeds.profile_bench() == PROFILE_BENCH

    def test_family_defaults_shim_matches_loader(self) -> None:
        assert seeds.family_defaults() == FAMILY_DEFAULTS


class TestColdImportOrder:
    """Regression guard for spec risk R2 (schema<->seeds circular import).

    The real smoke test is running these as two separate ``python -c``
    processes (see the P3-schema verification checklist); this in-process
    check at least confirms both modules stay importable and self-consistent
    from a warm interpreter, which would catch a reintroduced module-level
    cross-import.
    """

    def test_both_modules_importable_and_consistent(self) -> None:
        import hal0.config.schema as schema_mod
        import hal0.config.seeds as seeds_mod

        assert seeds_mod.seed_profiles() == schema_mod.SEED_PROFILES
