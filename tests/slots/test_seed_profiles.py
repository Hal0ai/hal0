"""Seed profile catalog validation.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md:
- Every profile must have: name (implicit via [profile.<name>]), flags, intent.
- `device_class` field is REMOVED (slot owns device) — including the
  `promptforge` specialty seed (spec 2026-08-29, #1946 fix round 1):
  ROCm-only enforcement lives at the runner/guard layer
  (`RunnerSupports.supported_backends` + `_guard_specialty_runner`), not on
  the profile template.
- `flags` must NOT contain SLOT_HARDWARE_FLAGS or operational flags.
- Profile names match the catalog: 10 shipped SEEDS (the minimal core, one
  per runtime family plus the generic llama-server workloads) plus the 8
  DEMOTED definitions parked in legacy_seed_profiles.toml. Both files ship,
  both must honour the same flag-ownership invariants, so every check below
  runs over the union.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).resolve().parents[2] / "src/hal0/config/data"
SEED_PROFILES_PATH = _DATA_DIR / "seed_profiles.toml"
LEGACY_SEED_PROFILES_PATH = _DATA_DIR / "legacy_seed_profiles.toml"

# Per spec §4.1: hardware + operational flags removed from every profile.
SLOT_HARDWARE_FLAG_FRAGMENTS = (
    "-ngl ",
    "--n-gpu-layers",
    "-ngl=",
    "-dev ",
    "--device ",
    "--threads ",
    "-t ",
    "--threads=",
    "-tb ",
    "--main-gpu",
    "--tensor-split",
    "--split-mode",
    "-ngld",
)
OPERATIONAL_FLAG_FRAGMENTS = (
    "--parallel",
    "--metrics",
    "--no-webui",
    "--poll",
    "--poll-batch",
    "--slot-prompt-similarity",
    "--no-mmap",
)

CORE_SEED_PROFILES = [
    "profile.chat",
    "profile.embedding",
    "profile.reranking",
    "profile.cpu-chat",
    "profile.flm",
    "profile.kokoro",
    "profile.qwen3-tts",
    "profile.moonshine",
    "profile.comfyui",
    "profile.brain",
]

#: Demoted out of the seed catalog by the profile-system overhaul; still
#: shipped (legacy_seed_profiles.toml) and injected once into an existing
#: install as ordinary custom profiles.
LEGACY_SEED_PROFILES = [
    "profile.chat-long-context",
    "profile.dense",
    "profile.moe",
    "profile.chadrock-dense",
    "profile.chadrock-moe",
    "profile.thinking",
    "profile.coding",
    "profile.promptforge",
]

ALL_SEED_PROFILES = CORE_SEED_PROFILES + LEGACY_SEED_PROFILES


def _load(path: Path) -> dict:
    raw = tomllib.loads(path.read_text())
    # TOML dotted keys like [profile.chat] create nested tables:
    # {"profile": {"chat": {...}, "dense": {...}, ...}}
    # Prefix keys back so callers can use "profile.chat" consistently.
    inner = raw.get("profile", {})
    return {f"profile.{k}": v for k, v in inner.items()}


def _load_seed_profiles() -> dict:
    """Both shipped definition files, merged — the flag invariants apply to
    every profile hal0 ships, seed or demoted."""
    return {**_load(SEED_PROFILES_PATH), **_load(LEGACY_SEED_PROFILES_PATH)}


def test_seed_profiles_loads() -> None:
    """Catalog parses as TOML and contains [profile.*] tables."""
    profiles = _load(SEED_PROFILES_PATH)
    assert len(profiles) >= 10, f"expected ≥10 profiles, got {len(profiles)}: {list(profiles)}"


def test_seed_catalog_is_the_minimal_core() -> None:
    names = sorted(_load(SEED_PROFILES_PATH))
    assert names == sorted(CORE_SEED_PROFILES), f"seed catalog drifted: {names}"


def test_legacy_catalog_holds_the_demoted_eight() -> None:
    names = sorted(_load(LEGACY_SEED_PROFILES_PATH))
    assert names == sorted(LEGACY_SEED_PROFILES), f"legacy catalog drifted: {names}"


def test_the_two_files_are_disjoint() -> None:
    assert not set(_load(SEED_PROFILES_PATH)) & set(_load(LEGACY_SEED_PROFILES_PATH))


@pytest.mark.parametrize("profile_name", ALL_SEED_PROFILES)
def test_every_seed_profile_is_device_agnostic(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    assert profile_name in profiles, f"missing {profile_name}"
    assert "device_class" not in profiles[profile_name], (
        f"{profile_name} should be device-agnostic; the slot owns device "
        "per seeded-profile-rework §4.1"
    )


@pytest.mark.parametrize("profile_name", ALL_SEED_PROFILES)
def test_all_profiles_have_no_hardware_flags(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    flags = profiles[profile_name].get("flags", "")
    for fragment in SLOT_HARDWARE_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain SLOT_HARDWARE flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", ALL_SEED_PROFILES)
def test_all_profiles_have_no_operational_flags(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    flags = profiles[profile_name].get("flags", "")
    for fragment in OPERATIONAL_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain operational flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", ALL_SEED_PROFILES)
def test_all_profiles_have_no_managed_or_hardware_flags(profile_name: str) -> None:
    """§21.7 regression tripwire: a seed's flags must never carry a flag hal0
    owns (managed denylist: --model/--ctx-size/-c/--host/--port/-ngl/--alias)
    or a slot-hardware flag. Token-exact via the argv alias table, so
    --model_path / --threads-batch never false-positive.
    """
    import shlex

    from hal0.slots.argv import MANAGED_ARGS_DENYLIST, SLOT_HARDWARE_FLAGS, strip_managed_flags

    profiles = _load_seed_profiles()
    flags = str(profiles[profile_name].get("flags", ""))
    _, removed = strip_managed_flags(
        shlex.split(flags), denylist=MANAGED_ARGS_DENYLIST | SLOT_HARDWARE_FLAGS
    )
    assert not removed, f"{profile_name} flags carry hal0-owned flag(s) {removed}: {flags}"


@pytest.mark.parametrize("profile_name", ALL_SEED_PROFILES)
def test_all_profiles_have_intent(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    assert "intent" in profiles[profile_name], f"{profile_name} missing intent"
    assert isinstance(profiles[profile_name]["intent"], str)
    assert profiles[profile_name]["intent"].strip(), f"{profile_name} intent is blank"
