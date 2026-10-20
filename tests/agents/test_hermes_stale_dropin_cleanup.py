"""RATIFIED 2026-07-18 (deliverable 5) — stale hal0-agent@ drop-in cleanup.

A stale `ConfigurationDirectory=` drop-in from an old install brick-loops the
unit with status=241/CONFIGURATION_DIRECTORY (halo150 O3). The convergent
cleanup removes non-shipped fragments carrying that directive and daemon-reloads
only when something changed; a clean box is a no-op. Fakes only — no real
/etc/systemd writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
        run=lambda argv, **_kw: reloads.append(list(argv)),
    )
    assert str(stale) in result.disabled
    assert not stale.exists()
    # renamed, not deleted: operator content survives
    assert stale.with_name("10-legacy.conf.hal0-disabled").read_text().startswith("[Service]")
    assert result.daemon_reloaded
    assert reloads == [["systemctl", "daemon-reload"]]


def test_keeps_shipped_10_hal0_conf(tmp_path: Path) -> None:
    shipped = _mk_dropin(
        tmp_path, "hermes", "10-hal0.conf", "[Service]\nEnvironment=HERMES_HOME=/x\n"
    )
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert shipped.exists()
    assert result.disabled == []
    assert not result.daemon_reloaded


def test_keeps_nonshipped_dropin_without_stale_directive(tmp_path: Path) -> None:
    # An operator drop-in that doesn't carry a stale directive is left alone.
    op = _mk_dropin(tmp_path, "hermes", "50-operator.conf", "[Service]\nNice=5\n")
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert op.exists()
    assert result.disabled == []


def test_clean_box_is_noop(tmp_path: Path) -> None:
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert result.disabled == []
    assert not result.daemon_reloaded


def test_convergent_second_run_after_cleanup(tmp_path: Path) -> None:
    _mk_dropin(tmp_path, "hermes", "10-legacy.conf", "ConfigurationDirectory=hal0\n")
    hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert result.disabled == []
    assert not result.daemon_reloaded


def test_operator_override_conf_with_stale_directive_is_renamed_not_deleted(
    tmp_path: Path,
) -> None:
    body = "[Service]\nConfigurationDirectory=hal0\nEnvironment=MINE=1\n"
    op = _mk_dropin(tmp_path, "hermes", "override.conf", body)
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert result.disabled == [str(op)]
    assert not op.exists()
    assert op.with_name("override.conf.hal0-disabled").read_text() == body
    # systemd loads only *.conf, so the renamed file is out of service
    assert not list(op.parent.glob("*.conf"))
    # convergent: second run finds nothing
    again = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    assert again.disabled == []


def test_second_disable_never_overwrites_earlier_copy(tmp_path: Path) -> None:
    _mk_dropin(tmp_path, "hermes", "override.conf", "ConfigurationDirectory=a\n# first\n")
    hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    _mk_dropin(tmp_path, "hermes", "override.conf", "ConfigurationDirectory=b\n# second\n")
    hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    d = tmp_path / "hal0-agent@hermes.service.d"
    texts = sorted(p.read_text() for p in d.glob("override.conf.hal0-disabled*"))
    assert len(texts) == 2
    assert any("# first" in t for t in texts) and any("# second" in t for t in texts)
    assert not list(d.glob("*.conf"))


def test_result_reports_real_rename_target(tmp_path: Path) -> None:
    d = tmp_path / "hal0-agent@hermes.service.d"
    d.mkdir()
    (d / "override.conf").write_text("[Service]\nConfigurationDirectory=foo\n")
    (d / "override.conf.hal0-disabled").write_text("old")
    result = hp.cleanup_stale_agent_dropins(systemd_dir=tmp_path, run=lambda *a, **k: None)
    src = str(d / "override.conf")
    assert result.renamed_to == {src: src + ".hal0-disabled.1"}


def test_root_prelude_runs_sweep_before_privilege_drop(monkeypatch) -> None:
    """#2803: install_hermes runs as hal0 post-reexec, so the root prelude must sweep."""
    from hal0.cli import agent_commands as ac

    calls: list[str] = []

    def _stop() -> str:
        raise RuntimeError("stop after sweep")

    monkeypatch.setattr(hp, "sweep_stale_agent_dropins_as_root", lambda: calls.append("sweep"))
    monkeypatch.setattr(hp, "resolve_hermes_python", _stop)
    with pytest.raises(RuntimeError):
        ac._hermes_root_prelude({})
    assert calls == ["sweep"]
