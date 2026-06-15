"""Tests for the shared run-as-hal0 privilege-drop guard.

``installer/lib/run-as-hal0.sh`` is sourced by the hermes wrapper (and any
future agent wrapper). Its sole job: when a hal0-managed process is launched
as root, re-exec it as the unprivileged service user with that user's HOME and
a sanitized env, so we never write a split-brain ``/root/.hermes`` tree or
``root:root`` perms (#843). It is a no-op for non-root callers.

The guard exposes one function, ``hal0_ensure_runas <user> <cmd...>``:
  * non-root          -> return 0 (caller proceeds with its own perms)
  * root + opt-out    -> return 0 (HAL0_ALLOW_ROOT=1 — deliberate root debug)
  * root              -> exec <cmd...> as <user> (process is replaced)
  * root, no dropper  -> return non-zero + refuse (never proceed as root)

Tests drive the guard through ``sh`` with:
  * ``HAL0_RUNAS_TEST_UID`` — a documented test-only seam to fake the euid
    (we can't become root in CI).
  * a stub ``runuser`` on PATH that echoes its argv instead of switching users,
    so we can assert exactly what would be exec'd.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD = _REPO_ROOT / "installer" / "lib" / "run-as-hal0.sh"


def _existing_unprivileged_user() -> str:
    """A user that exists on the test host but isn't us — 'nobody' on Linux."""
    if subprocess.run(["id", "nobody"], capture_output=True).returncode == 0:
        return "nobody"
    return os.environ.get("USER", "")


def _run_guard(
    script: str,
    *,
    env: dict[str, str] | None = None,
    extra_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Source the guard and run ``script`` under /bin/sh, capturing output."""
    full_env = dict(os.environ)
    if extra_path is not None:
        full_env["PATH"] = f"{extra_path}{os.pathsep}{full_env['PATH']}"
    if env:
        full_env.update(env)
    body = f". {_GUARD}\n{script}\n"
    return subprocess.run(
        ["/bin/sh", "-c", body],
        capture_output=True,
        text=True,
        env=full_env,
    )


def _write_stub(dir_: Path, name: str, body: str) -> None:
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def test_guard_file_exists() -> None:
    assert _GUARD.is_file(), f"guard library missing at {_GUARD}"


def test_non_root_returns_without_running_command() -> None:
    """A non-root caller proceeds with its own perms — the guard must NOT
    run (or exec) the command; the wrapper runs the real command itself."""
    res = _run_guard(
        "hal0_ensure_runas hal0 echo SHOULD_NOT_RUN; echo RETURNED",
        env={"HAL0_RUNAS_TEST_UID": "1000"},
    )
    assert res.returncode == 0, res.stderr
    assert "RETURNED" in res.stdout
    assert "SHOULD_NOT_RUN" not in res.stdout


def test_root_with_opt_out_does_not_reexec() -> None:
    """HAL0_ALLOW_ROOT=1 lets a deliberate root session through unchanged."""
    res = _run_guard(
        "hal0_ensure_runas hal0 echo X; echo RETURNED",
        env={"HAL0_RUNAS_TEST_UID": "0", "HAL0_ALLOW_ROOT": "1"},
    )
    assert res.returncode == 0, res.stderr
    assert "RETURNED" in res.stdout


def test_root_reexecs_as_target_user_via_runuser(tmp_path: Path) -> None:
    """As root, the guard re-execs the command as the target user with HOME
    set and HERMES_HOME stripped, preferring runuser. The stub captures argv."""
    user = _existing_unprivileged_user()
    if not user:
        pytest.skip("no unprivileged target user available on this host")
    _write_stub(tmp_path, "runuser", 'echo "RUNUSER $*"')
    res = _run_guard(
        f"hal0_ensure_runas {user} echo HELLO; echo SHOULD_NOT_REACH",
        env={"HAL0_RUNAS_TEST_UID": "0"},
        extra_path=tmp_path,
    )
    assert res.returncode == 0, res.stderr
    assert f"-u {user}" in res.stdout
    assert "echo HELLO" in res.stdout
    assert "HERMES_HOME" in res.stdout  # explicitly stripped via `env -u`
    assert "HOME=" in res.stdout  # target HOME forced
    assert "SHOULD_NOT_REACH" not in res.stdout


_HERMES_WRAPPER = _REPO_ROOT / "installer" / "wrappers" / "hermes"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


def test_install_sh_installs_guard_into_lib_dir() -> None:
    """install.sh must drop the guard lib at the absolute path the wrapper
    sources (``${LIB_DIR}/guards/run-as-hal0.sh``), matching the hermes-hooks
    install idiom so dev-mode PREFIX shadowing works."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert "run-as-hal0.sh" in text
    assert "guards" in text
    assert "install -m 0755" in text and "guards/run-as-hal0.sh" in text


def test_hermes_wrapper_sources_guard_and_calls_it_first() -> None:
    """The hermes wrapper must source the guard and invoke it before doing any
    real work (sourcing secrets, exec'ing the venv binary), so a root launch is
    re-exec'd as hal0 before anything touches state."""
    text = _HERMES_WRAPPER.read_text(encoding="utf-8")
    assert "run-as-hal0.sh" in text
    assert "hal0_ensure_runas hal0" in text
    # Guard call precedes both the secrets sourcing and the final exec.
    guard_at = text.index("hal0_ensure_runas hal0")
    assert guard_at < text.index("HAL0_HERMES_SECRETS")
    assert guard_at < text.rindex("exec ")


def test_root_without_any_dropper_refuses(tmp_path: Path) -> None:
    """If no runuser/setpriv/sudo is available, the guard refuses (non-zero)
    rather than silently proceeding as root."""
    # Minimal PATH: only id + getent stubs, NO privilege-drop tools.
    _write_stub(tmp_path, "id", "echo 0")  # `id <user>` -> exists (exit 0)
    _write_stub(tmp_path, "getent", 'echo "x:x:0:0:x:/nonexistent:/bin/sh"')
    res = subprocess.run(
        ["/bin/sh", "-c", f". {_GUARD}\nhal0_ensure_runas hal0 echo X\n"],
        capture_output=True,
        text=True,
        env={"PATH": str(tmp_path), "HAL0_RUNAS_TEST_UID": "0"},
    )
    assert res.returncode != 0
    assert "root" in (res.stderr + res.stdout).lower()
