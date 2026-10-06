"""#1974 review (P1): the FLM-image probe must not stall the event loop.

``_flm_image_present()`` asks the ``hal0-podman-ro`` seam, a blocking call
that can take up to its 10 s timeout when podman is wedged. The async
handlers that reach it (``GET /api/capabilities`` via
``CapabilityOrchestrator.get_state``; ``GET /api/backends`` and
``GET /api/backends/{id}``) must run it on a worker thread, so a hung seam
slows that one request instead of freezing every request hal0-api serves.

Each test drives the real catalog path with a provider whose
``image_present`` sleeps, runs a heartbeat coroutine alongside the handler,
and asserts the heartbeat kept ticking and the probe ran off the loop thread.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest

from hal0.api.routes import backends as backends_routes
from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.providers import container as container_mod

#: How long the stub probe blocks. Long enough that a loop-blocking call
#: would starve the heartbeat completely; short enough for a unit test.
_PROBE_BLOCK_S = 0.3
_HEARTBEAT_S = 0.01


class _SlowProvider:
    """``ContainerProvider`` stand-in whose ``image_present`` blocks like a
    hung seam, then answers "present"."""

    def __init__(self) -> None:
        self.probe_threads: list[int] = []

    def image_present(self, image: str) -> bool | None:
        self.probe_threads.append(threading.get_ident())
        time.sleep(_PROBE_BLOCK_S)
        return True


@pytest.fixture
def slow_probe(monkeypatch: pytest.MonkeyPatch) -> _SlowProvider:
    provider = _SlowProvider()
    monkeypatch.setattr(container_mod, "container_provider", lambda: provider)
    monkeypatch.setattr(
        catalog,
        "load_hardware_info",
        lambda: types.SimpleNamespace(npu=types.SimpleNamespace(present=True), gpus=[]),
    )
    catalog.reset_flm_image_present_cache()
    yield provider
    catalog.reset_flm_image_present_cache()


async def _with_heartbeat[T](awaitable: Awaitable[T]) -> tuple[T, int]:
    """Await ``awaitable`` while a heartbeat ticks; return (result, ticks)."""
    ticks = 0
    done = asyncio.Event()

    async def beat() -> None:
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            ticks += 1

    task = asyncio.create_task(beat())
    try:
        result = await awaitable
    finally:
        done.set()
        await task
    return result, ticks


def _assert_off_loop(provider: _SlowProvider, ticks: int, loop_thread: int) -> None:
    assert provider.probe_threads, "the FLM-image probe never ran"
    assert loop_thread not in provider.probe_threads, "probe ran on the event-loop thread"
    # A loop-blocking probe leaves the heartbeat at 0-1 ticks; off the loop
    # it keeps ticking for the whole block (~30 at these settings).
    assert ticks >= 5, f"event loop stalled during the probe (heartbeat ticks={ticks})"


class _NoSlots:
    """Slot manager with nothing loaded: every lookup misses."""

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []

    async def status(self, slot_name: str) -> Any:
        raise LookupError(slot_name)

    async def list(self) -> list[Any]:
        return []


async def test_capabilities_get_state_probes_off_the_loop(
    slow_probe: _SlowProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)
    catalog_threads: list[int] = []

    def _catalogs(registry: Any = None) -> dict[str, Any]:
        catalog_threads.append(threading.get_ident())
        return {}

    monkeypatch.setattr(catalog, "catalogs_by_slot", _catalogs)
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    orch = CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]

    state, ticks = await _with_heartbeat(orch.get_state())

    loop_thread = threading.get_ident()
    _assert_off_loop(slow_probe, ticks, loop_thread)
    assert catalog_threads and loop_thread not in catalog_threads
    assert state["backends"][0]["id"] == "npu"


async def test_list_backends_probes_off_the_loop(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends_routes, "load_hardware_info", _raise)

    rows, ticks = await _with_heartbeat(
        backends_routes.list_backends(None, _NoSlots())  # type: ignore[arg-type]
    )

    _assert_off_loop(slow_probe, ticks, threading.get_ident())
    assert rows[0]["id"] == "npu"
    assert rows[0]["state"] == "ready"
    assert len(slow_probe.probe_threads) == 1, "probe must run once per request, not per row"


async def test_get_backend_details_probes_off_the_loop(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends_routes, "load_hardware_info", _raise)

    row, ticks = await _with_heartbeat(
        backends_routes.get_backend_details("npu", None, _NoSlots())  # type: ignore[arg-type]
    )

    _assert_off_loop(slow_probe, ticks, threading.get_ident())
    assert row["id"] == "npu"
    assert row["state"] == "ready"


def _raise() -> Any:
    raise FileNotFoundError("no hardware probe in unit tests")


# ── concurrent misses share one probe (#1974 review P2) ─────────────────────


def test_concurrent_cold_cache_callers_share_one_probe(slow_probe: _SlowProvider) -> None:
    """N threads missing the cache at once must cost ONE seam call, not N.
    Each would otherwise hold a default-executor thread for the whole probe."""
    n = 8
    barrier = threading.Barrier(n)
    results: list[bool] = []

    def caller() -> None:
        barrier.wait()
        results.append(catalog._flm_image_present())

    threads = [threading.Thread(target=caller) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results == [True] * n
    assert len(slow_probe.probe_threads) == 1


async def test_concurrent_handler_requests_share_one_probe(
    slow_probe: _SlowProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The burst Codex described: several /api/backends GETs on a cold cache."""
    monkeypatch.setattr(backends_routes, "load_hardware_info", _raise)

    rows = await asyncio.gather(
        *(backends_routes.list_backends(None, _NoSlots()) for _ in range(5))  # type: ignore[arg-type]
    )

    assert all(r[0]["id"] == "npu" for r in rows)
    assert len(slow_probe.probe_threads) == 1


def test_reset_during_a_probe_drops_its_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that started before an FLM pull's reset must not cache its
    (possibly pre-pull) answer, and the reset must not wait for the probe."""
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    class _GatedProvider:
        def image_present(self, image: str) -> bool | None:
            calls.append(image)
            if len(calls) == 1:
                started.set()
                release.wait(timeout=5)
                return False
            return True

    monkeypatch.setattr(container_mod, "container_provider", lambda: _GatedProvider())
    catalog.reset_flm_image_present_cache()
    first: list[bool] = []
    t = threading.Thread(target=lambda: first.append(catalog._flm_image_present()))
    t.start()
    assert started.wait(timeout=5)

    reset_began = time.monotonic()
    catalog.reset_flm_image_present_cache()
    assert time.monotonic() - reset_began < 0.1, "reset waited on the in-flight probe"
    release.set()
    t.join(timeout=5)

    assert first == [False]
    assert catalog._flm_image_present() is True, "stale pre-reset 'absent' was cached"
    assert len(calls) == 2
    catalog.reset_flm_image_present_cache()
