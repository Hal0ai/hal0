"""Installer skips the hal0 subuid/subgid range in a nested userns (#3236).

Inside an unprivileged LXC/Incus container the uid map is a slice, so a
``100000:65536`` range is outside the ids the container owns and rootless
podman as hal0 fails in ``newuidmap``. The block must allocate on a full
identity map (host / privileged) and skip, with an info line, otherwise.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("    HAL0_SUBUID_JUST_ALLOCATED=0\n")
    end = text.index('ui_step "Filesystem layout"', start)
    # drop the closing `fi` of the enclosing prod-only guard
    return text[start:end].rstrip().removesuffix("fi").rstrip()


def _run(tmp_path: Path, uid_map: str, subuid: str | None = None) -> tuple[str, str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "usermod.log"
    usermod = bindir / "usermod"
    usermod.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n', encoding="utf-8")
    usermod.chmod(0o755)
    umap = tmp_path / "uid_map"
    umap.write_text(uid_map, encoding="utf-8")
    # Redirect /etc/subuid + /etc/subgid to temp copies.
    sub = tmp_path / "sub"
    sub.write_text(subuid or "", encoding="utf-8")
    block = _block().replace("/etc/subuid", str(sub)).replace("/etc/subgid", str(sub))
    script = (
        "set -euo pipefail\n"
        'info() { echo "INFO: $*"; }\n'
        f'export HAL0_UID_MAP_FILE="{umap}"\n'
        f"{block}\n"
        'echo "JUST=${HAL0_SUBUID_JUST_ALLOCATED}"\n'
    )
    import os

    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return proc.stdout, calls, sub.read_text(encoding="utf-8")


def test_privileged_identity_map_allocates(tmp_path: Path) -> None:
    out, calls, _ = _run(tmp_path, "         0          0 4294967295\n")
    assert "allocated rootless uid/gid range 100000:65536" in out
    assert "JUST=1" in out
    assert "--add-subuids 100000-165535" in calls


def test_unprivileged_slice_skips(tmp_path: Path) -> None:
    out, calls, _ = _run(tmp_path, "         0     100000      65536\n")
    assert "nested user namespace" in out
    assert "allocated" not in out
    assert "JUST=0" in out
    assert calls == ""


def test_existing_range_untouched_in_nested_userns(tmp_path: Path) -> None:
    out, calls, _ = _run(
        tmp_path, "         0     100000      65536\n", subuid="hal0:100000:65536\n"
    )
    assert "nested user namespace" not in out
    assert "JUST=0" in out
    assert calls == ""


def test_existing_range_untouched_on_host(tmp_path: Path) -> None:
    out, calls, _ = _run(
        tmp_path, "         0          0 4294967295\n", subuid="hal0:200000:65536\n"
    )
    assert "JUST=0" in out
    assert calls == ""
