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


def test_resolve_exposes_backend(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    assert catalog.resolve("rocm").backend == "rocm"
    assert catalog.resolve("rocm-mtp").backend == "rocm"
    assert catalog.resolve("vulkan").backend == "vulkan"
    # non-GPU seeds carry no backend
    assert catalog.resolve("flm").backend is None
    assert catalog.resolve("tts").backend is None
    assert catalog.resolve("comfyui").backend is None
    # backend round-trips through to_dict for the API/UI
    assert catalog.resolve("rocm").to_dict()["backend"] == "rocm"


def test_create_update_delete_profile(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "my-rocm",
        ProfileConfig(
            image="ghcr.io/x/y:z",
            flags="-fa on",
            mtp=True,
            device_class="gpu",
        ),
    )
    assert created.seed is False
    assert created.runtime_family == "llama-server"
    assert MTP_FLAG_BUNDLE in created.resolved_flags

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
    catalog.create("my-rocm", ProfileConfig(image="ghcr.io/x/y:z"))

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.in_use"
    assert exc.value.details["slots"] == ["chat"]


def test_cloned_from_persists_and_round_trips(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "vulkan-custom",
        ProfileConfig(image="ghcr.io/x/y:z", flags="-fa on", cloned_from="vulkan"),
    )
    assert created.cloned_from == "vulkan"
    assert created.to_dict()["cloned_from"] == "vulkan"

    # Survives the profiles.toml round trip on a fresh catalog instance.
    reloaded = ProfileCatalog().resolve("vulkan-custom")
    assert reloaded.cloned_from == "vulkan"


def test_cloned_from_defaults_to_none_and_survives_update(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    plain = catalog.create("my-rocm", ProfileConfig(image="ghcr.io/x/y:z"))
    assert plain.cloned_from is None

    catalog.create("my-copy", ProfileConfig(image="ghcr.io/x/y:z", cloned_from="my-rocm"))
    updated = catalog.update("my-copy", ProfilePatch(flags="-fa off"))
    assert updated.cloned_from == "my-rocm"
