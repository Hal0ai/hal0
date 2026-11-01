"""The fail-watcher and the per-request readiness gate on a halogen slot.

The engine can go silent under host memory pressure, and tearing it down then is
what leaves the driver holding its memory. So a /health that hangs must not read
as a death: only a connection that actually breaks does. These run the real
``ContainerProvider.health`` dispatch and ``HalogenProvider.liveness`` over a
scripted transport, beneath the real watchdog loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from hal0.providers import halogen as halogen_module
from hal0.providers.container import ContainerProvider
from hal0.providers.halogen import HalogenProvider
from hal0.slots import watchdog as wd_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


class _RealHealth(FakeContainerProvider):
    """A fake unit with the real health dispatch."""

    async def health(self, port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        return await ContainerProvider().health(port, slot_cfg)


@pytest.fixture
def provider(
    slot_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _RealHealth:
    (slot_root / "chat.toml").write_text(
        'name = "chat"\ntype = "llm"\ndevice = "gpu-rocm"\nport = 8081\n'
        'profile = "halogen"\n[model]\ndefault = "qwen3.8-flash-next-ht43"\n',
        encoding="utf-8",
    )
    fake = _RealHealth()
    fake.active.add("chat")
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)
    monkeypatch.setattr(wd_mod, "_FAIL_WATCH_INTERVAL_S", 0.02)
    return fake


def _http(handler: Callable[[httpx.Request], Any]) -> Any:
    transport = httpx.MockTransport(handler)
    return patch.object(
        halogen_module, "async_client", lambda **kw: httpx.AsyncClient(transport=transport)
    )


async def _ready(sm: SlotManager) -> None:
    await sm._transition("chat", SlotState.READY, model_id="m", port=8081, force=True)


async def _wait_for(sm: SlotManager, target: SlotState, *, timeout_s: float = 3.0) -> SlotState:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and sm.state("chat") is not target:
        await asyncio.sleep(0.01)
    return sm.state("chat")


def _hangs(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer")


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


# Probes the watcher must have made before a hang is judged. A /health that was
# counted as a strike would stamp ERROR after the second probe and the
# re-verification under the lock (the refused-connection test below: 3 probes,
# then ERROR) and the watcher would stop, so a fourth probe is proof the hang
# was tolerated, not just that it had not been caught yet.
_PROBES_PAST_THE_STRIKES = 4

# Only reached when the watcher stops probing (the failure this test exists to
# catch), never on the passing path: the wait below ends on the probes, not on
# elapsed time, so a loaded host only makes it slower, never wrong.
_PROBE_WAIT_CEILING_S = 15.0


async def test_a_hanging_health_never_stamps_error_on_a_ready_slot(provider: _RealHealth) -> None:
    calls = {"n": 0}
    enough = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] >= _PROBES_PAST_THE_STRIKES:
            enough.set()
        return _hangs(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        try:
            await asyncio.wait_for(enough.wait(), timeout=_PROBE_WAIT_CEILING_S)
        except TimeoutError:
            pytest.fail(
                f"the watcher stopped probing after {calls['n']} probes; "
                f"the slot is {sm.state('chat')}"
            )
    assert calls["n"] >= _PROBES_PAST_THE_STRIKES, "the watcher must have probed past the strikes"
    assert sm.state("chat") is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_503_never_stamps_error_on_a_ready_slot(provider: _RealHealth) -> None:
    """Upstream's ``engine_unresponsive``: the front end is up and the engine's PING
    failed, which is the silent engine this probe exists to leave alone."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="engine_unresponsive")

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        await asyncio.sleep(0.3)
        assert calls["n"] >= 2, "the watcher must have probed more than the strike count"
        assert sm.state("chat") is SlotState.READY
        assert await sm.container_readiness_check("chat") == (False, "starting")
        assert await sm._probe_health("chat", strict=True) is False
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_500_still_stamps_error_after_two_strikes(provider: _RealHealth) -> None:
    """Only the engine-silent status is a stall."""
    sm = SlotManager()
    with _http(lambda request: httpx.Response(500, text="boom")):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR


async def test_a_refused_connection_still_stamps_error_after_two_strikes(
    provider: _RealHealth,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _refused(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR
    # Two strikes, then the re-verification under the lock.
    assert calls["n"] == 3
    record = sm._states[sm._key("chat")]
    assert record.message == "model server failed /health probe"
    assert record.extra.get("health_ok") is False


async def test_a_stall_that_breaks_is_struck_from_then_on(provider: _RealHealth) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _hangs(request) if calls["n"] <= 3 else _refused(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR


async def test_readiness_holds_a_request_only_for_the_short_budget(provider: _RealHealth) -> None:
    """The per-request gate must not sit out the watcher's 35 s read."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30.0)
        return httpx.Response(200, json={})

    sm = SlotManager()
    started = time.monotonic()
    with _http(handler), patch.object(HalogenProvider, "readiness_budget_s", 0.05):
        assert await sm.container_readiness_check("chat") == (False, "starting")
    assert time.monotonic() - started < 5.0


async def test_readiness_maps_a_reported_stall_to_starting(provider: _RealHealth) -> None:
    sm = SlotManager()
    with _http(_hangs):
        assert await sm.container_readiness_check("chat") == (False, "starting")


async def test_readiness_passes_a_slot_that_answers(provider: _RealHealth) -> None:
    sm = SlotManager()
    with _http(lambda request: httpx.Response(200, json={})):
        assert await sm.container_readiness_check("chat") == (True, "ready")


async def test_a_stall_is_inconclusive_to_both_probe_modes(provider: _RealHealth) -> None:
    """Nobody demotes on it, and nobody promotes an adopted slot on it either."""
    sm = SlotManager()
    with _http(_hangs):
        assert await sm._probe_health("chat") is True
        assert await sm._probe_health("chat", strict=True) is False
