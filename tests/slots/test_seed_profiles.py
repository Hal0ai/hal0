"""Seed profile catalog validation.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md:
- Every profile must have: name (implicit via [profile.<name>]), flags, intent.
- `device_class` field is REMOVED (slot owns device).
- `flags` must NOT contain SLOT_HARDWARE_FLAGS or operational flags.
- Profile names match the 1.0 catalog (16 total — 11 kept + 5 new for 1.0).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

SEED_PROFILES_PATH = Path(__file__).resolve().parents[2] / "src/hal0/config/data/seed_profiles.toml"

# Per spec §4.1: hardware + operational flags removed from every profile.
SLOT_HARDWARE_FLAG_FRAGMENTS = (
    "-ngl ", "--n-gpu-layers", "-ngl=", "-dev ", "--device ",
    "--threads ", "-t ", "--threads=", "-tb ",
    "--main-gpu", "--tensor-split", "--split-mode", "-ngld",
)
OPERATIONAL_FLAG_FRAGMENTS = (
    "--parallel", "--metrics", "--no-webui",
    "--poll", "--poll-batch", "--slot-prompt-similarity", "--no-mmap",
)

ALL_16_PROFILES = [
    "profile.chat", "profile.chat-long-context", "profile.dense", "profile.moe",
    "profile.embedding", "profile.reranking", "profile.cpu-chat",
    "profile.flm", "profile.kokoro", "profile.qwen3-tts", "profile.comfyui",
    # New for 1.0 (per spec §4.2):
    "profile.brain", "profile.chadrock-dense", "profile.chadrock-moe",
    "profile.thinking", "profile.coding",
]


def _load_seed_profiles() -> dict:
    raw = tomllib.loads(SEED_PROFILES_PATH.read_text())
    # TOML dotted keys like [profile.chat] create nested tables:
    # {"profile": {"chat": {...}, "dense": {...}, ...}}
    # Prefix keys back so callers can use "profile.chat" consistently.
    inner = raw.get("profile", {})
    return {f"profile.{k}": v for k, v in inner.items()}


def test_seed_profiles_loads() -> None:
    """Catalog parses as TOML and contains [profile.*] tables."""
    profiles = _load_seed_profiles()
    assert len(profiles) >= 11, f"expected ≥11 profiles, got {len(profiles)}: {list(profiles)}"


def test_catalog_has_exactly_16_profiles() -> None:
    """1.0 catalog is exactly 16 profiles (11 kept + 5 new)."""
    profiles = _load_seed_profiles()
    names = sorted(profiles)
    assert names == sorted(ALL_16_PROFILES), f"catalog profiles out of order or missing: {names}"


@pytest.mark.parametrize("profile_name", ALL_16_PROFILES)
def test_all_profiles_have_no_device_class(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    assert profile_name in profiles, f"missing {profile_name}"
    assert "device_class" not in profiles[profile_name], (
        f"{profile_name} still has device_class (slot owns device per spec §1)"
    )


@pytest.mark.parametrize("profile_name", ALL_16_PROFILES)
def test_all_profiles_have_no_hardware_flags(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    flags = profiles[profile_name].get("flags", "")
    for fragment in SLOT_HARDWARE_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain SLOT_HARDWARE flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", ALL_16_PROFILES)
def test_all_profiles_have_no_operational_flags(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    flags = profiles[profile_name].get("flags", "")
    for fragment in OPERATIONAL_FLAG_FRAGMENTS:
        assert fragment not in flags, (
            f"{profile_name} flags contain operational flag {fragment!r}: {flags}"
        )


@pytest.mark.parametrize("profile_name", ALL_16_PROFILES)
def test_all_profiles_have_intent(profile_name: str) -> None:
    profiles = _load_seed_profiles()
    assert "intent" in profiles[profile_name], f"{profile_name} missing intent"
    assert isinstance(profiles[profile_name]["intent"], str)
    assert profiles[profile_name]["intent"].strip(), f"{profile_name} intent is blank"
