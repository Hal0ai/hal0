"""``hal0.memory.compat_client``: root never reads or writes the shared client
config inside a directory tree another user controls.

In a home owned by someone else the work runs in a forked child dropped to the
home owner (``_run_as_owner``); in a root-owned home root writes directly, and
only when every directory down to the file is root's and writable by nobody
else. The suite cannot run as root, so ``_is_root`` and ``_home_owner`` are
faked where a test needs root's branch; the fork itself is exercised for real
with the suite's own uid."""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from hal0.memory import compat_client as cc


def _as_root_in(
    monkeypatch: pytest.MonkeyPatch, *, home_owner: tuple[int, int] | None = None
) -> list[tuple[int, int]]:
    """Fake root. ``home_owner``: what the home's owner looks like (default: the
    real owner of the tmp tree, i.e. not root). Returns the identities the
    owner-run was asked to drop to; the work itself runs in-process."""
    dropped: list[tuple[int, int]] = []

    def run(uid: int, gid: int, fn: Callable[[], bytes]) -> bytes:
        dropped.append((uid, gid))
        return fn()

    monkeypatch.setattr(cc, "_is_root", lambda: True)
    monkeypatch.setattr(cc, "_run_as_owner", run)
    if home_owner is not None:
        monkeypatch.setattr(cc, "_home_owner", lambda home: home_owner)
    return dropped


def test_root_writes_an_operator_home_as_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "op"
    home.mkdir()
    dropped = _as_root_in(monkeypatch)
    cfg = home / ".hindsight" / "coding-agent.json"

    cc.write_client_config(cfg, {"a": 1}, home=home)

    st = os.stat(home)
    assert dropped == [(st.st_uid, st.st_gid)]
    assert json.loads(cfg.read_text()) == {"a": 1}
    # Created by the owner, so it is theirs, and private.
    assert os.stat(cfg).st_uid == st.st_uid
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cfg.parent).st_mode) == 0o700


def test_root_reads_an_operator_home_as_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "op"
    cfg = home / ".hindsight" / "coding-agent.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"b": 2}')
    dropped = _as_root_in(monkeypatch)

    assert cc.read_client_config(cfg, home) == {"b": 2}
    assert len(dropped) == 1


def test_a_symlinked_parent_leaving_an_operator_home_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "op"
    home.mkdir()
    elsewhere = tmp_path / "root-owned-dir"
    elsewhere.mkdir()
    (home / ".hindsight").symlink_to(elsewhere)
    dropped = _as_root_in(monkeypatch)

    with pytest.raises(cc.UnsafeConfigPath):
        cc.write_client_config(home / ".hindsight" / "coding-agent.json", {}, home=home)

    # Even the refusal ran as the owner, and nothing landed outside the home.
    assert len(dropped) == 1 and list(elsewhere.iterdir()) == []


def test_root_refuses_a_root_home_with_a_directory_someone_else_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A root-owned home whose .hindsight belongs to another user (here: the
    # tmp tree's owner, which is not root): a symlinked or user-owned parent
    # inside the home is refused, not written by root, and not run as anyone.
    home = tmp_path / "root-home"
    (home / "dotfiles").mkdir(parents=True)
    (home / ".hindsight").symlink_to(home / "dotfiles")
    dropped = _as_root_in(monkeypatch, home_owner=(0, 0))
    cfg = home / ".hindsight" / "coding-agent.json"

    with pytest.raises(cc.UnsafeConfigPath, match=r"symlink|root-owned directory|outside"):
        cc.write_client_config(cfg, {}, home=home)

    assert dropped == [] and not (home / "dotfiles" / "coding-agent.json").exists()
    assert cc.read_client_config(cfg, home) is None


def test_root_writes_directly_in_a_root_only_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "root-home"
    home.mkdir()
    dropped = _as_root_in(monkeypatch, home_owner=(0, 0))
    # The tmp tree is the suite user's: stand it in for root's.
    real_lstat = os.lstat

    class _RootStat:
        def __init__(self, st: os.stat_result) -> None:
            self._st = st

        def __getattr__(self, name: str) -> object:
            return 0 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(cc.os, "lstat", lambda p, **kw: _RootStat(real_lstat(p, **kw)))
    cfg = home / ".hindsight" / "coding-agent.json"
    (home / ".hindsight").mkdir(mode=0o700)

    cc.write_client_config(cfg, {"c": 3}, home=home)

    assert dropped == [] and json.loads(cfg.read_text()) == {"c": 3}


def test_the_tmp_file_is_fsynced_before_the_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(cc.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        cc.os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1]
    )
    monkeypatch.setattr(cc, "_is_root", lambda: False)

    cc.write_client_config(tmp_path / ".hindsight" / "x.json", {}, home=tmp_path)

    assert order[:2] == ["fsync", "replace"]


# ── the fork, for real, as the suite's own user ────────────────────────────


def test_run_as_owner_returns_the_childs_bytes() -> None:
    assert cc._run_as_owner(os.getuid(), os.getgid(), lambda: b"hello") == b"hello"


def test_run_as_owner_raises_the_childs_error_with_its_errno(tmp_path: Path) -> None:
    def missing() -> bytes:
        return (tmp_path / "nope").read_bytes()

    with pytest.raises(OSError) as exc:
        cc._run_as_owner(os.getuid(), os.getgid(), missing)
    assert exc.value.errno == errno.ENOENT

    def unsafe() -> bytes:
        raise cc.UnsafeConfigPath("refused")

    with pytest.raises(cc.UnsafeConfigPath, match="refused"):
        cc._run_as_owner(os.getuid(), os.getgid(), unsafe)


def test_run_as_owner_a_child_that_cannot_drop_is_an_error() -> None:
    # Not root: setuid to another uid fails in the child, and the parent says so.
    with pytest.raises(OSError):
        cc._run_as_owner(os.getuid() + 4242, os.getgid(), lambda: b"never")


def test_is_managed_section() -> None:
    url = "http://127.0.0.1:8080/api/memory/compat"
    assert cc.is_managed_section({}, "pi-coder")
    assert cc.is_managed_section({"apiUrl": url, "apiToken": "hal0bk_pi-coder.x"}, "pi-coder")
    assert not cc.is_managed_section({"apiUrl": "http://other:9177"}, "pi-coder")
    assert not cc.is_managed_section({"apiToken": "hal0bk_someone.x"}, "pi-coder")


class _RootStat:
    """An lstat result reported as root's (the suite cannot run as root)."""

    def __init__(self, st: os.stat_result, uid: int = 0) -> None:
        self._st, self._uid = st, uid

    def __getattr__(self, name: str) -> object:
        return self._uid if name == "st_uid" else getattr(self._st, name)


def _owned_by(monkeypatch: pytest.MonkeyPatch, others: set[Path]) -> None:
    """Every path is root's except ``others``, which belong to uid 1234."""
    real_lstat = os.lstat
    owned = {os.path.abspath(p) for p in others}
    monkeypatch.setattr(
        cc.os,
        "lstat",
        lambda p, **kw: _RootStat(real_lstat(p, **kw), 1234 if os.path.abspath(p) in owned else 0),
    )


def test_root_follows_its_own_dotfiles_link_in_its_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stow-style link that root owns, in a directory only root can write,
    still works: the config is written through it."""
    home = tmp_path / "root"
    (home / "dotfiles" / "hs").mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    (home / ".hindsight").symlink_to(Path("dotfiles") / "hs")
    _as_root_in(monkeypatch, home_owner=(0, 0))
    _owned_by(monkeypatch, set())

    cc.write_client_config(home / ".hindsight" / "coding-agent.json", {"a": 1}, home=home)

    assert json.loads((home / "dotfiles" / "hs" / "coding-agent.json").read_text()) == {"a": 1}


def test_root_refuses_a_link_inside_its_home_held_by_another_users_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link stays inside the home, so the resolved path looks root-only;
    but the directory holding the link is someone else's, and they could
    repoint it between the check and the write."""
    home = tmp_path / "root"
    (home / "safe").mkdir(parents=True, mode=0o700)
    (home / "shared").mkdir(mode=0o700)
    home.chmod(0o700)
    (home / "shared" / "hs").symlink_to(Path("..") / "safe")
    (home / ".hindsight").symlink_to(Path("shared") / "hs")
    _as_root_in(monkeypatch, home_owner=(0, 0))
    _owned_by(monkeypatch, {home / "shared"})

    with pytest.raises(cc.UnsafeConfigPath, match="root-owned directory"):
        cc.write_client_config(home / ".hindsight" / "coding-agent.json", {"a": 1}, home=home)
    assert not (home / "safe" / "coding-agent.json").exists()


def test_root_refuses_a_link_it_does_not_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "root"
    (home / "safe").mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    (home / ".hindsight").symlink_to("safe")
    _as_root_in(monkeypatch, home_owner=(0, 0))
    _owned_by(monkeypatch, {home / ".hindsight"})

    with pytest.raises(cc.UnsafeConfigPath, match="symlink root does not own"):
        cc.write_client_config(home / ".hindsight" / "coding-agent.json", {"a": 1}, home=home)


def test_home_owner_uses_the_accounts_own_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's gid is the owner's primary group, not the home's group."""
    import pwd

    home = tmp_path / "op"
    home.mkdir()
    uid = os.stat(home).st_uid
    assert cc._home_owner(home) == (uid, pwd.getpwuid(uid).pw_gid)

    class _NoAccount:
        @staticmethod
        def getpwuid(_uid: int) -> object:
            raise KeyError(_uid)

    monkeypatch.setattr(cc, "pwd", _NoAccount)
    with pytest.raises(cc.UnsafeConfigPath, match="no account"):
        cc._home_owner(home)
