"""Tests for ``hal0 memory migrate`` (legacy default) and ``migrate unify``.

The legacy Cognee→Hindsight dry-run behaviour must be unchanged now that it
lives behind a Typer sub-app's default callback instead of a single
``@app.command("migrate")`` (see hal0/cli/memory_migrate_commands.py). The
``unify`` subcommand's ``--apply`` path is checked against a live Hindsight
0.7.2 instance's ``/openapi.json``, which has no cross-bank document
transfer endpoint — so this test pins that ``--apply`` refuses below
hindsight-api 0.8.0, and refuses even at/above 0.8.0 rather than fake a
migration through export/import (which drops the tags this command adds).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_migrate_commands as mgc

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mgc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": []}
    state: dict[str, Any] = {
        "version": "0.7.2",
        "banks": [
            {"bank_id": "shared", "fact_count": 100, "last_document_at": "t0"},
            {"bank_id": "private__hermes", "fact_count": 12, "last_document_at": "t1"},
            {"bank_id": "private__pi-coder", "fact_count": 7, "last_document_at": None},
        ],
    }

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append(path)
        if path == "/api/memory/engine":
            return {"version": state["version"]}
        if path == "/api/memory/banks":
            return {"banks": state["banks"]}
        raise AssertionError(f"unexpected GET {path}")

    monkeypatch.setattr(mgc, "api_get", fake_get)
    return {"calls": calls, "state": state}


# ── legacy ``hal0 memory migrate`` (Cognee dry-run) ─────────────────────────


def test_legacy_migrate_empty_store_is_noop(tmp_path) -> None:
    result = runner.invoke(mgc.app, ["--cognee-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"rows_total": 0, "rows_mapped": 0, "rows_unmapped": 0, "noop": True}


def test_legacy_migrate_reports_mapped_rows(tmp_path) -> None:
    sidecar = tmp_path / "hal0_memory_index.sqlite"
    conn = sqlite3.connect(sidecar)
    conn.execute("CREATE TABLE hal0_memory_items (id TEXT, dataset TEXT)")
    conn.execute("INSERT INTO hal0_memory_items VALUES ('a', 'shared')")
    conn.commit()
    conn.close()
    result = runner.invoke(mgc.app, ["--cognee-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_total"] == 1
    assert payload["noop"] is False


def test_legacy_migrate_apply_not_implemented(tmp_path) -> None:
    result = runner.invoke(mgc.app, ["--no-dry-run", "--cognee-dir", str(tmp_path)])
    assert result.exit_code != 0


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


def test_unify_apply_below_min_version_refuses(stub_api) -> None:
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--apply"])
    assert result.exit_code != 0
    assert "0.8.0" in result.output


def test_unify_apply_at_min_version_still_refuses_no_known_endpoint(stub_api) -> None:
    stub_api["state"]["version"] = "0.8.4"
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--apply"])
    assert result.exit_code != 0
    assert "transfer" in result.output.lower()


def test_unify_dry_run_never_calls_delete(stub_api) -> None:
    # No delete/write helper is even imported into this module — the only
    # network calls dry-run makes are the two GETs below.
    runner.invoke(mgc.app, ["unify", "--source", "private__hermes", "--json"])
    assert stub_api["calls"]["get"] == ["/api/memory/engine", "/api/memory/banks"]
