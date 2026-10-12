"""Regression tests for the 1.0 seeded workload catalog."""

from hal0.config import seeds

CANONICAL_PROFILES = {
    "chat",
    "agent",
    "utility",
    "coding",
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


def test_seed_catalog_is_canonical_and_complete() -> None:
    """ADR-0029 decision 4: the canonical seeds the shipped slots name
    (agent / utility / coding) are seeds proper."""
    assert set(seeds.seed_profiles()) == CANONICAL_PROFILES


def test_there_is_no_demoted_seed_catalog() -> None:
    """The demoted-seed file and its first-reference adoption are deleted."""
    import hal0.config.schema as schema

    assert not hasattr(seeds, "legacy_seed_profiles")
    assert not hasattr(schema, "LEGACY_SEED_PROFILES")


def test_seed_profiles_are_device_agnostic_and_partition_safe() -> None:
    # Slot-hardware flags (§5), hal0-managed args (§21.7) and model-fact flags
    # (ADR-0029) — token-exact, so --model_path / --threads-batch in the
    # TTS / cpu-chat seeds never trip.
    forbidden = {"-ngl", "--n-gpu-layers", "-dev", "--device", "--threads", "-t"}
    forbidden |= {"-c", "--ctx-size", "--host", "--port", "--model", "--alias"}
    forbidden |= {"--jinja", "--chat-template", "--chat-template-file", "--mmproj", "--no-mmproj"}
    for name, profile in seeds.seed_profiles().items():
        for hint in ("image", "backend", "device_class", "quant"):
            assert hint not in profile, (name, hint)
        assert not forbidden.intersection(str(profile.get("flags", "")).split()), name


def test_seed_profiles_carry_no_mtp_draft_flags() -> None:
    """The MTP draft bundle is added by the launch path when the profile's
    ``mtp`` is in effect; the draft device comes from the slot. No seed
    hardcodes either, and ``mtp`` is the ``auto``/``off`` switch."""
    for name, profile in seeds.seed_profiles().items():
        flags = str(profile.get("flags", "")).split()
        for flag in (
            "--spec-type",
            "--spec-draft-device",
            "--spec-draft-ngl",
            "--spec-draft-threads",
        ):
            assert flag not in flags, (name, flag)
        assert profile.get("mtp", "auto") in ("auto", "off"), name


def test_seed_stacks_keep_runtime_hardware_on_slots() -> None:
    saber = seeds.seed_stacks()["saber"]
    agent = next(slot for slot in saber.slots if slot.slot == "agent")
    assert agent.profile == "agent"
    assert agent.device == "gpu-rocm"
    # ADR-0029 §6: stack rows carry no tuning; MTP is the profile's.
    assert not hasattr(agent, "mtp")


def test_seed_stack_rows_name_seed_profiles() -> None:
    catalog = seeds.seed_profiles()
    for slug, stack in seeds.seed_stacks().items():
        for row in stack.slots:
            assert row.profile in catalog, (slug, row.slot, row.profile)


def test_profile_bench_keys_are_seed_profiles() -> None:
    assert set(seeds.profile_bench()) <= set(seeds.seed_profiles())


def test_schema_shims_match_loaded_seed_data() -> None:
    from hal0.config.schema import FAMILY_DEFAULTS, PROFILE_BENCH, SEED_PROFILES, SEED_STACKS

    assert seeds.seed_profiles() == SEED_PROFILES
    assert seeds.seed_stacks() == SEED_STACKS
    assert seeds.profile_bench() == PROFILE_BENCH
    assert seeds.family_defaults() == FAMILY_DEFAULTS
