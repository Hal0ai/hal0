"""#1465 — install.sh must fail loud when a privileged seam does not install.

Before this, ``install.sh`` warned mid-log and carried on to its success box,
and nothing verified the seams afterwards. These tests pin both halves of the
fix: the installer now *dies* on a missing/invalid grant for the two
load-bearing seams, and ``preflight_seams`` (installer/lib/preflight.sh) is a
real post-install assertion the installer runs.

The shell function is exercised directly against fake directories, so this
needs no root, no sudo and no provisioned box.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO / "installer" / "lib" / "preflight.sh"
INSTALL_SH = REPO / "installer" / "install.sh"


def _call(func: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source preflight.sh (+ its ui.sh deps) and invoke one function."""
    script = f"""
set -uo pipefail
source "{REPO}/installer/lib/ui.sh"
source "{PREFLIGHT}"
{func} {" ".join(f'"{a}"' for a in args)}
echo "rc=$?"
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


def _seam_dirs(tmp_path: Path, *, names: tuple[str, ...] = ("hal0-systemctl", "hal0-update")):
    bin_dir = tmp_path / "bin"
    sudoers_dir = tmp_path / "sudoers.d"
    bin_dir.mkdir()
    sudoers_dir.mkdir()
    for name in names:
        (bin_dir / name).write_text("#!/bin/bash\n")
        (bin_dir / name).chmod(0o755)
        (sudoers_dir / name).write_text(f"hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/{name}\n")
        (sudoers_dir / name).chmod(0o440)
    return bin_dir, sudoers_dir


# ── the shell predicate ────────────────────────────────────────────────────────


def test_missing_wrapper_is_reported_as_a_failure(tmp_path: Path) -> None:
    bin_dir, sudoers = _seam_dirs(tmp_path)
    (bin_dir / "hal0-systemctl").unlink()

    proc = _call("_preflight_seam", "hal0-systemctl", "required", str(bin_dir), str(sudoers))

    assert "rc=1" in proc.stdout
    assert "wrapper" in proc.stderr and "is missing" in proc.stderr


def test_missing_sudoers_grant_is_reported_as_a_failure(tmp_path: Path) -> None:
    bin_dir, sudoers = _seam_dirs(tmp_path)
    (sudoers / "hal0-update").unlink()

    proc = _call("_preflight_seam", "hal0-update", "required", str(bin_dir), str(sudoers))

    assert "rc=1" in proc.stdout
    assert "sudoers grant" in proc.stderr and "is missing" in proc.stderr


def test_wrong_sudoers_mode_is_reported(tmp_path: Path) -> None:
    """sudo silently ignores a drop-in that is not 0440 root — a total failure."""
    bin_dir, sudoers = _seam_dirs(tmp_path)
    (sudoers / "hal0-update").chmod(0o644)

    proc = _call("_preflight_seam", "hal0-update", "required", str(bin_dir), str(sudoers))

    assert "rc=1" in proc.stdout
    assert "expected root 440" in proc.stderr


def test_an_optional_seam_only_warns(tmp_path: Path) -> None:
    bin_dir, sudoers = _seam_dirs(tmp_path, names=("hal0-benchctl",))
    (bin_dir / "hal0-benchctl").unlink()

    proc = _call("_preflight_seam", "hal0-benchctl", "optional", str(bin_dir), str(sudoers))

    assert "rc=1" in proc.stdout  # still reported…
    assert "✖" not in proc.stderr  # …but not as an error glyph


def test_preflight_seams_is_a_no_op_when_not_root(tmp_path: Path) -> None:
    """A grant written for `hal0` cannot be exercised from another account."""
    bin_dir, sudoers = _seam_dirs(tmp_path)

    proc = _call("preflight_seams", str(bin_dir), str(sudoers))

    assert "rc=0" in proc.stdout
    assert "skipping" in proc.stderr


def test_preflight_seams_is_not_in_preflight_all() -> None:
    """Preflight runs BEFORE the seams exist; asserting them there would always fail."""
    text = PREFLIGHT.read_text()
    body = text.split("preflight_all() {", 1)[1].split("}", 1)[0]
    assert "preflight_seams" not in body
    assert "preflight_seams()" in text


# ── inventory parity ───────────────────────────────────────────────────────────


def test_shell_and_python_inventories_agree() -> None:
    from hal0.system.seam_check import SEAMS

    text = PREFLIGHT.read_text()
    required_line = next(
        line for line in text.splitlines() if line.startswith("HAL0_REQUIRED_SEAMS=")
    )
    optional_line = next(
        line for line in text.splitlines() if line.startswith("HAL0_OPTIONAL_SEAMS=")
    )
    shell_required = set(required_line.split("(", 1)[1].rstrip(")").replace('"', "").split())
    shell_optional = set(optional_line.split("(", 1)[1].rstrip(")").replace('"', "").split())

    assert shell_required == {s.name for s in SEAMS if s.required}
    assert shell_optional == {s.name for s in SEAMS if not s.required}


# ── install.sh no longer ships a box with a broken grant ──────────────────────


@pytest.mark.parametrize("seam", ["hal0-systemctl", "hal0-update"])
def test_install_sh_dies_on_a_failed_grant_for_a_required_seam(seam: str) -> None:
    text = INSTALL_SH.read_text()
    var = "SYSTEMCTL_SUDOERS_SRC" if seam == "hal0-systemctl" else "UPDATE_SUDOERS_SRC"
    block = text.split(f'{var}="', 1)[1].split("# Privileged seam", 1)[0]
    assert "failed visudo check" in block
    # The pre-#1465 behaviour was `warn`; a required grant must now abort.
    assert 'warn "${' + var + "} failed visudo check" not in block
    assert block.count("die ") >= 2


def test_install_sh_installs_the_update_seam_and_grant() -> None:
    text = INSTALL_SH.read_text()
    assert 'install -m 0755 "${UPDATE_SRC}" "${LIB_DIR}/bin/hal0-update"' in text
    assert 'install -m 0440 "${UPDATE_SUDOERS_SRC}" "${UPDATE_SUDOERS_DST}"' in text


def test_install_sh_runs_the_post_install_seam_assertion() -> None:
    text = INSTALL_SH.read_text()
    assert "preflight_seams " in text
    assert "privileged seam verification failed" in text


def test_install_sh_requires_visudo_on_a_real_install() -> None:
    text = INSTALL_SH.read_text()
    assert "visudo not found" in text


def test_restart_self_queues_the_job_instead_of_blocking() -> None:
    """`restart-self` must pass --no-block (#1540).

    hal0-api invokes this verb from inside hal0-api.service's own cgroup. A
    blocking `systemctl restart` waits for the unit to stop — but stopping
    the unit SIGTERMs that systemctl too, so the caller sees a signal-killed
    status for a restart that actually succeeded, and any bookkeeping after
    the call never runs. Queue the job and return.
    """
    wrapper = (REPO / "installer" / "wrappers" / "hal0-systemctl").read_text(encoding="utf-8")
    arm = wrapper.split("restart-self)", 1)[1].split(";;", 1)[0]
    assert "--no-block" in arm, f"restart-self must use --no-block, got:\n{arm}"
