"""Tests for ``hal0 memory ops {list,retry}``.

Pins the cross-bank fan-out: with no ``--bank``, ``ops list``/``ops retry
--all-failed`` hit ``GET /api/memory/banks`` first, then one call per bank.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_ops_commands as oc

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(oc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": [], "post": []}

    ops_by_bank = {
        "shared": [
            {
                "id": "op-1",
                "task_type": "retain",
                "status": "failed",
                "items_count": 1,
                "created_at": "t1",
            },
            {
                "id": "op-2",
                "task_type": "retain",
                "status": "completed",
                "items_count": 1,
                "created_at": "t2",
            },
        ],
        "private__hermes": [
            {
                "id": "op-3",
                "task_type": "consolidation",
                "status": "failed",
                "items_count": 4,
                "created_at": "t3",
            },
        ],
    }

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append((path, kw.get("params")))
        if path == "/api/memory/banks":
            return {"banks": [{"bank_id": b} for b in ops_by_bank]}
        for bank, ops in ops_by_bank.items():
            if path == f"/api/memory/banks/{bank}/operations":
                params = kw.get("params") or {}
                filtered = (
                    [o for o in ops if o["status"] == "failed"]
                    if params.get("status") == "failed"
                    else ops
                )
                return {"bank_id": bank, "total": len(filtered), "operations": filtered}
        raise AssertionError(f"unexpected GET {path}")

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append(path)
        return {
            "success": True,
            "message": "queued for retry",
            "operation_id": path.rsplit("/", 2)[1],
        }

    monkeypatch.setattr(oc, "api_get", fake_get)
    monkeypatch.setattr(oc, "api_post", fake_post)
    return calls


def test_ops_list_single_bank(stub_api) -> None:
    result = runner.invoke(oc.app, ["list", "--bank", "shared", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["operations"]) == 2
    assert all(op["bank_id"] == "shared" for op in payload["operations"])


def test_ops_list_fans_out_across_banks(stub_api) -> None:
    result = runner.invoke(oc.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    banks = {op["bank_id"] for op in payload["operations"]}
    assert banks == {"shared", "private__hermes"}
    assert ("/api/memory/banks", None) in stub_api["get"]


def test_ops_list_failed_only_sets_status_param(stub_api) -> None:
    result = runner.invoke(oc.app, ["list", "--bank", "shared", "--failed", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["operations"]) == 1
    assert payload["operations"][0]["id"] == "op-1"


def test_ops_retry_requires_exactly_one_mode(stub_api) -> None:
    result = runner.invoke(oc.app, ["retry"])
    assert result.exit_code != 0
    result2 = runner.invoke(oc.app, ["retry", "--all-failed", "--id", "op-1", "--bank", "shared"])
    assert result2.exit_code != 0
    assert not stub_api["post"]


def test_ops_retry_by_id_requires_bank(stub_api) -> None:
    result = runner.invoke(oc.app, ["retry", "--id", "op-1"])
    assert result.exit_code != 0
    assert not stub_api["post"]


def test_ops_retry_by_id(stub_api) -> None:
    result = runner.invoke(oc.app, ["retry", "--bank", "shared", "--id", "op-1", "--json"])
    assert result.exit_code == 0, result.output
    assert stub_api["post"] == ["/api/memory/banks/shared/operations/op-1/retry"]


def test_ops_retry_all_failed_fans_out(stub_api) -> None:
    result = runner.invoke(oc.app, ["retry", "--all-failed", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    retried_ids = {r["operation_id"] for r in payload["retried"]}
    assert retried_ids == {"op-1", "op-3"}


def test_ops_retry_all_failed_scoped_to_bank(stub_api) -> None:
    result = runner.invoke(oc.app, ["retry", "--bank", "shared", "--all-failed", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {r["operation_id"] for r in payload["retried"]} == {"op-1"}
