"""Contract tests for ``preflight_ports``' own-service detection (#F24).

install.sh's pre-install port gate used to hard-fail whenever 8080/3001
were already LISTENing — including the documented
``HAL0_INSTALL_HONCHO=1 sudo bash install.sh`` re-install-over-a-live-box
path, where the listener is hal0's own hal0-api/hal0-openwebui unit. These
tests drive ``preflight_ports`` through fake ``ss``/``systemctl`` shims on
PATH (real port binding + real systemd units aren't available in CI) to
prove: a port held by hal0's own unit passes; a port held by a foreign
process still hard-fails; and ``HAL0_DOCTOR_PORTS_SOFT`` keeps its existing
blanket-warn behaviour regardless of ownership.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

PREFLIGHT = Path(__file__).resolve().parents[2] / "installer" / "lib" / "preflight.sh"

PORT = "9999"
OWN_PID = "4242"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_ss(tmp_path: Path, pid: str) -> None:
    _write_exec(
        tmp_path / "ss",
        (
            'echo "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process"\n'
            f'echo "LISTEN 0 128 0.0.0.0:{PORT} 0.0.0.0:* '
            f'users:((\\"fake\\",pid={pid},fd=3))"\n'
        ),
    )


def _fake_systemctl(tmp_path: Path, unit_to_pid: dict[str, str]) -> None:
    branches = []
    for unit, pid in unit_to_pid.items():
        branches.append(f'if [[ "$5" == "{unit}" ]]; then echo "{pid}"; exit 0; fi')
    body = "\n".join(branches) + '\necho "0"\n'
    _write_exec(tmp_path / "systemctl", body)


def _run_preflight_ports(tmp_path: Path, env_overrides: dict[str, str]) -> tuple[int, str]:
    script = (
        "set -uo pipefail\n"
        f"source {PREFLIGHT!s}\n"
        "rc=0\n"
        f"preflight_ports {PORT} || rc=$?\n"
        "exit $rc\n"
    )
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}", **env_overrides}
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_own_service_port_passes(tmp_path: Path) -> None:
    """Port held by hal0-api's own MainPID → OK (0), not a hard fail."""
    _fake_ss(tmp_path, OWN_PID)
    _fake_systemctl(tmp_path, {"hal0-api.service": OWN_PID})
    rc, out = _run_preflight_ports(tmp_path, {})
    assert rc == 0, out
    assert "OK for a re-install" in out


def test_foreign_process_port_hard_fails(tmp_path: Path) -> None:
    """Port held by an unrelated process (no unit MainPID match) → hard fail."""
    _fake_ss(tmp_path, "9999")
    _fake_systemctl(tmp_path, {"hal0-api.service": OWN_PID})
    rc, out = _run_preflight_ports(tmp_path, {})
    assert rc != 0, out
    assert "already in use" in out


def test_no_systemctl_hard_fails_like_before(tmp_path: Path) -> None:
    """No systemctl on PATH (non-systemd host) → falls back to the old hard fail."""
    _fake_ss(tmp_path, OWN_PID)
    rc, out = _run_preflight_ports(tmp_path, {})
    assert rc != 0, out


def test_soft_mode_warns_regardless_of_ownership(tmp_path: Path) -> None:
    """HAL0_DOCTOR_PORTS_SOFT=1 (hal0 doctor) still just warns — unchanged."""
    _fake_ss(tmp_path, "1")
    _fake_systemctl(tmp_path, {"hal0-api.service": OWN_PID})
    rc, out = _run_preflight_ports(tmp_path, {"HAL0_DOCTOR_PORTS_SOFT": "1"})
    assert rc == 0, out
    assert "expected if hal0's own services are running" in out


@pytest.mark.parametrize("unit", ["hal0-api.service", "hal0-openwebui.service"])
def test_either_own_unit_is_recognised(tmp_path: Path, unit: str) -> None:
    """Both hal0-api and hal0-openwebui are recognised as own-service owners."""
    _fake_ss(tmp_path, OWN_PID)
    _fake_systemctl(tmp_path, {unit: OWN_PID})
    rc, out = _run_preflight_ports(tmp_path, {})
    assert rc == 0, out
