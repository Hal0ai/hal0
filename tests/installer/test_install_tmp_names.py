"""#2407: the installer runs as root and must never open a guessable /tmp name.

A plain ``>`` redirect or ``curl -o`` on a fixed or timestamped /tmp path
follows a symlink a local user planted there, so a root install could clobber
any file through it; a file the attacker pre-created can also be swapped
between a check and its use. Every /tmp file the installer creates comes from
``mktemp`` (O_EXCL, random name) instead.

The install log and the failure report have behavioural tests next to their
libs (test_install_logging.py, test_failure_report.py). This file covers the
NodeSource setup script (executed as root) and the FastFlowLM .deb (installed
as root), plus a source guard over every installer shell file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO / "installer" / "lib" / "preflight.sh"
INSTALL_SH = REPO / "installer" / "install.sh"

_OLD_NODESOURCE = Path("/tmp/hal0-nodesource-setup.sh")


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


class TestNodesourceSetupScript:
    def test_a_planted_symlink_at_the_old_name_is_not_followed(self, tmp_path: Path) -> None:
        victim = tmp_path / "victim"
        victim.write_text("precious\n")
        try:
            _OLD_NODESOURCE.unlink(missing_ok=True)
            _OLD_NODESOURCE.symlink_to(victim)
        except OSError:
            pytest.skip(f"{_OLD_NODESOURCE} is held by another user")
        ran = tmp_path / "ran"
        script = f"""
IFS=$'\\n\\t'
info() {{ :; }}; warn() {{ :; }}; err() {{ :; }}
source "{PREFLIGHT}"
distro_family() {{ echo debian; }}
# Stand-ins for the network fetch and the root exec: curl writes like the
# real one does (open the -o path for writing), bash records what it ran.
curl() {{
    local out=""
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "-o" ]] && {{ out="$2"; shift; }}
        shift
    done
    printf 'echo nodesource\\n' >"$out"
}}
bash() {{ printf '%s\\n' "$1" >"{ran}"; cat "$1" >>"{ran}"; }}
apt-get() {{ :; }}
HAL0_NODE_AUTOINSTALL=1
NODE_MIN_MAJOR=22
_node_autoinstall || true
"""
        try:
            proc = _run(script)
            assert victim.read_text() == "precious\n", proc.stderr
            executed, body = ran.read_text().split("\n", 1)
            assert executed != str(_OLD_NODESOURCE)
            assert re.fullmatch(r"/tmp/hal0-nodesource-setup-[A-Za-z0-9]{6}\.sh", executed)
            assert body == "echo nodesource\n"
            # Cleaned up after running.
            assert not Path(executed).exists()
        finally:
            _OLD_NODESOURCE.unlink(missing_ok=True)


class TestFlmDebDownload:
    def test_the_deb_goes_into_a_mktemp_dir(self) -> None:
        src = INSTALL_SH.read_text()
        assert '"/tmp/fastflowlm_' not in src
        assert 'FLM_DEB_DIR="$(mktemp -d /tmp/hal0-flm-XXXXXX' in src
        assert 'FLM_DEB_TMP="${FLM_DEB_DIR}/fastflowlm_${FLM_DEB_VERSION}.deb"' in src
        # Each removal takes the whole private dir, not just the file.
        assert 'rm -f "${FLM_DEB_TMP}"' not in src


_TMP_LITERAL = re.compile(r"(?<![\w/.$])/tmp/[\w.${}-]+")


def _installer_shell_files() -> list[Path]:
    root = REPO / "installer"
    return sorted([*root.glob("*.sh"), *root.glob("lib/*.sh")])


def test_no_installer_shell_file_names_a_fixed_tmp_path() -> None:
    """Any literal /tmp/<name> outside a comment must be a mktemp template or
    a prefix handed to hal0_excl_open (which appends a random part)."""
    offenders = []
    for path in _installer_shell_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#") or "hal0_excl_open " in line:
                continue
            for match in _TMP_LITERAL.findall(line):
                if "XXXXXX" not in match:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {match}")
    assert offenders == []
