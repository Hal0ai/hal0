"""Tests for ``hal0 memory mm {list,refresh,history}``."""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_mm_commands as mmc

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mmc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": [], "post": []}

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append(path)
        if path.endswith("/mental-models"):
            return {"items": [{"id": "mm-1", "name": "user-prefs", "updated_at": "t1"}]}
        if path.endswith("/history"):
            return {
                "items": [
                    {"updated_at": "t1", "content": "v1"},
                    {"updated_at": "t2", "content": "v2"},
                ]
            }
        raise AssertionError(f"unexpected GET {path}")

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append(path)
        return {"operation_id": "op-9", "status": "queued"}

    monkeypatch.setattr(mmc, "api_get", fake_get)
    monkeypatch.setattr(mmc, "api_post", fake_post)
    return calls


def test_mm_list(stub_api) -> None:
    result = runner.invoke(mmc.app, ["list", "shared", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["items"][0]["id"] == "mm-1"
    assert stub_api["get"] == ["/api/memory/banks/shared/mental-models"]


def test_mm_refresh(stub_api) -> None:
    result = runner.invoke(mmc.app, ["refresh", "shared", "mm-1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation_id"] == "op-9"
    assert stub_api["post"] == ["/api/memory/banks/shared/mental-models/mm-1/refresh"]


def test_mm_history(stub_api) -> None:
    result = runner.invoke(mmc.app, ["history", "shared", "mm-1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["items"]) == 2
    assert stub_api["get"] == ["/api/memory/banks/shared/mental-models/mm-1/history"]
