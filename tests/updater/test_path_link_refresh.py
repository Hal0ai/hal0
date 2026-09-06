"""#1844/#2019 — `hal0 update` never refreshed the `/usr/local/bin/hal0` PATH
link (or `hal0-agent`'s sibling), only `install.sh` did.

These tests pin :func:`hal0.updater.updater.refresh_path_links`: privileged-side
only (a no-op unless euid 0), reads its source shims from the ACTUAL running
venv (``sys.prefix``) rather than the release tree, and is best-effort per
link so a missing `hal0-agent` shim never fails an otherwise-successful
activation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.updater.updater import refresh_path_links


def _fake_venv(tmp_path: Path, *, shims: tuple[str, ...]) -> Path:
    venv = tmp_path / "venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    for name in shims:
        (bin_dir / name).write_text("#!/bin/sh\n", encoding="utf-8")
    return venv


@pytest.fixture
def link_dst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dst = tmp_path / "usr-local-bin" / "hal0"
    monkeypatch.setattr("hal0.updater.updater.HAL0_PATH_LINK_DST", dst)
    return dst


def test_noop_when_not_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_dst: Path
) -> None:
    """The unprivileged daemon must never write /usr/local/bin itself."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 1000)
    venv = _fake_venv(tmp_path, shims=("hal0", "hal0-agent"))
    monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(venv))

    result = refresh_path_links(tmp_path / "release-tree")

    assert result == {}
    assert not link_dst.exists()


def test_root_links_both_shims_from_the_running_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_dst: Path
) -> None:
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    venv = _fake_venv(tmp_path, shims=("hal0", "hal0-agent"))
    monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(venv))

    result = refresh_path_links(tmp_path / "release-tree", job_id="job-1")

    assert result == {"hal0": "linked", "hal0-agent": "linked"}
    assert link_dst.is_symlink()
    assert Path(link_dst).resolve() == (venv / "bin" / "hal0").resolve()
    agent_dst = link_dst.with_name("hal0-agent")
    assert agent_dst.is_symlink()
    assert agent_dst.resolve() == (venv / "bin" / "hal0-agent").resolve()


def test_missing_agent_shim_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_dst: Path
) -> None:
    """An older release / a venv still provisioning may lack hal0-agent —
    the hal0 link must still succeed."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    venv = _fake_venv(tmp_path, shims=("hal0",))
    monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(venv))

    result = refresh_path_links(tmp_path / "release-tree")

    assert result == {"hal0": "linked", "hal0-agent": "missing"}
    assert link_dst.is_symlink()
    assert not link_dst.with_name("hal0-agent").exists()


def test_re_linking_is_idempotent_and_replaces_a_stale_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_dst: Path
) -> None:
    """A rebuilt venv (#2019) must overwrite the OLD link, not refuse."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    old_venv = _fake_venv(tmp_path / "old", shims=("hal0",))
    link_dst.parent.mkdir(parents=True, exist_ok=True)
    link_dst.symlink_to(old_venv / "bin" / "hal0")

    new_venv = _fake_venv(tmp_path / "new", shims=("hal0", "hal0-agent"))
    monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(new_venv))

    result = refresh_path_links(tmp_path / "release-tree")

    assert result["hal0"] == "linked"
    assert Path(link_dst).resolve() == (new_venv / "bin" / "hal0").resolve()


def test_swap_failure_is_recorded_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_dst: Path
) -> None:
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    venv = _fake_venv(tmp_path, shims=("hal0",))
    monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(venv))

    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("hal0.updater.updater._atomic_symlink_swap", _boom)

    result = refresh_path_links(tmp_path / "release-tree")

    assert result["hal0"] == "failed"
