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
import sys
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


REPO_ROOT = INSTALL_SH.parents[1]
MARKER = ".install-in-progress"

_PREAMBLE = f"""
warn() {{ printf 'WARN: %s\\n' "$*"; }}
PY="${{TEST_PY}}"
REPO_ROOT="{REPO_ROOT}"
nofollow() {{ "${{PY}}" -I "${{REPO_ROOT}}/src/hal0/security/nofollow.py" "$@"; }}
"""


def _run(fragment, var_dir, dev_mode, *, py=None, extra="", bin_dir=None):
    script = (
        f'set -euo pipefail\nVAR_DIR="{var_dir}"\nDEV_MODE={dev_mode}\n'
        + _PREAMBLE
        + extra
        + fragment
        + "\necho REACHED_NEXT_LINE\n"
    )
    path = os.environ["PATH"] if bin_dir is None else f"{bin_dir}:{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": path, "TEST_PY": py or sys.executable},
        capture_output=True,
        text=True,
    )


def _create() -> str:
    return _slice(_CREATE_START, _CREATE_END)


def test_marker_created_before_system_user_step(tmp_path):
    assert TEXT.index(_CREATE_START) < TEXT.index(_CREATE_END)
    var_dir = tmp_path / "hal0"
    proc = _run(_create(), var_dir, 0)
    assert proc.returncode == 0, proc.stderr
    assert (var_dir / MARKER).is_file()
    assert "WARN" not in proc.stdout


def test_marker_created_when_directory_exists(tmp_path):
    proc = _run(_create(), tmp_path, 0)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / MARKER).is_file()


def test_directory_made_root_0755_through_nofollow(tmp_path):
    # Record the arguments of every nofollow call instead of running it.
    log = tmp_path / "calls"
    extra = f'nofollow() {{ printf "%s\\n" "$*" >> "{log}"; }}\n'
    var_dir = tmp_path / "absent"
    proc = _run(_create(), var_dir, 0, extra=extra)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text().splitlines()
    assert any(
        c.startswith("dir ") and "--owner root:root" in c and "--mode 0755" in c and str(var_dir) in c
        for c in calls
    ), calls
    assert any(
        c.startswith("write ") and "--owner root:root" in c and c.endswith(MARKER) for c in calls
    ), calls


def test_shell_fallback_creates_dir_root_0755(tmp_path):
    # No usable Python: the pure-shell path runs `install -d -m 0755 -o root -g root`.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "install-args"
    (bin_dir / "install").write_text(
        f'#!/bin/bash\necho "$*" > "{log}"\nmkdir -p "${{@: -1}}"\n'
    )
    (bin_dir / "install").chmod(0o755)
    var_dir = tmp_path / "absent"
    proc = _run(_create(), var_dir, 0, py="/nonexistent/python", bin_dir=bin_dir)
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().split() == ["-d", "-m", "0755", "-o", "root", "-g", "root", str(var_dir)]
    assert (var_dir / MARKER).is_file()


def test_planted_symlink_is_not_followed(tmp_path):
    for py in (sys.executable, "/nonexistent/python"):
        var_dir = tmp_path / ("a" if py == sys.executable else "b")
        var_dir.mkdir()
        victim = tmp_path / f"victim-{var_dir.name}"
        victim.write_text("precious")
        (var_dir / MARKER).symlink_to(victim)
        proc = _run(_create(), var_dir, 0, py=py)
        assert proc.returncode == 0, proc.stderr
        assert victim.read_text() == "precious"
        assert (var_dir / MARKER).is_file() and not (var_dir / MARKER).is_symlink()


def test_failure_to_create_warns_and_continues(tmp_path):
    for py in (sys.executable, "/nonexistent/python"):
        blocker = tmp_path / f"file-{Path(py).name}"
        blocker.write_text("not a directory")
        proc = _run(_create(), blocker / "hal0", 0, py=py)
        assert proc.returncode == 0, proc.stderr
        assert "WARN: could not write" in proc.stdout
        assert MARKER in proc.stdout
        assert "REACHED_NEXT_LINE" in proc.stdout


def test_marker_not_created_in_dev_mode(tmp_path):
    var_dir = tmp_path / "var"
    proc = _run(_create(), var_dir, 1)
    assert proc.returncode == 0, proc.stderr
    assert not var_dir.exists()


def test_uninstall_default_path_clears_marker():
    text = (INSTALL_SH.parent / "uninstall.sh").read_text(encoding="utf-8")
    rm = text.index('rm -f "${VAR_DIR}/.install-in-progress"')
    assert text.index('step "Data directories"') < rm
    assert rm < text.index('if [[ "${PURGE}" -eq 0 ]]; then', rm - 10)


def test_marker_removed_only_after_finalize(tmp_path):
    marker_name = MARKER
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
    assert marker_name not in head

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
