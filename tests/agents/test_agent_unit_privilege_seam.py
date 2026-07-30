"""Regression tests for the agent-unit privilege seam.

Bug (user-visible, reproducible): uninstalling the hermes agent popped a
desktop polkit dialog — *"Authentication is required to stop
'hal0-agent@hermes.service'"*. ``HermesDriver._stop_services()`` ran a bare
``systemctl stop hal0-agent@hermes.service``. That teardown executes inside
``hal0-api``, which is ``User=hal0`` post-P3-perms, so an unprivileged
state-mutating call on a *system* unit escalates via polkit. Worse, both calls
were wrapped in ``contextlib.suppress(...)`` with ``capture_output=True``, so
cancelling the dialog left the agent running and silently recreating files
mid-teardown — precisely the condition #453 added ``_stop_services()`` to
prevent.

hal0's established answer to this class of op is sudoers-plus-seam
(``installer/wrappers/hal0-systemctl`` + ``packaging/sudoers/hal0-systemctl``),
but that seam was slot-only: every verb ran ``validate_slot_id`` against
``hal0-slot@``, so there was no verb that could act on a ``hal0-agent@<id>``
unit and the driver had nothing to call.

Three layers are covered here, all hermetic — no root, no live systemd, no
sudo, no real ``hal0`` user:

1. **The wrapper itself**, executed as real bash with a stub ``systemctl`` on
   ``PATH``: the new ``stop-agent`` / ``disable-agent`` verbs build the unit
   name from a *validated id*, and a malformed id is rejected before anything
   is executed.
2. **The routing layer** (:class:`hal0.system.seam.SystemCtlSeam`): as the hal0
   service user an agent-unit stop/disable becomes
   ``sudo -n hal0-systemctl stop-agent <id>``; everything else still passes
   through.
3. **The driver** (:meth:`HermesDriver._stop_services`): it goes through the
   seam rather than emitting bare ``systemctl`` argv, and a seam failure is
   surfaced instead of swallowed while uninstall still proceeds.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from hal0.agents.hermes.driver import HermesDriver
from hal0.system.seam import SEAM_BIN, SystemCtlSeam

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "installer" / "wrappers" / "hal0-systemctl"

#: The wrapper's ``die()`` exit code — a *validation* rejection, distinct from
#: whatever systemctl itself might have returned.
VALIDATION_EXIT = 64


# ── Layer 1: the wrapper (real bash, stub systemctl) ─────────────────────────


@pytest.fixture
def wrapper_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A PATH whose ``systemctl`` is a stub that records its argv and exits 0.

    Lets the real wrapper script run end-to-end without systemd, root, or dbus:
    if the wrapper ever executed systemctl we see exactly what argv it built;
    if it rejected the input we see an empty log.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "systemctl.log"
    stub = bin_dir / "systemctl"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexit 0\n')
    stub.chmod(0o755)

    import os

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env, log


def _run_wrapper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - every supported host has bash
        pytest.skip("bash not available")
    return subprocess.run(  # nosec B603 - fixed argv, repo-anchored script
        [bash, str(WRAPPER), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_wrapper_stop_agent_builds_the_agent_unit_name(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    """``stop-agent hermes`` → ``systemctl stop hal0-agent@hermes.service``.

    The seam builds the unit name from the validated id; the caller never
    supplies a unit string.
    """
    env, log = wrapper_env
    proc = _run_wrapper(env, "stop-agent", "hermes")
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().splitlines() == ["stop hal0-agent@hermes.service"]


def test_wrapper_disable_agent_builds_the_agent_unit_name(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    env, log = wrapper_env
    proc = _run_wrapper(env, "disable-agent", "hermes")
    assert proc.returncode == 0, proc.stderr
    assert log.read_text().splitlines() == ["disable hal0-agent@hermes.service"]


def test_wrapper_agent_verbs_never_touch_the_slot_unit_family(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    """The agent verbs are a separate family — no ``hal0-slot@`` leakage."""
    env, log = wrapper_env
    _run_wrapper(env, "stop-agent", "hermes")
    assert "hal0-slot@" not in log.read_text()


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param("", id="empty"),
        pytest.param("hermes.service", id="dotted-unit-suffix"),
        pytest.param("hal0-api", id="ok-charset-but-see-note"),
        pytest.param("../../../etc/passwd", id="traversal"),
        pytest.param("hermes hal0-api", id="argv-smuggling-via-space"),
        pytest.param("hermes@evil", id="template-escape"),
        pytest.param("hermes;reboot", id="shell-metachar-semicolon"),
        pytest.param("$(reboot)", id="command-substitution"),
        pytest.param("`reboot`", id="backtick-substitution"),
        pytest.param("herme*", id="glob"),
        pytest.param("hermes\nsshd", id="newline"),
        pytest.param("h" * 65, id="over-length"),
    ],
)
def test_wrapper_rejects_malformed_agent_id(
    wrapper_env: tuple[dict[str, str], Path], bad_id: str
) -> None:
    """Malformed ids are rejected by ``validate_agent_id`` before execution.

    ``hal0-api`` is the deliberate odd one out: it IS in the allowed identifier
    class, so the wrapper accepts it as an *id* — and that is safe precisely
    because the seam prefixes it, yielding ``hal0-agent@hal0-api.service``,
    never ``hal0-api.service``. It is asserted separately below.
    """
    env, log = wrapper_env
    proc = _run_wrapper(env, "stop-agent", bad_id)

    if bad_id == "hal0-api":
        assert proc.returncode == 0
        assert log.read_text().splitlines() == ["stop hal0-agent@hal0-api.service"]
        return

    assert proc.returncode == VALIDATION_EXIT, f"expected rejection, got {proc!r}"
    assert "bad agent id" in proc.stderr or "missing agent id" in proc.stderr
    # Nothing was executed — rejection happens before systemctl is reached.
    assert not log.exists() or log.read_text() == ""


def test_wrapper_rejects_malformed_agent_id_for_disable(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    env, log = wrapper_env
    proc = _run_wrapper(env, "disable-agent", "../hal0-api")
    assert proc.returncode == VALIDATION_EXIT
    assert not log.exists() or log.read_text() == ""


def test_wrapper_help_documents_the_agent_verbs(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    env, _ = wrapper_env
    proc = _run_wrapper(env, "help")
    assert proc.returncode == 0
    assert "stop-agent <agent-id>" in proc.stdout
    assert "disable-agent <agent-id>" in proc.stdout


def test_wrapper_grants_no_start_or_restart_for_agent_units(
    wrapper_env: tuple[dict[str, str], Path],
) -> None:
    """Only stop + disable are granted: the daemon never brings an agent UP.

    A narrower verb set is a smaller privileged surface; the install path that
    *does* start the unit is a root-only interactive CLI and needs no seam.
    """
    env, log = wrapper_env
    for verb in ("start-agent", "restart-agent", "enable-agent"):
        proc = _run_wrapper(env, verb, "hermes")
        assert proc.returncode == VALIDATION_EXIT, verb
        assert "bad cmd" in proc.stderr
    assert not log.exists() or log.read_text() == ""


# ── Layer 2: SystemCtlSeam routing ───────────────────────────────────────────


def _recorder() -> tuple[list[list[str]], Any]:
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    return calls, _run


@pytest.mark.parametrize(
    ("verb", "seam_verb"),
    [("stop", "stop-agent"), ("disable", "disable-agent")],
)
def test_seam_routes_agent_unit_verbs_when_hal0_user(verb: str, seam_verb: str) -> None:
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", verb, "hal0-agent@hermes.service", check=False)

    assert calls == [["sudo", "-n", SEAM_BIN, seam_verb, "hermes"]]


def test_seam_agent_routing_never_emits_bare_systemctl() -> None:
    """The regression guard: no bare ``systemctl`` argv reaches the OS."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "stop", "hal0-agent@hermes.service", check=False)

    assert calls[0][0] == "sudo"
    assert calls[0][:3] == ["sudo", "-n", SEAM_BIN]


def test_seam_passes_agent_unit_through_when_not_hal0_user() -> None:
    """Root installer / dev / CI: nothing to escalate, behave exactly as before."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    seam.systemctl("systemctl", "stop", "hal0-agent@hermes.service", check=False)

    assert calls == [["systemctl", "stop", "hal0-agent@hermes.service"]]


@pytest.mark.parametrize("verb", ["start", "restart", "enable", "reset-failed"])
def test_seam_does_not_route_ungranted_agent_verbs(verb: str) -> None:
    """Verbs with no agent counterpart in the wrapper are not invented here."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", verb, "hal0-agent@hermes.service", check=False)

    assert calls == [["systemctl", verb, "hal0-agent@hermes.service"]]


@pytest.mark.parametrize(
    "unit",
    [
        "hermes-gateway.service",
        "hal0-api.service",
        "sshd.service",
        "hal0-agent@bad id.service",
        "hal0-agent@.service",
        "hal0-agent@hermes.timer",
    ],
)
def test_seam_does_not_route_non_agent_units(unit: str) -> None:
    """Only well-formed ``hal0-agent@<id>.service`` names route to the seam."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "stop", unit, check=False)

    assert calls == [["systemctl", "stop", unit]]


def test_seam_still_routes_slot_units() -> None:
    """The pre-existing slot routing is untouched."""
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    seam.systemctl("systemctl", "stop", "hal0-slot@chat.service", check=False)

    assert calls == [["sudo", "-n", SEAM_BIN, "stop", "chat"]]


def test_seam_forwards_timeout() -> None:
    """A bounded call: a wedged dbus must not hang the uninstall forever."""
    seen: list[dict[str, Any]] = []

    def _run(argv: object, **kwargs: Any) -> MagicMock:
        seen.append(kwargs)
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    seam = SystemCtlSeam(run=_run, is_hal0_user=lambda: True)
    seam.systemctl("systemctl", "stop", "hal0-agent@hermes.service", check=False, timeout=10.0)

    assert seen[0]["timeout"] == 10.0


def test_seam_omits_timeout_when_not_requested() -> None:
    seen: list[dict[str, Any]] = []

    def _run(argv: object, **kwargs: Any) -> MagicMock:
        seen.append(kwargs)
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    seam = SystemCtlSeam(run=_run, is_hal0_user=lambda: True)
    seam.systemctl("systemctl", "daemon-reload", check=False)

    assert "timeout" not in seen[0]


# ── Layer 3: HermesDriver._stop_services ─────────────────────────────────────


class _FakeSeam:
    """Records every ``systemctl(...)`` call and replays canned return codes."""

    def __init__(self, *, returncodes: list[int] | None = None, raises: Exception | None = None):
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self._returncodes = list(returncodes or [])
        self._raises = raises

    def systemctl(self, *args: str, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        rc = self._returncodes.pop(0) if self._returncodes else 0
        proc = MagicMock()
        proc.returncode = rc
        proc.stdout = ""
        proc.stderr = "Interactive authentication required." if rc else ""
        return proc


@pytest.fixture
def _systemctl_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hal0.agents.hermes.driver.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )


@pytest.mark.usefixtures("_systemctl_present")
def test_driver_stop_services_uses_the_seam_not_bare_systemctl() -> None:
    """The core regression: teardown goes through the seam.

    Before the fix this method called ``subprocess.run(["systemctl", "stop",
    ...])`` directly, which is what triggered the polkit dialog.
    """
    seam = _FakeSeam()

    HermesDriver._stop_services(seam=seam)

    assert [c[0] for c in seam.calls] == [
        ("systemctl", "stop", "hal0-agent@hermes.service"),
        ("systemctl", "disable", "hal0-agent@hermes.service"),
    ]


@pytest.mark.usefixtures("_systemctl_present")
def test_driver_stop_services_never_raises_and_bounds_each_call() -> None:
    """Best-effort contract preserved: never raises, always time-bounded."""
    seam = _FakeSeam()

    HermesDriver._stop_services(seam=seam)

    for _args, kwargs in seam.calls:
        assert kwargs["check"] is False
        assert kwargs["timeout"] > 0


def test_driver_stop_services_noop_without_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host with no systemd is not an error — documented best-effort."""
    monkeypatch.setattr("hal0.agents.hermes.driver.shutil.which", lambda _name: None)
    seam = _FakeSeam()

    HermesDriver._stop_services(seam=seam)

    assert seam.calls == []


@pytest.mark.usefixtures("_systemctl_present")
def test_driver_stop_services_logs_a_seam_failure() -> None:
    """A non-zero seam exit must be SURFACED, not swallowed.

    This is the silent-failure half of the bug: cancelling the polkit prompt
    produced a non-zero rc that ``contextlib.suppress`` + ``capture_output``
    threw away, so uninstall reported success while the agent kept running.
    """
    from structlog.testing import capture_logs

    seam = _FakeSeam(returncodes=[1, 0])

    with capture_logs() as logs:
        HermesDriver._stop_services(seam=seam)

    failures = [entry for entry in logs if entry["event"] == "hermes.stop_services.nonzero"]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "warning"
    assert failures[0]["verb"] == "stop"
    assert failures[0]["returncode"] == 1
    assert "Interactive authentication required." in failures[0]["stderr"]
    # ... and the teardown still continued to `disable` afterwards.
    assert len(seam.calls) == 2


@pytest.mark.usefixtures("_systemctl_present")
def test_driver_stop_services_logs_a_timeout() -> None:
    """A wedged systemd/sudo call is logged and uninstall still proceeds."""
    from structlog.testing import capture_logs

    seam = _FakeSeam(raises=subprocess.TimeoutExpired(cmd="systemctl", timeout=10))

    with capture_logs() as logs:
        HermesDriver._stop_services(seam=seam)

    events = [entry for entry in logs if entry["event"] == "hermes.stop_services.failed"]
    assert len(events) == 2  # both verbs attempted, both reported
    assert all(entry["log_level"] == "warning" for entry in events)
    assert "TimeoutExpired" in events[0]["error"]


@pytest.mark.usefixtures("_systemctl_present")
def test_driver_stop_services_quiet_on_success() -> None:
    """No warning noise when both verbs succeed."""
    from structlog.testing import capture_logs

    seam = _FakeSeam(returncodes=[0, 0])

    with capture_logs() as logs:
        HermesDriver._stop_services(seam=seam)

    assert [e for e in logs if e["log_level"] == "warning"] == []


def test_driver_stop_services_end_to_end_argv_as_hal0_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full stack: driver → real SystemCtlSeam (as hal0) → sudo seam argv.

    The single assertion that proves the polkit prompt is gone: with a real
    :class:`SystemCtlSeam` believing it is the ``hal0`` service user, the argv
    that would have reached the OS is the NOPASSWD sudo seam call, never
    ``["systemctl", "stop", "hal0-agent@hermes.service"]``.
    """
    monkeypatch.setattr(
        "hal0.agents.hermes.driver.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    calls, run = _recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    HermesDriver._stop_services(seam=seam)

    assert calls == [
        ["sudo", "-n", SEAM_BIN, "stop-agent", "hermes"],
        ["sudo", "-n", SEAM_BIN, "disable-agent", "hermes"],
    ]
    assert not any(argv[0] == "systemctl" for argv in calls)
