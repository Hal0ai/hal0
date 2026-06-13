"""Unit tests for the profile catalog — schema, loader, and flag resolver.

Targeted file run:
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

    def test_backend_default_none(self) -> None:
        p = ProfileConfig(image="ghcr.io/hal0ai/foo:bar")
        assert p.backend is None

    def test_backend_accepts_rocm_and_vulkan(self) -> None:
        assert ProfileConfig(image="x", backend="rocm").backend == "rocm"
        assert ProfileConfig(image="x", backend="vulkan").backend == "vulkan"

    def test_backend_rejects_unknown(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig(image="x", backend="cuda")

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
        assert len(cfg.profile) == 6  # rocm, rocm-mtp, vulkan, flm, tts, comfyui

    def test_seed_profiles_have_correct_names(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert "rocm" in cfg.profile
        assert "rocm-mtp" in cfg.profile
        assert "vulkan" in cfg.profile

    def test_seed_rocm_mtp_false(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["rocm"].mtp is False

    def test_seed_rocm_mtp_mtp_true(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["rocm-mtp"].mtp is True

    def test_seed_vulkan_correct_image(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert "vulkan-radv-server" in cfg.profile["vulkan"].image

    def test_seed_gpu_profiles_have_backend(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["rocm"].backend == "rocm"
        assert cfg.profile["rocm-mtp"].backend == "rocm"
        assert cfg.profile["vulkan"].backend == "vulkan"
        assert cfg.profile["flm"].backend is None
        assert cfg.profile["tts"].backend is None
        assert cfg.profile["comfyui"].backend is None

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


# ── tts (kokoro) seed profile ─────────────────────────────────────────────────


def test_tts_seed_profile() -> None:
    prof = SEED_PROFILES["tts"]
    assert prof["image"] == "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
    assert "--model_path" in prof["flags"]
    assert prof["mtp"] is False


# ── device_class + backend + DEVICE_DEFAULT_PROFILES ──────────────────────────


def test_profile_device_class_defaults_gpu() -> None:
    assert ProfileConfig(image="x").device_class == "gpu"


def test_seed_device_classes() -> None:
    assert SEED_PROFILES["vulkan"]["device_class"] == "gpu"
    assert SEED_PROFILES["rocm"]["device_class"] == "gpu"
    assert SEED_PROFILES["rocm-mtp"]["device_class"] == "gpu"
    assert SEED_PROFILES["flm"]["device_class"] == "npu"
    assert SEED_PROFILES["tts"]["device_class"] == "cpu"
    assert SEED_PROFILES["comfyui"]["device_class"] == "img"


def test_seed_backends() -> None:
    assert SEED_PROFILES["rocm"]["backend"] == "rocm"
    assert SEED_PROFILES["rocm-mtp"]["backend"] == "rocm"
    assert SEED_PROFILES["vulkan"]["backend"] == "vulkan"
    # non-GPU profiles carry no backend (device_class drives display)
    assert SEED_PROFILES["flm"].get("backend") is None
    assert SEED_PROFILES["tts"].get("backend") is None
    assert SEED_PROFILES["comfyui"].get("backend") is None


def test_device_default_profiles_map() -> None:
    from hal0.config.schema import DEVICE_DEFAULT_PROFILES

    assert DEVICE_DEFAULT_PROFILES == {
        "gpu-rocm": "rocm",
        "gpu-vulkan": "vulkan",
        "cpu": "tts",
        "npu": "flm",
    }
