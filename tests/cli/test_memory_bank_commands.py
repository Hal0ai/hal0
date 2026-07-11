"""Tests for ``hal0 memory bank {list,stats,profile,export,import,delete,consolidate}``.

Mocks the shared API helpers so the CLI runs offline; pins the request
shapes sent to hal0-api's ``/api/memory/banks/...`` passthrough.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_bank_commands as bc

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bc, "_api_unreachable", lambda _url: False)

    calls: dict[str, list[Any]] = {"get": [], "put": [], "patch": [], "post": [], "delete": []}
    responses: dict[str, Any] = {}

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append((path, kw))
        if path == "/api/memory/banks":
            return {
                "banks": [
                    {
                        "bank_id": "shared",
                        "name": "shared",
                        "fact_count": 42,
                        "last_document_at": "2026-07-01T00:00:00Z",
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            }
        if path.endswith("/stats"):
            return {
                "total_nodes": 10,
                "total_links": 5,
                "total_documents": 3,
                "pending_operations": 0,
                "failed_operations": 1,
                "operations_by_status": {"failed": 1, "completed": 9},
                "last_consolidated_at": None,
            }
        if path.endswith("/profile"):
            return responses.get(
                "profile",
                {
                    "bank_id": "shared",
                    "name": "shared",
                    "mission": "",
                    "disposition": {"skepticism": 3, "literalism": 3, "empathy": 3},
                },
            )
        return {}

    def fake_put(path: str, **kw: Any) -> Any:
        calls["put"].append((path, kw.get("json")))
        return {"bank_id": "shared", **(kw.get("json") or {})}

    def fake_patch(path: str, **kw: Any) -> Any:
        calls["patch"].append((path, kw.get("json")))
        return {"bank_id": "shared", "overrides": (kw.get("json") or {}).get("updates", {})}

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append((path, kw.get("json"), kw.get("params")))
        if path.endswith("/consolidate"):
            return {"operation_id": "op-1", "deduplicated": False}
        return {"imported": True}

    def fake_delete(path: str, **kw: Any) -> Any:
        calls["delete"].append((path, kw.get("params")))
        return {"deleted": True}

    monkeypatch.setattr(bc, "api_get", fake_get)
    monkeypatch.setattr(bc, "api_put", fake_put)
    monkeypatch.setattr(bc, "api_patch", fake_patch)
    monkeypatch.setattr(bc, "api_post", fake_post)
    monkeypatch.setattr(bc, "api_delete", fake_delete)
    calls["_responses"] = responses  # type: ignore[assignment]
    return calls


def test_bank_list_table(stub_api) -> None:
    result = runner.invoke(bc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "shared" in result.output
    assert "42" in result.output


def test_bank_list_json(stub_api) -> None:
    result = runner.invoke(bc.app, ["list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["banks"][0]["bank_id"] == "shared"


def test_bank_stats(stub_api) -> None:
    result = runner.invoke(bc.app, ["stats", "shared", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total_nodes"] == 10
    assert stub_api["get"][-1][0] == "/api/memory/banks/shared/stats"


def test_profile_get(stub_api) -> None:
    result = runner.invoke(bc.app, ["profile", "get", "shared", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["disposition"]["skepticism"] == 3


def test_profile_set_requires_at_least_one_field(stub_api) -> None:
    result = runner.invoke(bc.app, ["profile", "set", "shared"])
    assert result.exit_code != 0
    assert not stub_api["put"]
    assert not stub_api["patch"]


def test_profile_set_disposition_merges_and_puts_full_object(stub_api) -> None:
    result = runner.invoke(bc.app, ["profile", "set", "shared", "--skepticism", "5"])
    assert result.exit_code == 0, result.output
    path, payload = stub_api["put"][-1]
    assert path == "/api/memory/banks/shared/profile"
    # All three disposition fields must be present (upstream requires it),
    # only skepticism should differ from the fetched default.
    assert payload == {"disposition": {"skepticism": 5, "literalism": 3, "empathy": 3}}
    assert not stub_api["patch"]


def test_profile_set_mission_fields_go_through_config_patch(stub_api) -> None:
    result = runner.invoke(
        bc.app,
        [
            "profile",
            "set",
            "shared",
            "--retain-mission",
            "extract carefully",
            "--reflect-mission",
            "be terse",
        ],
    )
    assert result.exit_code == 0, result.output
    path, payload = stub_api["patch"][-1]
    assert path == "/api/memory/banks/shared/config"
    assert payload == {
        "updates": {"retain_mission": "extract carefully", "reflect_mission": "be terse"}
    }
    assert not stub_api["put"]


def test_profile_set_can_touch_both_resources_in_one_call(stub_api) -> None:
    result = runner.invoke(
        bc.app,
        ["profile", "set", "shared", "--empathy", "5", "--observations-mission", "notice patterns"],
    )
    assert result.exit_code == 0, result.output
    assert stub_api["put"], "disposition PUT expected"
    assert stub_api["patch"], "config PATCH expected"


def test_export_writes_file(stub_api, tmp_path) -> None:
    out_path = tmp_path / "export.json"
    result = runner.invoke(bc.app, ["export", "shared", "--out", str(out_path)])
    assert result.exit_code == 0, result.output
    assert out_path.exists()


def test_export_stdout(stub_api) -> None:
    result = runner.invoke(bc.app, ["export", "shared"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("{")


def test_import_rejects_missing_file(stub_api) -> None:
    result = runner.invoke(bc.app, ["import", "shared", "--file", "/nonexistent/path.json"])
    assert result.exit_code != 0
    assert not stub_api["post"]


def test_import_sends_dry_run_param(stub_api, tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"bank_id": "shared"}))
    result = runner.invoke(bc.app, ["import", "shared", "--file", str(manifest), "--dry-run"])
    assert result.exit_code == 0, result.output
    path, body, params = stub_api["post"][-1]
    assert path == "/api/memory/banks/shared/import"
    assert body == {"bank_id": "shared"}
    assert params == {"dry_run": "true"}


def test_delete_refuses_on_mismatch(stub_api) -> None:
    result = runner.invoke(bc.app, ["delete", "shared", "--confirm", "not-shared"])
    assert result.exit_code != 0
    assert not stub_api["delete"]


def test_delete_sends_confirm_on_match(stub_api) -> None:
    result = runner.invoke(bc.app, ["delete", "shared", "--confirm", "shared"])
    assert result.exit_code == 0, result.output
    path, params = stub_api["delete"][-1]
    assert path == "/api/memory/banks/shared"
    assert params == {"confirm": "shared"}


def test_consolidate_without_scope_sends_no_body(stub_api) -> None:
    result = runner.invoke(bc.app, ["consolidate", "shared", "--json"])
    assert result.exit_code == 0, result.output
    path, body, _params = stub_api["post"][-1]
    assert path == "/api/memory/banks/shared/consolidate"
    assert body is None


def test_consolidate_with_scope_builds_single_scope(stub_api) -> None:
    result = runner.invoke(
        bc.app, ["consolidate", "shared", "--scope", "topic:x", "--scope", "topic:y"]
    )
    assert result.exit_code == 0, result.output
    _path, body, _params = stub_api["post"][-1]
    assert body == {"observation_scopes": [["topic:x", "topic:y"]]}
