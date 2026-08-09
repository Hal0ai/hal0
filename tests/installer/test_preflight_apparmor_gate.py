"""Contract tests for the AppArmor-remediation path in ``_container_runtime_gate``
(#1563).

``preflight_container_runtime``'s REQUIRED-mode gate used to hard-``die()``
whenever ``<rt> run`` failed, before the automated AppArmor remediation
(``src/hal0/agents/containers_apparmor.py``, wired in only at install.sh's
late "AppArmor preflight" block) ever ran — making that fix dead code on the
exact platform shape (a privileged Ubuntu 24.04 LXC with
``lxc.apparmor.profile: unconfined`` on the Proxmox host) it exists for.

``_container_runtime_gate`` now detects the AppArmor profile-load failure
signature from the smoke-test output and runs the SAME remediation script
(as a bare script, not ``-m hal0.agents.containers_apparmor``, since the
venv/hal0 package don't exist yet at this point in the installer) before
falling back to the hard failure. These tests drive it through a fake
``podman`` shim on PATH (no real container runtime / AppArmor state
required) covering:

* apparmor-failure-signature detected → remediation invoked → retry
  succeeds → gate returns 0 (install continues)
* apparmor remediation fails (retry never succeeds) → gate still returns
  non-zero (hard die path preserved)
* an unrelated `<rt> run` failure never triggers remediation (existing
  keyring/LXC branches stay untouched)
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"

_APPARMOR_STDERR = (
    "Error: default OCI runtime spec: install profile containers-default apparmor: exit status 243"
)


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _fake_podman(tmp_path: Path, *, fail_calls: int, fail_stderr: str) -> None:
    """A ``podman`` shim: fails (with ``fail_stderr``) for the first
    ``fail_calls`` invocations of ``run``, then succeeds forever after.
    Call count is tracked in a state file so both the outer bash smoke test
    and the Python remediation script's own internal smoke share one
    sequence.
    """
    counter = tmp_path / "podman-calls"
    _write_exec(
        tmp_path / "podman",
        f"""
if [[ "$1" != "run" ]]; then
    exit 0
fi
n=0
[[ -f {counter!s} ]] && n=$(cat {counter!s})
n=$((n + 1))
echo "$n" > {counter!s}
if [[ "$n" -le {fail_calls} ]]; then
    echo {fail_stderr!r} >&2
    exit 243
fi
exit 0
""",
    )


def _run_gate(tmp_path: Path, env_overrides: dict[str, str]) -> tuple[int, str]:
    script = (
        "set -uo pipefail\n"
        f"source {_PREFLIGHT!s}\n"
        "rc=0\n"
        "_container_runtime_gate podman || rc=$?\n"
        "exit $rc\n"
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "REPO_ROOT": str(_REPO_ROOT),
        "PY": os.environ.get("HAL0_PYTHON", "python3"),
        "HAL0_APPARMOR_CONF": str(tmp_path / "containers.conf"),
        **env_overrides,
    }
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, cwd=str(tmp_path)
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_apparmor_signature_remediated_and_retry_succeeds(tmp_path: Path) -> None:
    # First outer smoke fails w/ the apparmor signature; the remediation
    # script's own internal smoke also still fails once (nothing written
    # yet), then its post-write retry succeeds, then our own re-verify
    # smoke succeeds too — 3 total apparmor-signature failures modelled.
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_APPARMOR_STDERR)
    rc, output = _run_gate(tmp_path, {})
    assert rc == 0, output
    assert "apparmor remediation applied" in output
    conf = tmp_path / "containers.conf"
    assert conf.exists()
    assert 'apparmor_profile = "unconfined"' in conf.read_text()


def test_apparmor_signature_detected_but_remediation_fails_still_hard_dies(
    tmp_path: Path,
) -> None:
    # podman never recovers (e.g. a genuinely broken AppArmor policy) — the
    # remediation script writes the config but the retry keeps failing, and
    # our own re-verify keeps failing too. The gate must still return
    # non-zero (hard-die path preserved upstream in install.sh).
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_APPARMOR_STDERR)
    rc, output = _run_gate(tmp_path, {})
    assert rc != 0, output
    assert "runtime can't actually launch a container" in output
    assert "automated AppArmor remediation did not resolve it" in output


def test_unrelated_failure_does_not_trigger_apparmor_remediation(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: unable to pull image: not found")
    rc, output = _run_gate(tmp_path, {})
    assert rc != 0, output
    assert "apparmor" not in output.lower()
    assert not (tmp_path / "containers.conf").exists()


@pytest.mark.parametrize(
    "blob",
    [
        "Error: default OCI runtime spec: install profile containers-default apparmor: exit status 243",
        "apparmor_parser: Warning ... apparmor_parser: profile load failed: Access denied",
        "some wrapper text ... apparmor ... exit status 243 ... more text",
    ],
)
def test_signature_matcher_recognizes_known_shapes(blob: str) -> None:
    script = (
        f"set -uo pipefail\nsource {_PREFLIGHT!s}\n_is_apparmor_profile_load_failure {blob!r}\n"
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_signature_matcher_rejects_unrelated_output() -> None:
    script = (
        "set -uo pipefail\n"
        f"source {_PREFLIGHT!s}\n"
        "_is_apparmor_profile_load_failure 'Error: unable to pull image: not found'\n"
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode != 0
