"""Unit tests for the profile catalog — schema, loader, and flag resolver.

Targeted file run only (full suite hangs due to lemond health waits):
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_profiles.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.loader import ConfigParseError, load_profiles_config
from hal0.config.schema import (
    MTP_FLAG_BUNDLE,
    SEED_PROFILES,
    ProfileConfig,
    ProfilesConfig,
    resolve_profile_flags,
)

# ── ProfileConfig validation ──────────────────────────────────────────────────


class TestProfileConfigValidation:
    def test_valid_profile(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="-fa on", mtp=False)
        assert p.image == "ghcr.io/hal0ai/foo:bar"
        assert p.flags == "-fa on"
        assert p.mtp is False

    def test_mtp_default_false(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar")
        assert p.mtp is False

    def test_flags_default_empty(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar")
        assert p.flags == ""

    def test_empty_image_raises(self) -> None:
        with pytest.raises(Exception, match="image"):
            ProfileConfig(image="")

    def test_whitespace_image_raises(self) -> None:
        with pytest.raises(Exception, match="image"):
            ProfileConfig(image="   ")

    def test_missing_image_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig.model_validate({"flags": "-fa on"})

    def test_extra_fields_forbidden(self) -> None:
        """extra='forbid' catches typos in profile toml keys."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig.model_validate({"image": "ghcr.io/hal0ai/foo:bar", "unknown_key": "bad"})


# ── ProfilesConfig ────────────────────────────────────────────────────────────


class TestProfilesConfig:
    def test_empty_profiles(self) -> None:
        cfg = ProfilesConfig()
        assert cfg.profile == {}

    def test_parse_from_dict(self) -> None:
        cfg = ProfilesConfig.model_validate(
            {
                "profile": {
                    "test": {
                        "image": "ghcr.io/hal0ai/test:v1",
                        "flags": "-fa on",
                        "mtp": False,
                    }
                }
            }
        )
        assert "test" in cfg.profile
        assert cfg.profile["test"].image == "ghcr.io/hal0ai/test:v1"


# ── resolve_profile_flags ─────────────────────────────────────────────────────


class TestResolveProfileFlags:
    def test_mtp_false_returns_base_flags(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="-fa on -b 512", mtp=False)
        result = resolve_profile_flags(p)
        assert result == "-fa on -b 512"
        assert "--spec-type" not in result

    def test_mtp_true_appends_bundle(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="-fa on -b 512", mtp=True)
        result = resolve_profile_flags(p)
        assert result.startswith("-fa on -b 512 ")
        assert "--spec-type draft-mtp" in result

    def test_mtp_true_contains_all_key_flags(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="-fa on", mtp=True)
        result = resolve_profile_flags(p)
        assert "--spec-draft-device ROCm0" in result
        assert "--spec-draft-ngl all" in result
        assert "--spec-draft-n-max 4" in result
        assert "--spec-draft-type-k q4_0" in result
        assert "--spec-draft-type-v q4_0" in result

    def test_mtp_bundle_literal_match(self) -> None:
        """MTP_FLAG_BUNDLE constant is verbatim in the resolved string."""
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="-fa on", mtp=True)
        result = resolve_profile_flags(p)
        assert MTP_FLAG_BUNDLE in result

    def test_empty_flags_mtp_false_returns_empty(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="", mtp=False)
        assert resolve_profile_flags(p) == ""

    def test_empty_flags_mtp_true_returns_bundle(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="", mtp=True)
        result = resolve_profile_flags(p)
        assert result == MTP_FLAG_BUNDLE

    def test_flags_stripped(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar", flags="  -fa on  ", mtp=False)
        assert resolve_profile_flags(p) == "-fa on"


# ── load_profiles_config ──────────────────────────────────────────────────────


class TestLoadProfilesConfig:
    def test_missing_file_returns_seeds(self, tmp_path: Path) -> None:
        """Absent file → seed defaults; no fixture needed."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert set(cfg.profile.keys()) == set(SEED_PROFILES.keys())

    def test_seed_count(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert len(cfg.profile) == 4  # moe-rocmfp4, dense-mtp-rocmfp4, vulkan-std, flm-npu

    def test_seed_profiles_have_correct_names(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert "moe-rocmfp4" in cfg.profile
        assert "dense-mtp-rocmfp4" in cfg.profile
        assert "vulkan-std" in cfg.profile

    def test_seed_moe_rocmfp4_mtp_false(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["moe-rocmfp4"].mtp is False

    def test_seed_dense_mtp_rocmfp4_mtp_true(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["dense-mtp-rocmfp4"].mtp is True

    def test_seed_vulkan_std_correct_image(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert "vulkan-radv-server" in cfg.profile["vulkan-std"].image

    def test_load_valid_file(self, tmp_path: Path) -> None:
        toml_content = (
            '[profile.custom]\nimage = "ghcr.io/hal0ai/test:v1"\nflags = "-fa on"\nmtp   = false\n'
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())
        cfg = load_profiles_config(path=p)
        assert "custom" in cfg.profile
        assert cfg.profile["custom"].flags == "-fa on"

    def test_missing_image_raises_config_parse_error(self, tmp_path: Path) -> None:
        """``image`` is required — missing it must surface as ConfigParseError."""
        toml_content = '[profile.bad]\nflags = "-fa on"\nmtp = false\n'
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())
        with pytest.raises(ConfigParseError):
            load_profiles_config(path=p)

    def test_invalid_toml_raises_config_parse_error(self, tmp_path: Path) -> None:
        p = tmp_path / "profiles.toml"
        p.write_bytes(b"[profile\nbad toml <<<")
        with pytest.raises(ConfigParseError):
            load_profiles_config(path=p)

    def test_unknown_field_raises_config_parse_error(self, tmp_path: Path) -> None:
        """extra='forbid' on ProfileConfig: typos in profile keys raise at load."""
        toml_content = (
            "[profile.bad]\n"
            'image = "ghcr.io/hal0ai/test:v1"\n'
            'not_a_field = "surprise"\n'  # unknown key
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())
        with pytest.raises(ConfigParseError):
            load_profiles_config(path=p)


# ── seed file parity check ────────────────────────────────────────────────────


class TestSeedFileParity:
    """Installer seed file must match the code-level SEED_PROFILES constant."""

    @pytest.fixture
    def seed_file(self) -> Path:
        here = Path(__file__).resolve()
        # tests/config/test_profiles.py → repo root → installer/etc-hal0/profiles.toml
        return here.parents[2] / "installer" / "etc-hal0" / "profiles.toml"

    def test_seed_file_exists(self, seed_file: Path) -> None:
        assert seed_file.is_file(), f"seed file missing at {seed_file}"

    def test_seed_file_names_match_code(self, seed_file: Path) -> None:
        raw = tomllib.loads(seed_file.read_text(encoding="utf-8"))
        file_names = set(raw.get("profile", {}).keys())
        code_names = set(SEED_PROFILES.keys())
        assert file_names == code_names, f"seed file names {file_names!r} != code {code_names!r}"

    def test_seed_file_mtp_flags_match_code(self, seed_file: Path) -> None:
        raw = tomllib.loads(seed_file.read_text(encoding="utf-8"))
        for name, code_vals in SEED_PROFILES.items():
            file_entry = raw["profile"][name]
            assert file_entry["mtp"] == code_vals["mtp"], (
                f"profiles.toml mtp for {name!r} ({file_entry['mtp']}) "
                f"!= SEED_PROFILES ({code_vals['mtp']})"
            )
            assert file_entry["image"] == code_vals["image"], (
                f"profiles.toml image for {name!r} differs from SEED_PROFILES"
            )
