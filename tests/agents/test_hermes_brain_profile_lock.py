"""The hal0-brain profile's ``config.yaml`` is written under the profile lock (#3224).

``<profiles>/hal0-brain/.config.yaml.lock`` is the flock every named-profile
read-modify-write holds (``_profile_lock``). The brain profile has two writers:
the provisioning phase (``_phase_brain_profile_mcp_wire``, through
``_merge_profile_config``) takes it, and the user-installed MCP join
(``apply_brain_profile_mcp_entries``) did not, so the join could replace the file
from a parse that the phase had already outdated, or the phase could replace it
from a parse the join had outdated.

The concurrency tests do not rely on lucky timing: one writer is paused inside
its read-merge-replace window (just before its replace) and the other is
started from there. Without the lock the second runs to completion in that
window and the first's replace then erases it; with the lock it waits and runs
on the first's result.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp

yaml = pytest.importorskip("yaml")

#: How long the paused writer waits for the other one inside its window.
#: Without the lock the other finishes in milliseconds; with it, it cannot.
_WINDOW_S = 0.5

_JOIN_ENTRIES: dict[str, dict[str, Any]] = {"joined-srv": {"url": "http://joined.example/mcp"}}

#: What the provisioning phase merges into the brain profile, reduced to one key.
_SWEEP_KEYS: dict[str, Any] = {"memory": {"provider": "hal0-memory"}}


def _brain_home(tmp_path: Path, text: str = "model:\n  default: agent\n") -> tuple[Path, Path]:
    """``(hermes_home, brain profile directory)`` with a ``config.yaml`` inside."""
    home = tmp_path / "hermes-home"
    profile = home / "profiles" / hp._BRAIN_PROFILE_NAME
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text(text, encoding="utf-8")
    return home, profile


def _lock_held_elsewhere(profile: Path) -> bool:
    """True when another descriptor cannot take the profile's lock right now."""
    fd = os.open(profile / ".config.yaml.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _load(profile: Path) -> dict[str, Any]:
    return yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))


def _join(home: Path, **kw: Any) -> dict[str, Any]:
    return hp.apply_brain_profile_mcp_entries(_JOIN_ENTRIES, hermes_home=home, **kw)


def _sweep(profile: Path) -> dict[str, Any]:
    """The provisioning phase's writer: path form, descriptor-anchored, locked."""
    return hp._merge_profile_config(profile / "config.yaml", _SWEEP_KEYS)


def _start(fn: Callable[[], Any], results: list[Any]) -> tuple[threading.Thread, threading.Event]:
    """A daemon thread running ``fn``; its result or exception lands in ``results``."""
    done = threading.Event()

    def run() -> None:
        try:
            results.append(fn())
        except BaseException as exc:  # surfaced by the test, not lost in the thread
            results.append(exc)
        finally:
            done.set()

    return threading.Thread(target=run, daemon=True), done


# ── Two writers in one window both land ─────────────────────────────────────


def test_a_profile_sweep_started_inside_the_join_window_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join has read and merged; the sweep starts before the join's replace."""
    home, profile = _brain_home(tmp_path)
    results: list[Any] = []
    thread_b, b_done = _start(lambda: _sweep(profile), results)
    armed = [True]
    real_write = hp._atomic_write

    def paused_write(path: Path, content: str, **kw: Any) -> str:
        if armed:
            armed.clear()
            thread_b.start()
            b_done.wait(_WINDOW_S)
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", paused_write)
    out = _join(home)
    thread_b.join(10)

    assert not thread_b.is_alive()
    assert out["wired"] is True and out["changed"] is True
    assert results == [{"wired": True, "changed": True}]
    cfg = _load(profile)
    assert cfg["mcp_servers"]["joined-srv"]["url"] == "http://joined.example/mcp"
    assert cfg["memory"] == {"provider": "hal0-memory"}, f"the sweep's change was lost: {cfg}"
    assert cfg["model"] == {"default": "agent"}


def test_a_join_started_inside_the_profile_sweep_window_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep has read and merged; the join starts before the sweep's replace."""
    home, profile = _brain_home(tmp_path)
    results: list[Any] = []
    thread_b, b_done = _start(lambda: _join(home), results)
    armed = [True]
    real_replace = hp._replace_profile_config_at

    def paused_replace(dir_fd: int, text: str, st: os.stat_result) -> None:
        if armed:
            armed.clear()
            thread_b.start()
            b_done.wait(_WINDOW_S)
        real_replace(dir_fd, text, st)

    monkeypatch.setattr(hp, "_replace_profile_config_at", paused_replace)
    assert _sweep(profile) == {"wired": True, "changed": True}
    thread_b.join(10)

    assert not thread_b.is_alive()
    assert len(results) == 1 and isinstance(results[0], dict), results
    assert results[0]["wired"] is True and results[0]["changed"] is True
    cfg = _load(profile)
    assert cfg["memory"] == {"provider": "hal0-memory"}
    assert cfg["mcp_servers"]["joined-srv"]["url"] == "http://joined.example/mcp", (
        f"the join's entries were lost: {cfg}"
    )


# ── Where the lock lives, and when it is held ───────────────────────────────


def test_the_join_holds_the_profile_lock_while_it_reads_and_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, profile = _brain_home(tmp_path)
    held_at_replace: list[bool] = []
    real_write = hp._atomic_write

    def spy(path: Path, content: str, **kw: Any) -> str:
        held_at_replace.append(_lock_held_elsewhere(profile))
        return real_write(path, content, **kw)

    monkeypatch.setattr(hp, "_atomic_write", spy)
    assert _join(home)["changed"] is True
    assert held_at_replace == [True]
    assert not _lock_held_elsewhere(profile), "the lock must be released afterwards"
    assert (profile / ".config.yaml.lock").stat().st_mode & 0o777 == 0o600


def test_the_join_holds_the_lock_across_its_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is taken before the file is read, not only around the replace."""
    home, profile = _brain_home(tmp_path)
    held_at_read: list[bool] = []
    real_load = yaml.safe_load

    def spy(stream: Any) -> Any:
        held_at_read.append(_lock_held_elsewhere(profile))
        return real_load(stream)

    monkeypatch.setattr(yaml, "safe_load", spy)
    _join(home)
    assert held_at_read == [True]


def test_a_join_that_changes_nothing_still_releases_the_lock(tmp_path: Path) -> None:
    home, profile = _brain_home(tmp_path)
    assert _join(home)["changed"] is True
    assert _join(home)["changed"] is False
    assert not _lock_held_elsewhere(profile)


def test_a_failing_join_releases_the_lock(tmp_path: Path) -> None:
    home, profile = _brain_home(tmp_path, "mcp_servers: [unterminated\n")
    out = _join(home)
    assert out["wired"] is False and "error" in out
    assert not _lock_held_elsewhere(profile)


def test_an_absent_brain_profile_has_nothing_to_lock(tmp_path: Path) -> None:
    home = tmp_path / "hermes-home"
    out = _join(home)
    assert out["wired"] is False and out["reason"] == "brain profile config absent"
    assert not home.exists(), "an absent profile must not be created to hold a lock"


def test_a_symlinked_brain_profile_directory_is_refused(tmp_path: Path) -> None:
    """The profile directory is opened without following links, like the sweep's."""
    home = tmp_path / "hermes-home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config.yaml").write_text("model:\n  default: agent\n", encoding="utf-8")
    (home / "profiles").mkdir(parents=True)
    (home / "profiles" / hp._BRAIN_PROFILE_NAME).symlink_to(elsewhere)

    out = _join(home, remove_ids=["gone"])

    assert out["wired"] is False
    assert out["error"]
    assert out["remove_errors"] == [out["error"]]
    assert (elsewhere / "config.yaml").read_text(encoding="utf-8") == "model:\n  default: agent\n"
    assert not (elsewhere / ".config.yaml.lock").exists()


def test_a_join_under_the_root_config_lock_does_not_deadlock(tmp_path: Path) -> None:
    """The root lock and the profile lock are different files, so holding one is no hold on the other."""
    home, profile = _brain_home(tmp_path)
    (home / "config.yaml").write_text("agent: {}\n", encoding="utf-8")
    results: list[Any] = []

    def under_root_lock() -> dict[str, Any]:
        with hp.root_config_lock(home):
            return _join(home)

    thread, _ = _start(under_root_lock, results)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "deadlocked taking the profile lock under the root lock"
    assert len(results) == 1 and isinstance(results[0], dict), results
    assert results[0]["changed"] is True
    assert "joined-srv" in _load(profile)["mcp_servers"]
