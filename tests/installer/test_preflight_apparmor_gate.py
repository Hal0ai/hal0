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
import re
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


def _ns_level_file(tmp_path: Path, value: str = "1", name: str = "ns_level") -> Path:
    """The ``.ns_level`` stand-in: >0 = nested AppArmor policy namespace (#3234)."""
    f = tmp_path / name
    f.write_text(f"{value}\n")
    return f


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
        # Pretend to be inside a nested AppArmor policy namespace unless a test
        # overrides it; never depend on the machine running the suite.
        "HAL0_APPARMOR_NS_LEVEL_FILE": str(_ns_level_file(tmp_path)),
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


#: One corpus, both layers: preflight.sh's `_is_apparmor_profile_load_failure`
#: (the early shell gate) and containers_apparmor.py's `_is_apparmor_failure`
#: (the helper it runs) must classify every blob the same way (#3251).
_PREDICATE_CORPUS: tuple[tuple[str, bool], ...] = (
    (_APPARMOR_STDERR, True),
    ("apparmor_parser: Warning ... apparmor_parser: profile load failed: Access denied", True),
    ("some wrapper text ... apparmor ... exit status 243 ... more text", True),
    ("Error: install profile containers-default: boom, see apparmor logs", True),
    # no "apparmor" at all
    ("Error: exit status 243", False),
    ("install profile containers-default: boom", False),
    # "access denied" only counts on the same line AFTER apparmor_parser
    ("apparmor: something\nunrelated: Access denied", False),
    ("Access denied while reading apparmor profile", False),
    ("Error: unable to pull image: not found", False),
    ("dial udp 9.9.9.9:53: socket: permission denied", False),
)


@pytest.mark.parametrize(("blob", "expected"), _PREDICATE_CORPUS)
def test_1563_predicates_agree_between_shell_and_python(blob: str, expected: bool) -> None:
    from hal0.agents import containers_apparmor as ca

    script = (
        f"set -uo pipefail\nsource {_PREFLIGHT!s}\n_is_apparmor_profile_load_failure {blob!r}\n"
    )
    shell = subprocess.run(["bash", "-c", script], capture_output=True, text=True).returncode == 0
    py = ca._is_apparmor_failure(
        subprocess.CompletedProcess(args=[], returncode=243, stdout="", stderr=blob)
    )
    assert shell is expected, blob
    assert py is expected, blob


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


def _fake_apparmor_parser(tmp_path: Path, *, rc: int = 0) -> Path:
    """An ``apparmor_parser`` shim that logs its argv and exits ``rc``."""
    log = tmp_path / "apparmor_parser.log"
    _write_exec(tmp_path / "apparmor_parser", f'echo "$@" >> {log!s}\nexit {rc}')
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
    # The success line ends with the revert command for what was disabled.
    for n in _PROFILE_NAMES:
        assert f"rm {aa}/disable/{n} && apparmor_parser -r {aa}/{n}" in output
    assert "continuing install (revert: rm " in output


def test_socket_denied_without_profile_files_is_not_the_3234_case(tmp_path: Path) -> None:
    # The helper ran to completion and found no flags=(unconfined) files: say so
    # and point at the other causes; do NOT print the manual disable loop.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    parser_log = _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    aa.mkdir()

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "runtime can't actually launch a container" in output
    assert "socket: permission denied" in output
    assert "typically caused by" in output
    assert "no guest unconfined profiles present — this is not the #3234 case" in output
    assert "ln -sf" not in output  # no manual loop when the helper completed
    assert "THIS container" not in output
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output
    assert _NESTING_HINT not in output
    assert "pct stop" not in output
    assert not (aa / "disable").exists()
    assert not parser_log.exists()  # nothing to unload -> parser never called


def test_socket_denied_helper_missing_prints_guarded_manual_loop(tmp_path: Path) -> None:
    # The helper script cannot be located (it never ran): the manual remedy is
    # the only way forward, and it must be guarded so running it on a profile
    # that is NOT unconfined is a no-op.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    rc, output = _run_gate(
        tmp_path,
        {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged", "REPO_ROOT": str(tmp_path / "norepo")},
    )

    assert rc != 0, output
    assert "can't locate" in output
    assert "did not run or did not complete" in output
    assert "mkdir -p /etc/apparmor.d/disable" in output
    # the printed guard ignores comments (mirrors the python comment stripping)
    assert "grep -q '^[^#]*flags=(unconfined)'" in output
    assert "grep -q 'flags=(unconfined)'" not in output
    assert "apparmor_parser -R" in output
    for n in _PROFILE_NAMES:
        assert n in output
    assert "THIS container" not in output
    assert "profiles are marked disabled" not in output
    assert _NESTING_HINT not in output


def test_socket_denied_retry_still_failing_rolls_back_and_stays_red(tmp_path: Path) -> None:
    # Profiles exist and get disabled, but podman still can't open a socket:
    # the profiles were not the cause, so the helper re-enables what it
    # disabled, and the gate (which re-smokes itself, never trusting the
    # helper) stays red without offering a revert for something not disabled.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    parser_log = _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "runtime can't actually launch a container" in output
    assert _NESTING_HINT not in output
    # disable (-R) then rollback (-r) for each profile; no link left behind
    assert parser_log.read_text().splitlines() == [f"-R {aa / n}" for n in _PROFILE_NAMES] + [
        f"-r {aa / n}" for n in _PROFILE_NAMES
    ]
    for n in _PROFILE_NAMES:
        assert not (aa / "disable" / n).is_symlink()
    assert "rolled back (not disabled)" in output  # the helper's own summary
    assert "re-enabled them" in output  # the gate's diagnosis
    assert "profiles are marked disabled" not in output
    assert "ln -sf" not in output
    assert "revert:" not in output  # nothing left disabled
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output
    # the refreshed (post-remediation) smoke output is printed, labelled as such
    assert "after remediation" in output


def test_nested_namespace_level_is_visible_in_the_log(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _fake_apparmor_parser(tmp_path)
    _seed_profiles(tmp_path / "apparmor.d", ("podman",))
    two = _ns_level_file(tmp_path, "2", name="ns_level_two")

    rc, output = _run_gate(
        tmp_path,
        {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged", "HAL0_APPARMOR_NS_LEVEL_FILE": str(two)},
    )

    assert rc == 0, output
    assert ".ns_level=2" in output  # both the bash branch and the helper's summary line


def test_revert_hint_omits_links_that_existed_before_the_run(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    (aa / "disable").mkdir()
    (aa / "disable" / "podman").symlink_to(aa / "podman")  # the operator's own link

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc == 0, output
    assert f"rm {aa}/disable/crun && apparmor_parser -r {aa}/crun" in output
    assert f"rm {aa}/disable/podman" not in output
    assert (aa / "disable" / "podman").is_symlink()  # untouched


def test_helper_crash_mid_loop_still_prints_the_revert_hint(tmp_path: Path) -> None:
    # The helper dies (exit 1, not the "ran, did not help" exit 3) after it has
    # already disabled one profile. The gate must not leave the operator to find
    # that link: it prints the manual loop AND the revert for what was disabled.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))
    crash_py = tmp_path / "crashpy"
    _write_exec(
        crash_py,
        f"mkdir -p {aa}/disable && ln -s {aa}/podman {aa}/disable/podman\necho boom >&2\nexit 1",
    )

    rc, output = _run_gate(
        tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged", "PY": str(crash_py)}
    )

    assert rc != 0, output
    assert "did not run or did not complete" in output
    assert "ln -sf" in output  # the manual loop
    assert f"to undo: rm {aa}/disable/podman && apparmor_parser -r {aa}/podman" in output
    assert f"rm {aa}/disable/crun" not in output


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
    # this arm's reason to exist), so nesting is not the sole cause: it is one
    # listed candidate next to AppArmor, keyring and subuid, each with its check.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: crun: something odd")
    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "crun: something odd" in output  # smoke output is printed
    assert "podman info works but 'podman run' fails" in output
    # the old sole-cause sentence is gone; nesting appears only as the first of
    # several candidates, qualified as one that `info` can pass without
    assert "this needs 'features: nesting=1'" not in output
    # nesting is still offered, as one candidate with its host-side check
    assert "missing nesting (podman info can pass without it)" in output
    assert _NESTING_HINT in output
    assert "/etc/pve/lxc/<CTID>.conf" in output
    assert "pct stop <CTID>" in output
    # host-neutral: Proxmox is one example, Incus/LXD another
    assert "container HOST" in output
    assert "security.nesting=true" in output
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output


def test_lxc_privileged_and_keyring_arms_unchanged(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr="Error: crun: something odd")
    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-privileged"})
    assert rc != 0, output
    assert "set 'features: nesting=1,keyctl=1,fuse=1,mknod=1'" in output
    assert "PROXMOX" not in output  # host-neutral: Proxmox and Incus/LXD are both named
    assert "container HOST" in output
    assert "security.nesting=true" in output

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
    assert "PROXMOX" not in output
    assert "Incus/LXD host" in output
    assert "AppArmor" not in output


def test_printed_manual_loop_guard_ignores_comments(tmp_path: Path) -> None:
    # G1: run the PRINTED grep against real files. A profile that only mentions
    # the flag in a comment (an enforced profile the operator tightened) must NOT
    # match, or an operator pasting the loop would disable it.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _, output = _run_gate(
        tmp_path,
        {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged", "REPO_ROOT": str(tmp_path / "norepo")},
    )
    m = re.search(r"grep -q '([^']*flags=\(unconfined\))' /etc/apparmor\.d/", output)
    assert m, output
    pattern = m.group(1)

    def _matches(text: str) -> bool:
        f = tmp_path / "profile"
        f.write_text(text)
        return subprocess.run(["grep", "-q", pattern, str(f)]).returncode == 0

    assert _matches("profile podman /usr/bin/podman flags=(unconfined) {\n  userns,\n}\n")
    assert _matches("profile crun /usr/bin/crun flags=(unconfined) { # stub\n}\n")
    assert not _matches(
        "# was flags=(unconfined) before the operator tightened it\nprofile x /bin/x {\n}\n"
    )
    assert not _matches("profile x /bin/x {  # flags=(unconfined)\n  userns,\n}\n")
    assert not _matches("profile x /bin/x flags=(complain) {\n}\n")


def test_unload_failure_is_surfaced_and_the_gate_stays_red(tmp_path: Path) -> None:
    # G3: `apparmor_parser -R` exits 1 for every profile and podman never
    # recovers. The helper's summary names the profiles whose unload failed (it
    # is printed above the gate's own diagnosis), and the gate says the profiles
    # are only MARKED disabled instead of claiming the cause is elsewhere.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _fake_apparmor_parser(tmp_path, rc=1)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa, ("podman", "crun"))

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "unload failed: podman, crun" in output  # the helper's own summary
    assert (aa / "disable" / "podman").is_symlink()
    assert "profiles are marked disabled" in output
    assert f"apparmor_parser -R {aa}/<p>" in output
    assert "profiles already disabled; the cause is elsewhere" not in output
    # checks list and revert hint are kept
    for hint in ("aa-status", "journalctl -k | grep apparmor", "/proc/key-users", "/etc/subuid"):
        assert hint in output
    assert f"revert: rm {aa}/disable/podman && apparmor_parser -r {aa}/podman" in output


def test_unload_failure_but_podman_recovers_still_passes(tmp_path: Path) -> None:
    # The unload failed but `podman run` works (the symlink + a reload will do):
    # exit semantics are unchanged, the gate passes.
    _fake_podman(tmp_path, fail_calls=2, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _fake_apparmor_parser(tmp_path, rc=1)
    _seed_profiles(tmp_path / "apparmor.d", ("podman",))

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc == 0, output
    assert "apparmor remediation applied" in output
    assert "unload failed: podman" in output


def _fake_podman_phased(tmp_path: Path, *, first_stderr: str, later_stderr: str) -> None:
    """A ``podman`` shim whose FIRST ``run`` prints ``first_stderr`` and every
    later ``run`` prints ``later_stderr`` (all exit 126)."""
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
if [[ "$n" -le 1 ]]; then
    echo {first_stderr!r} >&2
else
    echo {later_stderr!r} >&2
fi
exit 126
""",
    )


def test_changed_failure_after_remediation_falls_through_to_the_keyring_arm(
    tmp_path: Path,
) -> None:
    # I2: the first smoke shows the socket denial; after the helper runs, the
    # re-smoke fails DIFFERENTLY (keyring quota). The gate must print the NEW
    # output and diagnose it with the existing keyring arm, not the stale
    # AppArmor remedy.
    _fake_podman_phased(
        tmp_path,
        first_stderr=_SOCKET_DENIED_STDERR,
        later_stderr="crun: create keyring `x`: Disk quota exceeded",
    )
    _fake_apparmor_parser(tmp_path)
    _seed_profiles(tmp_path / "apparmor.d")

    rc, output = _run_gate(tmp_path, {"HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged"})

    assert rc != 0, output
    assert "kernel keyring quota exhausted" in output
    assert "Disk quota exceeded" in output
    assert "ln -sf" not in output  # the AppArmor manual loop is not printed
    assert "profiles are marked disabled" not in output
    assert "no guest unconfined profiles present" not in output
    assert _NESTING_HINT not in output


def test_root_apparmor_namespace_skips_the_3234_branch_entirely(tmp_path: Path) -> None:
    # Bare metal / privileged container (.ns_level=0): those flags=(unconfined)
    # stubs are the userns grant. Neither the bash branch nor the helper may run,
    # and nothing on disk may change; the generic LXC arm diagnoses instead.
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    parser_log = _fake_apparmor_parser(tmp_path)
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    zero = _ns_level_file(tmp_path, "0", name="ns_level_zero")

    rc, output = _run_gate(
        tmp_path,
        {"HAL0_LXC_KIND_OVERRIDE": "lxc-privileged", "HAL0_APPARMOR_NS_LEVEL_FILE": str(zero)},
    )

    assert rc != 0, output
    assert "apparmor remediation" not in output
    assert "typically caused by" not in output
    assert "apparmor-preflight" not in output  # the helper never ran
    assert not (aa / "disable").exists()
    assert not parser_log.exists()
    assert "set 'features: nesting=1,keyctl=1,fuse=1,mknod=1'" in output  # generic privileged arm


@pytest.mark.parametrize("value", ["abc", ""])
def test_unparseable_ns_level_skips_the_3234_branch(tmp_path: Path, value: str) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _seed_profiles(tmp_path / "apparmor.d")
    bad = _ns_level_file(tmp_path, value, name="ns_level_bad")
    rc, output = _run_gate(
        tmp_path,
        {"HAL0_LXC_KIND_OVERRIDE": "none", "HAL0_APPARMOR_NS_LEVEL_FILE": str(bad)},
    )
    assert rc != 0, output
    assert "typically caused by" not in output
    assert not (tmp_path / "apparmor.d" / "disable").exists()


def _matcher_rc(tmp_path: Path, blob: str, ns_value: str | None = "1", rt: str = "podman") -> int:
    script = (
        f"set -uo pipefail\nsource {_PREFLIGHT!s}\n"
        f"_is_unconfined_profile_network_denial {rt!r} {blob!r}\n"
    )
    ns = (
        tmp_path / "missing_ns"
        if ns_value is None
        else _ns_level_file(tmp_path, ns_value, name="ns_for_matcher")
    )
    env = {**os.environ, "HAL0_APPARMOR_NS_LEVEL_FILE": str(ns)}
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True
    ).returncode


@pytest.mark.parametrize(
    "blob",
    [
        "Error: netavark: failed to create socket: socket: permission denied",
        "dial udp 9.9.9.9:53: socket: permission denied",  # verbatim from the issue
        "dial udp 9.9.9.9:53: SOCKET: PERMISSION DENIED",
    ],
)
def test_socket_denial_matcher_recognizes_known_shapes(tmp_path: Path, blob: str) -> None:
    assert _matcher_rc(tmp_path, blob) == 0


@pytest.mark.parametrize(
    "blob",
    [
        "Error: unable to pull image: not found",
        # anchored: a path ending in `.socket` is a plain file permission error
        "Error: open /run/podman/podman.socket: permission denied",
    ],
)
def test_socket_denial_matcher_rejects_other_output(tmp_path: Path, blob: str) -> None:
    assert _matcher_rc(tmp_path, blob) == 1  # grep no-match, not 127


@pytest.mark.parametrize("ns_value", ["0", "abc", "", None])
def test_socket_denial_matcher_requires_a_nested_namespace(
    tmp_path: Path, ns_value: str | None
) -> None:
    blob = "dial udp 9.9.9.9:53: socket: permission denied"
    assert _matcher_rc(tmp_path, blob, ns_value) != 0
    assert _matcher_rc(tmp_path, blob, "2") == 0


def test_socket_denial_matcher_is_podman_only(tmp_path: Path) -> None:
    # The helper only drives `podman run` and the printed remedy is
    # podman-specific: a docker `run` with the same text must not enter the branch.
    blob = "dial udp 9.9.9.9:53: socket: permission denied"
    assert _matcher_rc(tmp_path, blob, "1", "podman") == 0
    assert _matcher_rc(tmp_path, blob, "1", "docker") != 0


def test_docker_socket_denial_never_runs_the_guest_profile_remediation(tmp_path: Path) -> None:
    _fake_podman(tmp_path, fail_calls=10_000, fail_stderr=_SOCKET_DENIED_STDERR, fail_rc=126)
    _write_exec(tmp_path / "docker", f"echo {_SOCKET_DENIED_STDERR!r} >&2\nexit 126")
    aa = tmp_path / "apparmor.d"
    _seed_profiles(aa)
    script = f"set -uo pipefail\nsource {_PREFLIGHT!s}\n_container_runtime_gate docker\n"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "REPO_ROOT": str(_REPO_ROOT),
        "HAL0_APPARMOR_D": str(aa),
        "HAL0_APPARMOR_NS_LEVEL_FILE": str(_ns_level_file(tmp_path)),
        "HAL0_LXC_KIND_OVERRIDE": "lxc-unprivileged",
    }
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, output
    assert "typically caused by" not in output
    assert "apparmor-preflight" not in output  # the helper never ran
    assert not (aa / "disable").exists()
