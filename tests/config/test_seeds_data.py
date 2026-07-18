"""Tests for hal0.config.seeds — the shipped-TOML seed-data loader (P3-schema).

Covers spec-p3-schema.final.md Part A: the four data files under
``hal0/config/data/`` load, validate, and resolve image sentinels correctly,
and the schema.py backward-compat shims (``SEED_PROFILES`` etc.) still expose
the same shape every existing caller expects.

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

# Grep-verified against src/hal0/config/data/seed_profiles.toml: 20 seed
# profiles today. The pre-refactor schema.py module docstring's "25" was
# already stale (per the P3-schema spec's own verified count of 21); the
# actual SEED_PROFILES dict this externalization is byte-for-byte replacing
# has 20 entries — see test_seeds_parity.py for the byte-identical guard.
_EXPECTED_PROFILE_COUNT = 20
_EXPECTED_STACK_SLUGS = {"saber", "forge", "pi"}


class TestSeedProfilesToml:
    def test_loads_expected_count(self) -> None:
        profiles = seeds.seed_profiles()
        assert len(profiles) == _EXPECTED_PROFILE_COUNT

    def test_every_entry_validates(self) -> None:
        for name, raw in seeds.seed_profiles().items():
            ProfileConfig.model_validate(raw), name  # raises on failure

    def test_no_unresolved_sentinels(self) -> None:
        """Every ``@NAME`` placeholder in the shipped TOML must resolve to a
        real image ref — a leftover sentinel would ship a broken image pin."""
        for name, raw in seeds.seed_profiles().items():
            image = raw.get("image", "")
            assert not str(image).startswith("@"), (
                f"profile {name!r} still carries an unresolved sentinel: {image!r}"
            )

    def test_rocmfpx_lanes_resolve_to_the_live_constant(self) -> None:
        """Profiles that reference the ROCmFPX sentinel must resolve to the
        SAME live ``DEFAULT_ROCMFPX_IMAGE`` constant, not a frozen copy —
        this is the whole point of the sentinel indirection (spec R1)."""
        from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, FALLBACK_VULKAN_IMAGE

        profiles = seeds.seed_profiles()
        for name in ("rocm", "rocm-dense", "vulkan", "embed", "rerank"):
            assert profiles[name]["image"] == DEFAULT_ROCMFPX_IMAGE, name
        assert profiles["cpu-llm"]["image"] == FALLBACK_VULKAN_IMAGE

    def test_literal_non_rocmfpx_images_stay_literal(self) -> None:
        """flm/kokoro/qwen3tts/the upstream CUDA image/the comfyui digest are
        NOT part of the ROCmFPX pin the ML-runner registry is absorbing —
        they must NOT be sentinel-resolved, just passed through verbatim."""
        profiles = seeds.seed_profiles()
        assert profiles["flm"]["image"] == "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44"
        assert profiles["tts"]["image"] == "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
        assert profiles["tts-qwen3"]["image"] == "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1"
        assert profiles["cuda"]["image"] == "ghcr.io/ggml-org/llama.cpp:server-cuda"
        assert profiles["comfyui"]["image"].startswith("docker.io/kyuz0/amd-strix-halo-comfyui@")


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
        assert bench["rocm"] == {"tps": 52.8}
        assert bench["tts-qwen3"] == {"rtf": 0.48}


class TestFamilyDefaultsToml:
    def test_gemma_entry(self) -> None:
        fam = seeds.family_defaults()
        assert fam["gemma"] == "-ctk f16 -ctv f16 --cache-reuse 0"


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
