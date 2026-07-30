"""reset_profile_catalog — the one-shot, schema-version-gated v1.0 wipe.

This is the DESTRUCTIVE profile migration. Its sibling ``ensure_seed_profiles``
(tests/updater/test_seed_profiles_migration.py) is the conservative one, and the
two contracts are opposites:

    ensure_seed_profiles   prune materialised seeds, RESCUE divergent ones
    reset_profile_catalog  delete the whole file, once, and stamp the gate

Which one applies is decided purely by ``hal0.toml``'s ``[meta] schema_version``:
below the v2 watermark the reset is still owed; at or above it only the prune
contract runs, forever. Both live side by side on purpose — see
``test_prune_contract_still_applies_after_reset``.

A gate bug here is shipped data loss, so the "fires exactly once" and "never
without consent" paths are tested from both directions.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION
from hal0.config.paths import hal0_toml, profiles_toml, var_lib
from hal0.config.schema import SEED_PROFILES
from hal0.updater.updater import (
    ensure_seed_profiles,
    profile_reset_status,
    reset_profile_catalog,
)

_OPERATOR_PROFILES = '[profile.my-bench]\nflags = "-fa on -b 2048"\n'


def _write_hal0_toml(version: int | None = 1) -> None:
    path = hal0_toml()
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = "" if version is None else f"[meta]\nschema_version = {version}\n\n"
    path.write_text(f"{meta}[slots]\nport_range_start = 8081\n", encoding="utf-8")


def _write_profiles(text: str) -> None:
    target = profiles_toml()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _schema_version() -> int:
    return int(tomllib.loads(hal0_toml().read_text(encoding="utf-8"))["meta"]["schema_version"])


def _backups() -> list[Path]:
    root = var_lib() / "backups"
    return sorted(root.glob("profiles-*.toml")) if root.is_dir() else []


# ── the gate ──────────────────────────────────────────────────────────────────


def test_fires_once_then_never_again(tmp_hal0_home: str) -> None:
    """The whole point: converge a pre-v1.0 box exactly once."""
    _write_hal0_toml(1)
    _write_profiles(_OPERATOR_PROFILES)

    first = reset_profile_catalog(approved=True)
    assert first["performed"] is True
    assert first["outcome"] == "reset"
    assert not profiles_toml().exists()
    assert _schema_version() == PROFILE_CATALOG_SCHEMA_VERSION

    # An operator authors a NEW profile under v1.0 rules. A second update must
    # not touch it — this is the data-loss regression the gate exists to stop.
    _write_profiles('[profile.post-v1-tune]\nflags = "-fa on"\n')
    second = reset_profile_catalog(approved=True)
    assert second["performed"] is False
    assert second["outcome"] == "already_reset"
    assert profiles_toml().exists()
    assert "post-v1-tune" in profiles_toml().read_text(encoding="utf-8")


def test_absent_hal0_toml_refuses_rather_than_firing_every_run(tmp_hal0_home: str) -> None:
    """No hal0.toml → the gate can be neither read nor recorded.

    Without this guard the reset would look "due" on every single update and
    delete profiles.toml each time, since there is nowhere to record that it
    already ran. Refusing is the only safe reading.
    """
    _write_profiles(_OPERATOR_PROFILES)
    assert not hal0_toml().exists()

    status = profile_reset_status()
    assert status["due"] is False
    assert status["reason"] == "no_config"

    result = reset_profile_catalog(approved=True)
    assert result["performed"] is False
    assert profiles_toml().exists()


def test_unstamped_meta_table_is_treated_as_pre_v1(tmp_hal0_home: str) -> None:
    """A hal0.toml with no [meta] table reads as v1, not as the model default."""
    _write_hal0_toml(None)
    _write_profiles(_OPERATOR_PROFILES)
    assert profile_reset_status()["due"] is True
    assert reset_profile_catalog(approved=True)["performed"] is True
    assert _schema_version() == PROFILE_CATALOG_SCHEMA_VERSION


# ── consent ───────────────────────────────────────────────────────────────────


def test_headless_skips_when_operator_profiles_would_be_lost(tmp_hal0_home: str) -> None:
    """approved=None is what an unattended commit/cron run passes.

    It must never destroy operator-authored profiles, and it must NOT stamp —
    otherwise the one-shot is consumed and the box never converges.
    """
    _write_hal0_toml(1)
    _write_profiles(_OPERATOR_PROFILES)

    result = reset_profile_catalog(approved=None)
    assert result["performed"] is False
    assert result["outcome"] == "declined"
    assert profiles_toml().exists()
    assert _schema_version() == 1
    assert _backups() == []

    # Still offered on the next run.
    assert profile_reset_status()["due"] is True


def test_explicit_decline_is_identical_to_headless(tmp_hal0_home: str) -> None:
    _write_hal0_toml(1)
    _write_profiles(_OPERATOR_PROFILES)
    assert reset_profile_catalog(approved=False)["outcome"] == "declined"
    assert profiles_toml().exists()
    assert _schema_version() == 1


def test_nothing_to_lose_converges_without_consent(tmp_hal0_home: str) -> None:
    """A catalog holding only materialised seeds has no operator content.

    Prompting for a no-op would be noise, and leaving a fresh install unstamped
    would arm the wipe against profiles the operator creates later under v1.0.
    """
    seed = next(iter(SEED_PROFILES))
    _write_hal0_toml(1)
    _write_profiles(f'[profile.{seed}]\nflags = "{SEED_PROFILES[seed]["flags"]}"\n')

    status = profile_reset_status()
    assert status["due"] is True
    assert status["custom_profiles"] == []
    assert status["needs_consent"] is False

    result = reset_profile_catalog(approved=None)
    assert result["performed"] is True
    assert not profiles_toml().exists()
    assert _schema_version() == PROFILE_CATALOG_SCHEMA_VERSION


def test_fresh_install_with_no_profiles_file_is_stamped(tmp_hal0_home: str) -> None:
    """A fresh box has nothing to delete but MUST still be stamped.

    install.sh writes `schema_version = 1` on a brand-new box. If the reset
    left that at 1, the first post-release `hal0 update` would fire the wipe on
    a box whose profiles were all authored under v1.0 rules.
    """
    _write_hal0_toml(1)
    assert not profiles_toml().exists()

    result = reset_profile_catalog(approved=None)
    assert result["performed"] is True
    assert result["backup"] is None
    assert _schema_version() == PROFILE_CATALOG_SCHEMA_VERSION


def test_unparseable_catalog_converges_without_consent(tmp_hal0_home: str) -> None:
    """The shape that makes ensure_seed_profiles raise ConfigParseError.

    There is no recoverable operator content to weigh (it does not parse), the
    file is actively breaking this box, and the timestamped backup preserves the
    original bytes — so it converges rather than blocking on a prompt.
    """
    _write_hal0_toml(1)
    _write_profiles("[profile.broken\nthis is not toml at all")

    status = profile_reset_status()
    assert status["unreadable"] is True
    assert status["needs_consent"] is False

    result = reset_profile_catalog(approved=None)
    assert result["performed"] is True
    assert not profiles_toml().exists()
    backups = _backups()
    assert len(backups) == 1
    assert "this is not toml at all" in backups[0].read_text(encoding="utf-8")


# ── backup ────────────────────────────────────────────────────────────────────


def test_backup_is_written_before_the_delete(tmp_hal0_home: str) -> None:
    _write_hal0_toml(1)
    _write_profiles(_OPERATOR_PROFILES)

    result = reset_profile_catalog(approved=True)
    assert result["backup"] is not None
    backup = Path(result["backup"])
    assert backup.exists()
    assert backup.parent == var_lib() / "backups"
    assert backup.read_text(encoding="utf-8") == _OPERATOR_PROFILES


def test_backup_never_clobbers_a_previous_one(tmp_hal0_home: str) -> None:
    """Unlike the write-once `.pre-virtual-seeds.bak`, which goes stale.

    Every run keeps its own copy. The stamp only has one-second resolution, so
    this deliberately runs both resets back to back — a same-second collision
    must suffix, not overwrite. (Regression: it overwrote, which is the
    write-once flaw wearing a different hat.)
    """
    _write_hal0_toml(1)
    _write_profiles('[profile.first]\nflags = "-fa on"\n')
    first = Path(reset_profile_catalog(approved=True)["backup"])

    # Force the gate open again, within the same second.
    _write_hal0_toml(1)
    _write_profiles('[profile.second]\nflags = "-fa off"\n')
    second = Path(reset_profile_catalog(approved=True)["backup"])

    assert second != first
    assert first.read_text(encoding="utf-8") == '[profile.first]\nflags = "-fa on"\n'
    assert second.read_text(encoding="utf-8") == '[profile.second]\nflags = "-fa off"\n'
    assert len(_backups()) == 2


# ── the reseed is free ────────────────────────────────────────────────────────


def test_seeds_are_still_served_after_the_wipe(tmp_hal0_home: str) -> None:
    """No reseed logic exists, and none is needed: the catalog is virtual."""
    from hal0.config.loader import load_profiles_config

    _write_hal0_toml(1)
    _write_profiles(_OPERATOR_PROFILES)
    reset_profile_catalog(approved=True)

    cfg = load_profiles_config()
    assert set(SEED_PROFILES).issubset(set(cfg.profile))
    assert "my-bench" not in cfg.profile


# ── what the operator is actually told (#1411) ────────────────────────────────
#
# Every pre-existing custom profile whose stored flags carry a hardware flag
# (-dev / --threads / -ngl) is rejected by the v1.0 hardware screen on PUT, so it
# cannot be edited or repaired through the UI. The wipe DELETES those profiles,
# which superficially "fixes" the un-editability by destruction. An operator who
# has been fighting an un-editable profile must not learn that from its absence,
# so both prompts have to say it out loud — and the backup has to be named as the
# only recourse, because it is.


def _install_sh() -> str:
    return (Path(__file__).resolve().parents[2] / "installer" / "install.sh").read_text(
        encoding="utf-8"
    )


def _update_commands() -> str:
    return (
        Path(__file__).resolve().parents[2] / "src" / "hal0" / "cli" / "update_commands.py"
    ).read_text(encoding="utf-8")


def test_installer_prompt_discloses_uneditable_profiles_are_deleted() -> None:
    text = _install_sh()
    assert "refuses to save" in text
    assert 'deleted,")' in text or "not repaired" in text
    assert "only way to get any of them back" in text


def test_cli_prompt_discloses_uneditable_profiles_are_deleted() -> None:
    text = _update_commands()
    assert "refuses to save" in text
    assert "#1411" in text
    assert "not repaired" in text
    assert "only way to get any of them back" in text


def test_installer_announces_the_unreadable_case_even_though_it_never_prompts() -> None:
    """``needs_consent`` is False for an unparseable file, so it converges silently.

    Silently deleting a file the operator believes holds their work is
    indistinguishable from a bug, so the no-prompt path still has to narrate.
    """
    text = _install_sh()
    assert 'elif status["unreadable"]:' in text
    assert "does NOT parse" in text
    assert "/var/lib/hal0/backups/" in text


def test_the_uneditable_flags_named_in_the_prompt_are_the_ones_actually_screened() -> None:
    """Guard against the prompt naming flags the screen does not reject.

    A prompt that lists the wrong flags is worse than none — it teaches an
    operator to look for the wrong thing in their backup.
    """
    from hal0.api.routes.profiles import _screen_profile_flags

    for flag, value in (("-dev", "Vulkan0"), ("--threads", "8"), ("-ngl", "99")):
        with pytest.raises(Exception) as ei:
            _screen_profile_flags(f"-fa on {flag} {value}")
        assert "hardware" in str(ei.value).lower() or "slot" in str(ei.value).lower()

    # …and a genuinely tuning-only profile still passes, so the test is not
    # merely asserting that everything raises.
    _screen_profile_flags("-fa on -ctk q8_0 -b 2048 --no-mmap")


# ── contract coexistence ──────────────────────────────────────────────────────


def test_prune_contract_still_applies_after_reset(tmp_hal0_home: str) -> None:
    """Post-v2 the conservative prune/rescue contract is the only one that runs.

    ensure_seed_profiles keeps rescuing divergent seed-named entries to
    ``<name>-custom``; the destructive reset is permanently off.
    """
    _write_hal0_toml(PROFILE_CATALOG_SCHEMA_VERSION)
    _write_profiles(
        '[profile.embedding]\nflags = "--embedding -ub 4096"\nmtp = false\n'
        '\n[profile.my-bench]\nflags = "-fa on -b 2048"\n'
    )

    assert reset_profile_catalog(approved=True)["outcome"] == "already_reset"
    assert ensure_seed_profiles() == 1

    on_disk = tomllib.loads(profiles_toml().read_text(encoding="utf-8"))["profile"]
    assert "embedding" not in on_disk
    assert on_disk["embedding-custom"]["flags"] == "--embedding -ub 4096"
    assert on_disk["my-bench"]["flags"] == "-fa on -b 2048"
