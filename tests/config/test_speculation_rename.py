"""ADR-0031 §7: the one-shot ``mtp`` -> ``speculation`` profile rename."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.migrations import speculation_rename

_OLD = """\
[profile.quiet]
flags = "-fa on"
mtp = "off"

[profile.loud]
flags = "-b 64"
mtp = "auto"

[profile.plain]
flags = "-b 32"
"""


def _write(tmp_path: Path, text: str = _OLD) -> Path:
    path = tmp_path / "profiles.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _profiles(path: Path) -> dict[str, dict[str, object]]:
    return tomllib.loads(path.read_text(encoding="utf-8"))["profile"]


def test_convert_rewrites_mtp_off_to_speculation_off_with_backup(tmp_path: Path) -> None:
    path = _write(tmp_path)
    report = speculation_rename.convert(path, dry_run=False)
    prof = _profiles(path)
    assert prof["quiet"] == {"flags": "-fa on", "speculation": "off"}
    assert report.changed == ["loud", "quiet"]
    assert report.backup is not None
    assert report.backup.parent == path.parent
    assert report.backup.name.startswith("profiles.toml.bak-")
    assert report.backup.read_text(encoding="utf-8") == _OLD


def test_convert_drops_mtp_auto(tmp_path: Path) -> None:
    path = _write(tmp_path)
    speculation_rename.convert(path, dry_run=False)
    prof = _profiles(path)
    assert prof["loud"] == {"flags": "-b 64"}
    assert prof["plain"] == {"flags": "-b 32"}


def test_convert_is_idempotent(tmp_path: Path) -> None:
    path = _write(tmp_path)
    speculation_rename.convert(path, dry_run=False)
    after_first = path.read_text(encoding="utf-8")
    report = speculation_rename.convert(path, dry_run=False)
    assert report.changed == [] and report.backup is None
    assert path.read_text(encoding="utf-8") == after_first
    assert len(list(tmp_path.glob("profiles.toml.bak-*"))) == 1


def test_convert_dry_run_touches_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path)
    report = speculation_rename.convert(path, dry_run=True)
    assert report.changed == ["loud", "quiet"]
    assert report.backup is None
    assert path.read_text(encoding="utf-8") == _OLD
    assert list(tmp_path.glob("profiles.toml.bak-*")) == []


def test_convert_missing_file_is_a_no_op(tmp_path: Path) -> None:
    report = speculation_rename.convert(tmp_path / "profiles.toml", dry_run=False)
    assert report.changed == [] and report.backup is None


def test_convert_keeps_an_existing_speculation_and_drops_the_stale_mtp(tmp_path: Path) -> None:
    path = _write(tmp_path, '[profile.both]\nmtp = "off"\nspeculation = "ngram"\n')
    speculation_rename.convert(path, dry_run=False)
    assert _profiles(path)["both"] == {"speculation": "ngram"}


def test_convert_leaves_old_shape_boolean_mtp_to_the_profile_centric_converter(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, '[profile.legacy]\nflags = "-b 1"\nmtp = false\n')
    report = speculation_rename.convert(path, dry_run=False)
    assert report.changed == []
    assert _profiles(path)["legacy"]["mtp"] is False


def test_convert_leaves_an_unrecognised_mtp_for_the_schema_to_quarantine(tmp_path: Path) -> None:
    path = _write(tmp_path, '[profile.odd]\nflags = "-b 1"\nmtp = "maybe"\n')
    report = speculation_rename.convert(path, dry_run=False)
    assert report.changed == [] and report.backup is None
    assert _profiles(path)["odd"]["mtp"] == "maybe"


def test_convert_names_an_unreadable_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "[profile.broken\nnot toml")
    with pytest.raises(speculation_rename.SpeculationRenameError) as exc:
        speculation_rename.convert(path, dry_run=False)
    assert exc.value.code == "profiles.conversion_failed"
    assert path.read_text(encoding="utf-8") == "[profile.broken\nnot toml"


# ── --spec-type in a profile's flags (ADR-0031 §3: hal0 owns it now) ────────


def test_convert_turns_a_spec_type_ngram_flag_into_speculation_ngram(tmp_path: Path) -> None:
    path = _write(tmp_path, '[profile.ng]\nflags = "-fa on --spec-type ngram-mod -b 64"\n')
    report = speculation_rename.convert(path, dry_run=False)
    assert _profiles(path)["ng"] == {"flags": "-fa on -b 64", "speculation": "ngram"}
    assert report.changed == ["ng"]
    assert report.backup is not None


def test_convert_ngram_flag_wins_over_a_renamed_mtp_off(tmp_path: Path) -> None:
    """The flag is what launched: ``mtp = "off"`` + ``--spec-type ngram-mod`` ran n-gram."""
    path = _write(tmp_path, '[profile.ng]\nflags = "--spec-type=ngram-simple"\nmtp = "off"\n')
    speculation_rename.convert(path, dry_run=False)
    assert _profiles(path)["ng"] == {"flags": "", "speculation": "ngram"}


def test_convert_drops_a_spec_type_draft_mtp_flag(tmp_path: Path) -> None:
    path = _write(
        tmp_path, '[profile.m]\nflags = "--spec-type draft-mtp --spec-draft-n-max 8 -fa on"\n'
    )
    report = speculation_rename.convert(path, dry_run=False)
    assert _profiles(path)["m"] == {"flags": "--spec-draft-n-max 8 -fa on"}
    assert report.changed == ["m"]


def test_convert_drops_any_other_spec_type_flag_and_logs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        speculation_rename.log, "warning", lambda event, **kw: events.append((event, kw))
    )
    path = _write(tmp_path, '[profile.x]\nflags = "-b 32 --spec_type draft-eagle3"\n')
    report = speculation_rename.convert(path, dry_run=False)
    assert _profiles(path)["x"] == {"flags": "-b 32"}
    assert report.changed == ["x"]
    assert events == [
        ("migrate.speculation_rename.spec_type_dropped", {"profile": "x", "value": "draft-eagle3"})
    ]


def test_convert_leaves_flags_without_spec_type_alone(tmp_path: Path) -> None:
    text = '[profile.clean]\nflags = "-fa on --spec-draft-n-max 8"\n\n[stack.s]\nflags = "--spec-type ngram-mod"\n'
    path = _write(tmp_path, text)
    report = speculation_rename.convert(path, dry_run=False)
    assert report.changed == [] and report.backup is None
    assert path.read_text(encoding="utf-8") == text
