"""`hal0 app install openwebui` — deferred install verb (issue #1102 / Q9).

Pins that the CLI verb delegates to the exact same wiring function the
install-time path (`apply_setup` → `install_extension`) uses, so "skip now,
install later" is behaviourally lossless.
"""

from __future__ import annotations

import pytest
import typer

import hal0.cli.app_commands as app_cmds
from hal0.install.orchestrate import ExtensionOutcome


def test_app_install_openwebui_delegates_to_install_openwebui(monkeypatch) -> None:
    calls = []

    def _fake_install_openwebui():
        calls.append("called")
        return ExtensionOutcome(ext_id="openwebui", installed=True)

    monkeypatch.setattr("hal0.install.extensions.install_openwebui", _fake_install_openwebui)

    app_cmds.app_install(name="openwebui")

    assert calls == ["called"]


def test_app_install_openwebui_success_does_not_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        "hal0.install.extensions.install_openwebui",
        lambda: ExtensionOutcome(ext_id="openwebui", installed=True),
    )
    # No exception / typer.Exit raised on the happy path.
    app_cmds.app_install(name="openwebui")


def test_app_install_openwebui_no_runtime_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(
        "hal0.install.extensions.install_openwebui",
        lambda: ExtensionOutcome(ext_id="openwebui", skipped="no_container_runtime"),
    )
    with pytest.raises(typer.Exit) as exc_info:
        app_cmds.app_install(name="openwebui")
    assert exc_info.value.exit_code == 1


def test_app_install_openwebui_hard_failure_dies(monkeypatch) -> None:
    monkeypatch.setattr(
        "hal0.install.extensions.install_openwebui",
        lambda: ExtensionOutcome(ext_id="openwebui", error="systemctl enable --now failed"),
    )
    with pytest.raises((SystemExit, typer.Exit)):
        app_cmds.app_install(name="openwebui")


def test_app_install_openwebui_slow_start_is_not_fatal(monkeypatch) -> None:
    """installed=True but the active-confirmation lagged — success, not error."""
    monkeypatch.setattr(
        "hal0.install.extensions.install_openwebui",
        lambda: ExtensionOutcome(ext_id="openwebui", installed=True, error="not_active_yet"),
    )
    app_cmds.app_install(name="openwebui")  # must not raise


def test_app_install_unknown_app_dies() -> None:
    with pytest.raises((SystemExit, typer.Exit)):
        app_cmds.app_install(name="comfyui")
