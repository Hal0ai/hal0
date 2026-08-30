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


# ── the grant probe: transients, and honest reporting (#2084) ─────────────────
#
# install.sh runs preflight_seams in the same log second as "wrote
# /etc/sudoers.d/hal0-podman-ro", on a box whose hal0 user was created minutes
# earlier. rc.10/ct151 warned there; the identical invocation succeeded on the
# untouched box minutes later. These pin the retry AND the message, because
# the message is what an operator (or a validation agent) acts on.


def _call_probe(
    tmp_path: Path,
    *,
    fail_attempts: int,
    stderr_line: str = "",
    attempts: int = 3,
    name: str = "hal0-podman-ro",
    required: str = "optional",
    set_e: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Drive `_preflight_seam` past its stat checks with a stubbed grant probe.

    Two shell-level stubs, both installed *after* sourcing so they shadow the
    real definitions: ``stat`` (tmp files belong to the test user, and the
    ownership check would otherwise short-circuit before the probe) and
    ``_hal0_seam_probe_run`` (no root, no sudo, no provisioned box in CI).

    ``set -e`` is on by default because install.sh runs ``set -euo pipefail``:
    a retry loop that aborts the installer on its first failed attempt would
    be a worse bug than the one being fixed.
    """
    bin_dir, sudoers_dir = _seam_dirs(tmp_path, names=(name,))
    counter = tmp_path / "attempts"
    argv_log = tmp_path / "argv"
    flags = "set -euo pipefail" if set_e else "set -uo pipefail"
    script = f"""
{flags}
source "{REPO}/installer/lib/ui.sh"
source "{PREFLIGHT}"

stat() {{
    case "$2" in
        '%a')    case "$3" in *sudoers.d*) echo 440 ;; *) echo 755 ;; esac ;;
        '%U:%G') echo root:root ;;
        '%U')    echo root ;;
    esac
}}

echo 0 > "{counter}"
_hal0_seam_probe_run() {{
    local n
    n=$(( $(cat "{counter}") + 1 ))
    echo "$n" > "{counter}"
    printf '%s\\n' "$*" >> "{argv_log}"
    if (( n <= {fail_attempts} )); then
        printf '%s\\n' "{stderr_line}"
        return 1
    fi
    return 0
}}

HAL0_SEAM_PROBE_ATTEMPTS={attempts}
HAL0_SEAM_PROBE_DELAY=0
rc=0
_preflight_seam "{name}" "{required}" "{bin_dir}" "{sudoers_dir}" || rc=$?
echo "rc=$rc"
echo "attempts=$(cat "{counter}")"
echo "argv=$(cat "{argv_log}" 2>/dev/null | head -1)"
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


def test_a_transient_grant_probe_failure_is_retried(tmp_path: Path) -> None:
    """The rc.10/ct151 false negative: one failed probe, healthy seam."""
    proc = _call_probe(tmp_path, fail_attempts=1)

    assert "rc=0" in proc.stdout, proc.stderr
    assert "attempts=2" in proc.stdout
    assert "does not apply" not in proc.stderr


def test_a_retried_probe_says_so_rather_than_passing_silently(tmp_path: Path) -> None:
    """A seam that needed a second look is worth one line in the log."""
    proc = _call_probe(tmp_path, fail_attempts=1)

    assert "grant verified on attempt 2/3" in proc.stdout


def test_the_probe_is_not_retried_when_it_passes_first_time(tmp_path: Path) -> None:
    proc = _call_probe(tmp_path, fail_attempts=0)

    assert "rc=0" in proc.stdout
    assert "attempts=1" in proc.stdout
    assert "grant verified on attempt" not in proc.stdout  # nothing to report


def test_a_genuinely_broken_grant_still_fails_after_every_attempt(tmp_path: Path) -> None:
    """Retrying must not turn a real breakage into a pass."""
    proc = _call_probe(tmp_path, fail_attempts=99)

    assert "rc=1" in proc.stdout
    assert "attempts=3" in proc.stdout
    assert "3 attempt" in proc.stderr


def test_the_failure_quotes_the_command_it_actually_ran(tmp_path: Path) -> None:
    """Nit 1 on #2084: the old text printed the bare wrapper name.

    `sudo -n hal0-podman-ro …` is not what the check runs and fails for an
    unrelated reason when pasted — the wrapper is not on sudo's secure_path —
    so the printed command sent operators down a second wrong path.
    """
    proc = _call_probe(tmp_path, fail_attempts=99)

    bin_path = str(tmp_path / "bin" / "hal0-podman-ro")
    assert f"sudo -n -u hal0 sudo -n {bin_path} check-slot-token hal0probe" in proc.stderr
    assert "'sudo -n hal0-podman-ro" not in proc.stderr


def test_the_failure_reports_the_probe_rc_and_its_stderr(tmp_path: Path) -> None:
    """Report the evidence, don't assert a cause we never observed.

    seam_check.py has carried rc + stderr tail since #1465; the shell copy
    threw both away, so "sudo: a password is required" (grant broken) and
    "bad slot token" (wrapper stale) printed identically.
    """
    proc = _call_probe(tmp_path, fail_attempts=99, stderr_line="sudo: a password is required")

    assert "exited 1" in proc.stderr
    assert "sudo: a password is required" in proc.stderr


def test_the_retry_loop_does_not_abort_an_installer_running_set_e(tmp_path: Path) -> None:
    """install.sh is `set -euo pipefail`; a failing attempt must not kill it."""
    proc = _call_probe(tmp_path, fail_attempts=2, set_e=True)

    assert "rc=0" in proc.stdout, proc.stderr
    assert "attempts=3" in proc.stdout


def test_required_seams_get_the_same_retry(tmp_path: Path) -> None:
    """The transient is not podman-ro's; a load-bearing seam must not die on it."""
    proc = _call_probe(tmp_path, fail_attempts=1, name="hal0-systemctl", required="required")

    assert "rc=0" in proc.stdout, proc.stderr
    assert "attempts=2" in proc.stdout


def test_one_probe_attempt_is_bounded_by_a_timeout() -> None:
    """Retrying an unbounded sudo would wedge the installer for 3x as long.

    seam_check.py already passes timeout=20 to subprocess.run; keep the shell
    copy in lock-step.
    """
    text = PREFLIGHT.read_text()
    body = text.split("_hal0_seam_probe_run() {", 1)[1].split("\n}", 1)[0]
    assert "timeout" in body
    assert 'HAL0_SEAM_PROBE_TIMEOUT="${HAL0_SEAM_PROBE_TIMEOUT:-20}"' in text


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
