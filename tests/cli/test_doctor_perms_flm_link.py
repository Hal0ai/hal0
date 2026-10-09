"""#2446: ``hal0 doctor perms`` audits and repairs the FLM store link.

The installer runs ``doctor perms --fix --force`` before every service start,
so this is also the upgrade path for boxes whose FLM pulls already stranded in
``$HOME/.config/flm/models``. The filesystem logic lives in
``hal0.providers.flm`` (``tests/providers/test_flm_store_repair.py``); these
tests pin the command's wiring: drift is reported, ``--fix`` runs the repair as
root, a conflict fails the command, and nothing is written without ``--fix``.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import doctor_commands as dc
from hal0.install import perms as perms_mod
from hal0.providers import flm as flm_mod

_DRIFT = [
    {
        "path": "/var/lib/hal0/.config/flm/models",
        "label": "flm cache → FLM store link",
        "status": "drift",
        "detail": "real directory (1 entries): host pulls land here",
    }
]
_CLEAN = [
    {
        "path": "/var/lib/hal0/.config/flm/models",
        "label": "flm cache → FLM store link",
        "status": "ok",
        "detail": "links to /var/lib/hal0/models/flm/models",
    }
]


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Silence every other ``doctor perms`` surface; record FLM repair calls."""
    state: dict[str, Any] = {"audits": [_DRIFT, _CLEAN], "repairs": 0}
    monkeypatch.setattr(dc, "detect_editable_root", lambda start: None)
    monkeypatch.setattr(dc, "check_hermes_ownership", lambda **kw: [])
    monkeypatch.setattr(perms_mod, "plan", lambda table=None, **kw: perms_mod.OwnershipPlan(()))

    def _audit() -> list[dict[str, str]]:
        return state["audits"].pop(0) if len(state["audits"]) > 1 else state["audits"][0]

    def _repair() -> list[str]:
        state["repairs"] += 1
        return ["/var/lib/hal0/.config/flm/models → /var/lib/hal0/models/flm/models"]

    monkeypatch.setattr(flm_mod, "audit_host_flm_store_link", _audit)
    monkeypatch.setattr(flm_mod, "repair_host_flm_store_link", _repair)
    return state


def _run(*args: str) -> Any:
    return CliRunner().invoke(dc.app, ["perms", *args])


def test_audit_reports_drift_and_writes_nothing(isolated: dict[str, Any]) -> None:
    result = _run()
    assert result.exit_code == 1, result.output
    assert "FLM store link" in result.output
    assert "doctor perms --fix" in result.output
    assert isolated["repairs"] == 0


def test_fix_as_root_repairs_and_exits_clean(
    isolated: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dc.os, "geteuid", lambda: 0)
    result = _run("--fix", "--force")
    assert isolated["repairs"] == 1
    assert result.exit_code == 0, result.output
    assert "ownership clean" in result.output


def test_fix_without_root_refuses(
    isolated: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dc.os, "geteuid", lambda: 1000)
    result = _run("--fix", "--force")
    assert result.exit_code == 1
    assert "needs root" in result.output
    assert isolated["repairs"] == 0


def test_conflict_fails_the_command(
    isolated: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dc.os, "geteuid", lambda: 0)
    isolated["audits"] = [_DRIFT]

    def _conflict() -> list[str]:
        raise flm_mod.FLMStoreLinkError("both hold Gemma3-1B-NPU2. Nothing was deleted")

    monkeypatch.setattr(flm_mod, "repair_host_flm_store_link", _conflict)
    result = _run("--fix", "--force")
    assert result.exit_code == 1
    assert "Gemma3-1B-NPU2" in result.output


def test_json_carries_flm_drift(isolated: dict[str, Any]) -> None:
    result = _run("--json")
    assert result.exit_code == 1
    assert "/var/lib/hal0/.config/flm/models" in result.output
