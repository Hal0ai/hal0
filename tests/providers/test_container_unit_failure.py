"""#1791 / #1424 — the provider's systemd truth probe and start-limit recovery.

``is-active`` alone cannot tell "not started yet" from "systemd gave up on it":
both read inactive. ``unit_status`` / ``unit_failure_reason`` are the probe that
separates them, and they are what stops a crash-looping slot being reported as
``warming`` forever.

``reset_failed`` is the other half: once systemd hits ``StartLimitBurst`` it
refuses every ``start``/``restart`` for the rest of ``StartLimitIntervalSec``
("Start request repeated too quickly"), so a crash-looped slot was unloadable
via the API until an operator ran ``systemctl reset-failed`` by hand.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from hal0.providers.container import ContainerProvider
from hal0.slots.state import SlotSpawnFailed


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["systemctl"], returncode=returncode, stdout=stdout)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ContainerProvider:
    return ContainerProvider()


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    provider: ContainerProvider,
    stdout: str,
    calls: list[tuple[str, ...]] | None = None,
) -> None:
    def _run(self: Any, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(args)
        return _completed(stdout)

    monkeypatch.setattr(ContainerProvider, "_run", _run)


# ── unit_status / unit_failure_reason ───────────────────────────────────────


def test_unit_status_parses_systemctl_show(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nSubState=failed\n"
        "Result=start-limit-hit\nNRestarts=5\n",
        calls,
    )

    props = provider.unit_status("chat")

    assert props["ActiveState"] == "failed"
    assert props["Result"] == "start-limit-hit"
    assert props["NRestarts"] == "5"
    # Read-only verb — must never be a mutating seam call.
    assert calls[0][:2] == ("systemctl", "show")


def test_start_limit_hit_names_the_cause_and_the_recovery(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=start-limit-hit\nNRestarts=5\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert "crash-looping" in reason
    assert "start-limit-hit" in reason
    assert "5 restarts" in reason
    # An operator reading `hal0 status` must be told how to recover (#1791
    # facet 2: nothing surfaced the loss and nothing recovered it).
    assert "hal0 slot load chat" in reason


def test_plain_failure_is_distinguished_from_the_start_limit(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nNRestarts=1\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert "start-limit-hit" not in reason
    assert "result=exit-code" in reason
    assert "journalctl" in reason


# ── #2126: naming the deterministic fault behind a failed unit ─────────────


def test_sigill_failure_names_the_image_hardware_mismatch(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2126's exact shape: podman propagates the container's SIGILL as its
    own 128+signal exit, so systemd records ``status=132/n/a``. The old reason
    string said only ``result=exit-code`` — an operator's only clue that the
    runner image was built for a CPU this box does not have was the raw
    journal. Say it in the reason.
    """
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nNRestarts=1\nExecMainStatus=132\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert "SIGILL" in reason
    assert "ISA baseline" in reason
    assert "#2126" in reason
    # The generic reason survives underneath — this ANNOTATES, never replaces.
    assert "result=exit-code" in reason
    assert "journalctl" in reason


def test_sigill_annotation_also_reaches_the_crash_loop_reason(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash-looped slot is where an operator actually looks, so the fault
    must be named there too — that is the surface #2126 reported as showing
    nothing but ``warming`` and then ``start-limit-hit``."""
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=start-limit-hit\n"
        "NRestarts=5\nExecMainStatus=132\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert "crash-looping" in reason
    assert "SIGILL" in reason
    assert "hal0 slot load chat" in reason


@pytest.mark.parametrize(
    ("status", "needle"),
    [("134", "SIGABRT"), ("139", "SIGSEGV")],
)
def test_other_deterministic_faults_are_named_without_the_sigill_hint(
    provider: ContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    needle: str,
) -> None:
    """SIGSEGV/SIGABRT get named, but not the ISA-mismatch diagnosis — a
    segfault is far more often a model the build cannot parse (#1790)."""
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\n"
        f"NRestarts=1\nExecMainStatus={status}\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert needle in reason
    assert "ISA baseline" not in reason


@pytest.mark.parametrize("status", ["", "1", "78", "137", "not-a-number"])
def test_non_fault_statuses_leave_the_reason_untouched(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A real exit code, the OOM-killer's 137, an absent or unparseable
    property: no annotation. This must never invent a fault it did not read.
    """
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\n"
        f"NRestarts=1\nExecMainStatus={status}\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert reason == (
        "hal0-slot@chat.service failed (result=exit-code, restarts=1) — "
        "see `journalctl -u hal0-slot@chat.service`"
    )


def test_exec_main_status_is_actually_requested_from_systemd(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The annotation is worthless if the property is never asked for."""
    calls: list[tuple[str, ...]] = []
    _stub_run(monkeypatch, provider, "LoadState=loaded\nActiveState=failed\n", calls)

    provider.unit_status("chat")

    assert "--property=ExecMainStatus" in calls[0]


def test_a_healthy_unit_with_a_stale_fault_status_stays_silent(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ExecMainStatus`` lingers on a unit that has since been restarted
    successfully. Only a terminal ActiveState earns a reason at all — the
    annotation must not create one."""
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=active\nSubState=running\n"
        "Result=success\nExecMainStatus=132\n",
    )

    assert provider.unit_failure_reason("chat") == ""


def test_a_removed_unit_is_a_failure_reason_not_silence(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start-limit exhaustion eventually leaves ``could not be found`` (#1791)."""
    _stub_run(monkeypatch, provider, "LoadState=not-found\nActiveState=inactive\n")

    reason = provider.unit_failure_reason("chat")

    assert "no longer exists" in reason
    assert "hal0 slot load chat" in reason


@pytest.mark.parametrize(
    "stdout",
    [
        "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n",
        "LoadState=loaded\nActiveState=activating\nResult=success\n",
        "LoadState=loaded\nActiveState=inactive\nResult=success\n",
        "",
    ],
)
def test_a_healthy_or_merely_stopped_unit_reports_no_failure(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    """Only a terminal systemd condition earns a string.

    Callers treat a non-empty reason as PROOF the slot is dead and stamp a red
    ERROR on it, so a legitimate stop (GPU arbiter handoff, idle policy) or an
    unreadable probe must stay silent.
    """
    _stub_run(monkeypatch, provider, stdout)

    assert provider.unit_failure_reason("chat") == ""


def test_a_probe_that_explodes_is_not_a_failure_verdict(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: Any, *args: str, **kwargs: Any) -> None:
        raise OSError("systemctl missing")

    monkeypatch.setattr(ContainerProvider, "_run", _boom)

    assert provider.unit_failure_reason("chat") == ""


# ── reset-failed on the start path ──────────────────────────────────────────


def test_reset_failed_is_best_effort_and_routes_the_seam_verb(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    kwargs_seen: list[dict[str, Any]] = []

    def _run(self: Any, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        kwargs_seen.append(kwargs)
        return _completed("")

    monkeypatch.setattr(ContainerProvider, "_run", _run)

    provider.reset_failed("chat")

    assert calls == [("systemctl", "reset-failed", provider._unit_name("chat"))]
    # A reset-failed that itself fails must never block the start it precedes.
    assert kwargs_seen[0]["check"] is False


def test_the_start_path_clears_a_start_limited_unit_before_restarting(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1424 facet 3 / #1791 facet 2 — `hal0 slot load` must work FIRST try.

    Without this, a slot that crash-looped past ``StartLimitBurst`` stayed
    unloadable via the API for the whole ``StartLimitIntervalSec`` window: every
    ``restart`` returned 1 with "Start request repeated too quickly". This is the
    ONE path every load / restart / swap funnels through.
    """
    calls: list[tuple[str, ...]] = []

    def _run(self: Any, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed("")

    monkeypatch.setattr(ContainerProvider, "_run", _run)
    monkeypatch.setattr(
        "hal0.providers.container._SYSTEMCTL_SEAM.write_quadlet",
        lambda path, text, timeout=None: None,
    )

    provider._write_and_start_unit("chat", "[Container]\n")

    verbs = [args[1] for args in calls if args[0] == "systemctl"]
    assert verbs == ["daemon-reload", "reset-failed", "restart"], (
        "reset-failed must sit between the generator reload and the start"
    )


# ── #1424 facet 2 — a failed start surfaces typed, reason-first ─────────────


def test_a_failed_restart_raises_slot_spawn_failed_with_the_systemd_reason(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1424 facet 2 — the spawn seam's ``CalledProcessError`` must not escape raw.

    Raw, it is not a ``Hal0Error``: it falls through the API's generic
    ``Exception`` handler as ``500 system.internal``, and ``str(exc)`` — the
    full sudo seam argv — is what gets stamped into the slot's user-visible
    ``metadata.message``. Typed, the envelope is ``slot.spawn_failed`` and the
    message is systemd's failure reason (#1791 probe), not the argv.
    """
    unit = provider._unit_name("chat")
    seam_argv = ["sudo", "-n", "hal0-systemctl", "restart", unit]

    def _run(self: Any, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("systemctl", "restart"):
            raise subprocess.CalledProcessError(1, seam_argv, stderr="Job failed.")
        if args[:2] == ("systemctl", "show"):
            return _completed(
                "LoadState=loaded\nActiveState=failed\nResult=exit-code\nNRestarts=1\n"
            )
        return _completed("")

    monkeypatch.setattr(ContainerProvider, "_run", _run)
    monkeypatch.setattr(
        "hal0.providers.container._SYSTEMCTL_SEAM.write_quadlet",
        lambda path, text, timeout=None: None,
    )

    with pytest.raises(SlotSpawnFailed) as excinfo:
        provider._write_and_start_unit("chat", "[Container]\n")

    err = excinfo.value
    assert err.code == "slot.spawn_failed"
    # The message is systemd's reason, written for an operator…
    assert "result=exit-code" in err.message
    assert "journalctl" in err.message
    # …never the seam argv (the original report leaked the full sudo command).
    assert "sudo" not in err.message
    assert "hal0-systemctl" not in err.message
    # The raw error stays on the cause chain for logs, not for users.
    assert isinstance(err.__cause__, subprocess.CalledProcessError)


def test_a_failed_restart_is_typed_even_when_the_probe_has_no_reason(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``unit_failure_reason`` reads nothing terminal (seam denied the
    verb, race with systemd, …) the error must STILL be ``slot.spawn_failed``
    with an operator-facing fallback that names the unit — never the argv."""
    unit = provider._unit_name("chat")
    seam_argv = ["sudo", "-n", "hal0-systemctl", "restart", unit]

    def _run(self: Any, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("systemctl", "restart"):
            raise subprocess.CalledProcessError(64, seam_argv)
        if args[:2] == ("systemctl", "show"):
            # Healthy-looking props → the #1791 probe returns "".
            return _completed("LoadState=loaded\nActiveState=inactive\nResult=success\n")
        return _completed("")

    monkeypatch.setattr(ContainerProvider, "_run", _run)
    monkeypatch.setattr(
        "hal0.providers.container._SYSTEMCTL_SEAM.write_quadlet",
        lambda path, text, timeout=None: None,
    )

    with pytest.raises(SlotSpawnFailed) as excinfo:
        provider._write_and_start_unit("chat", "[Container]\n")

    err = excinfo.value
    assert err.code == "slot.spawn_failed"
    assert unit in err.message
    assert "exit 64" in err.message
    assert "journalctl" in err.message
    assert "sudo" not in err.message
    assert "hal0-systemctl" not in err.message


# ── #2130: a deliberate SIGTERM stop is not a failure ───────────────────────


def test_sigterm_stop_on_a_presuccessexitstatus_unit_is_not_a_failure(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2130's exact shape: `systemctl stop` on a unit rendered before
    ``SuccessExitStatus=143`` existed. podman propagates the container's
    graceful SIGTERM shutdown as exit CODE 143 (128+15), so systemd records
    ``status=143 … Failed with result 'exit-code'`` and parks the unit in
    ``failed`` — but the service was ASKED to die and did so cleanly. The
    probe must stay silent: a non-empty reason stamps a red ERROR on every
    slot the documented ``migrate-flags --stop-services`` path stopped.
    """
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nNRestarts=0\nExecMainStatus=143\n",
    )

    assert provider.unit_failure_reason("chat") == ""


def test_a_crash_loop_of_sigterm_exits_is_still_a_failure(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowness guard: 143 only reads "deliberate stop" on a plain
    ``exit-code`` result. A unit that burned its whole restart ramp dying
    143 lands in ``start-limit-hit``, which stays a crash-loop verdict —
    only a requested stop parks a unit in ``failed`` with a bare 143 under
    ``Restart=always`` (systemd would otherwise have restarted it)."""
    _stub_run(
        monkeypatch,
        provider,
        "LoadState=loaded\nActiveState=failed\nResult=start-limit-hit\n"
        "NRestarts=5\nExecMainStatus=143\n",
    )

    reason = provider.unit_failure_reason("chat")

    assert "crash-looping" in reason
    assert "start-limit-hit" in reason


# ── #2130: unit_stopped_cleanly — positive evidence a stop was deliberate ──


@pytest.mark.parametrize(
    "stdout",
    [
        # Clean stop on a SuccessExitStatus=143 unit, or reset-failed, or
        # not started since boot.
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n",
        # Result unreported (older systemd / property elided) but inactive.
        "LoadState=loaded\nActiveState=inactive\nSubState=dead\n",
        # SIGTERM stop on a unit predating SuccessExitStatus=143.
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStatus=143\n",
    ],
)
def test_unit_stopped_cleanly_recognises_deliberate_stops(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _stub_run(monkeypatch, provider, stdout)

    assert provider.unit_stopped_cleanly("chat") is True


@pytest.mark.parametrize(
    "stdout",
    [
        # Crash loop: systemd gave up.
        "LoadState=loaded\nActiveState=failed\nResult=start-limit-hit\nNRestarts=5\n",
        # Usage-exit death (#2221's shape) — a real failure.
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStatus=64\n",
        # SIGILL — terminal fault (#2126).
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStatus=132\n",
        # Plain non-zero exit.
        "LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStatus=1\n",
        # Mid-restart between crashes: not evidence of a stop.
        "LoadState=loaded\nActiveState=activating\nResult=success\n",
        # Still running.
        "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n",
        # Removed unit (the output-sanity teardown shape) — the record's
        # verdict must survive, so a gone unit is NOT a clean stop.
        "LoadState=not-found\nActiveState=inactive\n",
        # Inactive but the last run genuinely failed (Restart=no style).
        "LoadState=loaded\nActiveState=inactive\nResult=exit-code\n",
        # Unreadable probe output.
        "",
    ],
)
def test_unit_stopped_cleanly_never_manufactures_an_offline(
    provider: ContainerProvider, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    """Callers use ``True`` to retire a cached ERROR, so absence of evidence
    (or evidence of an actual crash) must answer ``False``."""
    _stub_run(monkeypatch, provider, stdout)

    assert provider.unit_stopped_cleanly("chat") is False
