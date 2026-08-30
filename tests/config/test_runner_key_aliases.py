"""Persisted vulkanfpx values heal at config load (collapse spec §2)."""

import logging

import pytest

import hal0.config.schema as schema
from hal0.config.schema import SlotConfig, SlotsConfig


@pytest.fixture(autouse=True)
def _reset_alias_warn_dedup() -> None:
    """The alias warnings dedup once per (surface, key) per process (finding
    3) — clear the seen-set before each test so caplog assertions here stay
    deterministic regardless of test order/reruns in the same process."""
    schema._warned.clear()


def test_slot_binary_alias_normalized(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="hal0.config.schema"):
        cfg = SlotConfig(name="primary", port=8081, binary="vulkanfpx")
    assert cfg.binary == "rocmfpx"
    assert any("vulkanfpx" in r.message and "rocmfpx" in r.message for r in caplog.records)


def test_slot_binary_canonical_and_empty_untouched() -> None:
    assert SlotConfig(name="primary", port=8081, binary="rocmfpx").binary == "rocmfpx"
    assert SlotConfig(name="primary", port=8081, binary="").binary == ""
    # A genuinely unknown key is NOT rejected here — the drawer's
    # out-of-vocab handling owns that surface; load never hard-fails.
    assert SlotConfig(name="primary", port=8081, binary="ghost").binary == "ghost"


def test_default_images_alias_key_folds() -> None:
    s = SlotsConfig(default_images={"vulkanfpx": "ghcr.io/x/y:1"})
    assert s.default_images == {"rocmfpx": "ghcr.io/x/y:1"}


def test_default_images_canonical_wins_on_conflict() -> None:
    s = SlotsConfig(
        default_images={"rocmfpx": "ghcr.io/x/canon:1", "vulkanfpx": "ghcr.io/x/alias:1"}
    )
    assert s.default_images == {"rocmfpx": "ghcr.io/x/canon:1"}


def test_default_images_canonical_wins_on_conflict_alias_first() -> None:
    # Same conflict, opposite TOML order — the canonical entry must still
    # win regardless of which key appears first in the source dict.
    s = SlotsConfig(
        default_images={"vulkanfpx": "ghcr.io/x/alias:1", "rocmfpx": "ghcr.io/x/canon:1"}
    )
    assert s.default_images == {"rocmfpx": "ghcr.io/x/canon:1"}


def test_default_images_unknown_key_still_rejected() -> None:
    with pytest.raises(ValueError, match="not a known runner"):
        SlotsConfig(default_images={"ghost": "ghcr.io/x/y:1"})


def test_default_images_alias_null_clears_canonical_write() -> None:
    # An alias-origin value followed by a canonical-key null clear must
    # clear the family — the null is a clear on the CANONICAL family, not
    # a no-op tied to the literal spelling it arrived under.
    s = SlotsConfig(default_images={"vulkanfpx": "ghcr.io/x/y:1", "rocmfpx": None})
    assert s.default_images == {}


def test_default_images_canonical_null_blocks_later_alias_write() -> None:
    # Same clear, opposite order — a canonical-key null seen first must
    # still block a later alias-origin write to the same family.
    s = SlotsConfig(default_images={"rocmfpx": None, "vulkanfpx": "ghcr.io/x/y:1"})
    assert s.default_images == {}
