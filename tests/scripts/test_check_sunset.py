from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_sunset


def _configure_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str,
    source: str = "",
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "example.py").write_text(source, encoding="utf-8")
    baseline = tmp_path / "scar_baseline.txt"
    baseline.write_text("0\n", encoding="utf-8")

    monkeypatch.setattr(check_sunset, "ROOT", tmp_path)
    monkeypatch.setattr(check_sunset, "SRC", src)
    monkeypatch.setattr(check_sunset, "BASELINE", baseline)


def test_catalog_deprecation_status_is_not_a_scar_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0-alpha.0",
        source="deprecated = True\n",
    )

    assert check_sunset.scar_count() == 1

    example = tmp_path / "src" / "example.py"
    example.write_text("", encoding="utf-8")
    lifecycle = tmp_path / "src" / "hal0" / "lifecycle"
    lifecycle.mkdir(parents=True)
    status_file = lifecycle / "types.py"
    status_file.write_text("deprecated: bool = False\n", encoding="utf-8")

    assert check_sunset.scar_count() == 0

    status_file.write_text("deprecated: bool = False  # legacy shim\n", encoding="utf-8")
    assert check_sunset.scar_count() == 1

    status_file.write_text(
        "deprecated: bool = False  # DEPRECATED remove this branch\n",
        encoding="utf-8",
    )
    assert check_sunset.scar_count() == 1

    status_file.write_text("", encoding="utf-8")
    catalog_file = lifecycle / "catalog.py"
    catalog_file.write_text("if runner.deprecated:\n    pass\n", encoding="utf-8")
    assert check_sunset.scar_count() == 0

    catalog_file.write_text(
        "if runner.deprecated:  # ordinary deprecated branch\n    pass\n",
        encoding="utf-8",
    )
    assert check_sunset.scar_count() == 1


def test_prerelease_does_not_trigger_same_ga_sunset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0-alpha.0",
        source="# HAL0-SUNSET: v1.0.0\n",
    )

    cur = check_sunset.current_version()
    assert check_sunset.overdue_markers(cur) == []


def test_ga_triggers_same_version_sunset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0",
        source="# HAL0-SUNSET: v1.0.0\n",
    )

    cur = check_sunset.current_version()
    assert len(check_sunset.overdue_markers(cur)) == 1


def test_prerelease_still_triggers_earlier_ga_sunset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0-alpha.0",
        source="# HAL0-SUNSET: v0.9.0\n",
    )

    cur = check_sunset.current_version()
    assert len(check_sunset.overdue_markers(cur)) == 1


def test_main_reports_full_prerelease_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_project(tmp_path, monkeypatch, version="1.0.0-alpha.0")

    assert check_sunset.main([]) == 0
    assert "current v1.0.0-alpha.0" in capsys.readouterr().out


def test_main_overdue_branch_reports_full_prerelease_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0-alpha.0",
        source="# HAL0-SUNSET: v0.9.0\n",
    )

    assert check_sunset.main([]) == 1
    assert "OVERDUE HAL0-SUNSET shims (current v1.0.0-alpha.0)" in capsys.readouterr().out
