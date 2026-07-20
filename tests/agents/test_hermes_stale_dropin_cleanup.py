"""RATIFIED 2026-07-18 (deliverable 5) — stale hal0-agent@ drop-in cleanup.

A stale `ConfigurationDirectory=` drop-in from an old install brick-loops the
unit with status=241/CONFIGURATION_DIRECTORY (halo150 O3). The convergent
cleanup removes non-shipped fragments carrying that directive and daemon-reloads
only when something changed; a clean box is a no-op. Fakes only — no real
/etc/systemd writes.
"""

from __future__ import annotations

from pathlib import Path

from hal0.agents import hermes_provision as hp


def _mk_dropin(root: Path, instance: str, name: str, body: str) -> Path:
    d = root / f"hal0-agent@{instance}.service.d"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


def test_removes_stale_configuration_directory_dropin(tmp_path: Path) -> None:
    stale = _mk_dropin(
        tmp_path, "hermes", "10-legacy.conf", "[Service]\nConfigurationDirectory=hal0\n"
    )
    reloads: list[list[str]] = []
    result = hp.cleanup_stale_agent_dropins(
        systemd_dir=tmp_path,
        unlink=lambda p: p.unlink(),
        run=lambda argv, **_kw: reloads.append(list(argv)),
    )
    assert str(stale) in result.removed
    assert not stale.exists()
    assert result.daemon_reloaded
    assert reloads == [["systemctl", "daemon-reload"]]


def test_keeps_shipped_override_conf(tmp_path: Path) -> None:
    shipped = _mk_dropin(
        tmp_path, "hermes", "override.conf", "[Service]\nEnvironment=HERMES_HOME=/x\n"
    )
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert shipped.exists()
    assert result.removed == []
    assert not result.daemon_reloaded


def test_keeps_nonshipped_dropin_without_stale_directive(tmp_path: Path) -> None:
    # An operator drop-in that doesn't carry a stale directive is left alone.
    op = _mk_dropin(tmp_path, "hermes", "50-operator.conf", "[Service]\nNice=5\n")
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert op.exists()
    assert result.removed == []


def test_clean_box_is_noop(tmp_path: Path) -> None:
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert result.removed == []
    assert not result.daemon_reloaded


def test_convergent_second_run_after_cleanup(tmp_path: Path) -> None:
    _mk_dropin(tmp_path, "hermes", "10-legacy.conf", "ConfigurationDirectory=hal0\n")
    hp.cleanup_stale_agent_dropins(
        systemd_dir=tmp_path, unlink=lambda p: p.unlink(), run=lambda *a, **k: None
    )
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert result.removed == []
    assert not result.daemon_reloaded
