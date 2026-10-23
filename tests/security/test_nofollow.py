"""Real-filesystem tests for :mod:`hal0.security.nofollow` (#2975).

Each test builds a stand-in for ``/var/lib/hal0``: a ``2775`` directory, so the
walk treats it as writable by someone untrusted, exactly like the real state
root. Ownership is simulated through ``_trusted_uids``; every descriptor
operation runs for real.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hal0.security import nofollow


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    root.chmod(0o2775)
    return root


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    victim = tmp_path / "victim"
    victim.mkdir()
    return victim


def test_happy_path_writes_and_keeps_mode(state_root: Path) -> None:
    target = state_root / ".hermes" / "config.yaml"
    target.parent.mkdir()
    target.write_text("old\n")
    target.chmod(0o640)
    nofollow.write_file(target, "new\n")
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.stat().st_uid == os.geteuid()
    assert sorted(p.name for p in target.parent.iterdir()) == ["config.yaml"]


def test_explicit_mode_and_owner(state_root: Path) -> None:
    target = state_root / "f"
    nofollow.write_file(target, b"x", mode=0o600, owner=(os.geteuid(), os.getegid()))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_symlinked_parent_is_refused(state_root: Path, elsewhere: Path) -> None:
    (state_root / ".hermes").symlink_to(elsewhere)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.write_file(state_root / ".hermes" / "config.yaml", "pwn\n")
    assert list(elsewhere.iterdir()) == []


def test_symlinked_grandparent_is_refused(state_root: Path, elsewhere: Path) -> None:
    (elsewhere / "profiles").mkdir()
    (state_root / ".hermes").symlink_to(elsewhere)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.write_file(state_root / ".hermes" / "profiles" / "SOUL.md", "pwn\n")
    assert list((elsewhere / "profiles").iterdir()) == []


def test_mkdir_refuses_existing_symlink_component(state_root: Path, elsewhere: Path) -> None:
    (state_root / "state").symlink_to(elsewhere)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.open_dir(state_root / "state" / "agents" / "hermes", create=True)
    assert list(elsewhere.iterdir()) == []


def test_mkdir_creates_with_exact_mode(state_root: Path) -> None:
    fd = nofollow.open_dir(state_root / "secrets" / "agents", create=True, mkdir_mode=0o700)
    os.close(fd)
    for d in (state_root / "secrets", state_root / "secrets" / "agents"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_pre_existing_symlink_at_temp_name_is_not_followed(
    state_root: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = elsewhere / "shadow"
    victim.write_text("root secret\n")
    (state_root / ".config.yaml.planted").symlink_to(victim)
    monkeypatch.setattr(nofollow.secrets, "token_hex", lambda _n: "planted")
    with pytest.raises(FileExistsError):
        nofollow.write_file(state_root / "config.yaml", "pwn\n")
    assert victim.read_text() == "root secret\n"
    assert (state_root / ".config.yaml.planted").is_symlink()


def test_symlink_at_target_is_replaced_not_followed(state_root: Path, elsewhere: Path) -> None:
    victim = elsewhere / "shadow"
    victim.write_text("root secret\n")
    victim.chmod(0o600)
    (state_root / "config.yaml").symlink_to(victim)
    nofollow.write_file(state_root / "config.yaml", "new\n")
    assert victim.read_text() == "root secret\n"
    assert not (state_root / "config.yaml").is_symlink()
    assert (state_root / "config.yaml").read_text() == "new\n"


def test_swapped_root_only_dir_owned_by_service_account_is_refused(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The attacker renamed secrets/ away and made its own: a real directory,
    # not a link, owned by the (non-root) account running this test.
    fake = state_root / "secrets" / "agents"
    fake.mkdir(parents=True)
    fake.chmod(0o700)
    monkeypatch.setattr(nofollow, "_trusted_uids", lambda: frozenset({0}))
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.write_file(fake / "hermes.env", "API_KEY=s3cret\n", require_trusted_dir=True)
    assert list(fake.iterdir()) == []


def test_root_only_dir_accepted_when_trusted(state_root: Path) -> None:
    real = state_root / "secrets" / "agents"
    real.mkdir(parents=True)
    real.chmod(0o700)
    nofollow.write_file(real / "hermes.env", "K=v\n", mode=0o600, require_trusted_dir=True)
    assert (real / "hermes.env").read_text() == "K=v\n"


def test_group_writable_dir_is_never_trusted(state_root: Path) -> None:
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.write_file(state_root / "x", "v", require_trusted_dir=True)


def test_read_text_refuses_symlink(state_root: Path, elsewhere: Path) -> None:
    (elsewhere / "shadow").write_text("root secret\n")
    (state_root / "hermes.env").symlink_to(elsewhere / "shadow")
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.read_text(state_root / "hermes.env")
    assert nofollow.read_text(state_root / "absent") is None
    assert nofollow.read_text(state_root / "no" / "such") is None


def test_read_text_refuses_fifo(state_root: Path) -> None:
    os.mkfifo(state_root / "pipe")
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.read_text(state_root / "pipe")


def test_chown_chmod_refuse_symlinks(state_root: Path, elsewhere: Path) -> None:
    victim = elsewhere / "shadow"
    victim.write_text("x")
    victim.chmod(0o600)
    (state_root / "link").symlink_to(victim)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.chmod(state_root / "link", 0o666)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.chown(state_root / "link", os.geteuid(), os.getegid())
    (state_root / "dir").symlink_to(elsewhere)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.chmod(state_root / "dir" / "shadow", 0o666)
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600


def test_chmod_applies_to_files_and_dirs(state_root: Path) -> None:
    (state_root / "d").mkdir()
    (state_root / "d" / "f").write_text("x")
    nofollow.chmod(state_root / "d", 0o2770)
    nofollow.chmod(state_root / "d" / "f", 0o640)
    nofollow.chown(state_root / "d" / "f", os.geteuid(), os.getegid())
    assert stat.S_IMODE((state_root / "d").stat().st_mode) == 0o2770
    assert stat.S_IMODE((state_root / "d" / "f").stat().st_mode) == 0o640


def test_chown_tree_never_follows_links(
    state_root: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = state_root / "venv"
    (tree / "bin").mkdir(parents=True)
    (tree / "bin" / "python").symlink_to(elsewhere / "python3")
    (elsewhere / "python3").write_text("")
    (tree / "lib").symlink_to(elsewhere)
    (tree / "bin" / "activate").write_text("")
    seen: list[tuple[str, bool]] = []
    real_chown = os.chown

    def spy(name, uid, gid, *, dir_fd=None, follow_symlinks=True):  # type: ignore[no-untyped-def]
        seen.append((str(name), follow_symlinks))
        return real_chown(name, uid, gid, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(nofollow.os, "chown", spy)
    nofollow.chown_tree(tree, os.geteuid(), os.getegid())
    assert {n for n, _ in seen} == {"venv", "bin", "lib", "python", "activate"}
    assert all(not follow for _, follow in seen)


def test_trusted_symlink_above_the_state_root_is_followed(tmp_path: Path) -> None:
    # A link in a directory only a trusted uid can write (an operator's own
    # relocation, /var/lib/hal0 -> /srv/hal0) is followed.
    real = tmp_path / "srv"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    nofollow.write_file(tmp_path / "link" / "f", "v")
    assert (real / "f").read_text() == "v"


def test_chmod_applies_to_a_fifo_without_opening_it(state_root: Path) -> None:
    os.mkfifo(state_root / "pipe", 0o600)
    nofollow.chmod(state_root / "pipe", 0o660)
    assert stat.S_IMODE((state_root / "pipe").stat().st_mode) == 0o660
