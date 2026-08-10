"""``hal0 profile`` — thin CLI over /api/profiles (#1796).

There was no CLI surface for profiles at all before this file; ``list`` /
``show`` cover the read half of ``/api/profiles`` every slot TOML
references.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from hal0.cli import profile_commands

runner = CliRunner()


def _stub_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_commands, "_api_unreachable", lambda _url: False)


def test_list_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"name": "rocm", "device_class": "gpu", "backend": "rocm"}]
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_list_renders_table_with_names(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "name": "rocm",
            "device_class": "gpu",
            "backend": "rocm",
            "mtp": False,
            "intent": "MoE agents",
            "tps": 52.8,
            "used_by": ["primary"],
        },
        {
            "name": "cpu",
            "device_class": "cpu",
            "backend": None,
            "mtp": False,
            "intent": None,
            "tps": None,
            "used_by": [],
        },
    ]
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "rocm" in result.output
    assert "cpu" in result.output
    assert "primary" in result.output


def test_list_empty_shows_dim_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: [])

    result = runner.invoke(profile_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "No profiles available" in result.output


def test_show_json_emits_raw_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"name": "rocm", "device_class": "gpu", "used_by": ["primary"]}
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["show", "rocm", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_show_renders_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "name": "rocm",
        "device_class": "gpu",
        "backend": "rocm",
        "mtp": False,
        "intent": "MoE agents",
        "quant": "FP4",
        "tps": 52.8,
        "rtf": None,
        "used_by": ["primary"],
        "resolved_flags": "-fa on",
    }
    _stub_reachable(monkeypatch)
    monkeypatch.setattr(profile_commands, "api_get", lambda _p, **_k: payload)

    result = runner.invoke(profile_commands.app, ["show", "rocm"])
    assert result.exit_code == 0, result.output
    assert "rocm" in result.output
    assert "primary" in result.output
    assert "-fa on" in result.output
