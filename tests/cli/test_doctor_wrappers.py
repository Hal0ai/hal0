"""Tests for `hal0 doctor wrappers` (#1844/#2019).

`hal0 update commit` refreshes the privileged sudo wrappers + PATH links
automatically on every FHS activation, but that path is refused outright on
an editable/dev install — exactly the shape `scripts/deploy.sh`'s dev-deploy
runs under. `doctor wrappers` exposes the same refresh so a dev-deploy has a
way to pick one up. Audit-only by default; `--fix` needs root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli.main import app

runner = CliRunner()


def test_audit_only_by_default_makes_no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.updater.updater._editable_install_path", lambda: "/opt/hal0")
    calls: list[str] = []
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_privileged_wrappers",
        lambda *a, **k: calls.append("wrappers") or {"refreshed": [], "errors": {}},
    )
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_path_links",
        lambda *a, **k: calls.append("links") or {},
    )

    result = runner.invoke(app, ["doctor", "wrappers"])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "/opt/hal0" in result.output


def test_fix_without_root_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.updater.updater._editable_install_path", lambda: "/opt/hal0")
    monkeypatch.setattr("hal0.cli.doctor_commands.os.geteuid", lambda: 1000)

    result = runner.invoke(app, ["doctor", "wrappers", "--fix"])

    assert result.exit_code == 1
    assert "needs root" in result.output


def test_fix_as_root_refreshes_wrappers_and_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editable_root = tmp_path / "opt-hal0"
    monkeypatch.setattr("hal0.updater.updater._editable_install_path", lambda: str(editable_root))
    monkeypatch.setattr("hal0.cli.doctor_commands.os.geteuid", lambda: 0)

    seen_targets: list[Path] = []
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_privileged_wrappers",
        lambda target, **k: (
            seen_targets.append(target)
            or {"refreshed": ["hal0-systemctl"], "sudoers_refreshed": [], "errors": {}}
        ),
    )
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_path_links",
        lambda target, **k: seen_targets.append(target) or {"hal0": "linked"},
    )

    result = runner.invoke(app, ["doctor", "wrappers", "--fix"])

    assert result.exit_code == 0, result.output
    assert seen_targets == [editable_root, editable_root]
    assert "hal0-systemctl" in result.output
    assert "hal0 PATH link refreshed" in result.output


def test_fix_reports_errors_with_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hal0.updater.updater._editable_install_path", lambda: str(tmp_path / "opt-hal0")
    )
    monkeypatch.setattr("hal0.cli.doctor_commands.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_privileged_wrappers",
        lambda *a, **k: {
            "refreshed": [],
            "sudoers_refreshed": [],
            "errors": {"hal0-systemctl": "permission denied"},
        },
    )
    monkeypatch.setattr(
        "hal0.updater.updater.refresh_path_links", lambda *a, **k: {"hal0": "failed"}
    )

    result = runner.invoke(app, ["doctor", "wrappers", "--fix"])

    assert result.exit_code == 1
    assert "permission denied" in result.output
    assert "PATH link refresh failed" in result.output
