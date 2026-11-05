"""Tests for save_profiles_config — round-trip + atomicity.

Targeted file run:
    ~/dev/wt-phase-c/.venv/bin/python -m pytest tests/config/test_loader_profiles_save.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.config.loader import ConfigParseError, load_profiles_config, save_profiles_config
from hal0.config.schema import SEED_PROFILES, ProfileConfig, ProfilesConfig

# ── save_profiles_config ──────────────────────────────────────────────────────


class TestSaveProfilesConfig:
    def test_save_profiles_config_round_trips(self, tmp_hal0_home: str) -> None:
        catalog = load_profiles_config()
        catalog.profile["my-custom"] = ProfileConfig(flags="-fa on")
        save_profiles_config(catalog)
        reloaded = load_profiles_config()
        assert "my-custom" in reloaded.profile
        # seeds survive the round-trip — they are re-overlaid from code on load
        # (they are NOT written to disk; see test_save_omits_virtual_seeds).
        assert set(SEED_PROFILES) <= set(reloaded.profile)

    def test_save_omits_virtual_seeds(self, tmp_hal0_home: str) -> None:
        """Seeds are virtual: save persists only operator (non-seed) profiles."""
        catalog = load_profiles_config()
        catalog.profile["extra"] = ProfileConfig()
        save_profiles_config(catalog)

        target = paths.profiles_toml()
        assert target.exists()

        # Parses as valid TOML
        with open(target, "rb") as f:
            raw = tomllib.load(f)

        # Validates as ProfilesConfig
        parsed = ProfilesConfig.model_validate(raw)
        # The operator profile is persisted…
        assert "extra" in parsed.profile
        # …but NOT a single seed profile is materialised on disk.
        assert set(SEED_PROFILES).isdisjoint(parsed.profile), (
            "seed profiles must never be written to disk (they overlay from code)"
        )

    def test_save_uses_profiles_toml_path_by_default(self, tmp_hal0_home: str) -> None:
        catalog = load_profiles_config()
        save_profiles_config(catalog)
        assert paths.profiles_toml().exists()

    def test_save_accepts_explicit_path(self, tmp_path: Path) -> None:
        catalog = ProfilesConfig.model_validate({"profile": SEED_PROFILES})
        catalog.profile["mine"] = ProfileConfig()
        target = tmp_path / "custom_profiles.toml"
        save_profiles_config(catalog, path=target)
        assert target.exists()
        with open(target, "rb") as f:
            raw = tomllib.load(f)
        parsed = ProfilesConfig.model_validate(raw)
        # Explicit path honoured; only the operator profile lands on disk —
        # seeds are virtual and stripped on write.
        assert set(parsed.profile.keys()) == {"mine"}

    def test_save_overwrites_previous_file(self, tmp_hal0_home: str) -> None:
        catalog = load_profiles_config()
        save_profiles_config(catalog)

        # Add a profile and save again
        catalog.profile["v2"] = ProfileConfig()
        save_profiles_config(catalog)

        reloaded = load_profiles_config()
        assert "v2" in reloaded.profile


# ── non-table [profile] (hal0#2650) ───────────────────────────────────────────


class TestNonTableProfileKey:
    """A ``profile`` key that is not a table must fail loud, never read as empty."""

    @pytest.mark.parametrize(
        "body",
        [
            '[[profile]]\nname = "mine"\nflags = "-fa on"\n',  # array of tables
            'profile = "oops"\n',  # scalar string
            "profile = 3\n",  # scalar int
        ],
        ids=["array-of-tables", "string", "int"],
    )
    def test_load_raises_and_file_untouched(self, tmp_path: Path, body: str) -> None:
        target = tmp_path / "profiles.toml"
        target.write_text(body)

        with pytest.raises(ConfigParseError, match="must be a table"):
            load_profiles_config(target)

        assert target.read_text() == body

    def test_load_then_save_cannot_clobber(self, tmp_path: Path) -> None:
        """The read-modify-write path aborts at load, so save never runs."""
        target = tmp_path / "profiles.toml"
        body = '[[profile]]\nname = "mine"\n'
        target.write_text(body)

        with pytest.raises(ConfigParseError):
            save_profiles_config(load_profiles_config(target), path=target)

        assert target.read_text() == body

    def test_empty_profile_table_still_loads(self, tmp_path: Path) -> None:
        target = tmp_path / "profiles.toml"
        target.write_text("[profile]\n")
        assert set(SEED_PROFILES) <= set(load_profiles_config(target).profile)

    def test_missing_profile_key_still_loads(self, tmp_path: Path) -> None:
        target = tmp_path / "profiles.toml"
        target.write_text('other = "kept"\n')
        assert set(SEED_PROFILES) <= set(load_profiles_config(target).profile)
