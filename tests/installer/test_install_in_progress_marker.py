"""installer/install.sh keeps an install-in-progress marker.

The marker exists exactly while an install is under way, so a re-run after an
abort can tell (``hal0_install_state`` in installer/lib/facts.sh reports
``unfinished``). Created just before the "System user" step, removed only after
``ui_step_finalize``. The two regions are sliced out by text markers and run
under real bash, the technique ``test_install_component_flags.py`` uses.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")

_CREATE_START = "# ── install-in-progress marker: create"
_CREATE_END = 'ui_step "System user"'
_REMOVE_START = "ui_step_finalize\n"
_REMOVE_END = 'info "Total install time'


def _slice(start: str, end: str) -> str:
    head = TEXT.rindex(start) if start == _REMOVE_START else TEXT.index(start)
    return TEXT[head : TEXT.index(end, head)]


def _run(fragment: str, var_dir: Path, dev_mode: int) -> subprocess.CompletedProcess:
    script = f'set -euo pipefail\nVAR_DIR="{var_dir}"\nDEV_MODE={dev_mode}\n' + fragment
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )


def test_marker_created_before_system_user_step(tmp_path):
    assert TEXT.index(_CREATE_START) < TEXT.index(_CREATE_END)
    var_dir = tmp_path / "missing" / "hal0"
    # A fresh box: the state directory does not exist yet. (Not root here, so
    # the chown inside the fragment is allowed to be a no-op via a stubbed
    # install; the marker file is what matters.)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "install").write_text(
        '#!/bin/bash\nwhile [[ "$1" == -* ]]; do case "$1" in -d) ;; -m|-o|-g) shift;; esac; shift; done\n'
        'mkdir -p "$@"\n'
    )
    (bin_dir / "install").chmod(0o755)
    script = (
        f'set -euo pipefail\nPATH="{bin_dir}:$PATH"\nVAR_DIR="{var_dir}"\nDEV_MODE=0\n'
        + _slice(_CREATE_START, _CREATE_END)
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (var_dir / ".install-in-progress").is_file()


def test_marker_created_when_directory_exists(tmp_path):
    proc = _run(_slice(_CREATE_START, _CREATE_END), tmp_path, 0)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / ".install-in-progress").is_file()


def test_marker_not_created_in_dev_mode(tmp_path):
    var_dir = tmp_path / "var"
    proc = _run(_slice(_CREATE_START, _CREATE_END), var_dir, 1)
    assert proc.returncode == 0, proc.stderr
    assert not var_dir.exists()


def test_marker_removed_only_after_finalize(tmp_path):
    removal = "rm -f"
    marker_name = ".install-in-progress"
    tail = TEXT[TEXT.rindex(_REMOVE_START) :]
    assert tail.index('rm -f "${INSTALL_MARKER}"') < tail.index(_REMOVE_END)

    # No path between creation and finalize touches the marker.
    between = TEXT[TEXT.index(_CREATE_END) : TEXT.rindex(_REMOVE_START)]
    assert "INSTALL_MARKER" not in between
    assert marker_name not in between
    # Failure paths (ERR trap, die, hal0_report_on_exit) never reference it.
    for path in (INSTALL_SH.parent / "lib").glob("*.sh"):
        if path.name == "facts.sh":
            continue
        assert marker_name not in path.read_text(encoding="utf-8"), path.name
    head = TEXT[: TEXT.index(_CREATE_START)]
    assert "INSTALL_MARKER" not in head
    assert removal in _slice(_REMOVE_START, _REMOVE_END)

    # The removal fragment deletes the marker and tolerates its absence.
    (tmp_path / marker_name).write_text("")
    fragment = _slice(_REMOVE_START, _REMOVE_END).replace("ui_step_finalize\n", "")
    fragment = 'INSTALL_MARKER="${VAR_DIR}/.install-in-progress"\n' + fragment
    assert _run(fragment, tmp_path, 0).returncode == 0
    assert not (tmp_path / marker_name).exists()
    assert _run(fragment, tmp_path, 0).returncode == 0


def test_no_exit_between_create_and_finalize_removes_marker():
    body = TEXT[TEXT.index(_CREATE_END) : TEXT.rindex(_REMOVE_START)]
    assert not re.search(r"\.install-in-progress|INSTALL_MARKER", body)
