"""#2407: the installer runs as root and must never open a guessable /tmp name.

A plain ``>`` redirect or ``curl -o`` on a fixed or timestamped /tmp path
follows a symlink a local user planted there, so a root install could clobber
any file through it; a file the attacker pre-created can also be swapped
between a check and its use. Every /tmp scratch file the installer creates
comes from ``mktemp`` (O_EXCL, random name, owner-only) instead, and is
registered with ``hal0_tmp_track`` so install.sh's EXIT trap removes it even
when the install aborts.

The install log and the failure report have behavioural tests next to their
libs (test_install_logging.py, test_failure_report.py). This file covers the
NodeSource setup script (executed as root) and the FastFlowLM .deb (installed
as root), the temp-file cleanup trap, the log fd being closed across a
privilege drop, plus a source guard over every installer shell file.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO / "installer" / "lib" / "preflight.sh"
LOGGING_SH = REPO / "installer" / "lib" / "logging.sh"
INSTALL_SH = REPO / "installer" / "install.sh"

_OLD_NODESOURCE = Path("/tmp/hal0-nodesource-setup.sh")
_NODESOURCE_NAME = re.compile(r"/tmp/hal0-nodesource-setup-[A-Za-z0-9]{6}\.sh")


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


def _nodesource_script(ran: Path, *, bash_body: str, prelude: str = "") -> str:
    """Source preflight.sh with the network fetch and the root exec stubbed.

    curl writes like the real one does (opens the -o path for writing);
    ``bash`` records the path it was handed in ``ran`` and then runs
    ``bash_body`` with $1 = that path.
    """
    return f"""
IFS=$'\\n\\t'
info() {{ :; }}; warn() {{ :; }}; err() {{ :; }}
{prelude}
source "{PREFLIGHT}"
distro_family() {{ echo debian; }}
curl() {{
    local out=""
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "-o" ]] && {{ out="$2"; shift; }}
        shift
    done
    printf 'echo nodesource\\n' >"$out"
}}
bash() {{ printf '%s\\n' "$1" >"{ran}"; {bash_body} }}
apt-get() {{ :; }}
HAL0_NODE_AUTOINSTALL=1
NODE_MIN_MAJOR=22
_node_autoinstall || true
"""


class TestNodesourceSetupScript:
    def test_a_planted_symlink_at_the_old_name_is_not_followed(self, tmp_path: Path) -> None:
        victim = tmp_path / "victim"
        victim.write_text("precious\n")
        # Never delete whatever already sits at the legacy name (a real
        # install's file, another run's plant): plant only when the name is
        # free, and clean up only the symlink this test made.
        if os.path.lexists(_OLD_NODESOURCE):
            pytest.skip(f"{_OLD_NODESOURCE} already exists; not touching it")
        try:
            _OLD_NODESOURCE.symlink_to(victim)
        except OSError:
            pytest.skip(f"could not plant a symlink at {_OLD_NODESOURCE}")
        ran = tmp_path / "ran"
        script = _nodesource_script(ran, bash_body=f'cat "$1" >>"{ran}";')
        try:
            proc = _run(script)
            assert victim.read_text() == "precious\n", proc.stderr
            executed, body = ran.read_text().split("\n", 1)
            assert executed != str(_OLD_NODESOURCE)
            assert _NODESOURCE_NAME.fullmatch(executed)
            assert body == "echo nodesource\n"
            # Cleaned up after running.
            assert not os.path.lexists(executed)
        finally:
            if _OLD_NODESOURCE.is_symlink() and os.readlink(_OLD_NODESOURCE) == str(victim):
                _OLD_NODESOURCE.unlink()

    def test_the_setup_script_is_created_owner_only(self, tmp_path: Path) -> None:
        ran = tmp_path / "ran"
        mode = tmp_path / "mode"
        script = _nodesource_script(ran, bash_body=f'stat -c %a "$1" >"{mode}";')
        proc = _run(script)
        assert mode.read_text().strip() == "600", proc.stderr

    def test_an_abort_mid_install_still_removes_the_setup_script(self, tmp_path: Path) -> None:
        """The setup script is registered with hal0_tmp_track, so an install
        that dies while running it (here the stubbed exec exits the shell)
        still has it removed by the EXIT trap install.sh arms."""
        ran = tmp_path / "ran"
        script = _nodesource_script(
            ran,
            bash_body="exit 7;",
            prelude=f'source "{LOGGING_SH}"\ntrap hal0_tmp_cleanup EXIT',
        )
        proc = _run(script)
        assert proc.returncode == 7, proc.stderr
        executed = ran.read_text().strip()
        assert _NODESOURCE_NAME.fullmatch(executed)
        assert not os.path.lexists(executed)


class TestFlmDebDownload:
    def test_the_deb_goes_into_a_mktemp_dir(self) -> None:
        src = INSTALL_SH.read_text()
        assert '"/tmp/fastflowlm_' not in src
        assert 'FLM_DEB_DIR="$(mktemp -d /tmp/hal0-flm-XXXXXX' in src
        assert 'FLM_DEB_TMP="${FLM_DEB_DIR}/fastflowlm_${FLM_DEB_VERSION}.deb"' in src
        # Each removal takes the whole private dir, not just the file, and
        # untracks it.
        assert 'rm -f "${FLM_DEB_TMP}"' not in src
        assert 'rm -rf "${FLM_DEB_DIR}"' not in src
        assert src.count('hal0_tmp_remove "${FLM_DEB_DIR}"') == 5

    def test_the_dir_is_tracked_before_anything_lands_in_it(self) -> None:
        src = INSTALL_SH.read_text()
        made = src.index('FLM_DEB_DIR="$(mktemp -d /tmp/hal0-flm-XXXXXX')
        tracked = src.index('hal0_tmp_track "${FLM_DEB_DIR}"')
        assert made < tracked < src.index('curl -fsSL -o "${FLM_DEB_TMP}"')


class TestTempCleanupTrap:
    def test_install_exit_trap_runs_tmp_cleanup(self) -> None:
        lines = INSTALL_SH.read_text().splitlines()
        traps = [ln for ln in lines if re.fullmatch(r"\s*trap .* EXIT\s*", ln)]
        assert traps == ["trap 'hal0_report_on_exit \"$?\"; hal0_tmp_cleanup' EXIT"]
        # logging.sh, which defines hal0_tmp_cleanup, is sourced before it.
        sourced = next(
            i for i, ln in enumerate(lines) if ln.startswith("source ") and "lib/logging.sh" in ln
        )
        assert sourced < lines.index(traps[0])

    def test_tracked_paths_are_removed_on_exit_and_the_status_kept(self, tmp_path: Path) -> None:
        f = tmp_path / "f"
        d = tmp_path / "d"
        script = f"""
source "{LOGGING_SH}"
trap 'hal0_tmp_cleanup' EXIT
: >"{f}"; mkdir -p "{d}/sub"; : >"{d}/sub/x"
hal0_tmp_track "{f}" "{d}"
exit 3
"""
        proc = _run(script)
        assert proc.returncode == 3, proc.stderr
        assert not f.exists()
        assert not d.exists()

    def test_remove_untracks_so_cleanup_leaves_the_name_alone(self, tmp_path: Path) -> None:
        f = tmp_path / "f"
        script = f"""
source "{LOGGING_SH}"
: >"{f}"
hal0_tmp_track "{f}"
hal0_tmp_remove "{f}"
echo "left=${{#HAL0_TMP_PATHS[@]}}"
: >"{f}"   # someone else reuses the name after this run let it go
hal0_tmp_cleanup
"""
        proc = _run(script)
        assert "left=0" in proc.stdout, proc.stdout + proc.stderr
        assert f.exists()

    def test_cleanup_does_not_follow_a_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        (target / "keep").write_text("x")
        link = tmp_path / "link"
        link.symlink_to(target)
        script = f"""
source "{LOGGING_SH}"
hal0_tmp_track "{link}"
hal0_tmp_cleanup
"""
        _run(script)
        assert not os.path.lexists(link)
        assert (target / "keep").read_text() == "x"


class TestPrivilegeDropClosesTheLogFd:
    """#2407 review: the log fd is write-only and inheritable, so a
    ``runuser -u hal0`` child could append to or truncate the root log
    through it. Every privilege drop goes through hal0_run_without_log_fd."""

    def test_child_does_not_inherit_the_fd_but_the_parent_keeps_it(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        script = f"""
source "{LOGGING_SH}"
exec {{HAL0_INSTALL_LOG_FD}}>"{log}"
export HAL0_INSTALL_LOG_FD
probe='[[ -e /dev/fd/$HAL0_INSTALL_LOG_FD ]] && echo child=open || echo child=closed'
hal0_run_without_log_fd bash -c "$probe"
[[ -e /dev/fd/$HAL0_INSTALL_LOG_FD ]] && echo parent=open || echo parent=closed
hal0_run_without_log_fd false; echo "rc=$?"
"""
        proc = _run(script)
        assert "child=closed" in proc.stdout, proc.stdout + proc.stderr
        assert "parent=open" in proc.stdout
        assert "rc=1" in proc.stdout

    def test_without_a_log_fd_the_command_still_runs(self) -> None:
        script = f"""
source "{LOGGING_SH}"
unset HAL0_INSTALL_LOG_FD
hal0_run_without_log_fd echo ran
"""
        assert "ran" in _run(script).stdout

    def test_every_runuser_and_setpriv_in_install_sh_is_wrapped(self) -> None:
        offenders = []
        for lineno, line in enumerate(INSTALL_SH.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"\b(runuser|setpriv)\b", line):
                if not line[: m.start()].rstrip().endswith("hal0_run_without_log_fd"):
                    offenders.append(f"install.sh:{lineno}: {line.strip()}")
        assert offenders == []


_TMP_LITERAL = re.compile(r"(?<![\w/.$])/tmp/[\w.${}-]+")

# The first argument of a hal0_excl_open call, quoted or not. It is a prefix
# the helper appends a random part to, so it alone is exempt; every other
# /tmp literal on the same line is still checked.
_EXCL_OPEN_PREFIX = re.compile(r"""\bhal0_excl_open\s+(?:"[^"]*"|'[^']*'|[^\s;|&]+)""")


def _tmp_literals(line: str) -> list[str]:
    """Fixed /tmp names on one shell line: not a comment, not a mktemp
    template, not the prefix argument of hal0_excl_open."""
    if line.lstrip().startswith("#"):
        return []
    scanned = _EXCL_OPEN_PREFIX.sub("hal0_excl_open", line)
    return [m for m in _TMP_LITERAL.findall(scanned) if "XXXXXX" not in m]


def _installer_shell_files() -> list[Path]:
    root = REPO / "installer"
    return sorted([*root.glob("*.sh"), *root.glob("lib/*.sh")])


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("hal0_excl_open /tmp/hal0-install- .log", []),
        ('    hal0_excl_open "/tmp/hal0-install-report-${stamp}-" .txt ||', []),
        ("rm -f /tmp/legacy; hal0_excl_open /tmp/hal0-x- .log", ["/tmp/legacy"]),
        ("hal0_excl_open /tmp/hal0-x- .log && cat /tmp/fixed", ["/tmp/fixed"]),
        ('x="$(mktemp /tmp/hal0-a-XXXXXX)"', []),
        ("curl -o /tmp/fixed.sh", ["/tmp/fixed.sh"]),
        ("    # a comment naming /tmp/fixed", []),
    ],
)
def test_tmp_literal_scanner(line: str, expected: list[str]) -> None:
    assert _tmp_literals(line) == expected


def test_no_installer_shell_file_names_a_fixed_tmp_path() -> None:
    """Any literal /tmp/<name> outside a comment must be a mktemp template or
    the prefix argument of hal0_excl_open (which appends a random part)."""
    offenders = [
        f"{path.relative_to(REPO)}:{lineno}: {match}"
        for path in _installer_shell_files()
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        for match in _tmp_literals(line)
    ]
    assert offenders == []


def _summary_brain_model_snippet() -> str:
    """The install summary's brain-model lookup, lifted out of install.sh."""
    lines = INSTALL_SH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if '_summary_log_src="/dev/null"' in line)
    end = next(i for i in range(start, len(lines)) if "s/^  brain model: ([^ ]+)" in lines[i])
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("log_body", [None, "no model line here\n"])
def test_summary_survives_a_log_without_a_brain_model_line(
    tmp_path: Path, log_body: str | None
) -> None:
    """Under `set -euo pipefail`, grep's exit 1 on no match used to abort a
    --summary-json run whose log had no "  brain model:" line."""
    setup = ""
    if log_body is not None:
        log = tmp_path / "log"
        log.write_text(log_body)
        setup = f'exec {{HAL0_INSTALL_LOG_FD}}<"{log}"'
    script = f"""
set -euo pipefail
IFS=$'\\n\\t'
unset HAL0_INSTALL_LOG_FD
{setup}
{_summary_brain_model_snippet()}
echo "survived model=[${{_summary_brain_model}}]"
"""
    proc = _run(script)
    assert "survived model=[]" in proc.stdout, proc.stdout + proc.stderr


def test_summary_finds_the_brain_model_through_the_log_fd(tmp_path: Path) -> None:
    log = tmp_path / "log"
    log.write_text("x\n  brain model: qwen3-30b (container)\n")
    script = f"""
set -euo pipefail
exec {{HAL0_INSTALL_LOG_FD}}<"{log}"
{_summary_brain_model_snippet()}
echo "model=[${{_summary_brain_model}}]"
"""
    proc = _run(script)
    assert "model=[qwen3-30b]" in proc.stdout, proc.stdout + proc.stderr


def test_background_pull_drops_the_install_log_fd() -> None:
    src = INSTALL_SH.read_text()
    start = src.index("hal0_pull_with_retry podman")
    block = src[src.rindex("(\n", 0, start) : src.index("&\n", start)]
    assert "exec {HAL0_INSTALL_LOG_FD}>&-" in block
