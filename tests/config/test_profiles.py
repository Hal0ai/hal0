"""Unit tests for the profile catalog — schema, loader, and flag resolver.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_profiles.py -q
"""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

import pytest

from hal0.config.loader import ConfigParseError, load_profiles_config, save_profiles_config
from hal0.config.schema import (
    MTP_DRAFT_BUNDLE,
    SEED_PROFILES,
    ProfileConfig,
    ProfilesConfig,
    mtp_draft_device,
)

# ── ProfileConfig validation (ADR-0029: the profile is the whole tune) ────────


class TestProfileConfigValidation:
    def test_valid_profile(self) -> None:
        p = ProfileConfig(flags="-fa on", mtp="off", ctx_size=16384, vision="off")
        assert p.flags == "-fa on"
        assert p.mtp == "off"
        assert p.ctx_size == 16384
        assert p.vision == "off"

    def test_defaults(self) -> None:
        p = ProfileConfig()
        assert p.flags == ""
        assert p.mtp == "auto"
        assert p.vision == "auto"
        assert p.ctx_size is None
        assert p.enable_thinking is None
        assert p.runner is None
        assert p.cloned_from is None
        assert p.intent == ""

    @pytest.mark.parametrize("field", ["mtp", "vision"])
    def test_tri_state_switches_reject_bools_and_unknowns(self, field: str) -> None:
        """``mtp``/``vision`` are ``auto``/``off``; the pre-ADR-0029 bool is refused."""
        from pydantic import ValidationError

        for bad in (True, False, "on"):
            with pytest.raises(ValidationError):
                ProfileConfig.model_validate({field: bad})

    @pytest.mark.parametrize("ctx", [127, 2**24 + 1])
    def test_ctx_size_out_of_range_rejected(self, ctx: int) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfileConfig(ctx_size=ctx)

    @pytest.mark.parametrize("ctx", [128, 65536, 2**24])
    def test_ctx_size_bounds_accepted(self, ctx: int) -> None:
        assert ProfileConfig(ctx_size=ctx).ctx_size == ctx

    @pytest.mark.parametrize("key", ["device_class", "backend", "quant", "image", "unknown_key"])
    def test_removed_and_unknown_fields_forbidden(self, key: str) -> None:
        """extra='forbid': the deleted hardware hints and typos fail loudly."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match=key):
            ProfileConfig.model_validate({key: "x"})


# ── ProfilesConfig ────────────────────────────────────────────────────────────


class TestProfilesConfig:
    def test_empty_profiles(self) -> None:
        cfg = ProfilesConfig()
        assert cfg.profile == {}

    def test_parse_from_dict(self) -> None:
        cfg = ProfilesConfig.model_validate(
            {"profile": {"test": {"flags": "-fa on", "mtp": "off", "ctx_size": 8192}}}
        )
        assert cfg.profile["test"].flags == "-fa on"
        assert cfg.profile["test"].ctx_size == 8192

    @pytest.mark.parametrize("marker", ["legacy_seeds_migrated", "legacy_seeds_adopted"])
    def test_demoted_seed_markers_are_gone(self, marker: str) -> None:
        """ADR-0029 §3 deleted the demoted-seed markers with the adoption path."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProfilesConfig.model_validate({marker: True})


# ── MTP draft bundle + draft device ───────────────────────────────────────────


class TestMtpDraftBundle:
    def test_bundle_carries_the_draft_tuning(self) -> None:
        tokens = shlex.split(MTP_DRAFT_BUNDLE)
        for flag, value in (
            ("--spec-type", "draft-mtp"),
            ("--spec-draft-ngl", "all"),
            ("--spec-draft-n-max", "4"),
            ("--spec-draft-type-k", "q8_0"),
            ("--spec-draft-type-v", "q8_0"),
            ("--spec-draft-threads", "16"),
            ("--spec-draft-poll", "1"),
        ):
            assert tokens[tokens.index(flag) + 1] == value

    def test_bundle_names_no_draft_device(self) -> None:
        """The draft device is placement: the slot layer derives it from the
        slot's device, so the profile-layer bundle never carries it."""
        assert "--spec-draft-device" not in shlex.split(MTP_DRAFT_BUNDLE)

    @pytest.mark.parametrize(
        ("backend", "device"),
        [("rocm", "ROCm0"), ("vulkan", "Vulkan0"), ("cuda", "CUDA0"), ("CUDA", "CUDA0")],
    )
    def test_draft_device_follows_the_slot_backend(self, backend: str, device: str) -> None:
        assert mtp_draft_device(backend) == device

    def test_unknown_backend_drafts_on_rocm0(self) -> None:
        assert mtp_draft_device(None) == "ROCm0"
        assert mtp_draft_device("") == "ROCm0"


# ── load_profiles_config ──────────────────────────────────────────────────────


class TestLoadProfilesConfig:
    def test_missing_file_returns_seeds(self, tmp_path: Path) -> None:
        """Absent file → seed defaults; no fixture needed."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert set(cfg.profile.keys()) == set(SEED_PROFILES.keys())

    def test_seed_profiles_have_correct_names(self, tmp_path: Path) -> None:
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        for name in ("chat", "agent", "utility", "coding", "cpu-chat", "embedding", "reranking"):
            assert name in cfg.profile

    def test_every_seed_drafts_mtp_when_the_model_can(self, tmp_path: Path) -> None:
        """No seed turns MTP off: ``auto`` drafts when the model has heads and
        the runner supports it."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert {name: cfg.profile[name].mtp for name in SEED_PROFILES} == dict.fromkeys(
            SEED_PROFILES, "auto"
        )

    def test_seeds_name_no_runner(self, tmp_path: Path) -> None:
        """Seeds are device-agnostic: none pins a runner (the slot's binary decides)."""
        cfg = load_profiles_config(path=tmp_path / "nonexistent.toml")
        assert all(profile.runner is None for profile in cfg.profile.values())

    def test_load_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "profiles.toml"
        p.write_text(
            '[profile.custom]\nflags = "-fa on"\nctx_size = 16384\nvision = "off"\n',
            encoding="utf-8",
        )
        cfg = load_profiles_config(path=p)
        assert cfg.profile["custom"].flags == "-fa on"
        assert cfg.profile["custom"].ctx_size == 16384
        assert cfg.profile["custom"].vision == "off"

    def test_invalid_toml_raises_config_parse_error(self, tmp_path: Path) -> None:
        p = tmp_path / "profiles.toml"
        p.write_bytes(b"[profile\nbad toml <<<")
        with pytest.raises(ConfigParseError):
            load_profiles_config(path=p)

    def test_an_invalid_entry_is_quarantined_not_fatal(self, tmp_path: Path) -> None:
        """One invalid entry never fails the catalog: it is set aside with its
        reason, and every other profile (and every seed) still loads."""
        p = tmp_path / "profiles.toml"
        p.write_text(
            '[profile.bad]\nnot_a_field = "surprise"\n\n[profile.good]\nflags = "-fa on"\n',
            encoding="utf-8",
        )
        cfg = load_profiles_config(path=p)
        assert "bad" not in cfg.profile
        assert cfg.quarantined["bad"]["entry"] == {"not_a_field": "surprise"}
        assert cfg.quarantined["bad"]["reason"]
        assert cfg.profile["good"].flags == "-fa on"
        assert set(SEED_PROFILES) <= set(cfg.profile)

    @pytest.mark.parametrize(
        "body",
        [
            '[profile.old]\nflags = "-fa on"\nmtp = false\n',
            '[profile.old]\nflags = "-fa on"\ndevice_class = "gpu"\n',
            '[profile.old]\nflags = "-fa on"\nbackend = "rocm"\n',
        ],
    )
    def test_an_unconverted_entry_is_quarantined(self, tmp_path: Path, body: str) -> None:
        """An entry still in the pre-ADR-0029 shape is set aside (``hal0
        profile migrate`` converts it); the rest of the catalog loads."""
        p = tmp_path / "profiles.toml"
        p.write_text(body + "\n[profile.fine]\nctx_size = 8192\n", encoding="utf-8")
        cfg = load_profiles_config(path=p)
        assert "old" not in cfg.profile
        assert "old" in cfg.quarantined
        assert cfg.profile["fine"].ctx_size == 8192

    def test_a_quarantined_profile_blocks_only_its_slots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slot naming the quarantined profile refuses to launch with
        ``profiles.invalid`` (naming the reason); a slot on a valid profile
        still resolves."""
        from hal0.errors import UnprocessableEntity
        from hal0.profiles import ProfileCatalog
        from hal0.providers import container

        p = tmp_path / "profiles.toml"
        p.write_text(
            '[profile.old]\nflags = "-fa on"\nmtp = false\n\n[profile.good]\nflags = "-fa on"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            container, "_resolve_profile", lambda name: ProfileCatalog(path=p).resolve(name)
        )

        with pytest.raises(UnprocessableEntity) as exc:
            container.resolve_slot_profile({"name": "agent", "profile": "old"})
        assert exc.value.code == "profiles.invalid"
        assert exc.value.details["profile"] == "old"

        assert container.resolve_slot_profile({"name": "chat", "profile": "good"}).name == "good"

    def test_a_quarantined_entry_is_written_back_verbatim(self, tmp_path: Path) -> None:
        """Saving the catalog never drops an operator's (invalid) entry: it is
        written back exactly as stored, along with unknown top-level keys."""
        p = tmp_path / "profiles.toml"
        p.write_text(
            'legacy_seeds_migrated = true\n\n[profile.old]\nflags = "-fa on"\nmtp = false\n',
            encoding="utf-8",
        )
        cfg = load_profiles_config(path=p)
        assert "old" in cfg.quarantined
        save_profiles_config(cfg, path=p)
        on_disk = tomllib.loads(p.read_text(encoding="utf-8"))
        assert on_disk["profile"]["old"] == {"flags": "-fa on", "mtp": False}
        assert on_disk["legacy_seeds_migrated"] is True

    def test_unknown_top_level_keys_do_not_fail_the_catalog(self, tmp_path: Path) -> None:
        p = tmp_path / "profiles.toml"
        p.write_text(
            'legacy_seeds_migrated = true\n[profile.old]\nflags = "-fa on"\n', encoding="utf-8"
        )
        cfg = load_profiles_config(path=p)
        assert cfg.profile["old"].flags == "-fa on"
        assert not cfg.quarantined

    # ── virtual seeds (overlay from code, never trusted from disk) ─────────────

    def test_partial_file_gets_missing_seeds_merged_in(self, tmp_path: Path) -> None:
        """A profiles.toml with only one profile still exposes every seed."""
        p = tmp_path / "profiles.toml"
        p.write_text('[profile.chat]\nflags = "-fa on -b 512"\n', encoding="utf-8")

        cfg = load_profiles_config(path=p)

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
        p = tmp_path / "profiles.toml"
        p.write_text(
            '[profile.chat]\nflags = "-fa on --stale-flag"\nctx_size = 1024\n', encoding="utf-8"
        )

        cfg = load_profiles_config(path=p)

        assert cfg.profile["chat"].flags == SEED_PROFILES["chat"]["flags"]
        assert cfg.profile["chat"].ctx_size == SEED_PROFILES["chat"].get("ctx_size")

    def test_partial_file_custom_profile_preserved(self, tmp_path: Path) -> None:
        """A non-seed (operator-created) profile survives the additive merge."""
        p = tmp_path / "profiles.toml"
        p.write_text(
            '[profile.my-special]\nflags = "--special-flag"\ncloned_from = "chat"\n',
            encoding="utf-8",
        )

        cfg = load_profiles_config(path=p)

        assert cfg.profile["my-special"].flags == "--special-flag"
        assert cfg.profile["my-special"].cloned_from == "chat"
        assert set(SEED_PROFILES.keys()) <= set(cfg.profile.keys())

    def test_load_is_read_only(self, tmp_path: Path) -> None:
        """A load never rewrites the file: custom profiles come back as written
        (the write seam and the launch screen their flags, not the loader)."""
        p = tmp_path / "profiles.toml"
        body = '[profile.tts-clone]\nflags = "--model_path /models/kokoro --threads-batch 8"\n'
        p.write_text(body, encoding="utf-8")

        cfg = load_profiles_config(path=p)

        assert cfg.profile["tts-clone"].flags == "--model_path /models/kokoro --threads-batch 8"
        assert p.read_text(encoding="utf-8") == body

    def test_complete_seed_file_no_extras_added(self, tmp_path: Path) -> None:
        """A file that already contains all seeds gets no duplicates."""
        import tomli_w

        raw = {"profile": {k: dict(v) for k, v in SEED_PROFILES.items()}}
        p = tmp_path / "profiles.toml"
        with open(p, "wb") as f:
            tomli_w.dump(raw, f)

        cfg = load_profiles_config(path=p)

        assert set(cfg.profile.keys()) == set(SEED_PROFILES.keys())

    def test_save_drops_seeds_and_round_trips_custom_fields(self, tmp_path: Path) -> None:
        from hal0.config.loader import save_profiles_config

        p = tmp_path / "profiles.toml"
        cfg = load_profiles_config(path=p)
        cfg.profile["mine"] = ProfileConfig(
            flags="-fa auto", ctx_size=32768, enable_thinking=False, vision="off", mtp="off"
        )
        save_profiles_config(cfg, path=p)

        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        assert set(raw["profile"]) == {"mine"}
        reloaded = load_profiles_config(path=p)
        assert reloaded.profile["mine"] == cfg.profile["mine"]
        assert set(reloaded.profile) == set(SEED_PROFILES) | {"mine"}


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
    """Kokoro's bundle is the pulled kokoro-v1 row: the provider derives
    ``--model_path`` from the store, so the seed carries no path (ADR-0030,
    #2739)."""
    prof = SEED_PROFILES["kokoro"]
    assert set(prof) <= {"flags", "intent"}
    assert "--model_path" not in prof.get("flags", "")


# ── seeds carry no hardware hints; DEVICE_DEFAULT_PROFILES ─────────────────────


@pytest.mark.parametrize("key", ["device_class", "backend", "quant", "image"])
def test_no_seed_carries_a_deleted_field(key: str) -> None:
    """Every seed is device-agnostic; the slot owns the device."""
    assert all(key not in profile for profile in SEED_PROFILES.values())


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


# ── seeds must survive the screens the API enforces on custom profiles ───────


def test_every_seed_profile_passes_the_profile_flag_screen() -> None:
    """The seeds are held to the identical standard as user input.

    Seeds are VIRTUAL: ``load_profiles_config`` overlays ``SEED_PROFILES`` over
    whatever is on disk, so a seed never passes through
    ``ProfileCatalog.create`` and never meets ``screen_profile_flags``. A seed
    could therefore acquire ``-ngl``, ``--ctx-size``, ``--jinja`` or
    ``--no-mmproj`` and ship, while a user typing the same flag into the
    profile editor gets a 400 — and the launch would refuse it. This runs the
    real screen over every seed.
    """
    from hal0.profiles import screen_profile_flags

    for name, profile in SEED_PROFILES.items():
        flags = profile.get("flags", "")
        try:
            screen_profile_flags(flags)
        except Exception as exc:  # pragma: no cover - only on a regression
            raise AssertionError(
                f"seed profile {name!r} carries a flag a profile may not set and "
                f"would be rejected by the API: {flags!r} → {exc}"
            ) from exc


def test_no_seed_profile_carries_a_flag_another_layer_owns() -> None:
    """Belt-and-braces, spelled out literally so the intent survives a future
    refactor of the screen internals: the window is ``ctx_size``, layers and
    threads are the slot's, ``--jinja``/``--mmproj`` are model facts and the
    projector switch is ``vision``."""
    banned = (
        "-c",
        "--ctx-size",
        "-ngl",
        "--n-gpu-layers",
        "--device",
        "-dev",
        "--threads",
        "--jinja",
        "--mmproj",
        "--no-mmproj",
        "--chat-template-file",
        "--spec-draft-device",
    )
    for name, profile in SEED_PROFILES.items():
        tokens = shlex.split(profile.get("flags", ""))
        for bad in banned:
            assert bad not in tokens, f"seed {name!r} carries {bad!r}: {profile.get('flags')!r}"


def test_demoted_seed_catalog_is_gone() -> None:
    """ADR-0029 §3 deleted ``LEGACY_SEED_PROFILES`` and its adoption path."""
    import hal0.config.schema as schema

    assert not hasattr(schema, "LEGACY_SEED_PROFILES")
