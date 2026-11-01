"""One lock for every read-modify-write of the root Hermes ``config.yaml`` (#2995).

``<HERMES_HOME>/.config.yaml.lock`` serialises the provisioner, the release
activation, the command-approvals route, the persona refresh and the MCP join.
Before it, each of them read the file, merged its own keys and atomically
replaced it, so two of them in the same window lost one side's change.

The concurrency tests do not rely on lucky timing: writer A is paused inside
its read-merge-replace window (just before its replace) and writer B is
started from there. Without the lock B runs to completion in that window and
A's replace then erases it; with the lock B waits and runs on A's result.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_command_approvals as cmd
from hal0.agents import hermes_provision as hp

yaml = pytest.importorskip("yaml")

#: How long writer A waits for writer B inside its window. Without the lock B
#: finishes in milliseconds; with it B cannot finish, so the wait runs out.
_WINDOW_S = 0.5


def _home(tmp_path: Path, text: str = "agent:\n  system_prompt: P\n") -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(text, encoding="utf-8")
    return home


def _lock_held_elsewhere(home: Path) -> bool:
    """True when another descriptor cannot take ``.config.yaml.lock`` right now."""
    fd = os.open(home / ".config.yaml.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _load(home: Path) -> dict[str, Any]:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))


# ── The writers that run concurrently with writer A ─────────────────────────


def _merge_alpha(home: Path) -> None:
    hp._merge_config_yaml_layers(
        home / "config.yaml", list_keys={"alpha": {"a": 1}}, overrides_path=home / "none.yaml"
    )


def _merge_beta(home: Path) -> None:
    hp._merge_config_yaml_layers(
        home / "config.yaml", list_keys={"beta": {"b": 2}}, overrides_path=home / "none.yaml"
    )


def _release_invariants(home: Path) -> None:
    hp.apply_release_config_invariants(home, overrides_path=home / "none.yaml")


def _mcp_prune(home: Path) -> None:
    hp._prune_mcp_servers_yaml(home / "config.yaml", remove_ids=["old"], desired_headers={})


_B_WRITERS: list[tuple[str, Callable[[Path], None], Callable[[dict[str, Any]], bool]]] = [
    ("merge", _merge_beta, lambda cfg: cfg.get("beta") == {"b": 2}),
    (
        "release-invariants",
        _release_invariants,
        lambda cfg: cfg.get("sessions") == {"auto_prune": False},
    ),
    ("mcp-prune", _mcp_prune, lambda cfg: "old" not in cfg.get("mcp_servers", {})),
]


@pytest.mark.parametrize(
    ("writer_b", "landed"),
    [pytest.param(w, ok, id=name) for name, w, ok in _B_WRITERS],
)
def test_two_concurrent_writers_both_land_their_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_b: Callable[[Path], None],
    landed: Callable[[dict[str, Any]], bool],
) -> None:
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    home = _home(tmp_path, "agent:\n  system_prompt: P\nmcp_servers:\n  old: {url: http://x}\n")
    errors: list[BaseException] = []
    b_done = threading.Event()

    def run_b() -> None:
        try:
            writer_b(home)
        except BaseException as exc:  # surfaced below, not lost in the thread
            errors.append(exc)
        finally:
            b_done.set()

    thread_b = threading.Thread(target=run_b, daemon=True)
    armed = [True]
    real_write = hp._atomic_write

    def paused_write(path: Path, content: str, **kw: Any) -> str:
        # Writer A has read and merged; its replace is next. Start B here.
        if armed:
            armed.clear()
            thread_b.start()
            b_done.wait(_WINDOW_S)
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", paused_write)
    _merge_alpha(home)
    thread_b.join(10)

    assert not thread_b.is_alive()
    assert errors == []
    cfg = _load(home)
    assert cfg["alpha"] == {"a": 1}, "writer A's keys are missing"
    assert landed(cfg), f"writer B's change was lost: {cfg}"


def test_persona_refresh_and_a_merge_do_not_lose_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dashboard persona edit (#2985) racing a repair-style merge."""
    from hal0.agents import persona_live

    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    home = _home(tmp_path, "agent:\n  system_prompt: OLD\n")
    monkeypatch.setattr(
        hp, "_active_persona_render", lambda state, **kw: ("PERSONA PROMPT", "Hermes")
    )
    errors: list[BaseException] = []
    b_done = threading.Event()

    def run_b() -> None:
        try:
            persona_live.refresh_persona_prompt(tmp_path / "personas", hermes_home=home)
        except BaseException as exc:
            errors.append(exc)
        finally:
            b_done.set()

    thread_b = threading.Thread(target=run_b, daemon=True)
    armed = [True]
    real_write = hp._atomic_write

    def paused_write(path: Path, content: str, **kw: Any) -> str:
        if armed:
            armed.clear()
            thread_b.start()
            b_done.wait(_WINDOW_S)
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", paused_write)
    _merge_alpha(home)
    thread_b.join(10)

    assert not thread_b.is_alive() and errors == []
    cfg = _load(home)
    assert cfg["alpha"] == {"a": 1}
    assert cfg["agent"]["system_prompt"] == "PERSONA PROMPT"


# ── Where the lock lives, and how it is made ────────────────────────────────


def test_the_lock_file_is_dot_config_yaml_lock_in_the_home_at_0600(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _merge_alpha(home)
    lock = home / ".config.yaml.lock"
    st = lock.lstat()
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert (st.st_uid, st.st_gid) == (home.stat().st_uid, home.stat().st_gid)
    assert sorted(p.name for p in home.iterdir()) == [".config.yaml.lock", "config.yaml"]


def test_the_lock_is_held_while_a_merge_reads_and_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    held_at_replace: list[bool] = []
    real_write = hp._atomic_write

    def spy(path: Path, content: str, **kw: Any) -> str:
        held_at_replace.append(_lock_held_elsewhere(home))
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", spy)
    _merge_alpha(home)
    assert held_at_replace == [True]
    assert not _lock_held_elsewhere(home), "the lock must be released afterwards"


def test_a_root_pass_owns_the_lock_like_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root repair must not leave a root-owned lock the hal0-api (User=hal0) cannot open."""
    home = _home(tmp_path)
    real_fstat = os.fstat

    def fake_fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            return st
        return os.stat_result(
            (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 4242, 4343, st.st_size, 0, 0, 0)
        )

    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fstat", fake_fstat)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    with hp.root_config_lock(home):
        pass
    assert chowned == [(4242, 4343)]


def test_a_missing_home_has_nothing_to_lock(tmp_path: Path) -> None:
    with hp.root_config_lock(tmp_path / "absent"):
        pass
    assert not (tmp_path / "absent").exists()


# ── Re-entrancy and exclusion ───────────────────────────────────────────────


def _in_thread(fn: Callable[[], None]) -> None:
    """Run ``fn`` on a worker thread; a deadlock fails the test instead of hanging it."""
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "deadlocked taking the root config lock twice"
    assert errors == []


def test_the_lock_is_reentrant_within_one_thread(tmp_path: Path) -> None:
    home = _home(tmp_path)

    def nested() -> None:
        with hp.root_config_lock(home):
            with hp.root_config_lock(home):
                assert _lock_held_elsewhere(home)
            # The inner exit must not drop the outer hold.
            assert _lock_held_elsewhere(home)
        assert not _lock_held_elsewhere(home)

    _in_thread(nested)


def test_a_writer_called_under_the_lock_does_not_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provisioner holds the lock across a read and then calls the merge."""
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    home = _home(tmp_path)

    def provisioner_like() -> None:
        with hp.root_config_lock(home):
            _merge_alpha(home)
            _release_invariants(home)
            assert _lock_held_elsewhere(home)

    _in_thread(provisioner_like)
    cfg = _load(home)
    assert cfg["alpha"] == {"a": 1}
    assert cfg["sessions"] == {"auto_prune": False}


def test_a_failed_holder_releases_the_lock_and_its_reentrancy_record(tmp_path: Path) -> None:
    home = _home(tmp_path)

    def fail_then_retake() -> None:
        with pytest.raises(RuntimeError, match="boom"), hp.root_config_lock(home):
            raise RuntimeError("boom")
        assert not _lock_held_elsewhere(home)
        # A leaked "already held" record would make this a silent no-op.
        with hp.root_config_lock(home):
            assert _lock_held_elsewhere(home)

    _in_thread(fail_then_retake)


def test_another_thread_waits_for_the_holder(tmp_path: Path) -> None:
    home = _home(tmp_path)
    entered = threading.Event()

    def contender() -> None:
        with hp.root_config_lock(home):
            entered.set()

    t = threading.Thread(target=contender, daemon=True)
    with hp.root_config_lock(home):
        t.start()
        assert not entered.wait(0.3), "a second thread got in while the lock was held"
    assert entered.wait(10)
    t.join(10)


# ── The writers that shell out, and the blind restore ───────────────────────


def _recording_run(home: Path, seen: list[bool]) -> Callable[..., subprocess.CompletedProcess]:
    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        seen.append(_lock_held_elsewhere(home))
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_hermes_config_set_runs_under_the_lock(tmp_path: Path) -> None:
    home = _home(tmp_path)
    seen: list[bool] = []
    applied, errors = hp._apply_config_set(
        [("approvals.mode", "manual"), ("sessions.auto_prune", False)],
        hermes_bin=Path("/fake/hermes"),
        hermes_home=home,
        run=_recording_run(home, seen),
    )
    assert (applied, errors) == (2, [])
    assert seen == [True, True]
    assert not _lock_held_elsewhere(home)


def test_hermes_config_migrate_runs_under_the_lock(tmp_path: Path) -> None:
    home = _home(tmp_path)
    seen: list[bool] = []
    assert hp._ensure_hermes_config(Path("/fake/hermes"), home, _recording_run(home, seen))
    assert seen == [True]


def test_the_approvals_rollback_replaces_config_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    monkeypatch.setattr(hp, "HERMES_HOME_DEFAULT", home)
    held: list[bool] = []
    real_write = hp._atomic_write

    def spy(path: Path, content: str, **kw: Any) -> str:
        held.append(_lock_held_elsewhere(home))
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", spy)
    cmd._restore_config("approvals:\n  mode: smart\n")
    assert held == [True]
    assert _load(home) == {"approvals": {"mode": "smart"}}


def test_the_release_activation_migrates_and_restores_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from hal0.agents import hermes_release as hr

    home = _home(tmp_path)
    layout = hr.Layout.for_venv(tmp_path / "venvs" / "hermes")
    seen: list[bool] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        seen.append(_lock_held_elsewhere(home))
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert hr._config_migrate(layout, run=run, hermes_home=home) is None
    assert seen == [True]

    outgoing = SimpleNamespace(path=tmp_path / "outgoing")
    outgoing.path.mkdir()
    (outgoing.path / hr._config_copy_name("new")).write_text("saved: 1\n", encoding="utf-8")
    real_copy = hr.shutil.copy2
    held: list[bool] = []

    def spy_copy(src: Any, dst: Any, **kw: Any) -> Any:
        held.append(_lock_held_elsewhere(home))
        return real_copy(src, dst, **kw)

    monkeypatch.setattr(hr.shutil, "copy2", spy_copy)
    assert hr._restore_config(outgoing, "new", home)  # type: ignore[arg-type]
    assert held == [True]
    assert _load(home) == {"saved": 1}
