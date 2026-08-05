"""Regression tests for the 1.0 seeded workload catalog."""

from hal0.config import seeds

CANONICAL_PROFILES = {
    "chat",
    "chat-long-context",
    "dense",
    "moe",
    "embedding",
    "reranking",
    "cpu-chat",
    "flm",
    "kokoro",
    "qwen3-tts",
    "comfyui",
    "brain",
    "chadrock-dense",
    "chadrock-moe",
    "thinking",
    "coding",
    "moonshine",
}


def test_seed_catalog_is_canonical_and_complete() -> None:
    assert set(seeds.seed_profiles()) == CANONICAL_PROFILES


def test_seed_profiles_are_device_agnostic_and_partition_safe() -> None:
    # Slot-hardware flags (§5) + hal0-managed args (§21.7) — token-exact, so
    # --model_path / --threads-batch in the TTS / cpu-chat seeds never trip.
    forbidden = {"-ngl", "--n-gpu-layers", "-dev", "--device", "--threads", "-t"}
    forbidden |= {"-c", "--ctx-size", "--host", "--port", "--model", "--alias"}
    for name, profile in seeds.seed_profiles().items():
        assert "image" not in profile, name
        assert profile.get("backend") is None, name
        assert not forbidden.intersection(str(profile.get("flags", "")).split()), name


def test_fpx_profiles_keep_logical_tune_and_drop_physical_mtp_flags() -> None:
    moe = seeds.seed_profiles()["moe"]["flags"]
    dense = seeds.seed_profiles()["dense"]["flags"]
    # Generic moe/dense are model-agnostic and carry no family-specific
    # kv-cache (chadrock-specific -ctk/-ctv moved to chadrock-moe/dense
    # per spec §4.2). Both still carry batch defaults; context is slot-owned
    # (context_size → --ctx-size, §21.7), so -c must never appear.
    assert "-b 2048" in moe
    assert "-c" not in moe.split()
    assert "-c" not in dense.split()
    # Chadrock family profiles own the family-specific KV quirks
    chadrock_moe = seeds.seed_profiles()["chadrock-moe"]["flags"]
    chadrock_dense = seeds.seed_profiles()["chadrock-dense"]["flags"]
    assert "-ctk f16" in chadrock_moe
    assert "-ctk q8_0" in chadrock_dense
    for flags in (moe, dense, chadrock_moe, chadrock_dense):
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
