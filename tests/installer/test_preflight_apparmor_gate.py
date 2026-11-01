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

#3234 adds a second branch for the unprivileged-LXC ``socket: permission
denied`` shape (the guest's own ``flags=(unconfined)`` AppArmor profiles):
the gate disables them via the same bare-script helper and re-smokes, and the
``lxc-unprivileged`` fallback no longer names a missing ``features:
nesting=1`` as the sole cause: it lists nesting as ONE candidate among the
guest-AppArmor, keyring-quota and subuid causes, each with its check.
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


def _fake_podman(tmp_path: Path, *, fail_calls: int, fail_stderr: str, fail_rc: int = 243) -> None:
    """A ``podman`` shim: fails (with ``fail_stderr``) for the first
    ``fail_calls`` invocations of ``run``, then succeeds forever after.
    Call count is tracked in a state file so both the outer bash smoke test
    and the Python remediation script's own internal smoke share one
    sequence.
    """
    counter = tmp_path / "podman-calls"
    argv_log = tmp_path / "podman-argv.log"
    _write_exec(
        tmp_path / "podman",
        f"""
echo "$@" >> {argv_log!s}
if [[ "$1" != "run" ]]; then
    exit 0
fi
n=0
[[ -f {counter!s} ]] && n=$(cat {counter!s})
n=$((n + 1))
echo "$n" > {counter!s}
if [[ "$n" -le {fail_calls} ]]; then
    echo {fail_stderr!r} >&2
    exit {fail_rc}
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
        # Never let a gate test touch the real /etc/apparmor.d (#3234).
        "HAL0_APPARMOR_D": str(tmp_path / "apparmor.d"),
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
    assert "did NOT resolve it" in output
    # The failed-remediation message must not tell the operator to re-set the
    # exact host-side flag that's already the documented cause of this branch
    # (that would be circular and fix nothing).
    assert "set it in /etc/pve/lxc" not in output


def test_apparmor_remediation_resolves_helper_path_without_repo_root(tmp_path: Path) -> None:
    # `HAL0_CONTAINER_REQUIRED=1 bash installer/lib/preflight.sh` (the
    # documented standalone mode) never sets REPO_ROOT — install.sh is the
    # only caller that does. The helper path must still resolve (from
    # preflight.sh's own location) instead of always reporting itself
    # missing.
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_APPARMOR_STDERR)
    rc, output = _run_gate(tmp_path, {"REPO_ROOT": ""})
    assert rc == 0, output
    assert "can't locate" not in output
    assert "apparmor remediation applied" in output


def test_unrelated_failure_does_not_trigger_apparmor_remediation(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: unable to pull image: not found")
    # Pin the platform: the lxc-unprivileged fallback legitimately points at
    # `aa-status` / apparmor logs (#3234), so the assertion below is only
    # meaningful for the generic arm.
    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "none"})
    assert rc != 0, output
    assert "apparmor" not in output.lower()
    assert not (tmp_path / "containers.conf").exists()


def test_remediation_honours_configured_smoke_image(tmp_path: Path) -> None:
    # HAL0_CONTAINER_SMOKE_IMAGE (air-gapped/mirrored-registry override) must
    # reach the Python remediation helper's own internal smoke too — if the
    # helper always probed a hardcoded quay.io image instead, an unreachable
    # quay.io on this host would make it misclassify the pull failure as
    # "unrelated" and skip the fix even though the outer gate already proved
    # this is a genuine apparmor failure. The fake podman shim here doesn't
    # care which image string it's called with; assert on the recorded argv
    # log instead to prove the override actually reached the Python helper's
    # own internal smoke, not just the outer bash smoke.
    mirror_image = "registry.internal.example/mirror/hello"
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_APPARMOR_STDERR)
    rc, output = _run_gate(tmp_path, {"HAL0_CONTAINER_SMOKE_IMAGE": mirror_image})
    assert rc == 0, output
    assert "apparmor remediation applied" in output
    assert (tmp_path / "containers.conf").exists()
    argv_log = (tmp_path / "podman-argv.log").read_text()
    assert mirror_image in argv_log
    assert "quay.io/podman/hello" not in argv_log


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


# ── #3234: unprivileged-LXC guest `flags=(unconfined)` profiles ──────────────

_SOCKET_DENIED_STDERR = "Error: netavark: failed to create socket: socket: permission denied"
_PROFILE_NAMES = ("podman", "crun", "buildah", "runc", "rootlesskit", "slirp4netns")
_NESTING_HINT = "features: nesting=1"


def _fake_apparmor_parser(tmp_path: Path) -> Path:
    """An ``apparmor_parser`` shim that logs its argv and exits 0."""
    log = tmp_path / "apparmor_parser.log"
    _write_exec(tmp_path / "apparmor_parser", f'echo "$@" >> {log!s}\nexit 0')
    return log


def _seed_profiles(apparmor_d: Path, names: tuple[str, ...] = _PROFILE_NAMES) -> None:
    apparmor_d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (apparmor_d / n).write_text(
            f"profile {n} /usr/bin/{n} flags=(unconfined) {{\n  userns,\n}}\n"
        )


def test_socket_denied_guest_profiles_disabled_and_gate_passes(tmp_path: Path) -> None:
    # Outer smoke (1) and the helper's first smoke (2) hit the socket denial;
    # after the six profiles are disabled the helper's retry (3) and the
    # gate's own re-verify (4) pass.
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    parser_log = _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc == 0, output
    assert "apparmor remediation applied" in output
    assert _NESTING_HINT not in output
    for n in _PROFILE_NAMES:
        link = aa / "disable" / n
        assert link.is_symlink(), output
        assert Path(os.readlink(link)) == aa / n
    assert parser_log.read_text().splitlines() == [f"-R {aa / n}" for n in _PROFILE_NAMES]
    # The #1563 containers.conf fix must not fire for this signature.
    assert not (tmp_path / "containers.conf").exists()


def test_socket_denied_without_profile_files_prints_manual_remedy(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    parser_log = _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    aa.mkdir()

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "runtime can't actually launch a container" in output
    assert "socket: permission denied" in output
    for n in _PROFILE_NAMES:
        assert n in output  # the manual remedy names every profile
    assert "apparmor_parser -R" in output
    assert _NESTING_HINT not in output
    assert "pct stop" not in output
    # The cause is stated as typical, not certain, and the failure path says
    # what to do when none of the six profile files exist (the loop is then a
    # no-op): the cause is elsewhere, with the same inspect list.
    assert "typically caused by" in output
    assert "none of those six profile files exist" in output
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output
    assert not (aa / "disable").exists()
    assert not parser_log.exists()  # nothing to unload -> parser never called


def test_socket_denied_retry_still_failing_is_a_hard_failure(tmp_path: Path) -> None:
    # Profiles exist and get disabled, but podman still can't open a socket:
    # the gate re-smokes itself and must stay red (bash never trusts the helper).
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _fake_apparmor_parser(tmp_path)
    _seed_profiles(tmp_path / "apparmor.d")

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "runtime can't actually launch a container" in output
    assert _NESTING_HINT not in output


def test_apparmor_1563_signature_keeps_precedence_over_socket_denied(tmp_path: Path) -> None:
    # Output carrying BOTH shapes goes down the #1563 branch (containers.conf),
    # never the guest-profile one.
    _fake_podman(
        tmp_path,
        fail_calls=2,
        fail_stderr=f"{_APPARMOR_STDERR} socket: permission denied",
    )
    _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)

    rc, output = _run_gate(tmp_path, {})

    assert rc == 0, output
    assert "AppArmor profile-load failure detected" in output
    assert "typically caused by" not in output  # the #3234 branch did not run
    assert (tmp_path / "containers.conf").exists()
    assert not (aa / "disable").exists()


def test_lxc_unprivileged_fallback_lists_causes_not_nesting_as_sole_cause(tmp_path: Path) -> None:
    # An unrelated failure on an unprivileged LXC. `podman info` CAN pass
    # without `features: nesting=1` while every `podman run` fails (that is
    # this arm's reason to exist), so nesting is neither claimed proven nor the
    # sole cause: it is one listed candidate next to AppArmor, keyring and
    # subuid, each with its check.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: crun: something odd")
    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "crun: something odd" in output  # smoke output is printed
    assert "podman info works but 'podman run' fails" in output
    # the old sole-cause sentence and any "nesting is proven" claim are gone
    assert "this needs 'features: nesting=1'" not in output
    assert "nesting is already" not in output
    assert "proven" not in output
    # nesting is still offered, as one candidate with its host-side check
    assert _NESTING_HINT in output
    assert "/etc/pve/lxc/<CTID>.conf" in output
    assert "pct stop <CTID>" in output
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output


def test_lxc_privileged_and_keyring_arms_unchanged(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: crun: something odd")
    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-privileged"})
    assert rc != 0, output
    assert "set 'features: nesting=1,keyctl=1,fuse=1,mknod=1'" in output

    keyring_dir = tmp_path / "keyring"
    keyring_dir.mkdir()
    _fake_podman(
        keyring_dir,
        fail_calls=10_000,
        fail_stderr="crun: create keyring `x`: Disk quota exceeded",
        fail_rc=126,
    )
    rc, output = _run_gate(keyring_dir, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})
    assert rc != 0, output
    assert "kernel keyring quota exhausted" in output
    assert "AppArmor" not in output
    assert "nesting is already" not in output


@pytest.mark.parametrize(
    "blob",
    [
        "Error: netavark: failed to create socket: socket: permission denied",
        "SOCKET: PERMISSION DENIED",
    ],
)
def test_socket_denial_matcher_recognizes_known_shapes(blob: str) -> None:
    script = (
        f"set -uo pipefail\nsource {_PREFLIGHT!s}\n_is_unconfined_profile_network_denial {blob!r}\n"
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_socket_denial_matcher_rejects_unrelated_output() -> None:
    script = (
        "set -uo pipefail\n"
        f"source {_PREFLIGHT!s}\n"
        "_is_unconfined_profile_network_denial 'Error: unable to pull image: not found'\n"
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr  # grep no-match, not 127
