"""hal0-agentenv's descriptor-based writer never follows a swapped directory (#2975).

The wrapper runs as root through sudo, on demand of the ``hal0`` account, and
writes into ``secrets/agents`` and ``/etc/hal0/agents``, whose parents that
account owns. These tests run the wrapper's embedded Python program against a
``tmp_path`` stand-in for the base directory, for real; only the root-only
``fchown`` is skipped, because the suite is not root.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "installer" / "wrappers" / "hal0-agentenv"


def _program() -> str:
    body = WRAPPER.read_text(encoding="utf-8")
    start = body.index("\n", body.index("<<'PY'")) + 1
    return body[start : body.index("\nPY\n", start)]


def _run(base: Path, verb: str, managed: str, name: str, stdin: str, modes=("0700", "0600")):
    return subprocess.run(
        [sys.executable, "-I", "-c", _program(), verb, str(base), managed, *modes, name],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "var-lib-hal0"
    root.mkdir()
    root.chmod(0o2775)  # service-owned, group-writable, not sticky
    return root


def test_merge_creates_root_dirs_and_merges(base: Path) -> None:
    assert _run(base, "merge", "secrets agents", "hermes.env", "A=1\n").returncode == 0
    proc = _run(base, "merge", "secrets agents", "hermes.env", "B=2\nA=3\n")
    assert proc.returncode == 0, proc.stderr
    vault = base / "secrets" / "agents" / "hermes.env"
    assert vault.read_text() == "A=3\nB=2\n"
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    for d in (base / "secrets", base / "secrets" / "agents"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert [p.name for p in vault.parent.iterdir()] == ["hermes.env"]


def test_symlinked_secrets_dir_is_refused(base: Path, tmp_path: Path) -> None:
    victim = tmp_path / "etc-sudoers-d"
    (victim / "agents").mkdir(parents=True)
    victim.chmod(0o755)
    (base / "secrets").symlink_to(victim)
    proc = _run(base, "merge", "secrets agents", "hermes.env", "K=v\n")
    assert proc.returncode == 3
    assert "symlink" in proc.stderr
    assert list((victim / "agents").iterdir()) == []
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755  # never re-moded


def test_symlinked_leaf_dir_is_refused(base: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (base / "agents").symlink_to(victim)
    proc = _run(base, "replace", "agents", "hermes.env", "SECRET=1\n", modes=("0755", "0600"))
    assert proc.returncode == 3
    assert list(victim.iterdir()) == []


def test_planted_vault_symlink_is_never_read(base: Path, tmp_path: Path) -> None:
    (base / "secrets" / "agents").mkdir(parents=True)
    shadow = tmp_path / "shadow"
    shadow.write_text("root:$6$hash\n")
    (base / "secrets" / "agents" / "hermes.env").symlink_to(shadow)
    proc = _run(base, "merge", "secrets agents", "hermes.env", "K=v\n")
    assert proc.returncode == 3
    assert shadow.read_text() == "root:$6$hash\n"


def test_replace_writes_the_full_body(base: Path) -> None:
    proc = _run(base, "replace", "agents", "hermes.toml", "[agent]\n", modes=("0755", "0644"))
    assert proc.returncode == 0, proc.stderr
    seed = base / "agents" / "hermes.toml"
    assert seed.read_text() == "[agent]\n"
    assert stat.S_IMODE(seed.stat().st_mode) == 0o644


def test_every_write_verb_goes_through_the_writer() -> None:
    body = WRAPPER.read_text(encoding="utf-8")
    for verb in ("merge-secrets", "write-driver-env", "write-seed-toml"):
        arm = body[body.index(f"  {verb})") :]
        arm = arm[: arm.index(";;")]
        code = "\n".join(ln for ln in arm.splitlines() if not ln.strip().startswith("#"))
        assert '"$_SAFE_WRITE"' in code, verb
        for unsafe in ("install -d", "mktemp", "mv -f", "chown ", "chmod "):
            assert unsafe not in code, (verb, unsafe)


# Runs before the wrapper's program: after the real mkdirat of ``secrets`` it
# swaps in the attacker's own directory (``rename(2)`` replaces an empty one),
# exactly as the hal0 account could between the wrapper's mkdirat and openat.
# The suite is not root, so ``os.fstat`` reports the attacker directory under
# a foreign uid, which is what it would carry on a real host.
_RACE_PRELUDE = """
import os
_real_mkdir, _real_fstat = os.mkdir, os.fstat
_ATTACKER = os.environ["ATTACKER_DIR"]
_ino = os.stat(_ATTACKER).st_ino
def _mkdir(name, mode=0o777, *, dir_fd=None):
    _real_mkdir(name, mode, dir_fd=dir_fd)
    if name == "secrets":
        os.rmdir(name, dir_fd=dir_fd)
        os.rename(_ATTACKER, name, dst_dir_fd=dir_fd)
def _fstat(fd):
    st = _real_fstat(fd)
    if st.st_ino == _ino:
        st = os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 4242, 4242, *tuple(st)[6:10]))
    return st
os.mkdir, os.fstat = _mkdir, _fstat
"""


def test_dir_swapped_in_after_mkdir_is_refused(base: Path, tmp_path: Path) -> None:
    victim = tmp_path / "root-owned-victim"
    victim.mkdir()
    victim.chmod(0o755)
    attacker = tmp_path / "attacker-secrets"
    attacker.mkdir()
    attacker.chmod(0o755)
    (attacker / "agents").symlink_to(victim)
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _RACE_PRELUDE + _program(),
            "merge",
            str(base),
            "secrets agents",
            "0700",
            "0600",
            "hermes.env",
        ],
        input="K=v\n",
        text=True,
        capture_output=True,
        check=False,
        env={"ATTACKER_DIR": str(attacker), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 3, proc.stderr
    assert "not a root-only directory" in proc.stderr
    swapped = base / "secrets"
    assert stat.S_IMODE(swapped.stat().st_mode) == 0o755  # never re-moded
    assert list(victim.iterdir()) == []
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755


def test_group_writable_managed_dir_is_refused(base: Path) -> None:
    (base / "secrets").mkdir()
    (base / "secrets").chmod(0o2775)
    proc = _run(base, "merge", "secrets agents", "hermes.env", "K=v\n")
    assert proc.returncode == 3
    assert not (base / "secrets" / "agents").exists()


def test_every_python_call_is_isolated() -> None:
    # sudo keeps the caller's cwd; without -I, `python3 -c` imports a planted
    # secrets.py from it as root.
    code = "\n".join(
        ln
        for ln in WRAPPER.read_text(encoding="utf-8").splitlines()
        if not ln.strip().startswith("#")
    )
    calls = [ln for ln in code.splitlines() if "python3 " in ln]
    assert calls
    for ln in calls:
        assert "python3 -I " in ln, ln
