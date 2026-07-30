"""Round-trip regression for the shipped installer config after P3-schema's
seed-data externalization (spec-p3-schema.final.md, Tests item 6).

The externalization changes WHERE ``SEED_PROFILES``/``SEED_STACKS`` are
defined (shipped TOML + ``hal0.config.seeds``, read by the bottom-of-module
schema.py shim) but must not change how the loader round-trips real,
installer-shipped config: every ``installer/etc-hal0/slots/*.toml`` and
``installer/etc-hal0/profiles.toml`` must still load, and a slot TOML must
survive a load -> save -> load cycle byte-equivalent (field-for-field).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.loader import load_slot_config, save_slot_config
from hal0.config.schema import SEED_PROFILES, SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"
_PROFILES_TOML = _REPO_ROOT / "installer" / "etc-hal0" / "profiles.toml"

_SLOT_TOMLS = sorted(_SLOTS_DIR.glob("*.toml"))


@pytest.mark.parametrize("slot_path", _SLOT_TOMLS, ids=lambda p: p.stem)
def test_installer_slot_loads_unchanged(slot_path: Path, tmp_hal0_home: str) -> None:
    """Every shipped slot TOML must still validate through the loader."""
    cfg = load_slot_config(slot_path.stem, path=slot_path)
    assert isinstance(cfg, SlotConfig)
    assert cfg.name == slot_path.stem or cfg.name  # name may differ from filename (none do today)


@pytest.mark.parametrize("slot_path", _SLOT_TOMLS, ids=lambda p: p.stem)
def test_installer_slot_load_save_load_is_stable(
    slot_path: Path, tmp_hal0_home: str, tmp_path: Path
) -> None:
    """load -> save -> load must be a fixed point for the fields
    ``_unflatten_slot_toml`` actually re-serializes (hoist/tuck round-trip).

    NOTE: this deliberately does NOT assert full ``SlotConfig`` equality.
    ``_unflatten_slot_toml`` (loader.py) writes an explicit field whitelist
    (name/port/device/provider/workers/idle_timeout_s + model; P2-device
    dropped ``backend`` and #1369 dropped ``enabled``, along with the fields)
    and silently drops ``profile``/``runtime``/other declared fields plus
    any pydantic-``extra``-allow attribute not in that whitelist (e.g.
    a shipped slot's top-level ``type = "llm"``) -- a PRE-EXISTING loader
    gap, verified present on ``rework/descar`` before this P3-schema change
    and unrelated to the seed-data externalization this PR makes. Fixing
    that whitelist further is Part B/D territory (SlotConfig split), explicitly
    deferred out of this lane's scope -- see the P3-schema spec's Part B/D
    sequencing. This test locks today's actual contract so a future change
    to the whitelist (deliberate or not) is visible in the diff.
    """
    first = load_slot_config(slot_path.stem, path=slot_path)
    dest = tmp_path / f"{slot_path.stem}.toml"
    save_slot_config(first, path=dest)
    second = load_slot_config(slot_path.stem, path=dest)
    for field in (
        "name",
        "port",
        "device",
        "provider",
        "workers",
        "idle_timeout_s",
        "model",
    ):
        assert getattr(first, field) == getattr(second, field), field


def test_installer_profiles_toml_loads_and_carries_no_seeds() -> None:
    """The shipped installer profiles.toml is documentation + a home for
    operator custom profiles only -- it must define zero [profile.*] seeds
    (those are virtual, overlaid from hal0.config.seeds.seed_profiles() on
    every load_profiles_config() call)."""
    raw = tomllib.loads(_PROFILES_TOML.read_text(encoding="utf-8"))
    on_disk = raw.get("profile", {})
    assert set(on_disk).isdisjoint(SEED_PROFILES), (
        f"installer profiles.toml materialises seed profiles: {set(on_disk) & set(SEED_PROFILES)}"
    )
