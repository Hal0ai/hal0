"""Regression tests for the 1.0 seeded workload catalog."""

from hal0.config import seeds

CANONICAL_PROFILES = {
    "chat",
    "embedding",
    "reranking",
    "cpu-chat",
    "flm",
    "kokoro",
    "qwen3-tts",
    "comfyui",
    "brain",
    "moonshine",
}

#: The 8 workload variants the seed catalog carried until the profile-system
#: overhaul. Shipped as demoted definitions (legacy_seed_profiles.toml), not
#: seeds — see that file's header.
LEGACY_PROFILES = {
    "chat-long-context",
    "dense",
    "moe",
    "thinking",
    "coding",
}


def test_seed_catalog_is_canonical_and_complete() -> None:
    assert set(seeds.seed_profiles()) == CANONICAL_PROFILES


def test_legacy_catalog_is_canonical_and_complete() -> None:
    assert set(seeds.legacy_seed_profiles()) == LEGACY_PROFILES


def test_seed_profiles_are_device_agnostic_and_partition_safe() -> None:
    # Slot-hardware flags (§5) + hal0-managed args (§21.7) — token-exact, so
    # --model_path / --threads-batch in the TTS / cpu-chat seeds never trip.
    forbidden = {"-ngl", "--n-gpu-layers", "-dev", "--device", "--threads", "-t"}
    forbidden |= {"-c", "--ctx-size", "--host", "--port", "--model", "--alias"}
    for name, profile in {**seeds.seed_profiles(), **seeds.legacy_seed_profiles()}.items():
        assert "image" not in profile, name
        assert profile.get("backend") is None, name
        assert not forbidden.intersection(str(profile.get("flags", "")).split()), name


def test_workload_profiles_keep_logical_tune_and_drop_physical_mtp_flags() -> None:
    # Demoted definitions; their flag text is what an upgraded install
    # inherits verbatim, so it stays pinned here.
    moe = seeds.legacy_seed_profiles()["moe"]["flags"]
    dense = seeds.legacy_seed_profiles()["dense"]["flags"]
    # Generic moe/dense are model-agnostic and carry no family-specific
    # kv-cache. Both still carry batch defaults; context is slot-owned
    # (context_size → --ctx-size, §21.7), so -c must never appear.
    assert "-b 2048" in moe
    assert "-c" not in moe.split()
    assert "-c" not in dense.split()
    assert "-ctk" not in moe.split() and "-ctk" not in dense.split()
    for flags in (moe, dense):
        assert "--spec-type" not in flags
        assert "--spec-draft-device" not in flags
        assert "--spec-draft-ngl" not in flags
        assert "--spec-draft-threads" not in flags


def test_seed_stacks_keep_runtime_hardware_on_slots() -> None:
    saber = seeds.seed_stacks()["saber"]
    agent = next(slot for slot in saber.slots if slot.slot == "agent")
    assert agent.profile == "moe"
    assert agent.device == "gpu-rocm"
    assert agent.mtp is True


def test_profile_bench_keys_are_seed_profiles() -> None:
    assert set(seeds.profile_bench()) <= set(seeds.seed_profiles())


def test_schema_shims_match_loaded_seed_data() -> None:
    from hal0.config.schema import FAMILY_DEFAULTS, PROFILE_BENCH, SEED_PROFILES, SEED_STACKS

    assert seeds.seed_profiles() == SEED_PROFILES
    assert seeds.seed_stacks() == SEED_STACKS
    assert seeds.profile_bench() == PROFILE_BENCH
    assert seeds.family_defaults() == FAMILY_DEFAULTS
