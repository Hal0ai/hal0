"""HTTP tests for ``POST /api/agents/{agent_id}/restart`` (v0.3 PR-11).

Pins the route contract the dashboard's SidebarAgentBlock + ServiceStatus
chip consume:

* Successful systemctl restart → 200 ``{status: "restarted"|"restarting"}``
* Non-zero systemctl exit → 5xx envelope w/ ``code="agent.restart_failed"``
* Missing systemctl on host → 5xx envelope w/ code ``agent.systemctl_unavailable``
* Subprocess timeout → 5xx envelope w/ ``code="agent.restart_timeout"``
* Unknown agent id → 404 ``code="agent.unknown"``

Uses :mod:`unittest.mock` to fake the systemctl subprocess so the test
doesn't actually invoke systemd — the real binary isn't even available
in many CI sandboxes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hal0.api.agents import restart as restart_route


class _FakeProc:
    """Minimal stand-in for ``asyncio.subprocess.Process``.

    Records the call so tests can assert ``systemctl restart <unit>``
    was invoked with the right argv, and lets the test inject
    ``returncode`` + ``stdout`` + ``stderr`` for failure paths.
    """

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        *,
        hang: bool = False,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            # Sleep longer than the route's wait_for timeout.
            await asyncio.sleep(60)
        return (self._stdout, self._stderr)

    def kill(self) -> None:
        self.killed = True


def _patch_subprocess(
    fake: _FakeProc,
) -> Callable[..., Awaitable[_FakeProc]]:
    """Return a coroutine that ``create_subprocess_exec`` can be patched
    to. The closure captures the recorded argv so tests can assert it."""

    captured: dict[str, Any] = {"argv": None}

    async def _fake_create(*argv: str, **_kwargs: Any) -> _FakeProc:
        captured["argv"] = argv
        return fake

    # Stash the captured dict on the function so tests can read it.
    _fake_create.captured = captured  # type: ignore[attr-defined]
    return _fake_create


@pytest.fixture
def patched_systemctl_ok(monkeypatch: pytest.MonkeyPatch) -> _FakeProc:
    """Pretend systemctl is on PATH and returns 0."""
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    fake = _FakeProc(returncode=0, stdout=b"", stderr=b"")
    patcher = _patch_subprocess(fake)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", patcher)
    return fake


def test_restart_ok_returns_restarted_status(
    client: TestClient, patched_systemctl_ok: _FakeProc
) -> None:
    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["unit"] == "hal0-agent@hermes.service"
    assert body["status"] == "restarted"
    assert "detail" in body


def test_restart_unknown_agent_returns_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    r = client.post("/api/agents/pi-coder/restart")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "agent.unknown"


def test_restart_no_systemctl_on_host_returns_5xx(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: None)
    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    body = r.json()
    assert body["error"]["code"] == "agent.systemctl_unavailable"


def test_restart_nonzero_exit_surfaces_stderr_in_error_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    fake = _FakeProc(
        returncode=1,
        stdout=b"",
        stderr=b"Failed to restart hal0-agent@hermes.service: Unit not found.",
    )
    patcher = _patch_subprocess(fake)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", patcher)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    body = r.json()
    assert body["error"]["code"] == "agent.restart_failed"
    # The envelope should surface the trimmed stderr in details for the
    # dashboard's toast, but NOT raw subprocess output in the message
    # (the message includes it but bounded to 200 chars per route docs).
    assert "Unit not found" in body["error"]["message"]


def test_restart_spawn_failure_surfaces_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")

    async def _raise(*_argv: str, **_kwargs: Any) -> Any:
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    body = r.json()
    assert body["error"]["code"] == "agent.restart_failed"
    assert "simulated spawn failure" in body["error"]["message"]


def test_restart_timeout_surfaces_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    # Shrink the timeout to keep the test fast.
    monkeypatch.setattr(restart_route, "SYSTEMCTL_TIMEOUT_SECONDS", 0.05)
    fake = _FakeProc(hang=True)
    patcher = _patch_subprocess(fake)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", patcher)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    body = r.json()
    assert body["error"]["code"] == "agent.restart_timeout"
    # Kill was invoked to clean up the wedged subprocess.
    assert fake.killed is True


def test_restart_invokes_correct_argv(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    fake = _FakeProc(returncode=0)
    patcher = _patch_subprocess(fake)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", patcher)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200
    argv = patcher.captured["argv"]  # type: ignore[attr-defined]
    assert argv == (
        "/usr/bin/systemctl",
        "restart",
        "hal0-agent@hermes.service",
    )


def test_restart_emits_audit_log_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit row goes to the ``hal0.agents.audit`` logger.

    We patch the logger's ``info`` method to capture every audit emit
    rather than rely on structlog capture (which has its own configuration
    surface).
    """
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    fake = _FakeProc(returncode=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _patch_subprocess(fake))

    audit_info_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_info(event: str, **kwargs: Any) -> None:
        audit_info_calls.append((event, kwargs))

    monkeypatch.setattr(restart_route.audit_log, "info", _capture_info)

    r = client.post(
        "/api/agents/hermes/restart",
        headers={"X-hal0-Agent": "test-runner"},
    )
    assert r.status_code == 200

    event_names = [name for name, _ in audit_info_calls]
    assert "agent.restart.invoked" in event_names
    assert "agent.restart.ok" in event_names
    # Actor identity from X-hal0-Agent is captured.
    invoked = next(kw for name, kw in audit_info_calls if name == "agent.restart.invoked")
    assert invoked["actor"] == "test-runner"
    assert invoked["agent_id"] == "hermes"
    assert invoked["unit"] == "hal0-agent@hermes.service"


def test_restart_actor_defaults_to_dashboard(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No X-hal0-Agent header → actor recorded as ``hal0-dashboard``."""
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _patch_subprocess(_FakeProc(0)))

    audit_calls: list[dict[str, Any]] = []

    def _capture_info(event: str, **kwargs: Any) -> None:
        if event == "agent.restart.invoked":
            audit_calls.append(kwargs)

    monkeypatch.setattr(restart_route.audit_log, "info", _capture_info)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200
    assert audit_calls[0]["actor"] == "hal0-dashboard"


def test_restart_activating_stderr_yields_restarting_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """systemctl returns 0 + ``activating`` in stderr → status=restarting."""
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")
    fake = _FakeProc(
        returncode=0,
        stdout=b"",
        stderr=b"Activating hal0-agent@hermes.service",
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _patch_subprocess(fake))

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200
    assert r.json()["status"] == "restarting"


# Mirrored AsyncMock-style test for a future contributor who prefers
# the AsyncMock idiom over the bespoke _FakeProc class. Documents that
# either pattern works.
def test_restart_with_async_mock_pattern(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(restart_route, "_systemctl_path", lambda: "/usr/bin/systemctl")

    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def _factory(*_argv: str, **_kwargs: Any) -> Any:
        return proc

    with patch.object(asyncio, "create_subprocess_exec", _factory):
        r = client.post("/api/agents/hermes/restart")

    assert r.status_code == 200
    proc.communicate.assert_awaited_once()
