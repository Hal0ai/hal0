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
            # Engine 1.2.0 at its default metadata detail: trigger is null.
            rows = [{**m, "trigger": None} for m in models.values()]
            return {"items": rows, "total": len(models)}
        if path.startswith("/api/memory/banks/shared/mental-models/"):
            return dict(models[path.rsplit("/", 1)[1]])
        if path == "/api/memory/banks/shared/documents":
            return {
                "items": [{"id": "d", "tags": ["visibility:private", "agent:alice"]}],
                "total": 1,
            }
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


BOB = "/api/memory/banks/shared/mental-models/bob-own"


def test_mm_repair_dry_run_writes_nothing_and_names_each_model(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair"])
    assert result.exit_code == 0, result.output
    assert repair_api == []
    lines = [ln for ln in result.output.splitlines() if "bob-own" in ln]
    assert len(lines) == 1 and "owner bob" in lines[0]
    assert "would clear and set" in lines[0]
    assert "whole" not in result.output.lower()
    assert "--apply" in result.output


def test_mm_repair_apply_clears_flags_and_fully_refreshes(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 0, result.output
    assert repair_api == [
        ("POST", f"{BOB}/clear", None),
        ("PATCH", BOB, {"trigger": {"exclude_mental_models": True}}),
        ("POST", f"{BOB}/refresh", {"mode": "full"}),
    ]
    assert "cleared and set" in result.output and "full refresh queued op-1" in result.output
    # Idempotent: a second run has nothing to do.
    again = runner.invoke(mmc.app, ["repair", "--apply"])
    assert again.exit_code == 0, again.output
    assert len(repair_api) == 3
    assert "nothing to repair" in again.output.lower()


def test_mm_repair_no_refresh_still_clears(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--apply", "--no-refresh"])
    assert result.exit_code == 0, result.output
    assert [(m, p) for m, p, _j in repair_api] == [("POST", f"{BOB}/clear"), ("PATCH", BOB)]


def test_mm_repair_json(repair_api) -> None:
    result = runner.invoke(mmc.app, ["repair", "--bank", "shared", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["models"] == [
        {"bank": "shared", "id": "bob-own", "name": "Bob", "owner": "bob", "hidden": True}
    ]
    assert payload["errors"] == [] and payload["skipped"] == []


def test_mm_repair_failed_write_exits_nonzero(repair_api, monkeypatch) -> None:
    def broken_patch(path: str, **kw: Any) -> Any:
        raise mmc.CliApiError("busy", status=409)

    monkeypatch.setattr(mmc, "api_patch", broken_patch)
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 1, result.output
    assert "cleared, but setting the flag failed" in result.output and "busy" in result.output


def test_mm_repair_failed_refresh_says_flagged_and_cleared(repair_api, monkeypatch) -> None:
    real_post = mmc.api_post

    def no_refresh(path: str, **kw: Any) -> Any:
        if path.endswith("/refresh"):
            raise mmc.CliApiError("engine busy", status=503)
        return real_post(path, **kw)

    monkeypatch.setattr(mmc, "api_post", no_refresh)
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 1, result.output
    out = " ".join(result.output.split())
    assert "flagged and cleared, but the refresh failed" in out and "engine busy" in out


def test_mm_repair_reports_a_skipped_model(repair_api, monkeypatch) -> None:
    real_get = mmc.api_get

    def clashing_docs(path: str, **kw: Any) -> Any:
        if path.endswith("/documents"):
            return {
                "items": [{"id": "d9", "tags": ["visibility:private", "agent:alice", "agent:bob"]}],
                "total": 1,
            }
        return real_get(path, **kw)

    monkeypatch.setattr(mmc, "api_get", clashing_docs)
    result = runner.invoke(mmc.app, ["repair", "--apply"])
    assert result.exit_code == 0, result.output
    assert repair_api == []
    out = " ".join(result.output.split())
    assert "skipped shared bob-own owner bob" in out and "agent:bob" in out
