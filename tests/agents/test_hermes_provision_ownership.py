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


# ── ownership_reconcile phase (fixes bug F: config.yaml root:root ordering) ───
#
# home_init (phase 4) chowns HERMES_HOME, but config_write (phase 7) writes
# config.yaml as root AFTER that, so on the happy path config.yaml lands
# root:root and the User=hal0 unit can't read it. The late always-run
# ownership_reconcile phase re-chowns the whole home + repairs 0711 on the
# agents dir. Spy on the chown helper so the assertion holds without being root.


def test_ownership_reconcile_rechows_hermes_home(tmp_path, monkeypatch) -> None:
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("x: 1\n")  # the file bug F strands
    state = hp.BootstrapState(hermes_home=str(hermes_home))

    chowned: list[Path] = []
    monkeypatch.setattr(hp, "_chown_tree_to_hal0", lambda p, **_k: chowned.append(Path(p)) or 1)
    # Redirect the agents dir under tmp so the phase never touches the real one.
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(mode=0o755)
    monkeypatch.setattr(hp, "AGENTS_DIR", agents_dir)

    out = hp._phase_ownership_reconcile(hp.context_for("ownership_reconcile", state))
    assert out.status == hp.PhaseStatus.OK
    assert hermes_home in chowned  # the config-bearing home got re-chowned


def test_ownership_reconcile_repairs_agents_dir_mode_to_0711(tmp_path, monkeypatch) -> None:
    import os as _os
    import stat as _stat

    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    monkeypatch.setattr(hp, "_chown_tree_to_hal0", lambda p, **_k: 0)

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(mode=0o755)
    _os.chmod(agents_dir, 0o755)  # wrong mode → the unit could enumerate siblings
    monkeypatch.setattr(hp, "AGENTS_DIR", agents_dir)

    out = hp._phase_ownership_reconcile(hp.context_for("ownership_reconcile", state))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["agents_dir_mode_fixed"] is True
    assert _stat.S_IMODE(agents_dir.stat().st_mode) == 0o711


def test_ownership_reconcile_agents_dir_mode_idempotent(tmp_path, monkeypatch) -> None:
    import stat as _stat

    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(hp, "_chown_tree_to_hal0", lambda p, **_k: 0)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(mode=0o711)
    agents_dir.chmod(0o711)  # already correct
    monkeypatch.setattr(hp, "AGENTS_DIR", agents_dir)

    out = hp._phase_ownership_reconcile(hp.context_for("ownership_reconcile", state))
    # Already 0711 → no churn, but still reported.
    assert out.details["agents_dir_mode_fixed"] is False
    assert _stat.S_IMODE(agents_dir.stat().st_mode) == 0o711


# ── symlink-following chown hazard (blocking review finding) ─────────────────
#
# _chown_tree_to_hal0 recurses HERMES_HOME as root. With os.chown (follow=True)
# a symlink entry chowns its TARGET — so a planted `evil -> /outside/secret`
# hands ownership of an out-of-tree file to the hal0 service user (which holds
# broad NOPASSWD sudo). Now that --adopt runs this over a foreign home of
# uncertain provenance, that's a real escalation path. The fix: os.lchown, so
# the LINK is chowned, never its target.


def test_chown_tree_default_uses_lchown_not_chown() -> None:
    """The default chown seam must be os.lchown (never follows symlinks)."""
    import inspect
    import os as _os

    default = inspect.signature(hp._chown_tree_to_hal0).parameters["chown"].default
    assert default is _os.lchown
    assert default is not _os.chown


def test_chown_tree_does_not_follow_symlink_to_outside(tmp_path: Path) -> None:
    """Regression: the recursion must chown the SYMLINK's OWN path, never the
    resolved out-of-tree target it points at.

    Uses an injected recording chown (no real privileged syscall), matching the
    file's convention, so it's CI-safe on any runner regardless of CAP_CHOWN.
    The lchown-vs-chown seam selection that actually enforces non-follow at the
    syscall level is proven separately by
    :func:`test_chown_tree_default_uses_lchown_not_chown`."""
    outside = tmp_path / "outside_secret"
    outside.write_text("do not touch")

    tree = tmp_path / "home"
    tree.mkdir()
    (tree / "evil").symlink_to(outside)  # planted inside->outside symlink
    (tree / "regular.txt").write_text("ok")

    calls, chown = _recorder()
    n = hp._chown_tree_to_hal0(
        tree,
        geteuid=lambda: 0,
        resolve_ids=lambda _u: (12345, 12345),
        chown=chown,
    )

    chowned = {Path(p) for p, _, _ in calls}
    # The symlink is chowned by its OWN path — never the resolved outside target.
    assert tree / "evil" in chowned
    assert outside not in chowned
    assert outside.resolve() not in chowned
    # Tree root + the regular file are still chowned; every entry gets the ids.
    assert tree in chowned
    assert tree / "regular.txt" in chowned
    assert all((uid, gid) == (12345, 12345) for _, uid, gid in calls)
    assert n == len(calls) == 3
