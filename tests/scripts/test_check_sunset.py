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


def test_scar_ok_waiver_excludes_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0",
        source="x = 1  # DEPRECATED  # scar-ok: false positive, name of an upstream field\n",
    )

    assert check_sunset.scar_count() == (0, 1)


def test_bare_scar_ok_does_not_waive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0",
        source="x = 1  # DEPRECATED  # scar-ok\ny = 2  # DEPRECATED  # scar-ok:\n",
    )

    assert check_sunset.scar_count() == (2, 0)


def test_main_reports_waiver_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0",
        source=(
            "x = 1  # DEPRECATED  # scar-ok: false positive\n"
            "y = 2  # compat shim  # scar-ok: waived with reason\n"
        ),
    )

    assert check_sunset.main([]) == 0
    out = capsys.readouterr().out
    assert "scar waivers in effect: 2" in out
    assert "scar markers: 0 <= baseline 0" in out


def test_main_silent_when_no_waivers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_project(tmp_path, monkeypatch, version="1.0.0")

    assert check_sunset.main([]) == 0
    assert "scar waivers" not in capsys.readouterr().out


def test_update_baseline_uses_waiver_aware_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_project(
        tmp_path,
        monkeypatch,
        version="1.0.0",
        source="x = 1  # legacy field\ny = 2  # DEPRECATED  # scar-ok: false positive\n",
    )

    assert check_sunset.main(["--update-baseline"]) == 0
    assert "scar_baseline.txt <- 1" in capsys.readouterr().out
    assert (tmp_path / "scar_baseline.txt").read_text() == "1\n"
