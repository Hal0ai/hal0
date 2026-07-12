"""Tests for ``hal0 memory recall`` (debug recall via the ACL front door)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from hal0.cli import memory_recall_commands as rc

runner = CliRunner()

# recall_cmd is a bare function (registered onto memory_commands.app via
# app.command("recall")(recall_cmd)), so wrap it in its own Typer for
# isolated CLI-runner invocation.
_test_app = typer.Typer()
_test_app.command()(rc.recall_cmd)


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"post": []}

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append((path, kw.get("json")))
        return {
            "items": [{"type": "fact", "content": "the user likes tea", "tags": ["preference"]}]
        }

    monkeypatch.setattr(rc, "api_post", fake_post)
    return calls


def test_recall_sends_query(stub_api) -> None:
    result = runner.invoke(_test_app, ["tea preferences"])
    assert result.exit_code == 0, result.output
    path, body = stub_api["post"][-1]
    assert path == "/api/memory/recall"
    assert body["query"] == "tea preferences"
    assert body["max_tokens"] == 4096
    assert "dataset" not in body


def test_recall_bank_maps_to_dataset(stub_api) -> None:
    result = runner.invoke(_test_app, ["tea preferences", "--bank", "shared"])
    assert result.exit_code == 0, result.output
    _path, body = stub_api["post"][-1]
    assert body["dataset"] == "shared"


def test_recall_tags_and_types_and_max_tokens(stub_api) -> None:
    result = runner.invoke(
        _test_app,
        ["tea preferences", "--tags", "preference", "--types", "fact", "--max-tokens", "1024"],
    )
    assert result.exit_code == 0, result.output
    _path, body = stub_api["post"][-1]
    assert body["tags"] == ["preference"]
    assert body["types"] == ["fact"]
    assert body["max_tokens"] == 1024


def test_recall_json_output(stub_api) -> None:
    result = runner.invoke(_test_app, ["tea preferences", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["content"] == "the user likes tea"
