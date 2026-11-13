"""Tests for ``hal0 memory migrate platform`` (ADR-0052)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_migrate_commands as mgc

runner = CliRunner()
TARGET = "Hal0::Platform"
FEATURES = {"document_export_api": True, "document_import_api": True}


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mgc, "_api_unreachable", lambda _url: False)
    calls: dict[str, list[Any]] = {"get": [], "get_bytes": [], "post": []}
    state: dict[str, Any] = {
        "features": dict(FEATURES),
        "banks": [
            {"bank_id": "shared", "fact_count": 100},
            {"bank_id": "agents", "fact_count": 10},
            {"bank_id": "private__hermes", "fact_count": 12},
        ],
        "operation": {"status": "completed", "result_metadata": {"facts_imported": 1}},
        # fact count the target reports once the clone + fold have run
        "target_after": 110,
    }

    def fake_get(path: str, **kw: Any) -> Any:
        calls["get"].append(path)
        if path == "/api/memory/engine":
            return {"version": "1.1.0", "features": state["features"]}
        if path == "/api/memory/banks":
            banks = list(state["banks"])
            if calls["post"]:
                banks.append({"bank_id": TARGET, "fact_count": state["target_after"]})
            return {"banks": banks}
        if "/operations/" in path:
            return {"operation_id": "op-1", **state["operation"]}
        raise AssertionError(f"unexpected GET {path}")

    def fake_get_bytes(path: str, **kw: Any) -> tuple[bytes, str]:
        calls["get_bytes"].append((path, kw.get("params")))
        return b"PK\x03\x04fake", "application/zip"

    def fake_post(path: str, **kw: Any) -> Any:
        calls["post"].append((path, kw.get("params")))
        return {"operation_id": "op-1"}

    monkeypatch.setattr(mgc, "api_get", fake_get)
    monkeypatch.setattr(mgc, "api_get_bytes", fake_get_bytes)
    monkeypatch.setattr(mgc, "api_post", fake_post)
    return {"calls": calls, "state": state}


def _run(*args: str):
    return runner.invoke(mgc.app, ["platform", "--target", TARGET, *args])


def test_dry_run_writes_nothing_and_reports_plan(stub_api) -> None:
    result = _run("--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is False
    assert payload["source"] == "shared"
    assert payload["target"] == TARGET
    assert payload["source_facts"] == 100
    assert payload["agents_bank"] == "agents"
    assert payload["agents_facts"] == 10
    assert not stub_api["calls"]["post"]
    assert not stub_api["calls"]["get_bytes"]


def test_target_equal_source_is_refused(stub_api) -> None:
    result = runner.invoke(mgc.app, ["platform", "--target", "shared"])
    assert result.exit_code != 0
    assert not stub_api["calls"]["get"]


def test_unknown_source_is_refused(stub_api) -> None:
    result = _run("--source", "nope")
    assert result.exit_code != 0


def test_existing_target_is_refused_without_skip_clone(stub_api) -> None:
    stub_api["state"]["banks"].append({"bank_id": TARGET, "fact_count": 3})
    result = _run("--apply")
    assert result.exit_code != 0
    assert "--skip-clone" in result.output
    assert not stub_api["calls"]["post"]


def test_apply_refuses_without_export_import_flags(stub_api) -> None:
    stub_api["state"]["features"] = {"document_export_api": True}
    result = _run("--apply")
    assert result.exit_code != 0
    assert not stub_api["calls"]["post"]


def test_apply_clones_then_folds_agents_and_verifies(stub_api) -> None:
    result = _run("--apply", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["verified"] is True
    assert payload["target_facts_after"] == 110
    assert payload["expected_facts_at_least"] == 110

    clone_path, clone_params = stub_api["calls"]["post"][0]
    assert clone_path == "/api/memory/banks/shared/clone"
    assert clone_params == {"target_bank_id": TARGET, "include_bank_config": "true"}
    # the clone op is recorded against the SOURCE bank
    assert "/api/memory/banks/shared/operations/op-1" in stub_api["calls"]["get"]

    export_path, _ = stub_api["calls"]["get_bytes"][0]
    assert export_path == "/api/memory/banks/agents/document-transfer"
    fold_path, fold_params = stub_api["calls"]["post"][1]
    assert fold_path == f"/api/memory/banks/{TARGET}/document-transfer"
    assert fold_params == {"on_conflict": "skip"}
    # only the clone + the fold write; nothing is ever deleted
    assert len(stub_api["calls"]["post"]) == 2


def test_apply_without_agents_bank_only_clones(stub_api) -> None:
    stub_api["state"]["banks"] = [b for b in stub_api["state"]["banks"] if b["bank_id"] != "agents"]
    stub_api["state"]["target_after"] = 100
    result = _run("--apply", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["agents_bank"] is None
    assert payload["verified"] is True
    assert not stub_api["calls"]["get_bytes"]
    assert len(stub_api["calls"]["post"]) == 1


def test_apply_exits_nonzero_when_target_is_short(stub_api) -> None:
    stub_api["state"]["target_after"] = 99
    result = _run("--apply", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["verified"] is False


def test_skip_bank_config_flag_is_forwarded(stub_api) -> None:
    result = _run("--apply", "--skip-bank-config", "--json")
    assert result.exit_code == 0, result.output
    assert stub_api["calls"]["post"][0][1]["include_bank_config"] == "false"


def test_failed_clone_operation_stops_before_fold(stub_api) -> None:
    stub_api["state"]["operation"] = {"status": "failed", "error_message": "boom"}
    result = _run("--apply")
    assert result.exit_code != 0
    assert not stub_api["calls"]["get_bytes"]
    assert len(stub_api["calls"]["post"]) == 1


def test_skip_clone_folds_and_verifies_an_existing_target(stub_api) -> None:
    stub_api["state"]["banks"].append({"bank_id": TARGET, "fact_count": 100})
    stub_api["state"]["target_after"] = 110
    # fake_get only appends the target after a POST; it is already present here,
    # so the post-fold read returns the pre-seeded count — raise it to match.
    stub_api["state"]["banks"][-1]["fact_count"] = 110
    result = _run("--apply", "--skip-clone", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["clone"] is False
    paths = [p for p, _ in stub_api["calls"]["post"]]
    assert all("/clone" not in p for p in paths)


# ── the default target (ADR-0058 decision 7) ───────────────────────────────────


def _config(monkeypatch: pytest.MonkeyPatch, **raw: Any) -> None:
    from types import SimpleNamespace

    from hal0.config import loader
    from hal0.config.schema import MemoryConfig

    memory = MemoryConfig.model_validate(raw)
    monkeypatch.setattr(loader, "load_hal0_config", lambda: SimpleNamespace(memory=memory))


@pytest.mark.parametrize("raw", [{}, {"bank": "hal0:shared"}])
def test_the_two_bank_default_is_never_a_target(stub_api, monkeypatch, raw) -> None:
    """The shipped default is the two-bank layout's bank, which a legacy
    store reaches through the fold-in, not a clone."""
    _config(monkeypatch, **raw)
    result = runner.invoke(mgc.app, ["platform"])
    assert result.exit_code != 0
    assert "--target" in result.output
    assert not stub_api["calls"]["get"]


def test_an_operator_set_bank_is_the_default_target(stub_api, monkeypatch) -> None:
    _config(monkeypatch, bank=TARGET)
    result = runner.invoke(mgc.app, ["platform", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["target"] == TARGET


@pytest.mark.parametrize("target", ["hal0:shared", "hal0:private"])
def test_a_two_bank_name_is_refused_as_a_target(stub_api, target) -> None:
    result = runner.invoke(mgc.app, ["platform", "--target", target])
    assert result.exit_code != 0
    assert "two-bank layout" in result.output
    assert not stub_api["calls"]["get"]


def test_migrate_unify_is_gone() -> None:
    result = runner.invoke(mgc.app, ["unify", "--source", "private__hermes"])
    assert result.exit_code != 0
    assert [c.name for c in mgc.app.registered_commands] == ["platform", "fold-in"]
