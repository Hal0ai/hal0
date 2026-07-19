"""Tests for ``hal0 ports`` (§5.2 R5 sync assessment) — PortAuthority CLI view.

``ports_cmd`` is registered as a bare function on the root app (like
``system-info``/``chat``), not a Typer sub-app — wrap it in a throwaway
Typer for CliRunner invocation, same pattern ``test_system_info.py`` uses
for its own bare-function commands.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from hal0.cli import ports_command

runner = CliRunner()


def _wrap() -> typer.Typer:
    app = typer.Typer()
    app.command()(ports_command.ports_cmd)
    return app


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        captured["path"] = path
        return {
            "pool": {"start": 8090, "end": 8099},
            "claims": [
                {"port": 8091, "owner": "slot:primary", "source": "slot-config"},
                {"port": 8092, "owner": "slot:embed", "source": "slot-runtime", "group": None},
            ],
            "conflicts": [],
            "next_free": 8093,
        }

    monkeypatch.setattr(ports_command, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(ports_command, "api_get", fake_get)
    return captured


def test_ports_json_output(captured: dict[str, Any]) -> None:
    app = _wrap()
    result = runner.invoke(app, ["--json"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/ports"
    assert "8091" in result.output


def test_ports_table_output(captured: dict[str, Any]) -> None:
    app = _wrap()
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert "8091" in result.output
    assert "No conflicts" in result.output


def test_ports_shows_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(_path: str, **_kw: Any) -> dict[str, Any]:
        return {
            "pool": {"start": 8090, "end": 8099},
            "claims": [
                {"port": 8091, "owner": "slot:primary", "source": "slot-config"},
                {"port": 8091, "owner": "listener:flm", "source": "listener"},
            ],
            "conflicts": [{"port": 8091, "owners": ["slot:primary", "listener:flm"]}],
            "next_free": 8092,
        }

    monkeypatch.setattr(ports_command, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(ports_command, "api_get", fake_get)
    app = _wrap()
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert "1 conflict" in result.output


def test_ports_is_registered_on_main_app() -> None:
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "ports" in result.output
