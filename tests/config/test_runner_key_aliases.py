"""Persisted vulkanfpx values heal at config load (collapse spec §2)."""

import logging

import pytest

from hal0.config.schema import SlotConfig, SlotsConfig


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


def test_default_images_unknown_key_still_rejected() -> None:
    with pytest.raises(ValueError, match="not a known runner"):
        SlotsConfig(default_images={"ghost": "ghcr.io/x/y:1"})
