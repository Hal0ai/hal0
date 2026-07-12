"""Tests for ``hal0 memory migrate unify``.

The ``migrate`` default callback (the Hindsight<->Honcho ``--from/--to``
engine migration) is covered in tests/cli/test_memory_provider_commands.py.
This module pins the ``unify`` subcommand: its ``--apply`` path gates on
``/version``'s ``features.document_export_api``/``document_import_api`` flags
(the live Hindsight instance here is still 0.7.2, which has neither) — so
these tests pin that ``--apply`` refuses when those flags are absent/false,
and exercise the full export→import→poll→retag flow when they're present.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_migrate_commands as mgc

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mgc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": [], "get_bytes": [], "post": [], "patch": []}
    state: dict[str, Any] = {
        "version": "0.7.2",
        "features": {},
        "banks": [
            {"bank_id": "shared", "fact_count": 100, "last_document_at": "t0"},
            {"bank_id": "private__hermes", "fact_count": 12, "last_document_at": "t1"},
            {"bank_id": "private__pi-coder", "fact_count": 7, "last_document_at": None},
        ],
        # documents-in-target-bank state, mutated by the fake import so the
        # before/after diff in _document_ids has something to find.
        "target_documents": [{"id": "d1", "tags": ["old"]}],
        "operation": {"status": "completed", "result_metadata": {"documents_imported": 1}},
        "doc_tags": {"d2": ["existing"]},
    }

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append((path, kw.get("params")))
        if path == "/api/memory/engine":
            return {"version": state["version"], "features": state["features"]}
        if path == "/api/memory/banks":
            return {"banks": state["banks"]}
        if path.endswith("/documents") and "/documents/" not in path:
            return {"documents": list(state["target_documents"])}
        if path.endswith("/operations/op-1"):
            return {"operation_id": "op-1", **state["operation"]}
        if "/documents/" in path:
            doc_id = path.rsplit("/", 1)[-1]
            return {"id": doc_id, "tags": state["doc_tags"].get(doc_id, [])}
        raise AssertionError(f"unexpected GET {path}")

    def fake_get_bytes(path: str, **kw: Any) -> tuple[bytes, str]:
        calls["get_bytes"].append((path, kw.get("params")))
        return b"PK\x03\x04fake-zip", "application/zip"

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append((path, kw.get("params"), bool(kw.get("files"))))
        # Simulate the import landing a new document so the before/after
        # diff in _document_ids has something to retag.
        state["target_documents"].append({"id": "d2", "tags": []})
        return {"operation_id": "op-1", "status": "queued"}

    def fake_patch(path: str, **kw: Any) -> Any:
        calls["patch"].append((path, kw.get("json")))
        return {"ok": True}

    monkeypatch.setattr(mgc, "api_get", fake_get)
    monkeypatch.setattr(mgc, "api_get_bytes", fake_get_bytes)
    monkeypatch.setattr(mgc, "api_post", fake_post)
    monkeypatch.setattr(mgc, "api_patch", fake_patch)
    return {"calls": calls, "state": state}


# ── ``hal0 memory migrate unify`` ───────────────────────────────────────────


def test_unify_requires_source(stub_api) -> None:
    result = runner.invoke(mgc.app, ["unify", "--target", "shared"])
    assert result.exit_code != 0


def test_unify_rejects_target_in_source(stub_api) -> None:
    result = runner.invoke(mgc.app, ["unify", "--source", "shared", "--target", "shared"])
    assert result.exit_code != 0


def test_unify_rejects_unknown_source(stub_api) -> None:
    result = runner.invoke(mgc.app, ["unify", "--source", "does-not-exist"])
    assert result.exit_code != 0


def test_unify_dry_run_plan_derives_agent_and_visibility_tags(stub_api) -> None:
    result = runner.invoke(
        mgc.app,
        ["unify", "--source", "private__hermes", "--source", "private__pi-coder", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["target"] == "shared"
    by_source = {s["source"]: s for s in payload["sources"]}
    assert by_source["private__hermes"]["fact_count"] == 12
    assert set(by_source["private__hermes"]["tags_to_add"]) == {
        "agent:hermes",
        "visibility:private",
    }
    assert set(by_source["private__pi-coder"]["tags_to_add"]) == {
        "agent:pi-coder",
        "visibility:private",
    }


def test_unify_dry_run_add_tag_appends_to_derived_tags(stub_api) -> None:
    result = runner.invoke(
        mgc.app, ["unify", "--source", "private__hermes", "--add-tag", "batch:2026-07", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    tags = payload["sources"][0]["tags_to_add"]
    assert "batch:2026-07" in tags
    assert "agent:hermes" in tags


def test_unify_dry_run_non_private_source_gets_only_add_tag(stub_api) -> None:
    result = runner.invoke(mgc.app, ["unify", "--source", "shared", "--target", "agents", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sources"][0]["tags_to_add"] == []


def test_unify_apply_refuses_without_feature_flags(stub_api) -> None:
    # 0.7.2 today: /version reports no document_export_api/document_import_api.
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--apply"])
    assert result.exit_code != 0
    assert "document_export_api" in result.output or "document-transfer" in result.output
    assert not stub_api["calls"]["get_bytes"]
    assert not stub_api["calls"]["post"]


def test_unify_apply_refuses_with_only_one_flag(stub_api) -> None:
    stub_api["state"]["features"] = {"document_export_api": True, "document_import_api": False}
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--apply"])
    assert result.exit_code != 0
    assert not stub_api["calls"]["get_bytes"]


def test_unify_apply_rejects_invalid_on_conflict_before_any_call(stub_api) -> None:
    result = runner.invoke(
        mgc.app, ["unify", "--source", "private__hermes", "--apply", "--on-conflict", "yolo"]
    )
    assert result.exit_code != 0
    assert not stub_api["calls"]["get"]


def test_unify_apply_success_exports_imports_polls_and_retags(stub_api) -> None:
    stub_api["state"]["features"] = {"document_export_api": True, "document_import_api": True}
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--apply", "--json"])
    assert result.exit_code == 0, result.output
    # Progress/warning chatter goes to stderr (see _progress in
    # memory_migrate_commands.py) so --json's stdout stays pure JSON.
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["on_conflict"] == "skip"
    assert payload["include_observations"] is True
    tr = payload["transfers"][0]
    assert tr["source"] == "private__hermes"
    assert tr["operation_id"] == "op-1"
    assert tr["result_metadata"] == {"documents_imported": 1}
    assert set(tr["tags_applied"]) == {"agent:hermes", "visibility:private"}
    assert tr["retagged_documents"] == {"d2": "ok"}

    # export hit the source bank with include_observations=true (default)
    export_path, export_params = stub_api["calls"]["get_bytes"][0]
    assert export_path == "/api/memory/banks/private__hermes/document-transfer"
    assert export_params == {"include_observations": "true"}

    # import posted a multipart file to the target bank with on_conflict=skip
    import_path, import_params, had_files = stub_api["calls"]["post"][0]
    assert import_path == "/api/memory/banks/shared/document-transfer"
    assert import_params == {"on_conflict": "skip"}
    assert had_files

    # retag: read then full-replace-merge d2's tags (existing + derived tags)
    patch_path, patch_body = stub_api["calls"]["patch"][0]
    assert patch_path == "/api/memory/banks/shared/documents/d2"
    assert set(patch_body["tags"]) == {"existing", "agent:hermes", "visibility:private"}


def test_unify_apply_on_conflict_and_skip_observations_thread_through(stub_api) -> None:
    stub_api["state"]["features"] = {"document_export_api": True, "document_import_api": True}
    result = runner.invoke(
        mgc.app,
        [
            "unify",
            "--source",
            "private__hermes",
            "--apply",
            "--on-conflict",
            "replace",
            "--skip-observations",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    _export_path, export_params = stub_api["calls"]["get_bytes"][0]
    assert export_params == {"include_observations": "false"}
    _import_path, import_params, _ = stub_api["calls"]["post"][0]
    assert import_params == {"on_conflict": "replace"}


def test_unify_apply_no_tags_means_no_retag_calls(stub_api) -> None:
    stub_api["state"]["features"] = {"document_export_api": True, "document_import_api": True}
    result = runner.invoke(
        mgc.app, ["unify", "--source", "shared", "--target", "agents", "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert not stub_api["calls"]["patch"]


def test_unify_dry_run_never_writes(stub_api) -> None:
    # Dry-run only ever GETs engine + banks — no export/import/patch calls.
    runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--json"])
    paths = [p for p, _params in stub_api["calls"]["get"]]
    assert paths == ["/api/memory/engine", "/api/memory/banks"]
    assert not stub_api["calls"]["get_bytes"]
    assert not stub_api["calls"]["post"]
    assert not stub_api["calls"]["patch"]
