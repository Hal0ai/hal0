"""``hal0 serve`` honours HAL0_BIND_HOST (WS-C network coherence).

The hal0-api systemd unit's ExecStart no longer passes --host; it relies
on serve reading HAL0_BIND_HOST from the EnvironmentFile. These tests pin
that env → bind-host contract (and the --host flag still overriding it)
so the unit and the CLI can never disagree on the bind address.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import main as cli_main

runner = CliRunner()


@pytest.fixture
def captured_bind(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub uvicorn.run so serve resolves the bind args without a server."""
    captured: dict[str, Any] = {}

    def _fake_run(_app: str, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_main.uvicorn, "run", _fake_run)
    return captured


def test_serve_defaults_to_loopback(
    captured_bind: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    result = runner.invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert captured_bind["host"] == "127.0.0.1"


def test_serve_reads_bind_host_env(
    captured_bind: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    result = runner.invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert captured_bind["host"] == "0.0.0.0"


def test_serve_flag_overrides_env(
    captured_bind: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    result = runner.invoke(cli_main.app, ["serve", "--host", "192.0.2.7"])
    assert result.exit_code == 0, result.output
    assert captured_bind["host"] == "192.0.2.7"
