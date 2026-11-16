"""Contract tests for scripts/deploy.sh's copied-install guard (#3475).

deploy.sh only changes the served backend when the venv imports hal0 from the
checkout it resets. On a copied FHS install it must refuse, before resetting
anything, instead of printing "deploy complete".
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _checkout(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, repo / "scripts" / "deploy.sh")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _fake_python(tmp_path: Path, origin: str) -> Path:
    fake = tmp_path / "python"
    fake.write_text(f"#!{sys.executable}\nprint({origin!r})\n")
    fake.chmod(0o755)
    return fake


def _stubs(tmp_path: Path, exec_bin: Path | None = None) -> Path:
    """A PATH dir with a fast curl (no 15x sleep loop) and an optional systemctl."""
    bindir = tmp_path / "stubs"
    bindir.mkdir(exist_ok=True)
    curl = bindir / "curl"
    curl.write_text('#!/bin/sh\ncase "$*" in *-w*) echo 200;; *) echo \'{"status":"ok"}\';; esac\n')
    curl.chmod(0o755)
    if exec_bin is not None:
        systemctl = bindir / "systemctl"
        systemctl.write_text(
            "#!/bin/sh\n"
            f'[ "$1" = show ] && echo "{{ path={exec_bin} ; argv[]={exec_bin} serve ; }}"\n'
            "exit 0\n"
        )
        systemctl.chmod(0o755)
    return bindir


def _run(
    repo: Path,
    python: Path | None,
    tmp_path: Path,
    *args: str,
    exec_bin: Path | None = None,
    restart: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{_stubs(tmp_path, exec_bin)}:/usr/bin:/bin",
        "HAL0_NO_CHGRP": "1",
        "HAL0_PORT": "1",
        "HOME": str(tmp_path),
    }
    if python is not None:
        env["HAL0_VENV_PYTHON"] = str(python)
    flags = ["--no-build", "--force", *args]
    if not restart:
        flags.append("--no-restart")
    return subprocess.run(
        ["bash", "scripts/deploy.sh", *flags],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _editable(tmp_path: Path) -> tuple[Path, Path]:
    repo = _checkout(tmp_path)
    init = repo / "src" / "hal0" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text("")
    return repo, _fake_python(tmp_path, str(init.resolve()))


def test_refuses_copied_install_before_touching_checkout(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    marker = repo / "scratch.txt"
    marker.write_text("x")
    _git(repo, "add", "scratch.txt")  # a tracked edit would be wiped by the reset
    python = _fake_python(
        tmp_path, "/usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/__init__.py"
    )
    proc = _run(repo, python, tmp_path)
    assert proc.returncode != 0
    assert "copied, non-editable install" in proc.stderr
    assert "deploy complete" not in proc.stdout
    assert marker.exists(), "guard must fire before the checkout is reset"


def test_editable_install_passes_the_guard(tmp_path: Path) -> None:
    repo, python = _editable(tmp_path)
    proc = _run(repo, python, tmp_path)
    assert "served package is this checkout" in proc.stdout
    assert "non-editable" not in proc.stderr
    # --no-restart: the identity is unverifiable, so no green "deploy complete".
    assert proc.returncode == 0
    assert "deploy complete" not in proc.stdout
    assert "NOT verified" in proc.stdout


def test_empty_build_sha_on_editable_layout_is_not_fatal(tmp_path: Path) -> None:
    """A root-owned editable checkout reports no build_sha (hal0 cannot read .git)."""
    repo, python = _editable(tmp_path)
    # Restart on; no unit exists so the restart is skipped, and the curl stub
    # reports no build_sha.
    proc = _run(repo, python, tmp_path, restart=True)
    assert proc.returncode == 0, proc.stderr
    assert "build identity not verified" in proc.stdout
    assert "hal0 update" not in proc.stdout + proc.stderr
    assert "deploy complete" not in proc.stdout


def test_guard_checks_the_interpreter_of_the_named_service(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    venv_bin = tmp_path / "custom" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    exec_bin = venv_bin / "hal0"
    exec_bin.write_text("#!/bin/sh\n")
    exec_bin.chmod(0o755)
    py = venv_bin / "python"
    py.write_text(
        f"#!{sys.executable}\nprint('/custom/venv/lib/python3.12/site-packages/hal0/__init__.py')\n"
    )
    py.chmod(0o755)
    # No HAL0_VENV_PYTHON: the interpreter must come from the service's ExecStart.
    proc = _run(repo, None, tmp_path, "--service=hal0-custom", exec_bin=exec_bin)
    assert proc.returncode != 0
    assert "copied, non-editable install" in proc.stderr
    assert str(py) in proc.stderr
