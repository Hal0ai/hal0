"""Tests for the Layer-2 ownership handover in hermes provisioning (#843).

The installer/bootstrap legitimately runs as root and creates the venv,
``$HERMES_HOME`` tree, and ``runtime.json``. If those stay ``root:root`` the
``User=hal0`` systemd unit can't read them (EACCES, or a silent fallback to the
default provider). ``_chown_tree_to_hal0`` hands ownership to the hal0 service
user — but only when actually root, so it's a safe no-op in dev/non-root
installs and idempotent under ``bootstrap --repair``.

We can't chown to a real ``hal0`` user in CI, so the euid check, id resolution,
and the chown syscall are injected seams.
"""

from __future__ import annotations

from pathlib import Path

from hal0.agents import hermes_provision as hp


def _recorder():
    calls: list[tuple[str, int, int]] = []

    def _chown(path: str, uid: int, gid: int) -> None:
        calls.append((path, uid, gid))

    return calls, _chown


def test_noop_when_not_root(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    calls, chown = _recorder()
    n = hp._chown_tree_to_hal0(
        tmp_path,
        geteuid=lambda: 1000,
        resolve_ids=lambda _u: (1, 1),
        chown=chown,
    )
    assert n == 0
    assert calls == []


def test_noop_when_user_unknown(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    calls, chown = _recorder()
    n = hp._chown_tree_to_hal0(
        tmp_path,
        geteuid=lambda: 0,
        resolve_ids=lambda _u: None,
        chown=chown,
    )
    assert n == 0
    assert calls == []


def test_noop_when_path_missing(tmp_path: Path) -> None:
    calls, chown = _recorder()
    n = hp._chown_tree_to_hal0(
        tmp_path / "does-not-exist",
        geteuid=lambda: 0,
        resolve_ids=lambda _u: (1, 1),
        chown=chown,
    )
    assert n == 0
    assert calls == []


def test_recursive_chown_to_hal0_ids_when_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("x")
    (tmp_path / "top.txt").write_text("y")
    calls, chown = _recorder()
    n = hp._chown_tree_to_hal0(
        tmp_path,
        geteuid=lambda: 0,
        resolve_ids=lambda _u: (4242, 4243),
        chown=chown,
    )
    chowned = {Path(p) for p, _, _ in calls}
    assert tmp_path in chowned  # the root itself
    assert tmp_path / "sub" in chowned
    assert tmp_path / "sub" / "deep.txt" in chowned
    assert tmp_path / "top.txt" in chowned
    assert n == len(calls) == 4
    assert all((uid, gid) == (4242, 4243) for _, uid, gid in calls)


def test_resolve_user_ids_returns_none_for_unknown_user() -> None:
    assert hp._resolve_user_ids("definitely-not-a-real-user-xyz") is None


def test_home_init_hands_hermes_home_to_hal0(tmp_path, monkeypatch) -> None:
    """_phase_home_init must chown the canonical HERMES_HOME tree to hal0 so a
    root-context bootstrap doesn't leave root:root files (#843). Spy on the
    helper so the assertion holds without being root."""
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    chowned: list[Path] = []
    monkeypatch.setattr(hp, "_chown_tree_to_hal0", lambda p, **_k: chowned.append(Path(p)) or 0)
    out = hp._phase_home_init(hp.context_for("home_init", state))
    assert out.status == hp.PhaseStatus.OK
    assert hermes_home in chowned
