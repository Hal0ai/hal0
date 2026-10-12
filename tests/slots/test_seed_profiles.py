"""Seed profile catalog validation (ADR-0029).

A seed is a complete, code-shipped tune: free ``flags`` plus the typed
``ctx_size`` / ``enable_thinking`` / ``vision`` / ``mtp`` / ``runner`` fields.
It never carries placement (the slot's typed fields), hal0-managed flags,
model-fact flags (``--jinja``, ``--chat-template*``, ``--mmproj*``),
``--spec-draft-device`` (the slot's device decides it) or the pre-ADR-0029
hint fields (``device_class``, ``backend``, ``quant``, boolean ``mtp``).

The catalog is one file (``seed_profiles.toml``); the demoted-seed file and
its first-reference adoption are gone. The seed set covers every slot hal0
ships, so the ``agent`` / ``utility`` / ``coding`` workloads are seeds.
"""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DATA_DIR = _REPO / "src/hal0/config/data"
SEED_PROFILES_PATH = _DATA_DIR / "seed_profiles.toml"
INSTALLER_SLOTS_DIR = _REPO / "installer/etc-hal0/slots"

# Placement + operational flags: the slot owns these, never a seed.
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

#: The shipped seed catalogue. Pinned exactly: adding or renaming a seed is a
#: deliberate change (installer slots and stacks name these).
SEED_PROFILE_NAMES = [
    "agent",
    "brain",
    "chat",
    "coding",
    "comfyui",
    "cpu-chat",
    "embedding",
    "flm",
    "kokoro",
    "moonshine",
    "qwen3-tts",
    "reranking",
    "utility",
]

#: Keys a seed table may carry — exactly :class:`ProfileConfig`'s fields.
ALLOWED_KEYS = {
    "flags",
    "ctx_size",
    "enable_thinking",
    "vision",
    "mtp",
    "runner",
    "cloned_from",
    "intent",
}


def _load() -> dict[str, dict]:
    raw = tomllib.loads(SEED_PROFILES_PATH.read_text())
    return dict(raw.get("profile", {}))


def _flags(name: str) -> str:
    return str(_load()[name].get("flags", ""))


def test_seed_catalog_is_pinned() -> None:
    assert sorted(_load()) == SEED_PROFILE_NAMES


def test_the_demoted_seed_file_is_gone() -> None:
    """ADR-0029 §3: ``legacy_seed_profiles.toml`` is deleted with adoption."""
    assert not (_DATA_DIR / "legacy_seed_profiles.toml").exists()


def test_runtime_catalogue_matches_the_shipped_file() -> None:
    from hal0.config.schema import SEED_PROFILES

    assert sorted(SEED_PROFILES) == SEED_PROFILE_NAMES


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_carries_only_profile_fields(name: str) -> None:
    """No ``device_class`` / ``backend`` / ``quant`` hint, nothing unknown."""
    keys = set(_load()[name])
    assert keys <= ALLOWED_KEYS, f"{name} carries non-profile keys {sorted(keys - ALLOWED_KEYS)}"


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_validates_as_profile_config(name: str) -> None:
    from hal0.config.schema import ProfileConfig

    cfg = ProfileConfig.model_validate(_load()[name])
    assert cfg.vision in ("auto", "off")
    assert cfg.mtp in ("auto", "off")


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_mtp_is_the_profile_switch_not_a_bool(name: str) -> None:
    raw = _load()[name]
    if "mtp" in raw:
        assert raw["mtp"] in ("auto", "off"), (
            f"{name}: mtp must be 'auto'/'off', got {raw['mtp']!r}"
        )


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_passes_the_profile_write_screen(name: str) -> None:
    """A seed would be accepted by the same screen every profile save runs."""
    from hal0.profiles import screen_profile_flags

    screen_profile_flags(_flags(name))


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_has_no_hardware_flags(name: str) -> None:
    flags = _flags(name)
    for fragment in SLOT_HARDWARE_FLAG_FRAGMENTS:
        assert fragment not in flags, f"{name} flags contain hardware flag {fragment!r}: {flags}"


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_has_no_operational_flags(name: str) -> None:
    flags = _flags(name)
    for fragment in OPERATIONAL_FLAG_FRAGMENTS:
        assert fragment not in flags, f"{name} flags contain operational flag {fragment!r}: {flags}"


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_has_no_managed_hardware_or_model_fact_flags(name: str) -> None:
    """Token-exact via the argv alias table (``--model_path`` / ``--threads-batch``
    never false-positive)."""
    from hal0.slots.argv import (
        DERIVED_PLACEMENT_FLAGS,
        MANAGED_ARGS_DENYLIST,
        MODEL_FACT_FLAGS,
        SLOT_HARDWARE_FLAGS,
        strip_managed_flags,
    )

    _, removed = strip_managed_flags(
        shlex.split(_flags(name)),
        denylist=MANAGED_ARGS_DENYLIST
        | SLOT_HARDWARE_FLAGS
        | MODEL_FACT_FLAGS
        | DERIVED_PLACEMENT_FLAGS,
    )
    assert not removed, f"{name} flags carry flag(s) owned elsewhere {removed}"


@pytest.mark.parametrize("name", SEED_PROFILE_NAMES)
def test_seed_has_intent(name: str) -> None:
    intent = _load()[name].get("intent")
    assert isinstance(intent, str) and intent.strip(), f"{name} missing intent"


@pytest.mark.parametrize(
    ("name", "mode_flag"), [("embedding", "--embedding"), ("reranking", "--reranking")]
)
def test_typed_seeds_carry_their_mode_flag(name: str, mode_flag: str) -> None:
    """A typed slot naming no profile launches with its type's seed, so the
    seed must put the server in that mode."""
    assert mode_flag in shlex.split(_flags(name))


@pytest.mark.parametrize("name", ["agent", "utility", "brain"])
def test_long_context_workload_seeds_pin_a_64k_window(name: str) -> None:
    """The bundled agent needs at least 64,000 tokens (ADR-0029 §4)."""
    assert _load()[name]["ctx_size"] == 65536


def test_flm_seed_carries_an_explicit_window() -> None:
    """FLM reads its window from the profile's ``ctx_size`` (else 8192)."""
    assert _load()["flm"]["ctx_size"] == 16384


@pytest.mark.parametrize("name", ["agent", "coding", "brain"])
def test_text_only_seeds_turn_vision_off_with_the_typed_field(name: str) -> None:
    """``--no-mmproj`` became ``vision = "off"``."""
    assert _load()[name]["vision"] == "off"
    assert "--no-mmproj" not in _flags(name)


def test_every_shipped_slot_names_a_seed() -> None:
    """Slots are built around the seeds hal0 ships (ADR-0029 §4)."""
    seeds = set(_load())
    named: dict[str, str] = {}
    for path in sorted(INSTALLER_SLOTS_DIR.glob("*.toml")):
        raw = tomllib.loads(path.read_text())
        table = raw.get("slot", raw)
        profile = table.get("profile")
        if profile:
            named[path.name] = str(profile)
    assert named, "no shipped slot names a profile"
    missing = {slot: prof for slot, prof in named.items() if prof not in seeds}
    assert not missing, f"shipped slots name non-seed profiles: {missing}"
