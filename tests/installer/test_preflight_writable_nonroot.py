"""``preflight_writable`` must not FAIL a healthy box for a non-root operator (#2278).

``preflight_writable`` probes the install-time system trees (``/opt``,
``/usr/lib``, ``/etc/hal0``, ``/etc/systemd/system``, ``/var/lib``,
``/usr/local/bin``). On a correctly installed box those are root's, so an
operator running ``hal0 doctor`` from their own shell (which shells
``preflight_all`` -> ``preflight_writable`` with no args) got a red
"not writable" row and a failed doctor for every one of them.

The rule: the probe is only meaningful as root (install.sh runs it after its
sudo re-exec). As root an unwritable tree (ro mount, overlay LXC) stays a
hard FAIL; as non-root an unwritable tree is an informational row that says
how to verify it, never a FAIL.

``EUID`` is read-only in bash and ``[[ -w ]]`` is always true for root on a
writable mount, so the tests drive the real function with the two seams it
exposes stubbed after sourcing (``_preflight_is_root`` and
``_preflight_path_writable``) — the same redefine-after-source technique as
``test_preflight_all_summary.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI = _REPO_ROOT / "installer" / "lib" / "ui.sh"
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"


def _run(tmp_path: Path, *, root: bool, unwritable: tuple[str, ...]) -> tuple[int, str]:
    """Run the real ``preflight_writable`` over three real dirs under *tmp_path*.

    *root* stubs the effective-uid check; *unwritable* names the dirs (by
    basename) the writability probe reports as not writable.
    """
    dirs = []
    for name in ("opt", "lib", "etc"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(str(d))
    deny = " ".join(str(tmp_path / n) for n in unwritable)
    script = (
        "set -uo pipefail\n"
        "export HAL0_PLAIN=1\n"
        f"source {_UI!s}\n"
        f"source {_PREFLIGHT!s}\n"
        f"_preflight_is_root() {{ return {0 if root else 1}; }}\n"
        f"_deny=({deny})\n"
        '_preflight_path_writable() { local x; for x in "${_deny[@]}"; do\n'
        '    [[ "$1" == "$x" ]] && return 1; done; return 0; }\n'
        f"preflight_writable {' '.join(dirs)}; rc=$?\n"
        'echo "EXIT:${rc} ERRS:${UI_ERR_COUNT} WARNS:${UI_WARN_COUNT}"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    tail = [line for line in proc.stdout.splitlines() if line.startswith("EXIT:")]
    assert tail, out
    return int(tail[-1].split()[0].split(":")[1]), out


def test_non_root_healthy_box_is_info_not_fail(tmp_path: Path) -> None:
    """Root-owned install trees, operator shell -> rc 0, no error/warning row."""
    rc, out = _run(tmp_path, root=False, unwritable=("opt", "lib", "etc"))
    assert rc == 0, out
    assert "ERRS:0 WARNS:0" in out, out
    assert "not writable:" not in out, out
    assert "sudo hal0 doctor" in out, out
    paths = " ".join(str(tmp_path / n) for n in ("opt", "lib", "etc"))
    assert f"cannot verify {paths} (" in out, out


def test_non_root_mixed_set_names_only_the_unwritable_tree(tmp_path: Path) -> None:
    """One unwritable tree among writable ones -> rc 0, and only it is named."""
    rc, out = _run(tmp_path, root=False, unwritable=("lib",))
    assert rc == 0, out
    assert "ERRS:0 WARNS:0" in out, out
    assert "not writable:" not in out, out
    assert f"cannot verify {tmp_path / 'lib'} (" in out, out
    assert str(tmp_path / "opt") not in out, out
    assert str(tmp_path / "etc") not in out, out
    assert "writable paths: ok" not in out, out


def test_non_root_all_writable_is_ok(tmp_path: Path) -> None:
    rc, out = _run(tmp_path, root=False, unwritable=())
    assert rc == 0, out
    assert "writable paths: ok" in out, out


def test_root_healthy_box_passes(tmp_path: Path) -> None:
    rc, out = _run(tmp_path, root=True, unwritable=())
    assert rc == 0, out
    assert "writable paths: ok" in out, out
    assert "ERRS:0" in out, out


def test_root_unwritable_tree_still_fails(tmp_path: Path) -> None:
    """A genuine misconfiguration (root cannot write an install tree) stays a FAIL."""
    rc, out = _run(tmp_path, root=True, unwritable=("lib",))
    assert rc == 1, out
    assert f"not writable: {tmp_path / 'lib'}" in out, out
    assert "ERRS:1" in out, out
