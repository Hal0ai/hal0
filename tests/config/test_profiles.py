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
        p = ProfileConfig(flags="-fa on", mtp=False)
        assert p.flags == "-fa on"
        assert p.mtp is False

    def test_mtp_default_false(self) -> None:
        p = ProfileConfig()
        assert p.mtp is False

    def test_flags_default_empty(self) -> None:
        p = ProfileConfig()
        assert p.flags == ""

    def test_backend_default_none(self) -> None:
        p = ProfileConfig()
        assert p.backend is None

    def test_backend_accepts_rocm_and_vulkan(self) -> None:
        assert ProfileConfig(backend="rocm").backend == "rocm"
        assert ProfileConfig(backend="vulkan").backend == "vulkan"
        # cuda joined the valid set with the gpu-cuda device (GPU generalization).
        assert ProfileConfig(backend="cuda").backend == "cuda"

    def test_backend_rejects_unknown(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig(backend="metal")

    def test_image_is_not_a_profile_field(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="image"):
            ProfileConfig(image="ghcr.io/hal0ai/foo:bar")

    def test_extra_fields_forbidden(self) -> None:
        """extra='forbid' catches typos in profile toml keys."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig.model_validate({"unknown_key": "bad"})


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
                        "flags": "-fa on",
                        "mtp": False,
                    }
                }
            }
        )
        assert "test" in cfg.profile
        assert cfg.profile["test"].flags == "-fa on"


# ── resolve_profile_flags ─────────────────────────────────────────────────────


class TestResolveProfileFlags:
    def test_mtp_false_returns_base_flags(self) -> None:
        p = ProfileConfig(flags="-fa on -b 512", mtp=False)
        result = resolve_profile_flags(p)
        assert result == "-fa on -b 512"
        assert "--spec-type" not in result

    def test_mtp_true_appends_bundle(self) -> None:
        """§7.1a / ML-5: profile.mtp is informational only — resolve_profile_flags
        needs an explicit mtp_override=True now (the real decision lives in
        providers.container._effective_mtp, driven by the model + runner)."""
        p = ProfileConfig(flags="-fa on -b 512", mtp=True)
        result = resolve_profile_flags(p, mtp_override=True)
        # base flags are preserved verbatim and the MTP bundle is present. The
        # bundle now leads (it supplies defaults the profile's explicit flags
        # override); a profile with no --spec flags keeps the bundle intact.
        assert "-fa on -b 512" in result
        assert "--spec-type draft-mtp" in result

    def test_mtp_true_profile_but_no_override_no_longer_expands(self) -> None:
        """profile.mtp=True alone (no explicit mtp_override) no longer
        expands the bundle — MTP moved off the profile entirely."""
        p = ProfileConfig(flags="-fa on -b 512", mtp=True)
        result = resolve_profile_flags(p)
        assert result == "-fa on -b 512"
        assert "--spec-type" not in result

    def test_explicit_profile_spec_flags_win_over_bundle(self) -> None:
        """A profile that pins its own --spec-draft-* values keeps them; the MTP
        bundle only fills the gaps. Regression: the bundle used to be appended
        verbatim and silently clobbered explicit profile spec flags."""
        p = ProfileConfig(
            flags="-fa on --spec-draft-type-k f16 --spec-draft-type-v f16 --spec-draft-p-min 0.25",
            mtp=True,
        )
        result = resolve_profile_flags(p, mtp_override=True)
        # explicit values survive, exactly once, with no clobber/duplication
        assert "--spec-draft-type-k f16" in result
        assert "--spec-draft-p-min 0.25" in result
        assert "--spec-draft-type-k q8_0" not in result
        assert "--spec-draft-p-min 0.0" not in result
        assert result.split().count("--spec-draft-type-k") == 1
        assert result.split().count("--spec-draft-p-min") == 1
        # gap-filling: bundle flags the profile did not set are still supplied
        assert "--spec-type draft-mtp" in result
        assert "--spec-draft-n-max 4" in result

    def test_mtp_true_contains_all_key_flags(self) -> None:
        p = ProfileConfig(flags="-fa on", mtp=True)
        result = resolve_profile_flags(p, mtp_override=True)
        assert "--spec-draft-device ROCm0" in result
        assert "--spec-draft-ngl all" in result
        assert "--spec-draft-n-max 4" in result
        assert "--spec-draft-type-k q8_0" in result
        assert "--spec-draft-type-v q8_0" in result
        assert "--spec-draft-threads 16" in result
        assert "--spec-draft-poll 1" in result

    def test_mtp_bundle_literal_match(self) -> None:
        """MTP_FLAG_BUNDLE constant is verbatim in the resolved string."""
        p = ProfileConfig(flags="-fa on", mtp=True)
        result = resolve_profile_flags(p, mtp_override=True)
        assert MTP_FLAG_BUNDLE in result

    def test_empty_flags_mtp_false_returns_empty(self) -> None:
        p = ProfileConfig(flags="", mtp=False)
        assert resolve_profile_flags(p) == ""

    def test_empty_flags_mtp_true_returns_bundle(self) -> None:
        p = ProfileConfig(flags="", mtp=True)
        result = resolve_profile_flags(p, mtp_override=True)
        assert result == MTP_FLAG_BUNDLE

    def test_flags_stripped(self) -> None:
        p = ProfileConfig(flags="  -fa on  ", mtp=False)
        assert resolve_profile_flags(p) == "-fa on"


# ── load_profiles_config ──────────────────────────────────────────────────────


class TestLoadProfilesConfig:
    def test_missing_file_returns_seeds(self, tmp_path: Path) -> None:
        """Absent file → seed defaults; no fixture needed."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert set(cfg.profile.keys()) == set(SEED_PROFILES.keys())

    def test_seed_count(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        # rocm, rocm-dense, rocm-moe, vulkan, vulkan-dense, vulkan-moe, cuda,
        # embed, rerank, flm, tts, tts-qwen3, cpu-llm, comfyui
        assert len(cfg.profile) == len(SEED_PROFILES)

    def test_seed_profiles_have_correct_names(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        for name in ("chat", "chat-long-context", "moe", "dense"):
            assert name in cfg.profile

    def test_seed_rocm_mtp_false(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["chat"].mtp is False

    def test_seed_rocmfpx_grid_mtp_true(self, tmp_path: Path) -> None:
        """Non-MTP profiles stay False; MTP-capable family profiles are True."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        _MTP_TRUE_PROFILES = {"chadrock-dense", "chadrock-moe"}
        for name in SEED_PROFILES:
            expected_mtp = name in _MTP_TRUE_PROFILES
            assert cfg.profile[name].mtp is expected_mtp, (
                f"profile {name}: expected mtp={expected_mtp}, got mtp={cfg.profile[name].mtp}"
            )

    def test_seed_vulkan_uses_rocmfpx_default(self, tmp_path: Path) -> None:
        """Canonical GPU seeds do not pin a backend or runner image."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert cfg.profile["chat"].backend is None

    def test_seed_gpu_profiles_have_backend(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert all(profile.backend is None for profile in cfg.profile.values())

    def test_load_valid_file(self, tmp_path: Path) -> None:
        toml_content = (
            '[profile.custom]\nimage = "ghcr.io/hal0ai/test:v1"\nflags = "-fa on"\nmtp   = false\n'
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())
        cfg = load_profiles_config(path=p)
        assert "custom" in cfg.profile
        assert cfg.profile["custom"].flags == "-fa on"

    def test_missing_image_loads_with_empty_default(self, tmp_path: Path) -> None:
        """spec-hw-slot-ownership §3: ``image`` is optional/inert now — a profile
        without it loads (default "") rather than raising, so a migration that
        deleted a folded image pin doesn't break config load."""
        toml_content = '[profile.ok]\nflags = "-fa on"\nmtp = false\n'
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())
        cfg = load_profiles_config(path=p)
        assert cfg.profile["ok"].flags == "-fa on"

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

    # ── virtual seeds (overlay from code, never trusted from disk) ─────────────

    def test_partial_file_gets_missing_seeds_merged_in(self, tmp_path: Path) -> None:
        """A profiles.toml with only one profile still exposes every seed."""
        # Write a file with just the 'rocm' seed — everything else is missing.
        toml_content = (
            "[profile.chat]\n"
            'image = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server"\n'
            'flags = "-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap"\n'
            "mtp = false\n"
            'device_class = "gpu"\n'
            'backend = "rocm"\n'
            'intent = "MoE agents"\n'
            'quant = "FP4"\n'
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())

        cfg = load_profiles_config(path=p)

        # All SEED_PROFILES keys must be present (overlaid from code).
        assert set(SEED_PROFILES.keys()) <= set(cfg.profile.keys()), (
            f"missing seeds after overlay: {set(SEED_PROFILES.keys()) - set(cfg.profile.keys())}"
        )

    def test_materialised_seed_on_disk_is_overwritten_by_code(self, tmp_path: Path) -> None:
        """Seeds are virtual: a stale on-disk seed is replaced by the code definition.

        This is the #PS-2 fix — an installer/upgrade that materialised a now-stale
        seed into profiles.toml must NOT shadow a re-tuned seed shipped in code.
        Seed profiles are immutable via the catalog API (operators clone to
        customise), so a seed key on disk is never a legitimate operator edit.
        """
        stale_image = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-OLD-STALE-server"
        toml_content = (
            "[profile.rocm]\n"
            f'image = "{stale_image}"\n'
            'flags = "-fa on --stale-flag"\n'
            "mtp = false\n"
            'device_class = "gpu"\n'
            'backend = "rocm"\n'
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())

        cfg = load_profiles_config(path=p)

        # The code seed wins — the stale on-disk copy is discarded.
        assert cfg.profile["chat"].backend == SEED_PROFILES["chat"].get("backend")
        assert cfg.profile["chat"].flags == SEED_PROFILES["chat"]["flags"]

    def test_partial_file_custom_profile_preserved(self, tmp_path: Path) -> None:
        """A non-seed (operator-created) profile survives the additive merge."""
        toml_content = (
            "[profile.my-special]\n"
            'image = "ghcr.io/my-org/special:v42"\n'
            'flags = "--special-flag"\n'
            "mtp = false\n"
            'device_class = "gpu"\n'
        )
        p = tmp_path / "profiles.toml"
        p.write_bytes(toml_content.encode())

        cfg = load_profiles_config(path=p)

        # Operator's custom profile survives.
        assert "my-special" in cfg.profile
        assert cfg.profile["my-special"].flags == "--special-flag"
        # Seeds are also present.
        assert set(SEED_PROFILES.keys()) <= set(cfg.profile.keys())

    def test_complete_seed_file_no_extras_added(self, tmp_path: Path) -> None:
        """A file that already contains all seeds gets no duplicates."""
        import tomli_w

        # Write a file with all seeds present.
        raw = {"profile": {k: dict(v) for k, v in SEED_PROFILES.items()}}
        p = tmp_path / "profiles.toml"
        with open(p, "wb") as f:
            tomli_w.dump(raw, f)

        cfg = load_profiles_config(path=p)

        # Exactly the seed keys — no extras injected.
        assert set(cfg.profile.keys()) == set(SEED_PROFILES.keys())


# ── seed file: virtual-seed invariant ─────────────────────────────────────────


class TestSeedFileParity:
    """Installer seed file must NOT materialise seeds — they overlay from code.

    Under virtual seeds, ``SEED_PROFILES`` is the single source of truth; the
    shipped ``profiles.toml`` is documentation + a home for operator custom
    profiles only.  Materialising seeds there is exactly the freeze bug the
    overlay fixes (#PS-2).
    """

    @pytest.fixture
    def seed_file(self) -> Path:
        here = Path(__file__).resolve()
        # tests/config/test_profiles.py → repo root → installer/etc-hal0/profiles.toml
        return here.parents[2] / "installer" / "etc-hal0" / "profiles.toml"

    def test_seed_file_exists(self, seed_file: Path) -> None:
        assert seed_file.is_file(), f"seed file missing at {seed_file}"

    def test_seed_file_materialises_no_seeds(self, seed_file: Path) -> None:
        raw = tomllib.loads(seed_file.read_text(encoding="utf-8"))
        on_disk = set(raw.get("profile", {}).keys())
        assert on_disk.isdisjoint(SEED_PROFILES), (
            f"installer profiles.toml materialises seed profiles: "
            f"{on_disk & set(SEED_PROFILES)} — seeds must live in code only"
        )


# ── tts (kokoro) seed profile ─────────────────────────────────────────────────


def test_kokoro_seed_profile() -> None:
    prof = SEED_PROFILES["kokoro"]
    assert "image" not in prof
    assert "--model_path" in prof["flags"]
    assert prof["mtp"] is False


# ── device_class + backend + DEVICE_DEFAULT_PROFILES ──────────────────────────


def test_profile_device_class_defaults_none() -> None:
    """Per spec §4.1: device_class defaults to None (device-agnostic)."""
    assert ProfileConfig().device_class is None


def test_seed_device_classes() -> None:
    """Generic profiles are device-agnostic (slot owns device per §4.1);
    device-specific profiles declare their class explicitly to preserve
    the existing fit-gating semantics."""
    DEVICE_AGNOSTIC = {
        "chat",
        "chat-long-context",
        "moe",
        "dense",
        "embedding",
        "reranking",
        "brain",
        "chadrock-dense",
        "chadrock-moe",
        "thinking",
        "coding",
    }
    for name in DEVICE_AGNOSTIC:
        assert "device_class" not in SEED_PROFILES[name], (
            f"profile {name} should be device-agnostic but has device_class"
        )
    # Device-specific profiles declare their class explicitly.
    DEVICE_SPECIFIC = {
        "cpu-chat": "cpu",
        "flm": "npu",
        "kokoro": "cpu",
        "qwen3-tts": "gpu",
        "comfyui": "img",
    }
    for name, expected_class in DEVICE_SPECIFIC.items():
        assert SEED_PROFILES[name]["device_class"] == expected_class, (
            f"profile {name}: expected device_class={expected_class!r}, "
            f"got {SEED_PROFILES[name].get('device_class')!r}"
        )


def test_seed_backends() -> None:
    assert all(profile.get("backend") is None for profile in SEED_PROFILES.values())


def test_device_default_profiles_map() -> None:
    from hal0.config.schema import DEVICE_DEFAULT_PROFILES

    assert DEVICE_DEFAULT_PROFILES == {
        "gpu-rocm": "chat",
        "gpu-vulkan": "chat",
        "gpu-cuda": "chat",
        "cpu": "cpu-chat",
        "npu": "flm",
    }


def test_cpu_default_profile_supports_llm(tmp_path: Path) -> None:
    """PS-1: DEVICE_DEFAULT_PROFILES["cpu"] must name a chat-capable profile."""
    from hal0.config.schema import DEVICE_DEFAULT_PROFILES
    from hal0.profiles import ProfileCatalog

    resolved = ProfileCatalog(path=tmp_path / "nonexistent.toml").resolve(
        DEVICE_DEFAULT_PROFILES["cpu"]
    )
    assert "llm" in resolved.supported_slot_types
