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


def _run(repo: Path, python: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # An empty PATH dir keeps npm/curl/systemctl out of the picture.
    return subprocess.run(
        ["bash", "scripts/deploy.sh", "--no-build", "--no-restart", "--force"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HAL0_VENV_PYTHON": str(python),
            "HAL0_NO_CHGRP": "1",
            "HAL0_PORT": "1",
            "HOME": str(tmp_path),
        },
        timeout=60,
    )


def test_refuses_copied_install_before_touching_checkout(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    marker = repo / "scratch.txt"
    marker.write_text("x")
    _git(repo, "add", "scratch.txt")  # tracked edit would be wiped by reset --hard
    python = _fake_python(
        tmp_path, "/usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/__init__.py"
    )
    proc = _run(repo, python, tmp_path)
    assert proc.returncode != 0
    assert "copied, non-editable install" in proc.stderr
    assert "deploy complete" not in proc.stdout
    assert marker.exists(), "guard must fire before git reset --hard"


def test_editable_install_passes_the_guard(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    python = _fake_python(tmp_path, str(repo / "src" / "hal0" / "__init__.py"))
    proc = _run(repo, python, tmp_path)
    assert "served package is this checkout" in proc.stdout
    assert "non-editable" not in proc.stderr
