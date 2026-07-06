"""Tests for ``hal0 serve``'s bind-host resolution (#1099 WS-C).

Before this fix, ``hal0-api.service``'s ExecStart baked a hardcoded
``--host 0.0.0.0`` into the unit at install time while ``hal0 serve`` run
directly (no ``--host`` flag) defaulted to ``127.0.0.1`` — the two could
disagree about how far the API is reachable. ``HAL0_BIND_HOST`` is now the
one env var both sides read; these tests lock ``hal0 serve``'s side of
that contract (the CLI's ``--host`` Typer option resolves from the env
var when the flag isn't passed explicitly).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import main as cli_main

runner = CliRunner()


@pytest.fixture
def captured_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub ``uvicorn.run`` so the test never actually binds a socket."""
    captured: dict[str, Any] = {}

    def fake_run(app_path: str, *, host: str, port: int, reload: bool) -> None:
        captured["app_path"] = app_path
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    monkeypatch.setattr(cli_main.uvicorn, "run", fake_run)
    return captured


def test_serve_defaults_to_loopback_without_env(
    monkeypatch: pytest.MonkeyPatch,
    captured_uvicorn_run: dict[str, Any],
) -> None:
    """No --host flag, no HAL0_BIND_HOST env → historical 127.0.0.1 default."""
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    result = runner.invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert captured_uvicorn_run["host"] == "127.0.0.1"


def test_serve_reads_hal0_bind_host_env(
    monkeypatch: pytest.MonkeyPatch,
    captured_uvicorn_run: dict[str, Any],
) -> None:
    """HAL0_BIND_HOST — the SAME var the unit's EnvironmentFile sets —
    becomes the effective default when --host isn't passed explicitly.
    """
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    result = runner.invoke(cli_main.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert captured_uvicorn_run["host"] == "0.0.0.0"


def test_serve_explicit_flag_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
    captured_uvicorn_run: dict[str, Any],
) -> None:
    """An explicit --host always overrides HAL0_BIND_HOST."""
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    result = runner.invoke(cli_main.app, ["serve", "--host", "192.0.2.5"])
    assert result.exit_code == 0, result.output
    assert captured_uvicorn_run["host"] == "192.0.2.5"
