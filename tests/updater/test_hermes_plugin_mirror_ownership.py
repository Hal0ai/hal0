"""#3852: the mirrored Hermes plugin trees stay hal0's, and a failed mirror is loud.

A root-run ``install.sh`` migration copied the plugin trees into HERMES_HOME as
root, so every later hal0-run ``hal0 update`` failed to replace them (EACCES)
and still reported a converged, warning-free update.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hal0.config import paths
from hal0.install import perms
from hal0.updater import updater as updater_module


def _plugin_src(tmp_path: Path) -> Path:
    src = tmp_path / "shipped"
    (src / "hal0-memory").mkdir(parents=True)
    (src / "hal0-memory" / "plugin.yaml").write_text("kind: memory\n")
    (src / "hal0-provider").mkdir()
    (src / "hal0-provider" / "plugin.yaml").write_text("kind: model-provider\n")
    (src / "hal0-provider" / "profile.py").write_text("NEW = True\n")
    return src


def _hermes_home() -> Path:
    home = paths.var_lib() / ".hermes"
    home.mkdir(parents=True)
    return home


# ── (a) the update names the failure ──────────────────────────────────────────


def test_a_failed_plugin_mirror_is_a_labelled_pass_warning() -> None:
    from hal0.cli.update_commands import _MIGRATION_PASS_LABELS

    for event in updater_module._UNCONVERGED_PASS_EVENTS:
        assert event in updater_module._NON_FATAL_MIGRATION_FAILURE_EVENTS
        assert "sudo hal0 agent reprovision hermes" in _MIGRATION_PASS_LABELS[event]


def test_the_cli_names_a_failed_plugin_mirror_and_refuses_a_clean_success(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hal0.cli import update_commands as uc

    converged = uc._print_convergence(
        {
            "post_activation_migrations": {
                "ok": True,
                "from": 1,
                "to": 1,
                "error": None,
                "pass_warnings": ["updater.hermes_plugin_mirror_failed"],
            },
            "converged": False,
        }
    )
    out = " ".join(capsys.readouterr().out.split())  # undo the console wrap
    assert converged is False
    assert "Hermes plugin refresh" in out
    assert "sudo hal0 agent reprovision hermes" in out


def test_a_mirror_failure_logs_the_event_the_update_scans_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # The shipped root lacks the required hal0-memory tree: a FAIL result.
    assert (
        updater_module.mirror_hermes_plugins(hermes_home=home, plugin_src_root=tmp_path / "empty")
        is False
    )
    assert "updater.hermes_plugin_mirror_failed" in capsys.readouterr().out


# ── (b) a root caller never copies as root ────────────────────────────────────


def _as_root(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Pretend to be root; record the ownership converge instead of running it."""
    events: list[str] = []
    monkeypatch.setattr(updater_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(updater_module, "_hal0_account_exists", lambda: True)
    monkeypatch.setattr(
        perms, "converge_hermes_plugin_trees", lambda: events.append("converge") or []
    )

    def _no_root_copy(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("root copied the plugin trees itself")

    monkeypatch.setattr("hal0.agents.hermes_provision._mirror_plugins", _no_root_copy)
    return events


def test_a_root_caller_runs_the_copy_as_hal0_after_handing_the_trees_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _as_root(monkeypatch)
    src = _plugin_src(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    real_run = subprocess.run
    seen: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        events.append("copy")
        seen.append(list(argv))
        # Run the child for real, minus the privilege drop and -I (the test
        # tree is on PYTHONPATH, not installed).
        child = argv[argv.index(sys.executable) :]
        child.remove("-I")
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        return real_run(child, capture_output=True, text=True, env=env, timeout=120)

    monkeypatch.setattr(updater_module.subprocess, "run", _fake_run)

    assert updater_module.mirror_hermes_plugins(hermes_home=home, plugin_src_root=src) is True
    assert events == ["converge", "copy"]
    from hal0.system.seam import as_hal0_argv

    drop = as_hal0_argv(["x"])[:-1]
    assert seen[0][: len(drop)] == drop  # through the shared run-as-hal0 helper
    tree = home / "plugins" / "model-providers" / "hal0"
    assert (tree / "profile.py").read_text() == "NEW = True\n"


def test_a_failed_copy_as_hal0_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _as_root(monkeypatch)
    src = _plugin_src(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    verdict = json.dumps({"changed": False, "reason": "plugin copy failed: [Errno 13]"})
    monkeypatch.setattr(
        updater_module.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=verdict + "\n", stderr=""),
    )
    assert updater_module.mirror_hermes_plugins(hermes_home=home, plugin_src_root=src) is False
    out = capsys.readouterr().out
    assert "updater.hermes_plugin_mirror_failed" in out
    assert "Errno 13" in out


def test_a_child_that_dies_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _as_root(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        updater_module.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, stdout="", stderr="ImportError: x\n"),
    )
    assert (
        updater_module.mirror_hermes_plugins(
            hermes_home=home, plugin_src_root=_plugin_src(tmp_path)
        )
        is False
    )
    assert "ImportError: x" in capsys.readouterr().out


# ── (c) the ownership table owns the plugin trees ─────────────────────────────


def test_the_table_covers_exactly_the_trees_the_mirror_writes(tmp_path: Path) -> None:
    from hal0.agents.hermes_provision import _mirror_plugins

    home = _hermes_home()
    _changed, details, failure = _mirror_plugins(_plugin_src(tmp_path), home)
    assert failure is None
    written = {Path(p).relative_to(home) for p in details["plugins"]}
    recursive = {r.target.relative_to(home) for r in perms.hermes_plugin_rows() if r.recursive}
    assert recursive == written
    # Every parent of a written tree has a row too, or a root-made parent
    # would still block the hal0 mirror.
    parents = {r.target.relative_to(home) for r in perms.hermes_plugin_rows() if not r.recursive}
    assert {p for w in written for p in w.parents if p != Path(".")} == parents


def test_doctor_perms_reports_a_root_owned_plugin_dir_and_never_a_file(tmp_path: Path) -> None:
    home = _hermes_home()
    from hal0.agents.hermes_provision import _mirror_plugins

    _mirror_plugins(_plugin_src(tmp_path), home)
    # Declared for the real service account; on this box the dirs are the
    # test user's, so each is drift, which is what a root-owned tree shows.
    rows = perms.audit_rows(perms.plan(perms.hermes_plugin_rows()))
    tree = home / "plugins" / "model-providers" / "hal0"
    by_path = {r["path"]: r for r in rows}
    assert str(tree / "profile.py") not in by_path  # files are the mirror's (#3852)
    if pwd.getpwuid(os.getuid()).pw_name != "hal0":
        assert by_path[str(tree)]["status"] == "drift"
        assert "want hal0:hal0 0755" in by_path[str(tree)]["detail"]


def _me() -> tuple[str, str]:
    return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name


def _my_rows() -> list[perms.PermRow]:
    """The plugin rows declared for the test user, so a real commit can run."""
    me, group = _me()
    return perms.hermes_plugin_rows(perms.ownership_table(service_user=me, service_group=group))


def test_a_healthy_tree_shows_no_drift(tmp_path: Path) -> None:
    """Build the tree the way a box does (#3852 review): the setgid 2775 state
    root, HERMES_HOME and its standard dirs from ``_phase_home_init``, the
    mirror's copytree, then the ``__pycache__`` Hermes writes on import, all
    under the units' default umask 0022."""
    from types import SimpleNamespace

    from hal0.agents.hermes_provision import _mirror_plugins, _phase_home_init

    old = os.umask(0o022)
    try:
        var_lib = paths.var_lib()
        var_lib.mkdir(parents=True)
        var_lib.chmod(0o2775)
        home = var_lib / ".hermes"
        _phase_home_init(SimpleNamespace(state=SimpleNamespace(hermes_home=str(home))))
        assert (home / "plugins").stat().st_mode & 0o7777 == 0o2755
        _mirror_plugins(_plugin_src(tmp_path), home)
        import importlib.util
        import py_compile

        for source in home.glob("plugins/**/*.py"):
            py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)))
    finally:
        os.umask(old)
    plan = perms.plan(_my_rows())
    assert (home / "plugins" / "model-providers" / "hal0" / "__pycache__") in {
        d.path for d in plan.diffs
    }
    assert plan.drifted == ()


@pytest.mark.parametrize("found", [0o755, 0o2755])
def test_the_plugin_parents_keep_whichever_setgid_bit_they_have(tmp_path: Path, found: int) -> None:
    """Real boxes have both (#3852): 2755 on a fresh 1.8.0 install, 0755 on a
    box whose HERMES_HOME an older release made. Neither is drift, and a fix
    of other drift leaves the bit as it found it."""
    from hal0.agents.hermes_provision import _mirror_plugins

    home = _hermes_home()
    _mirror_plugins(_plugin_src(tmp_path), home)
    parents = (home / "plugins", home / "plugins" / "model-providers")
    for d in parents:
        d.chmod(found)
    plan = perms.plan(_my_rows())
    assert not [d for d in plan.drifted if d.path in parents]

    for d in parents:
        d.chmod(found & ~0o022 | 0o070)  # group-writable: real drift, setgid as found
    plan = perms.plan(_my_rows())
    assert {d.path for d in plan.drifted} >= set(parents)
    perms.commit(plan)
    for d in parents:
        assert d.stat().st_mode & 0o7777 == found


def test_a_root_owned_tree_beside_a_hal0_sibling_is_repaired_then_mirrored(
    tmp_path: Path,
) -> None:
    """The affected box's state (#3852): ``model-providers/hal0`` is a
    root-owned dir of root-owned 0644 files, ``hal0-memory`` is hal0's. Stand-in
    without root: a dir the mirror cannot write (0555) of files it cannot write
    (0444). The mirror fails; the dirs-only converge fixes the dir and leaves
    the files alone; the mirror then replaces those files itself."""
    from hal0.agents.hermes_provision import _mirror_plugins

    home = _hermes_home()
    src = _plugin_src(tmp_path)
    (src / "hal0-provider" / "__init__.py").write_text("")  # the shipped tree's three files
    _mirror_plugins(src, home)
    tree = home / "plugins" / "model-providers" / "hal0"
    sibling = home / "plugins" / "hal0-memory"
    files = sorted(tree.iterdir())
    assert len(files) == 3
    for f in files:
        f.chmod(0o444)
    tree.chmod(0o555)
    (src / "hal0-provider" / "profile.py").write_text("SHIPPED_FIX = True\n")
    try:
        _changed, _d, failure = _mirror_plugins(src, home)
        assert failure is not None  # EACCES, as on the box

        plan = perms.plan(_my_rows())
        assert {d.path for d in plan.drifted} == {tree}  # not the sibling, not a file
        perms.commit(plan)
        assert tree.stat().st_mode & 0o7777 == 0o755
        assert all(f.stat().st_mode & 0o7777 == 0o444 for f in files)  # never touched

        _changed, _d, failure = _mirror_plugins(src, home)
        assert failure is None
        assert (tree / "profile.py").read_text() == "SHIPPED_FIX = True\n"
        assert (sibling / "plugin.yaml").is_file()
    finally:
        if tree.exists():
            tree.chmod(0o755)


def test_the_mirror_needs_write_on_the_dirs_only(tmp_path: Path) -> None:
    """Why handing back the directories is enough (#3852 review): unlinking a
    file needs write on its directory, not on the file, so files the mirror
    cannot write (root's, here read-only) are still replaced. A non-empty
    directory it cannot write is not, which is why the dirs are handed back
    recursively."""
    from hal0.agents.hermes_provision import _mirror_plugins

    home = _hermes_home()
    src = _plugin_src(tmp_path)
    (src / "hal0-provider" / "sub").mkdir()
    (src / "hal0-provider" / "sub" / "x.py").write_text("X = 1\n")
    _mirror_plugins(src, home)
    tree = home / "plugins" / "model-providers" / "hal0"
    for f in tree.rglob("*.py"):
        f.chmod(0o444)
    (src / "hal0-provider" / "profile.py").write_text("NEWER = True\n")
    _changed, _d, failure = _mirror_plugins(src, home)
    assert failure is None
    assert (tree / "profile.py").read_text() == "NEWER = True\n"

    (tree / "sub").chmod(0o555)
    (src / "hal0-provider" / "profile.py").write_text("NEWEST = True\n")
    try:
        _changed, _d, failure = _mirror_plugins(src, home)
        assert failure is not None
    finally:
        (tree / "sub").chmod(0o755)


def test_converge_is_a_no_op_without_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perms.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(perms, "commit", lambda *_a, **_k: pytest.fail("committed as non-root"))
    assert perms.converge_hermes_plugin_trees() == []


def test_converge_as_root_commits_only_the_plugin_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _hermes_home()
    (home / "plugins" / "model-providers" / "hal0").mkdir(parents=True)
    monkeypatch.setattr(perms.os, "geteuid", lambda: 0)
    planned: list[perms.OwnershipPlan] = []
    monkeypatch.setattr(perms, "commit", lambda p: planned.append(p) or [])
    perms.converge_hermes_plugin_trees()
    assert planned
    plugins = home / "plugins"
    assert all(d.path == plugins or plugins in d.path.parents for d in planned[0].diffs)


def test_a_failed_converge_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perms.os, "geteuid", lambda: 0)

    def _boom(_p: perms.OwnershipPlan) -> list[Path]:
        raise PermissionError("nope")

    monkeypatch.setattr(perms, "commit", _boom)
    assert perms.converge_hermes_plugin_trees() == []


def test_update_activation_hands_the_plugin_trees_back_as_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root activation step (``hal0-update activate``) converges the trees
    before the hal0-run post-swap mirror needs them."""
    root = updater_module._usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "hal0-1.0.0").mkdir()
    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: True)
    monkeypatch.setattr(updater_module.os, "geteuid", lambda: 0)
    for name in (
        "refresh_privileged_wrappers",
        "refresh_path_links",
        "refresh_gpu_perms_unit",
        "remove_retired_artifacts",
    ):
        monkeypatch.setattr(updater_module, name, lambda *_a, **_k: {"refreshed": []})
    monkeypatch.setattr(perms, "commit", lambda *_a, **_k: [])
    events: list[str] = []
    monkeypatch.setattr(perms, "converge_hermes_plugin_trees", lambda: events.append("converge"))

    updater_module.activate_release("hal0-1.0.0")

    assert events == ["converge"]


def test_the_reprovision_root_prelude_hands_the_plugin_trees_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sudo hal0 agent reprovision hermes`` (the doctor remedy) converges the
    trees while root is held, before the hal0 re-exec mirrors them."""
    from hal0.cli import agent_commands

    events: list[str] = []
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.sweep_stale_agent_dropins_as_root", lambda: None
    )
    monkeypatch.setattr(agent_commands, "_ensure_hermes_prelude_dirs", lambda: None)
    monkeypatch.setattr(agent_commands, "_hand_pre_layout_venv_to_hal0", lambda: None)
    monkeypatch.setattr(perms, "converge_hermes_plugin_trees", lambda: events.append("converge"))

    def _resolve(**_k: Any) -> str:
        events.append("resolve")
        raise RuntimeError("stop here")

    monkeypatch.setattr("hal0.agents.hermes_provision.resolve_hermes_python", _resolve)
    monkeypatch.setattr(agent_commands, "_hand_uv_trees_to_hal0", lambda: None)
    with pytest.raises(RuntimeError):
        agent_commands._hermes_root_prelude({})
    assert events == ["converge", "resolve"]


# ── symlinks planted in the tree (runs as root over a hal0-writable dir) ──────


def test_a_symlink_planted_in_the_plugin_tree_is_never_followed(tmp_path: Path) -> None:
    """The converge runs as root over a tree hal0 can write. A link to a file
    or a directory outside the tree must never be re-owned or re-moded through.

    Run for real with the test user as the declared owner (chown to yourself
    needs no privilege), so the real descriptor-based seams do the work; the
    proof is the outside targets' modes, which a followed chmod would change.
    """
    home = _hermes_home()
    from hal0.agents.hermes_provision import _mirror_plugins

    _mirror_plugins(_plugin_src(tmp_path), home)
    tree = home / "plugins" / "model-providers" / "hal0"
    outside_file = tmp_path / "outside" / "secret"
    outside_file.parent.mkdir()
    outside_file.write_text("x")
    outside_file.chmod(0o600)
    outside_dir = tmp_path / "outside" / "dir"
    outside_dir.mkdir()
    (outside_dir / "inner").write_text("y")
    (outside_dir / "inner").chmod(0o600)
    outside_dir.chmod(0o700)
    (tree / "evil").symlink_to(outside_file)
    (tree / "evil-dir").symlink_to(outside_dir, target_is_directory=True)
    # And a whole plugin tree replaced by a link.
    memory = home / "plugins" / "hal0-memory"
    import shutil

    shutil.rmtree(memory)
    memory.symlink_to(outside_dir, target_is_directory=True)
    tree.chmod(0o700)  # real drift inside the tree

    plan = perms.plan(_my_rows())
    planned = {d.path for d in plan.diffs}
    assert tree / "evil" not in planned
    assert tree / "evil-dir" not in planned
    assert not any(outside_dir in p.parents for p in planned)
    assert all(not d.changed for d in plan.diffs if d.path == memory)  # the link itself

    perms.commit(plan)

    assert tree.stat().st_mode & 0o7777 == 0o755  # drift inside fixed
    assert outside_file.stat().st_mode & 0o7777 == 0o600
    assert outside_dir.stat().st_mode & 0o7777 == 0o700
    assert (outside_dir / "inner").stat().st_mode & 0o7777 == 0o600
    assert (tree / "evil").is_symlink()
    assert memory.is_symlink()


def test_a_hardlink_planted_in_the_plugin_tree_keeps_its_owner_and_mode(tmp_path: Path) -> None:
    """#3852 review: hal0 can hard-link any file on the filesystem into the
    tree. Re-owning it as root would hand that inode over, so files are never
    planned, including one standing where a directory belongs."""
    home = _hermes_home()
    from hal0.agents.hermes_provision import _mirror_plugins

    _mirror_plugins(_plugin_src(tmp_path), home)
    tree = home / "plugins" / "model-providers" / "hal0"
    outside = tmp_path / "outside-secret"
    outside.write_text("secret")
    outside.chmod(0o600)
    os.link(outside, tree / "planted")
    # A whole tree replaced by a hard link, too.
    memory = home / "plugins" / "hal0-memory"
    import shutil

    shutil.rmtree(memory)
    os.link(outside, memory)
    before = outside.stat()

    plan = perms.plan(_my_rows())
    assert tree / "planted" not in {d.path for d in plan.diffs}
    assert memory not in {d.path for d in plan.diffs}
    perms.commit(plan)

    after = outside.stat()
    assert after.st_nlink == 3
    assert (after.st_uid, after.st_gid, after.st_mode) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
    )


def test_a_file_swapped_in_for_a_dir_after_the_plan_is_refused(tmp_path: Path) -> None:
    """The dirs-only apply opens the directory ``O_DIRECTORY``: a hard-linked
    file put in its place between plan and commit is refused, not re-owned."""
    home = _hermes_home()
    from hal0.agents.hermes_provision import _mirror_plugins

    _mirror_plugins(_plugin_src(tmp_path), home)
    tree = home / "plugins" / "model-providers" / "hal0"
    tree.chmod(0o700)
    outside = tmp_path / "outside-secret"
    outside.write_text("secret")
    outside.chmod(0o600)
    plan = perms.plan(_my_rows())
    import shutil

    shutil.rmtree(tree)
    os.link(outside, tree)
    with pytest.raises(OSError):
        perms.commit(plan)
    assert outside.stat().st_mode & 0o7777 == 0o600


def test_a_parent_swapped_for_a_symlink_after_the_plan_is_refused_and_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3852 review: with only root trusted (as on a real box, not the suite's
    default of trusting the test user too), a parent directory swapped for a
    link between plan and commit is refused, the dirs already changed are put
    back, and nothing behind the link is touched."""
    from hal0.agents.hermes_provision import _mirror_plugins
    from hal0.security import nofollow

    home = _hermes_home()
    _mirror_plugins(_plugin_src(tmp_path), home)
    plugins = home / "plugins"
    parent = plugins / "model-providers"
    plugins.chmod(0o700)  # drift, applied first
    parent.chmod(0o700)
    (parent / "hal0").chmod(0o700)
    outside = tmp_path / "outside" / "model-providers"
    (outside / "hal0").mkdir(parents=True)
    (outside / "hal0").chmod(0o700)
    outside.chmod(0o700)

    plan = perms.plan(_my_rows())
    assert plan.drifted[0].path == plugins
    parent.rename(home / "moved")
    parent.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(nofollow, "_trusted_uids", lambda: frozenset({0}))

    with pytest.raises(nofollow.UnsafePathError):
        perms.commit(plan)

    assert plugins.stat().st_mode & 0o7777 == 0o700  # rolled back
    assert outside.stat().st_mode & 0o7777 == 0o700
    assert (outside / "hal0").stat().st_mode & 0o7777 == 0o700
