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
        calls.setdefault("get_params", []).append(kw.get("params"))
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
    # The engine lists metadata only by default from 0.10.0 on.
    assert stub_api["get_params"] == [{"detail": "content"}]


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


# ── mm repair (#3576) ───────────────────────────────────────────────────────


@pytest.fixture
def repair_api(monkeypatch: pytest.MonkeyPatch):
    """The admin memory routes for ``mm repair``: one bank holding alice's
    private memory, bob's unflagged model, and a whole-bank model."""
    monkeypatch.setattr(mmc, "_api_unreachable", lambda _url: False)
    models = {
        "bob-own": {"id": "bob-own", "name": "Bob", "tags": ["agent:bob"], "trigger": {}},
        "whole": {"id": "whole", "name": "Whole", "tags": [], "trigger": {}},
    }
    writes: list[tuple[str, str, Any]] = []

    def fake_get(path: str, **kw: Any) -> Any:
        if path == "/api/memory/banks":
            return {"banks": [{"bank_id": "shared"}], "total": 1}
        if path == "/api/memory/banks/shared/mental-models":
            return {"items": [dict(m) for m in models.values()], "total": len(models)}
        if path == "/api/memory/banks/shared/documents":
            return {"items": [{"id": "d", "tags": ["visibility:private", "agent:alice"]}]}
        raise AssertionError(f"unexpected GET {path}")

    def fake_patch(path: str, *, json: Any = None, **kw: Any) -> Any:
        writes.append(("PATCH", path, json))
        mid = path.rsplit("/", 1)[1]
        models[mid]["trigger"] = {**models[mid].get("trigger", {}), **json["trigger"]}
        return models[mid]

    def fake_post(path: str, *, json: Any = None, **kw: Any) -> Any:
        writes.append(("POST", path, json))
        return {"operation_id": "op-1"}

    monkeypatch.setattr(mmc, "api_get", fake_get)
    monkeypatch.setattr(mmc, "api_patch", fake_patch)
    monkeypatch.setattr(mmc, "api_post", fake_post)
    return writes


def test_mm_repair_dry_run_writes_nothing_and_names_each_model(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair"])
    assert result.exit_code == 0, result.output
    assert repair_api == []
    lines = [ln for ln in result.output.splitlines() if "bob-own" in ln]
    assert len(lines) == 1 and "owner bob" in lines[0] and "would set" in lines[0]
    assert "whole" not in result.output.lower()
    assert "--apply" in result.output


def test_mm_repair_apply_flags_and_refreshes(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 0, result.output
    assert repair_api == [
        (
            "PATCH",
            "/api/memory/banks/shared/mental-models/bob-own",
            {"trigger": {"exclude_mental_models": True}},
        ),
        ("POST", "/api/memory/banks/shared/mental-models/bob-own/refresh", None),
    ]
    # Idempotent: a second run has nothing to do.
    again = runner.invoke(mmc.app, ["repair", "--apply"])
    assert again.exit_code == 0, again.output
    assert len(repair_api) == 2
    assert "nothing to repair" in again.output.lower()


def test_mm_repair_no_refresh(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--apply", "--no-refresh"])
    assert result.exit_code == 0, result.output
    assert [w[0] for w in repair_api] == ["PATCH"]


def test_mm_repair_json(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--bank", "shared", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["models"] == [
        {"bank": "shared", "id": "bob-own", "name": "Bob", "owner": "bob", "hidden": True}
    ]
    assert payload["errors"] == []


def test_mm_repair_failed_write_exits_nonzero(repair_api, monkeypatch) -> None:
    def broken_patch(path: str, **kw: Any) -> Any:
        raise mmc.CliApiError("busy", status=409)

    monkeypatch.setattr(mmc, "api_patch", broken_patch)
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 1, result.output
    assert "busy" in result.output
