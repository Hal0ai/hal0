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
upgrade paths already converge on. The cure is a fresh release of the live
ref through the one release builder (ADR-0032 decision 6), never an in-place
install; the update never waits for the builder lock.
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


from hal0.agents import hermes_release as rel  # noqa: E402


class _Ensure:
    """Stands in for ``hermes_release.ensure_release`` and records its kwargs."""

    def __init__(self, outcome: rel.ReleaseOutcome | None = None, exc: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome or rel.ReleaseOutcome(status="activated", release_id="r1")
        self.exc = exc

    def __call__(self, **kw: Any) -> rel.ReleaseOutcome:
        self.calls.append(kw)
        if self.exc is not None:
            raise self.exc
        return self.outcome


def test_rebuilds_a_release_when_its_mcp_client_cannot_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = _venv(tmp_path)
    probes: list[Path] = []
    ensure = _Ensure()
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN, OK], probes))
    monkeypatch.setattr(rel, "ensure_release", ensure)

    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is True
    [kw] = ensure.calls
    assert kw["venv"] == venv
    assert kw["force"] is True, "a repair is a fresh release of the live ref"
    assert kw["wait"] is False, "hal0 update never waits for the builder lock"
    assert kw["caller"] == "repair"
    assert isinstance(kw["units"], rel.NoUnits)
    # Probed before repairing, then again to prove the repair took.
    assert probes == [venv / "bin" / "python", venv / "bin" / "python"]


def test_units_are_managed_by_activation_when_restart_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With restart_gateway the builder's default unit controller is used.

    Activation records which units were active, stops only those, and starts
    only those again, so a gateway the operator stopped or disabled is never
    started (#2822/#2823); as hal0 it goes through the seam (#2828). Those
    invariants are tested against ``activate_release`` in
    tests/agents/test_hermes_release.py.
    """
    venv = _venv(tmp_path)
    ensure = _Ensure()
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN, OK], []))
    monkeypatch.setattr(rel, "ensure_release", ensure)
    assert repair_hermes_mcp_client(venv=venv, restart_gateway=True) is True
    assert ensure.calls[0]["units"] is None


def test_a_working_client_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv = _venv(tmp_path)
    ensure = _Ensure()
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([OK], []))
    monkeypatch.setattr(rel, "ensure_release", ensure)

    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False
    assert ensure.calls == []


def test_reports_without_installing_when_repair_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot-time safety net diagnoses; it must not build at startup.

    ``check_outstanding_migrations`` runs on every ``hal0-api`` boot, including
    crash-restarts. A network build there would put an unbounded, failing
    install in the startup path of a box that is already unhealthy.
    """
    venv = _venv(tmp_path)
    ensure = _Ensure()
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(rel, "ensure_release", ensure)

    assert repair_hermes_mcp_client(venv=venv, install=False, restart_gateway=False) is False
    assert ensure.calls == []


def test_absent_venv_is_a_quiet_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A box with no hermes install must not have its update disturbed."""
    probed: list[Path] = []
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], probed))

    assert repair_hermes_mcp_client(venv=tmp_path / "nope", restart_gateway=False) is False
    assert probed == []


@pytest.mark.parametrize("status", ["build_failed", "activation_failed", "offline"])
def test_a_failed_rebuild_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A build can fail (no network, resolver conflict) — an update must survive it."""
    venv = _venv(tmp_path)
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(
        rel, "ensure_release", _Ensure(rel.ReleaseOutcome(status=status, error="boom"))
    )
    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False


def test_a_held_builder_lock_is_busy_and_retried_next_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = _venv(tmp_path)
    warnings: list[tuple[str, dict[str, Any]]] = []
    holder = {"pid": 77, "caller": "provision", "started_at": "2026-10-21T00:00:00Z"}
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(
        rel, "ensure_release", _Ensure(rel.ReleaseOutcome(status="busy", holder=holder))
    )
    monkeypatch.setattr(
        "hal0.updater.updater.log.warning", lambda event, **kw: warnings.append((event, kw))
    )
    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False
    assert (
        "components.hermes_busy",
        {
            "job_id": None,
            "holder_pid": 77,
            "holder_caller": "provision",
            "holder_since": "2026-10-21T00:00:00Z",
        },
    ) in warnings


def test_a_rebuild_that_does_not_heal_reports_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a re-probe proves the repair worked — the builder's verdict does not."""
    venv = _venv(tmp_path)
    monkeypatch.setattr(hp, "_probe_mcp_client", _probe([BROKEN], []))
    monkeypatch.setattr(rel, "ensure_release", _Ensure())
    assert repair_hermes_mcp_client(venv=venv, restart_gateway=False) is False


def test_updater_venv_path_matches_the_layout_constant() -> None:
    from hal0.agents.hermes_layout import HERMES_VENV_PATH
    from hal0.updater import updater

    assert updater.HERMES_VENV_PATH == HERMES_VENV_PATH


def _plugin_src(tmp_path: Path, manifest: str) -> Path:
    src = tmp_path / "shipped"
    (src / "hal0-memory").mkdir(parents=True)
    (src / "hal0-memory" / "plugin.yaml").write_text("kind: memory\n")
    (src / "hal0-provider").mkdir()
    (src / "hal0-provider" / "plugin.yaml").write_text(manifest)
    return src


def test_update_converges_a_stale_plugin_mirror(tmp_path: Path) -> None:
    """#2884: an in-place update (no provisioning) refreshes stale plugin trees."""
    from hal0.updater.updater import mirror_hermes_plugins

    src = _plugin_src(tmp_path, "kind: model-provider\n")
    home = tmp_path / "home"
    stale = home / "plugins" / "model-providers" / "hal0"
    stale.mkdir(parents=True)
    (stale / "plugin.yaml").write_text("kind: provider\n")

    assert mirror_hermes_plugins(hermes_home=home, plugin_src_root=src) is True
    assert (stale / "plugin.yaml").read_text() == "kind: model-provider\n"
    assert (home / "plugins" / "hal0-memory" / "plugin.yaml").is_file()
    # converged: a second pass changes nothing
    assert mirror_hermes_plugins(hermes_home=home, plugin_src_root=src) is False


def test_plugin_mirror_skips_when_disabled_or_no_home(tmp_path: Path) -> None:
    from hal0.updater.updater import mirror_hermes_plugins

    src = _plugin_src(tmp_path, "kind: model-provider\n")
    home = tmp_path / "home"
    home.mkdir()
    assert mirror_hermes_plugins(hermes_home=home, plugin_src_root=src, install=False) is False
    assert not (home / "plugins").exists()
    assert mirror_hermes_plugins(hermes_home=tmp_path / "nope", plugin_src_root=src) is False


def test_plugin_mirror_failure_is_quiet(tmp_path: Path) -> None:
    from hal0.updater.updater import mirror_hermes_plugins

    home = tmp_path / "home"
    home.mkdir()
    # shipped root lacks the required hal0-memory: FAIL result, update unharmed
    assert mirror_hermes_plugins(hermes_home=home, plugin_src_root=tmp_path / "empty") is False
