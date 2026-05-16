"""Pytest fixtures and marker registration for the slots subtree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots import manager as mgr_mod
from hal0.slots.manager import SlotManager


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker so --strict-markers stays clean.

    The integration suite needs hal0-slot@.service installed on the host
    and is intended for CI / release-gate runs only.  See PLAN.md §10.
    """
    config.addinivalue_line(
        "markers",
        "integration: end-to-end slot lifecycle tests requiring a real "
        "hal0-slot@.service installation",
    )


# ── shared fixtures ─────────────────────────────────────────────────────────
#
# Consumed by test_manager.py, test_fail_watcher.py, and the
# state-state-state suite (test_pulling_serving_idle) so the systemctl +
# health-probe stubs aren't duplicated across files.


class _FakeProc:
    """Stand-in for asyncio.subprocess.Process used by systemctl calls."""

    def __init__(self, rc: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = rc
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def systemctl_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept asyncio.create_subprocess_exec and record systemctl invocations.

    Returns a dict tracking calls; tests can also set entries to simulate
    failure rcs by mutating ``state["force_rc"][(action, service)] = rc``,
    and flip ``state["is_active_state"]`` to drive the fail-watcher.
    """
    state: dict[str, Any] = {
        "calls": [],
        "is_active_state": "inactive",  # flips to 'active' after start
        "force_rc": {},  # {("action", "service"): rc}
    }

    async def fake_create(*args: str, **_: Any) -> _FakeProc:
        cmd = list(args)
        state["calls"].append(cmd)
        if cmd[:1] != ["systemctl"]:
            raise AssertionError(f"unexpected subprocess: {cmd}")
        action = cmd[1] if len(cmd) > 1 else ""
        service = cmd[2] if len(cmd) > 2 else ""
        key = (action, service)
        if key in state["force_rc"]:
            return _FakeProc(rc=state["force_rc"][key])
        if action == "is-active":
            return _FakeProc(rc=0 if state["is_active_state"] == "active" else 3)
        if action == "start":
            state["is_active_state"] = "active"
            return _FakeProc(rc=0)
        if action == "stop":
            state["is_active_state"] = "inactive"
            return _FakeProc(rc=0)
        if action == "daemon-reload":
            return _FakeProc(rc=0)
        return _FakeProc(rc=0)

    monkeypatch.setattr(mgr_mod.asyncio, "create_subprocess_exec", fake_create)
    return state


@pytest.fixture
def stub_await_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short-circuit the HTTP health probe so unit tests don't sleep.

    Returns ``SlotState.READY`` so callers exercise the happy-path
    transition.  Tests that need to drive the new WARMING → IDLE edge
    (issue #31) stub ``_await_ready`` themselves to return
    ``SlotState.IDLE``.
    """
    from hal0.slots.state import SlotState

    async def _ok(self: SlotManager, slot_name: str, port: int, provider: str) -> SlotState:
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_await_ready", _ok)


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Yield the slots-config root and ensure a sample slot exists on disk."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "primary.toml").write_text(
        "\n".join(
            [
                'name = "primary"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root
