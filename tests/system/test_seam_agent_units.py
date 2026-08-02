"""Agent-unit verbs on the hal0-systemctl privilege seam (#453 follow-up).

``HermesDriver._stop_services`` used to run a BARE ``systemctl stop/disable
hal0-agent@hermes.service``. Unprivileged systemctl on a system unit escalates
via polkit — an interactive password dialog in the middle of an uninstall, and
(when cancelled) a unit that is never actually stopped, which is exactly the
condition #453 added ``_stop_services`` to prevent. The seam was slot-only
(``UNIT_PREFIX="hal0-slot@"``, every verb calling ``validate_slot_id``), so the
driver had nothing to call.

Two halves are covered here:

* the **Python** side (:func:`hal0.system.seam.agent_unit_name` /
  :func:`~hal0.system.seam.agent_unit_argv`) — validation and privilege routing;
* the **shell** side (``installer/wrappers/hal0-systemctl``) — the actual
  security boundary, exercised by running the real script with a FAKE
  ``systemctl`` first on ``PATH``. Nothing here reaches live systemd: the
  wrapper's happy path execs the fake, and every rejection case dies in the
  validator before any exec.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hal0.system import seam as seam_mod
from hal0.system.seam import SEAM_BIN

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "installer" / "wrappers" / "hal0-systemctl"

# Inputs the seam must refuse. Each one is a way to smuggle a second unit name,
# a path, an extra systemd instance spec, or shell metacharacters through what
# is supposed to be a bare identifier.
BAD_AGENT_IDS = [
    "",
    "hermes evil",
    "hermes.service",
    "../etc/passwd",
    "/etc/passwd",
    "hal0-slot@x",
    "hermes;reboot",
    "hermes\nreboot",
    "her mes",
    "hermes@x",
    "$(id)",
    "`id`",
    "*",
    "hermes.service hal0-api.service",
    "a" * 65,
]


# ── Python side: validation ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", BAD_AGENT_IDS)
def test_agent_unit_name_rejects_malformed_id(bad: str) -> None:
    """A malformed agent id never becomes a unit name."""
    with pytest.raises(ValueError, match="bad agent id"):
        seam_mod.agent_unit_name(bad)


@pytest.mark.parametrize("bad", BAD_AGENT_IDS)
def test_agent_unit_argv_rejects_malformed_id(bad: str) -> None:
    """...and never becomes an argv either — validation happens before routing,
    so a bad id cannot reach ``sudo`` even to be rejected server-side."""
    with pytest.raises(ValueError, match="bad agent id"):
        seam_mod.agent_unit_argv("stop", bad, euid=1000)


@pytest.mark.parametrize("good", ["hermes", "a", "my_agent-2", "A" * 64])
def test_agent_unit_name_builds_expected_unit(good: str) -> None:
    assert seam_mod.agent_unit_name(good) == f"hal0-agent@{good}.service"


def test_agent_unit_argv_rejects_unknown_verb() -> None:
    """Only the AGENT_UNIT_VERBS map exists (start/stop/restart/enable/disable
    since #1590). A verb outside it is a caller bug, caught before exec."""
    with pytest.raises(KeyError):
        seam_mod.agent_unit_argv("mask", "hermes", euid=1000)


# ── Python side: privilege routing ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("verb", "seam_verb"),
    [
        ("stop", "stop-agent"),
        ("disable", "disable-agent"),
        ("start", "start-agent"),
        ("restart", "restart-agent"),
        ("enable", "enable-agent"),
    ],
)
def test_unprivileged_routes_through_sudo_seam(verb: str, seam_verb: str) -> None:
    """THE regression. Off root the argv must be the ``sudo -n`` seam call —
    never a bare ``systemctl``, which is what escalates via polkit.

    Note the seam receives the bare id, not a unit string: the wrapper builds
    ``hal0-agent@<id>.service`` itself on the root side.
    """
    argv = seam_mod.agent_unit_argv(verb, "hermes", euid=1000)

    assert argv == ["sudo", "-n", SEAM_BIN, seam_verb, "hermes"]
    assert argv[0] != "systemctl"
    assert "hal0-agent@hermes.service" not in argv
    # -n is load-bearing: it is what makes sudo non-interactive.
    assert "-n" in argv


@pytest.mark.parametrize(
    ("verb", "seam_verb"),
    [
        ("stop", "stop-agent"),
        ("disable", "disable-agent"),
        ("start", "start-agent"),
        ("restart", "restart-agent"),
        ("enable", "enable-agent"),
    ],
)
def test_root_runs_systemctl_directly(verb: str, seam_verb: str) -> None:
    """Root needs no seam — and the installer/uninstaller runs at moments when
    the seam binary and its sudoers grant may not exist yet."""
    argv = seam_mod.agent_unit_argv(verb, "hermes", euid=0)

    assert argv == ["systemctl", verb, "hal0-agent@hermes.service"]
    assert "sudo" not in argv


def test_agent_unit_argv_defaults_to_real_euid(monkeypatch: pytest.MonkeyPatch) -> None:
    """``euid=None`` (the production call) reads the live euid."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert seam_mod.agent_unit_argv("stop", "hermes")[0] == "sudo"
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert seam_mod.agent_unit_argv("stop", "hermes")[0] == "systemctl"


def test_seam_bin_is_overridable_but_defaults_to_installed_path() -> None:
    argv = seam_mod.agent_unit_argv("stop", "hermes", seam_bin="/tmp/x", euid=1000)
    assert argv[2] == "/tmp/x"
    assert SEAM_BIN == "/usr/lib/hal0/bin/hal0-systemctl"


# ── Shell side: the actual security boundary ─────────────────────────────────


@pytest.fixture
def fake_systemctl(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A ``systemctl`` that records its argv instead of talking to systemd.

    Placed FIRST on ``PATH`` so the wrapper's ``exec systemctl ...`` can never
    reach the real binary from a test run.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    shim = bindir / "systemctl"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexit 0\n')
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    return env, log


def _run_wrapper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(WRAPPER), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_wrapper_is_syntactically_valid() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("bad", BAD_AGENT_IDS)
def test_wrapper_rejects_malformed_agent_id(
    bad: str, fake_systemctl: tuple[dict[str, str], Path]
) -> None:
    """The seam re-validates server-side — client-side validation is a
    convenience, this is the boundary. Rejection must happen BEFORE any exec,
    so the fake systemctl must record nothing."""
    env, log = fake_systemctl

    proc = _run_wrapper(env, "stop-agent", bad)

    assert proc.returncode == 64, proc.stderr
    assert "agent id" in proc.stderr
    assert not log.exists(), f"wrapper exec'd systemctl for a rejected id: {bad!r}"


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("stop-agent", "stop hal0-agent@hermes.service"),
        ("disable-agent", "disable hal0-agent@hermes.service"),
        ("start-agent", "start hal0-agent@hermes.service"),
        ("restart-agent", "restart hal0-agent@hermes.service"),
        ("enable-agent", "enable hal0-agent@hermes.service"),
    ],
)
def test_wrapper_builds_the_unit_name_itself(
    cmd: str, expected: str, fake_systemctl: tuple[dict[str, str], Path]
) -> None:
    """The caller passes a bare id; the seam concatenates the prefix + suffix."""
    env, log = fake_systemctl

    proc = _run_wrapper(env, cmd, "hermes")

    assert proc.returncode == 0, proc.stderr
    assert log.read_text().strip() == expected


def test_wrapper_agent_verbs_cannot_reach_a_slot_unit(
    fake_systemctl: tuple[dict[str, str], Path],
) -> None:
    """The two id namespaces stay separate: an agent verb always produces a
    ``hal0-agent@`` unit, never a ``hal0-slot@`` one, whatever the id says."""
    env, log = fake_systemctl

    proc = _run_wrapper(env, "stop-agent", "slot")

    assert proc.returncode == 0, proc.stderr
    assert log.read_text().strip() == "stop hal0-agent@slot.service"


def test_wrapper_rejects_unknown_agent_verb(
    fake_systemctl: tuple[dict[str, str], Path],
) -> None:
    """The verb allow-list is the case statement; nothing else gets through."""
    env, log = fake_systemctl

    for verb in ("mask-agent", "kill-agent", "isolate-agent"):
        proc = _run_wrapper(env, verb, "hermes")
        assert proc.returncode == 64, f"{verb} should not exist: {proc.stderr}"
        assert "bad cmd" in proc.stderr
    assert not log.exists()


# ── Companion-service family (svc-<verb>, #1590) ─────────────────────────────


@pytest.mark.parametrize(
    ("cmd", "key", "expected"),
    [
        ("svc-start", "openwebui", "start hal0-openwebui.service"),
        ("svc-stop", "openwebui", "stop hal0-openwebui.service"),
        ("svc-restart", "hindsight", "restart hindsight-api.service"),
        ("svc-enable", "hindsight", "enable hindsight-api.service"),
        ("svc-disable", "openwebui", "disable hal0-openwebui.service"),
    ],
)
def test_wrapper_companion_family_maps_key_to_unit(
    cmd: str, key: str, expected: str, fake_systemctl: tuple[dict[str, str], Path]
) -> None:
    """The caller passes a service KEY from a closed map, never a unit string."""
    env, log = fake_systemctl

    proc = _run_wrapper(env, cmd, key)

    assert proc.returncode == 0, proc.stderr
    assert log.read_text().strip() == expected


@pytest.mark.parametrize("bad", ["hal0-api", "comfyui", "../etc", "hal0-openwebui.service", ""])
def test_wrapper_companion_family_rejects_unknown_keys(
    bad: str, fake_systemctl: tuple[dict[str, str], Path]
) -> None:
    """Anything outside the enumerated map dies before exec — including a raw
    unit string, so the family can never be widened from the caller side."""
    env, log = fake_systemctl

    proc = _run_wrapper(env, "svc-restart", bad)

    assert proc.returncode == 64, proc.stderr
    assert "bad service key" in proc.stderr
    assert not log.exists()


def test_wrapper_usage_documents_the_agent_verbs() -> None:
    proc = subprocess.run(
        ["bash", str(WRAPPER), "help"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "stop-agent <agent-id>" in proc.stdout
    assert "disable-agent <agent-id>" in proc.stdout
    assert "hal0-agent@<id>.service" in proc.stdout


def test_wrapper_and_python_validators_agree() -> None:
    """The two validators are copies; keep them provably in sync.

    A drift here is a real hazard: a Python side laxer than the shell side
    means confusing runtime failures, and a Python side STRICTER than the
    shell side would hide the fact that the shell is the boundary.
    """
    shell_re = None
    for line in WRAPPER.read_text().splitlines():
        if "bad agent id" in line and "=~" in line:
            shell_re = line.split("=~")[1].split("]]")[0].strip()
            break
    assert shell_re == "^[A-Za-z0-9_-]{1,64}$", f"wrapper regex changed: {shell_re!r}"
    assert seam_mod._AGENT_ID_RE.pattern == shell_re


# ── The test-isolation guard must cover the seam form too ────────────────────
#
# tests/conftest.py's `_no_real_systemctl` (added by #1357) matched on
# argv[0] == "systemctl". Routing teardown through the seam changes the argv a
# leaking test would emit to `sudo -n .../hal0-systemctl stop-agent hermes` —
# still a real escalation against the real host, just one indirection out.
# Without this coverage the seam fix would have quietly re-opened the hole the
# guard was built to close.


def _reject(argv: object) -> None:
    from tests.conftest import _reject_privileged_systemctl

    _reject_privileged_systemctl(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["systemctl", "stop", "hal0-agent@hermes.service"],
        ["sudo", "-n", SEAM_BIN, "stop-agent", "hermes"],
        ["sudo", "-n", SEAM_BIN, "disable-agent", "hermes"],
        ["sudo", "-n", SEAM_BIN, "write-unit", "slot"],
        ["sudo", "systemctl", "restart", "hal0-api.service"],
        ["sudo", "-u", "hal0", "systemctl", "stop", "x.service"],
        "sudo -n /usr/lib/hal0/bin/hal0-systemctl stop-agent hermes",
    ],
)
def test_guard_rejects_every_escalation_shape(argv: object) -> None:
    with pytest.raises(AssertionError, match=r"privilege seam|real system bus"):
        _reject(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["systemctl", "is-active", "hal0-agent@hermes.service"],
        ["systemctl", "show", "-p", "MainPID", "--value", "hal0-api.service"],
        ["journalctl", "-u", "hal0-api", "-n", "50"],
        ["sudo", "-n", SEAM_BIN, "help"],
        ["bash", "installer/wrappers/hal0-systemctl", "help"],
        ["echo", "stop"],
        None,
    ],
)
def test_guard_allows_read_only_and_non_systemd(argv: object) -> None:
    """Read-only verbs need no authorization and several probes legitimately
    use them — #1357 deliberately left them alone. Keep it that way."""
    _reject(argv)
