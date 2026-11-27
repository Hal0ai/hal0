"""Root linger is enabled before the installer starts any long-lived container (#3968).

``loginctl enable-linger root`` makes logind mount a fresh ``/run/user/0``.
Run from a session-less install (``pct exec``, cloud-init), that mount covers
the plain directory podman had been keeping container network namespaces in,
so a container started earlier is stranded and its next stop leaks a DNAT
rule. The linger block must therefore come first in the service-start branch,
and must wait for the mount before going on.

The block is sliced out of install.sh and run under ``bash -euo pipefail``
against stub ``loginctl`` / ``mountpoint`` / ``sleep`` binaries.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")

BLOCK_START = "    # Keep root's runtime directory pinned (#1814)."
BLOCK_END = "    start_or_restart_api\n"


def _block() -> str:
    s = TEXT.index(BLOCK_START)
    return TEXT[s : TEXT.index(BLOCK_END, s)]


def test_linger_block_precedes_every_service_start() -> None:
    branch = TEXT.index('    warn "not starting services automatically (dev / --no-start)."')
    linger = TEXT.index("loginctl enable-linger root >/dev/null 2>&1", branch)
    starts = [
        "    start_or_restart_api\n",
        "systemctl enable --now hal0.target",
        "systemctl enable --now hindsight-api",
        "systemctl enable --now hal0-openwebui",
    ]
    for needle in starts:
        assert linger < TEXT.index(needle, branch), needle
    # One block only: a second copy further down would hide a regression.
    assert TEXT.count("loginctl enable-linger root >/dev/null 2>&1") == 1


def _run(
    tmp_path: Path, *, linger_file: bool, mount_after: int, running: str = ""
) -> tuple[str, list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    counter = tmp_path / "mount_calls"
    counter.write_text("0")
    (bindir / "loginctl").write_text(f'#!/bin/bash\necho "loginctl $*" >> {calls}\n')
    (bindir / "sleep").write_text(f'#!/bin/bash\necho "sleep $*" >> {calls}\n')
    (bindir / "mountpoint").write_text(
        "#!/bin/bash\n"
        f'n=$(( $(cat {counter}) + 1 )); echo "$n" > {counter}\n'
        f"[[ $n -gt {mount_after} ]]\n"
    )
    (bindir / "podman").write_text(f"#!/bin/bash\nprintf {running!r}\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    linger_path = tmp_path / "linger-root"
    if linger_file:
        linger_path.touch()
    block = _block().replace("/var/lib/systemd/linger/root", str(linger_path))
    script = (
        "set -euo pipefail\nHAL0_BIN=hal0\n"
        'info() { echo "INFO $*"; }\nwarn() { echo "WARN $*"; }\n'
        "if true; then\n" + block + "fi\n"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout, calls.read_text().splitlines() if calls.exists() else []


def test_enables_linger_and_waits_for_the_runtime_dir(tmp_path: Path) -> None:
    out, calls = _run(tmp_path, linger_file=False, mount_after=3)
    assert calls[0] == "loginctl enable-linger root"
    assert calls.count("sleep 1") == 3
    assert "INFO root linger enabled" in out
    assert "WARN" not in out


def test_warns_when_the_runtime_dir_never_mounts(tmp_path: Path) -> None:
    out, calls = _run(tmp_path, linger_file=False, mount_after=999)
    assert calls.count("sleep 1") == 10
    assert "WARN root linger enabled, but /run/user/0 is not mounted yet" in out


def test_already_lingering_root_is_left_alone(tmp_path: Path) -> None:
    out, calls = _run(tmp_path, linger_file=True, mount_after=0)
    assert calls == []
    assert "INFO root linger already enabled" in out


def test_names_containers_that_predate_linger(tmp_path: Path) -> None:
    out, _ = _run(
        tmp_path, linger_file=False, mount_after=0, running="hal0-slot-brain\nhal0-openwebui\n"
    )
    assert "must be restarted once: hal0-slot-brain hal0-openwebui" in out
