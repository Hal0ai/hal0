"""Upgrade migration: retag stale former-default runner-image pins.

Covers :func:`hal0.updater.updater.retag_stale_slot_images` — slot ``image``
pins exactly equal to a KNOWN former default roll to the current
``DEFAULT_ROCMFPX_IMAGE``; any other pin is a deliberate operator override
and must survive untouched, as must the ``[image]`` TOML table (image-gen
settings) that shares the key.
"""

from __future__ import annotations

import tomllib

import pytest

from hal0.config.paths import slots_config_dir
from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, STALE_ROCMFPX_IMAGE_REFS
from hal0.updater.updater import retag_stale_slot_images

STALE = "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5"
CUSTOM = "ghcr.io/hal0ai/hal0-rocmfpx:my-debug-build"


def _write_slot(name: str, body: str) -> None:
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(body, encoding="utf-8")


def _image_of(name: str):
    raw = tomllib.loads((slots_config_dir() / f"{name}.toml").read_text(encoding="utf-8"))
    if isinstance(raw.get("image"), str):
        return raw["image"]
    slot = raw.get("slot")
    return slot.get("image") if isinstance(slot, dict) else None


def test_stale_pins_retag_and_custom_pins_survive(tmp_hal0_home: str) -> None:
    _write_slot("stale-flat", f'image = "{STALE}"\nname = "stale-flat"\n')
    _write_slot("stale-nested", f'[slot]\nimage = "{STALE}"\nname = "stale-nested"\n')
    _write_slot("custom", f'image = "{CUSTOM}"\nname = "custom"\n')
    _write_slot("unpinned", 'name = "unpinned"\n')
    # image-gen slot: [image] is a settings TABLE, not a ref — must be ignored.
    _write_slot("imggen", 'name = "imggen"\ntype = "image"\n[image]\nsteps = 4\n')

    assert retag_stale_slot_images() == 2
    assert _image_of("stale-flat") == DEFAULT_ROCMFPX_IMAGE
    assert _image_of("stale-nested") == DEFAULT_ROCMFPX_IMAGE
    assert _image_of("custom") == CUSTOM
    assert _image_of("unpinned") is None
    raw = tomllib.loads((slots_config_dir() / "imggen.toml").read_text(encoding="utf-8"))
    assert raw["image"] == {"steps": 4}


@pytest.mark.parametrize("ref", sorted(STALE_ROCMFPX_IMAGE_REFS))
def test_every_stale_ref_retags(tmp_hal0_home: str, ref: str) -> None:
    """Every known former-default ref in STALE_ROCMFPX_IMAGE_REFS rolls to the
    current DEFAULT_ROCMFPX_IMAGE — not just the single ghcr ref the other tests
    drive. Regression-proofs a future frozenset addition whose retag path might
    diverge (e.g. the localhost/ prefix or the amd-strix-halo-toolboxes ref).
    DEFAULT_ROCMFPX_IMAGE is intentionally NOT in the set, so no case is a no-op.
    """
    _write_slot("s", f'image = "{ref}"\nname = "s"\n')
    assert retag_stale_slot_images() == 1
    assert _image_of("s") == DEFAULT_ROCMFPX_IMAGE


def test_retag_is_idempotent(tmp_hal0_home: str) -> None:
    _write_slot("s", f'image = "{STALE}"\nname = "s"\n')
    assert retag_stale_slot_images() == 1
    assert retag_stale_slot_images() == 0
    assert _image_of("s") == DEFAULT_ROCMFPX_IMAGE


def test_custom_profile_stale_image_retagged_flags_kept(tmp_hal0_home: str) -> None:
    from hal0.config.paths import profiles_toml

    p = profiles_toml()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f'[profile.moe-tuned]\nimage = "{STALE}"\nflags = "-fa off -b 2048"\n\n'
        f'[profile.pinned]\nimage = "{CUSTOM}"\nflags = "-fa on"\n',
        encoding="utf-8",
    )
    assert retag_stale_slot_images() == 1
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert raw["profile"]["moe-tuned"]["image"] == DEFAULT_ROCMFPX_IMAGE
    assert raw["profile"]["moe-tuned"]["flags"] == "-fa off -b 2048"
    assert raw["profile"]["pinned"]["image"] == CUSTOM
