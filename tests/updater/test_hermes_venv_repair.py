"""``repair_hermes_mcp_client`` — heal a drifted hermes venv on upgrade (#2102).

rc.12 shipped the fleet repair for #2090: ``hal0 update`` rewrites the poisoned
``X-hal0-Private`` header so an upgraded box gets its memory tools back. That
pass is YAML-only, and it is not the only way a box ends up with zero memory
tools. A venv that drifted off the vetted pin — no ``mcp`` package at all, the
residual population of #2021 — survives every upgrade untouched: the header is
correct, the client cannot import, and the agent runs on with no memory tools.

Measured on ct150 during the rc.12 lane: header quoted by the #2090 pass, and
``hermes mcp test hal0-memory`` still failed at 7181 ms with
"mcp.client.streamable_http is not available". The remedy existed
(``hal0 agent reprovision hermes --repair``) but nothing named it.

So the capability check belongs beside the header repair, in the sequence both
upgrade paths already converge on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp
from hal0.updater.updater import repair_hermes_mcp_client


def _venv(tmp_path: Path) -> Path:
    venv = tmp_path / "venvs" / "hermes"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
    return venv


def _probe(verdicts: list[dict[str, Any]], seen: list[Path]) -> Any:
    def _fake(venv_python: Path, url: str | None, **_kw: Any) -> dict[str, Any]:
        seen.append(venv_python)
        return verdicts.pop(0) if len(verdicts) > 1 else verdicts[0]

    return _fake


OK = {"ok": True, "stage": "import", "tools": [], "error": None}
BROKEN = {
    "ok": False,
    "stage": "import",
    "tools": [],
    "error": "ModuleNotFoundError: No module named 'mcp'",
}


def test_reinstalls_the_venv_when_its_mcp_client_cannot_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = _venv(tmp_path)
    installs: list[tuple[Path, Path]] = []
    probes: list[Path] = []

    def _install(target: Path, requirements: Path, **_kw: Any) -> None:
        installs.append((target, requirements))

    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN, OK], probes))
    monkeypatch.setattr(hp, "_install_venv", _install)

    repaired = repair_hermes_mcp_client(venv=venv, restart_gateway=False)

    assert repaired is True
    assert [target for target, _ in installs] == [venv]
    assert installs[0][1] == hp.HERMES_REQUIREMENTS
    # Probed before repairing, then again to prove the repair took.
    assert probes == [venv / "bin" / "python", venv / "bin" / "python"]


def test_a_working_client_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv = _venv(tmp_path)
    installs: list[Path] = []

    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([OK], []))
    monkeypatch.setattr(hp, "_install_venv", lambda target, _req, **_kw: installs.append(target))

    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False
    assert installs == []


def test_reports_without_installing_when_repair_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot-time safety net diagnoses; it must not pip-install at startup.

    ``check_outstanding_migrations`` runs on every ``hal0-api`` boot, including
    crash-restarts. A network install there would put an unbounded, failing
    pip run in the startup path of a box that is already unhealthy.
    """
    venv = _venv(tmp_path)
    installs: list[Path] = []

    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(hp, "_install_venv", lambda target, _req, **_kw: installs.append(target))

    assert repair_hermes_mcp_client(venv=venv, install=False, restart_gateway=False) is False
    assert installs == []


def test_absent_venv_is_a_quiet_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with no hermes install must not have its update disturbed."""
    probed: list[Path] = []
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], probed))

    assert repair_hermes_mcp_client(venv=tmp_path / "nope", restart_gateway=False) is False
    assert probed == []


def test_a_failed_reinstall_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pip can fail (no network, resolver conflict) — an update must survive it."""
    venv = _venv(tmp_path)

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("pip exploded")

    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(hp, "_install_venv", _boom)

    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False


def test_a_reinstall_that_does_not_heal_reports_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a re-probe proves the repair worked — pip exiting 0 does not."""
    venv = _venv(tmp_path)

    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(hp, "_install_venv", lambda *_a, **_kw: None)

    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False
