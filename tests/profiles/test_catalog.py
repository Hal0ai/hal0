from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import MTP_FLAG_BUNDLE, ProfileConfig
from hal0.errors import Conflict
from hal0.profiles import ProfileCatalog, ProfilePatch


def test_resolve_seed_profile_includes_runtime_facts(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("flm")

    assert profile.seed is True
    assert profile.runtime_family == "flm"
    assert profile.supported_slot_types == ("llm", "embedding", "transcription")


def test_resolve_qwen3tts_seed_is_gpu_tts_family(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("qwen3-tts")

    assert profile.seed is True
    assert profile.runtime_family == "qwen3tts"
    # Qwen3-TTS is a TTS-only runtime family; seeded profiles are device-agnostic.
    assert profile.supported_slot_types == ("tts",)
    assert profile.device_class is None
    assert profile.backend is None
    assert profile.rtf == 0.48


def test_seed_profiles_do_not_select_backend(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    assert all(profile.backend is None for profile in catalog.list())


def test_create_update_delete_profile(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "my-rocm",
        ProfileConfig(
            flags="-fa on",
            mtp=True,
            device_class="gpu",
        ),
    )
    assert created.seed is False
    assert created.runtime_family == "llama-server"
    # §7.1a / ML-5: profile.mtp is informational only now — the catalog's
    # resolved_flags (no model bound at this level) never MTP-expands, even
    # for an mtp=true profile. See providers.container._effective_mtp for
    # where the real, model-aware decision now lives.
    assert MTP_FLAG_BUNDLE not in created.resolved_flags

    updated = catalog.update("my-rocm", ProfilePatch(flags="-fa off", mtp=False))
    assert updated.flags == "-fa off"
    assert updated.resolved_flags == "-fa off"

    catalog.delete("my-rocm")
    assert all(profile.name != "my-rocm" for profile in catalog.list())


def test_delete_profile_in_use_raises_conflict(tmp_hal0_home: str) -> None:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        "\n".join(
            [
                "[slot]",
                'name = "chat"',
                "port = 8081",
                'profile = "my-rocm"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    catalog = ProfileCatalog()
    catalog.create("my-rocm", ProfileConfig())

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.in_use"
    assert exc.value.details["slots"] == ["chat"]


def test_delete_profile_in_use_by_model_defaults_raises_conflict(tmp_hal0_home: str) -> None:
    """HAL0-41 / GH #1437: a model's ``defaults.profile`` is a live reference too.

    ``slots_using`` only scans slot TOMLs; a model that prefers a profile via
    ``defaults.profile`` but isn't bound to any slot yet was able to slip past
    the in-use guard entirely, leaving the model with a dangling reference
    after delete.
    """
    from hal0.registry.model import Model, ModelDefaults
    from hal0.registry.store import ModelRegistry

    ModelRegistry().add(
        Model(
            id="my-model",
            path="/tmp/my-model.gguf",
            defaults=ModelDefaults(profile="my-rocm"),
        )
    )
    catalog = ProfileCatalog()
    catalog.create("my-rocm", ProfileConfig())

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.in_use"
    assert exc.value.details["models"] == ["my-model"]
    assert exc.value.details["slots"] == []


def test_cloned_from_persists_and_round_trips(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "vulkan-custom",
        ProfileConfig(flags="-fa on", cloned_from="vulkan"),
    )
    assert created.cloned_from == "vulkan"
    assert created.to_dict()["cloned_from"] == "vulkan"

    # Survives the profiles.toml round trip on a fresh catalog instance.
    reloaded = ProfileCatalog().resolve("vulkan-custom")
    assert reloaded.cloned_from == "vulkan"


def test_cloned_from_defaults_to_none_and_survives_update(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    plain = catalog.create("my-rocm", ProfileConfig())
    assert plain.cloned_from is None

    catalog.create("my-copy", ProfileConfig(cloned_from="my-rocm"))
    updated = catalog.update("my-copy", ProfilePatch(flags="-fa off"))
    assert updated.cloned_from == "my-rocm"


# ── profiles overhaul: bench / quant / intent / used_by ─────────────────────────


def test_seed_bench_metrics_exposed(tmp_hal0_home: str) -> None:
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].tps == 52.8
    # TTS is synth — reported as a real-time factor, not tok/s.
    assert by_name["kokoro"].tps is None
    assert by_name["kokoro"].rtf == 0.18


def test_seed_intent_and_quant_exposed(tmp_hal0_home: str) -> None:
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].intent == "Generic chat (fallback for unknown models)"
    # Per spec §4.2: generic dense profile is model-agnostic (no quant hint);
    # the chadrock-dense family-specific profile carries the ROCmFP4 hint.
    assert by_name["dense"].quant == ""
    assert by_name["chadrock-dense"].quant == "ROCmFP4"


def test_custom_profile_has_no_bench_and_round_trips_intent_quant(
    tmp_hal0_home: str,
) -> None:
    catalog = ProfileCatalog()
    created = catalog.create(
        "my-tuned",
        ProfileConfig(intent="My workload", quant="Q5_K_M"),
    )
    assert created.intent == "My workload"
    assert created.quant == "Q5_K_M"
    assert created.tps is None
    assert created.rtf is None

    reloaded = ProfileCatalog().resolve("my-tuned")
    assert reloaded.intent == "My workload"
    assert reloaded.quant == "Q5_K_M"


def test_used_by_lists_bound_slots(tmp_hal0_home: str) -> None:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    for slot in ("primary", "agent"):
        (root / f"{slot}.toml").write_text(
            "\n".join(["[slot]", f'name = "{slot}"', "port = 8081", 'profile = "chat"', ""]),
            encoding="utf-8",
        )
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert sorted(by_name["chat"].used_by) == ["agent", "primary"]
    assert by_name["dense"].used_by == ()
    assert by_name["chat"].to_dict()["used_by"] == ["agent", "primary"]


def test_used_by_lists_bound_slots_id_keyed(tmp_hal0_home: str) -> None:
    # P3-runtime-db inc4: an id-keyed slot TOML (stem is the digit id, name
    # lives inside the file) must still be reported by its real display name,
    # not the digit stem — the "list_slots CLI/portable callers" id-awareness
    # fix deferred from inc1.
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "143.toml").write_text(
        "\n".join(["[slot]", "id = 143", 'name = "brain"', "port = 8081", 'profile = "chat"', ""]),
        encoding="utf-8",
    )
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].used_by == ("brain",)
    assert by_name["chat"].to_dict()["used_by"] == ["brain"]
