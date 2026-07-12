"""Tests for `hal0 config hardware [--refresh]` and the deprecated `hal0
probe` alias (CLI consolidation, 2026-07).

`probe` and `config hardware` hit the identical hardware-probe payload —
`probe` always forces a fresh POST, `config hardware` only GETs the cached
payload. `--refresh` on `config hardware` does what `probe` did; `probe`
becomes a thin hidden alias.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import config_commands

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_reachable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config_commands, "_api_unreachable", lambda _url: False)


def test_config_hardware_default_gets_cached_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        calls.append(path)
        return {"cpu_name": "demo-cpu"}

    monkeypatch.setattr(config_commands, "api_get", fake_get)
    result = runner.invoke(config_commands.app, ["hardware"])
    assert result.exit_code == 0, result.output
    assert calls == ["/api/hardware"]
    assert "demo-cpu" in result.output


def test_config_hardware_refresh_posts_a_fresh_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(path: str, **_kw: Any) -> dict[str, Any]:
        calls.append(path)
        return {"cpu_name": "fresh-cpu"}

    import hal0.cli._shared as shared

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(config_commands.app, ["hardware", "--refresh"])
    assert result.exit_code == 0, result.output
    assert calls == ["/api/hardware/probe"]
    assert "fresh-cpu" in result.output


def test_probe_is_hidden_deprecated_alias_for_config_hardware_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hal0.cli.main import app as main_app

    help_result = runner.invoke(main_app, ["--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "probe" not in help_result.output

    calls: list[str] = []

    def fake_post(path: str, **_kw: Any) -> dict[str, Any]:
        calls.append(path)
        return {"cpu_name": "fresh-cpu"}

    import hal0.cli._shared as shared

    monkeypatch.setattr(config_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(main_app, ["probe"])
    assert result.exit_code == 0, result.output
    assert calls == ["/api/hardware/probe"]
    assert "deprecat" in result.stderr.lower()
    assert "config hardware --refresh" in result.stderr.lower()
