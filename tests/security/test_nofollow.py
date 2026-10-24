"""Real-filesystem tests for :mod:`hal0.security.nofollow` (#2975).

Each test builds a stand-in for ``/var/lib/hal0``: a ``2775`` directory, so the
walk treats it as writable by someone untrusted, exactly like the real state
root. Ownership is simulated through ``_trusted_uids``; every descriptor
operation runs for real.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
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


# ── ensure_dir and the shell entry point installer/install.sh calls ──────────

_MODULE = Path(nofollow.__file__)


def _cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    # Exactly how install.sh's nofollow() runs it: by file path, isolated.
    return subprocess.run(
        [sys.executable, "-I", str(_MODULE), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        cwd="/",
    )


def test_ensure_dir_creates_with_exact_mode(state_root: Path) -> None:
    proc = _cli(
        "dir", "--create", "--mode", "0700", "--expect", "root", str(state_root / "secrets")
    )
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE((state_root / "secrets").stat().st_mode) == 0o700


def test_ensure_dir_refuses_a_symlink_and_leaves_its_target(
    state_root: Path, elsewhere: Path
) -> None:
    elsewhere.chmod(0o755)
    (state_root / "slots").symlink_to(elsewhere)
    proc = _cli("dir", "--mode", "2775", "--expect", "adopt", str(state_root / "slots"))
    assert proc.returncode == 3
    assert "symlink" in proc.stderr
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755


def test_ensure_dir_refuses_a_symlinked_ancestor(state_root: Path, elsewhere: Path) -> None:
    (elsewhere / "agents").mkdir()
    (state_root / "secrets").symlink_to(elsewhere)
    proc = _cli(
        "dir",
        "--create",
        "--mode",
        "0700",
        "--expect",
        "root",
        str(state_root / "secrets" / "agents"),
    )
    assert proc.returncode == 3


def test_expect_root_refuses_a_directory_root_does_not_own(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The service account renamed secrets/ away and made its own in its place.
    (state_root / "secrets").mkdir(mode=0o755)
    before = (state_root / "secrets").stat().st_mode
    monkeypatch.setattr(nofollow.os, "geteuid", lambda: 4242)
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.ensure_dir(state_root / "secrets", mode=0o700, owner=(0, 0), expect="root")
    monkeypatch.undo()
    assert (state_root / "secrets").stat().st_mode == before


def test_expect_adopt_refuses_a_root_dir_renamed_into_place(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # secrets/ (root-owned, with entries) renamed to slots/: adopting it would
    # hand the vault directory to the service account.
    slots = state_root / "slots"
    slots.mkdir(mode=0o700)
    (slots / "hermes.env").write_text("K=v\n")
    before = slots.stat().st_mode
    me = os.geteuid()
    with pytest.raises(nofollow.UnsafePathError):
        nofollow.ensure_dir(slots, mode=0o2775, owner=(me + 1, me + 1), expect="adopt")
    assert slots.stat().st_mode == before


def test_expect_adopt_takes_an_empty_new_dir_or_the_owners_own(state_root: Path) -> None:
    me = os.geteuid()
    fresh = state_root / "registry"
    fresh.mkdir(mode=0o755)
    nofollow.ensure_dir(fresh, mode=0o2775, owner=(me + 1, me + 1), expect="adopt")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o2775
    owned = state_root / "models"
    owned.mkdir(mode=0o755)
    (owned / "m.gguf").write_text("x")
    nofollow.ensure_dir(owned, mode=0o2775, owner=(me, me), expect="adopt")
    assert stat.S_IMODE(owned.stat().st_mode) == 0o2775


def test_cli_write_requires_a_trusted_dir(state_root: Path) -> None:
    secrets_dir = state_root / "secrets"
    assert (
        _cli("dir", "--create", "--mode", "0700", "--expect", "root", str(secrets_dir)).returncode
        == 0
    )
    proc = _cli(
        "write",
        "--mode",
        "0600",
        "--require-trusted-dir",
        str(secrets_dir / "hal0-api.env"),
        stdin="HF_TOKEN=x\n",
    )
    assert proc.returncode == 0, proc.stderr
    target = secrets_dir / "hal0-api.env"
    assert target.read_text() == "HF_TOKEN=x\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # The state root itself (2775) is not trusted: refused, nothing written.
    proc = _cli(
        "write", "--mode", "0600", "--require-trusted-dir", str(state_root / "x.env"), stdin="K=v\n"
    )
    assert proc.returncode == 3
    assert not (state_root / "x.env").exists()


def test_write_file_closes_the_descriptor_when_fdopen_fails(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[int] = []
    real_open = nofollow.os.open

    def tracking_open(*args: object, **kwargs: object) -> int:
        fd = real_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(fd)
        return fd

    def broken_fdopen(*_a: object, **_k: object) -> object:
        raise MemoryError

    monkeypatch.setattr(nofollow.os, "open", tracking_open)
    monkeypatch.setattr(nofollow.os, "fdopen", broken_fdopen)
    with pytest.raises(MemoryError):
        nofollow.write_file(state_root / "f", "v")
    monkeypatch.undo()
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert [p.name for p in state_root.iterdir()] == []
