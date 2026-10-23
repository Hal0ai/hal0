"""install.sh holds a re-run over an existing install to the upgrade floor.

``hal0 update`` refuses to stage a release from a box below manifest.json's
``upgrade_from`` (``updater._enforce_upgrade_floor``). Re-running the installer
over an existing install is the other upgrade path (the curl one-liner), so it
must refuse the same boxes, before it changes anything.

The ``check_upgrade_floor`` function is lifted out of install.sh and run under
real bash against a fake FHS root, with the test interpreter (which carries
``packaging``) standing in for the existing install's venv python.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_STUBS = (
    "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
    "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
    "err()  { printf 'ERR: %s\\n' \"$*\" >&2; }\n"
    'die()  { err "$*"; exit 1; }\n'
)


def _function_source() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"^check_upgrade_floor\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert m is not None, "check_upgrade_floor() not found in install.sh"
    return m.group(0)


def _fhs(tmp_path: Path, installed: str | None, *, version_file: bool = True) -> Path:
    """A fake /usr/lib/hal0 with ``current`` -> ``hal0-<installed>``."""
    root = tmp_path / "usr-lib-hal0"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").symlink_to(sys.executable)
    if installed is not None:
        tree = root / f"hal0-{installed}"
        tree.mkdir()
        if version_file:
            (tree / "VERSION").write_text(f"{installed}\n", encoding="utf-8")
        (root / "current").symlink_to(tree)
    return root


def _run(
    tmp_path: Path,
    installed: str | None,
    *,
    upgrade_from: str | None = None,
    dev_mode: int = 0,
    version_file: bool = True,
) -> subprocess.CompletedProcess[str]:
    root = _fhs(tmp_path, installed, version_file=version_file)
    if upgrade_from is None:
        repo_root = _REPO_ROOT
    else:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "manifest.json").write_text(
            json.dumps({"upgrade_from": upgrade_from}), encoding="utf-8"
        )
    script = (
        "set -euo pipefail\n"
        + _STUBS
        + f"DEV_MODE={dev_mode}\n"
        + f"CURRENT_LINK={root / 'current'!s}\n"
        + f"VENV_DIR={root / 'venv'!s}\n"
        + f"REPO_ROOT={repo_root!s}\n"
        + f"PY={sys.executable}\n"
        + "VERSION=1.6.0\n"
        + _function_source()
        + "check_upgrade_floor\n"
        + "echo FLOOR_PASSED\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@pytest.mark.parametrize(
    "installed",
    [
        "1.3.1",
        "1.3.0",
        "1.2.0",
        "0.9.8",
        "1.0.0-nightly.20260801120000",
        "1.4.0-nightly.20261011035008",
    ],
)
def test_rerun_below_the_shipped_floor_is_refused(tmp_path: Path, installed: str) -> None:
    proc = _run(tmp_path, installed)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FLOOR_PASSED" not in proc.stdout
    assert f"hal0 {installed} is installed" in proc.stderr
    assert "upgrades only from >=1.4.0" in proc.stderr
    assert "Upgrade to 1.4.0 first or reinstall" in proc.stderr
    assert "releases/download/v1.4.0/stable.json" in proc.stderr


@pytest.mark.parametrize("installed", ["1.4.0", "1.4.1", "1.5.0", "1.5.0-nightly.20261019194027"])
def test_rerun_inside_the_window_proceeds(tmp_path: Path, installed: str) -> None:
    proc = _run(tmp_path, installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FLOOR_PASSED" in proc.stdout


def test_fresh_install_has_nothing_to_check(tmp_path: Path) -> None:
    proc = _run(tmp_path, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_dev_mode_is_exempt(tmp_path: Path) -> None:
    proc = _run(tmp_path, "1.2.0", dev_mode=1)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_version_falls_back_to_the_release_dir_name(tmp_path: Path) -> None:
    proc = _run(tmp_path, "1.2.0", version_file=False)
    assert proc.returncode == 1
    assert "hal0 1.2.0 is installed" in proc.stderr


def test_no_declared_floor_proceeds(tmp_path: Path) -> None:
    proc = _run(tmp_path, "0.9.8", upgrade_from="")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_unparseable_installed_version_fails_open(tmp_path: Path) -> None:
    proc = _run(tmp_path, "not-a-version")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_floor_check_runs_before_anything_is_written() -> None:
    """The call sits in the pre-flight block, ahead of the first mutating step."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    call = text.index("\ncheck_upgrade_floor\n")
    assert call < text.index('ui_step "System user"')
    assert call < text.index('ui_step "Filesystem layout"')
