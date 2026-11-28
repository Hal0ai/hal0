"""The main venv is rebuilt when it no longer works with the selected Python (#3959).

After an in-place OS release upgrade the system python moves (3.12 -> 3.14 on
Ubuntu 24.04 -> 26.04). The venv's ``bin/python`` then resolves to the new
interpreter while its packages still sit under the old ``lib/pythonX.Y``, so
the installer's first ``pip`` call died and a re-run could not repair the box.

The venv block and the recovery hint are sliced out of install.sh and run
under ``bash -euo pipefail`` against real venvs built by this interpreter.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")


def _slice(start: str, end: str) -> str:
    s = TEXT.index(start)
    return TEXT[s : TEXT.index(end, s)]


VENV_BLOCK = _slice("main_venv_stale_reason() {", 'PIP="${VENV_DIR}/bin/pip"\n')
HINT_FN = _slice("python_env_recovery_hint() {", "# _hal0_report_path IS assigned below")
PREAMBLE = 'set -euo pipefail\ninfo() { echo "INFO $*"; }\nwarn() { echo "WARN $*"; }\n'


def _make_venv(path: Path) -> Path:
    subprocess.run([sys.executable, "-I", "-m", "venv", str(path)], check=True)
    marker = path / "old-install-marker"
    marker.write_text("x")
    return marker


def _run_block(venv: Path, py: str = sys.executable) -> str:
    script = f'{PREAMBLE}PY="{py}"\nVENV_DIR="{venv}"\n{VENV_BLOCK}'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout


def _pip_imports(venv: Path) -> bool:
    return (
        subprocess.run(
            [str(venv / "bin" / "python"), "-I", "-c", "import pip"], capture_output=True
        ).returncode
        == 0
    )


def test_missing_venv_is_created(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    out = _run_block(venv)
    assert "INFO created venv" in out
    assert _pip_imports(venv)


def test_working_venv_is_reused(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    marker = _make_venv(venv)
    out = _run_block(venv)
    assert out == ""
    assert marker.exists()


def test_venv_whose_packages_belong_to_a_removed_python_is_rebuilt(tmp_path: Path) -> None:
    """The release-upgrade shape: bin/python runs, lib/ is the old version's."""
    venv = tmp_path / "venv"
    marker = _make_venv(venv)
    (libdir,) = (venv / "lib").glob("python3.*")
    libdir.rename(venv / "lib" / "python3.1")
    assert not _pip_imports(venv)
    out = _run_block(venv)
    assert "WARN rebuilding the venv" in out
    assert "pip does not import" in out
    assert "INFO rebuilt venv" in out
    assert _pip_imports(venv)
    assert not marker.exists()


def test_venv_with_a_dead_interpreter_is_rebuilt(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    _make_venv(venv)
    for name in ("python", "python3"):
        link = venv / "bin" / name
        link.unlink()
        link.symlink_to("/nonexistent/python3.12")
    out = _run_block(venv)
    assert "its interpreter no longer runs" in out
    assert _pip_imports(venv)


def test_venv_on_another_python_version_is_rebuilt(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    _make_venv(venv)
    # A selected interpreter that reports another minor version, and otherwise
    # is this one (so the rebuild itself works).
    fake = tmp_path / "python-other"
    fake.write_text(
        "#!/bin/bash\n"
        'case "$*" in *version_info*) echo 3.99; exit 0 ;; esac\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    fake.chmod(0o755)
    out = _run_block(venv, py=str(fake))
    this = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert f"it runs Python {this} and this install uses Python 3.99" in out
    assert "INFO rebuilt venv" in out


def _run_hint(tmp_path: Path, present: list[str]) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in present:
        f = bindir / name
        f.write_text("#!/bin/bash\n")
        f.chmod(0o755)
    script = f"{PREAMBLE}{HINT_FN}\npython_env_recovery_hint\n"
    return subprocess.run(
        ["/bin/bash", "-c", script],
        env={**os.environ, "PATH": str(bindir)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_hint_names_only_interpreters_the_box_has(tmp_path: Path) -> None:
    out = _run_hint(tmp_path, ["python3.14"])
    assert "on this box: python3.14)" in out
    assert "python3.12" not in out.replace("Python 3.12 or newer", "")


def test_hint_without_any_compatible_interpreter(tmp_path: Path) -> None:
    out = _run_hint(tmp_path, [])
    assert "No python3.12 or newer is on PATH" in out


def test_trap_no_longer_hardcodes_python312() -> None:
    assert "Retry with HAL0_PYTHON=python3.12" not in TEXT
    assert '"Python environment")\n            python_env_recovery_hint ;;' in TEXT
