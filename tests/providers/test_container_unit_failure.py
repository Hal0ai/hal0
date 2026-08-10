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
        lambda path, text: None,
    )

    provider._write_and_start_unit("chat", "[Container]\n")

    verbs = [args[1] for args in calls if args[0] == "systemctl"]
    assert verbs == ["daemon-reload", "reset-failed", "restart"], (
        "reset-failed must sit between the generator reload and the start"
    )
