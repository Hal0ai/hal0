"""#2334: no async caller runs host ``flm list -j`` on the event loop.

``flm_served_models()`` / ``flm_catalog()`` are synchronous. On a cold or
expired cache they shell ``flm list -j`` with a 30 s timeout. Every async path
that can reach that probe — directly, or through ``models_for_capability``,
``is_flm_tag`` or ``is_resolvable`` — must run it on a worker thread, or a slow
``flm`` freezes all of hal0-api while it runs.

Each test replaces ``_probe_flm_catalog`` with a stub that blocks briefly and
records whether it ran on a thread with a running event loop, cold-starts the
catalog cache, and drives one async entry point.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import hal0.providers.flm as flm_mod
from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import Hal0Error

#: How long the stub ``flm list`` blocks.
_PROBE_BLOCK_S = 0.4
_HEARTBEAT_S = 0.01

_RAW_FLM_LIST: list[dict[str, Any]] = [
    {
        "model": "embed-gemma:300m",
        "label": ["embeddings"],
        "installed": True,
        "size": 300_000_000,
        "footprint": 0.6,
    },
    {
        "model": "qwen3:0.6b",
        "label": ["reasoning"],
        "installed": True,
        "size": 600_000_000,
        "footprint": 1.2,
        "url": "https://huggingface.co/FastFlowLM/Qwen3-0.6B-NPU2/resolve/main/x",
    },
]


class _SlowFlmList:
    """``_probe_flm_catalog`` stand-in that blocks like a slow ``flm list``."""

    def __init__(self) -> None:
        """Start with no recorded probes."""
        self.calls = 0
        self.on_loop: list[bool] = []
        #: How many of the next probes answer with an empty catalog.
        self.empty_answers = 0

    def __call__(self) -> list[dict[str, Any]]:
        """Record whether this thread runs an event loop, block, then answer."""
        self.calls += 1
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        time.sleep(_PROBE_BLOCK_S)
        if self.empty_answers > 0:
            self.empty_answers -= 1
            return []
        return [dict(e) for e in _RAW_FLM_LIST]

    def assert_ran_off_loop(self) -> None:
        """At least one probe ran, and none on the event-loop thread."""
        assert self.calls, "the FLM catalog probe never ran"
        assert not any(self.on_loop), "`flm list -j` ran on the event loop"


@pytest.fixture
def slow_flm_list(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Cold FLM catalog cache whose next probe is slow; host without an NPU."""
    probe = _SlowFlmList()
    monkeypatch.setattr(flm_mod, "_probe_flm_catalog", probe)
    # No NPU: available_backends() never starts the FLM-image probe (#1974).
    monkeypatch.setattr(
        catalog,
        "load_hardware_info",
        lambda: types.SimpleNamespace(npu=types.SimpleNamespace(present=False), gpus=[]),
    )
    flm_mod.reset_flm_catalog_cache()
    yield probe
    flm_mod.reset_flm_catalog_cache()


async def _heartbeat_ticks(coro: Any) -> tuple[Any, int]:
    """Await ``coro`` while a heartbeat ticks; return its result and the ticks."""
    ticks = 0
    done = asyncio.Event()

    async def beat() -> None:
        """Tick every heartbeat interval until told to stop."""
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(_HEARTBEAT_S)
            ticks += 1

    task = asyncio.create_task(beat())
    try:
        result = await coro
    finally:
        done.set()
        await task
    return result, ticks


class _NoSlots:
    """Slot manager with nothing loaded: every lookup misses."""

    async def iter_configs(self) -> list[dict[str, Any]]:
        """No slot configs."""
        return []

    async def status(self, slot_name: str) -> Any:
        """Every slot lookup misses."""
        raise LookupError(slot_name)

    async def list(self) -> list[Any]:
        """No live slots."""
        return []


def _flm_slot(model_id: str = "qwen3:0.6b") -> Any:
    """A resident FLM slot snapshot."""
    return types.SimpleNamespace(
        name="npu",
        state="ready",
        model_id=model_id,
        backend="npu",
        metadata={"provider": "flm"},
        served_by=None,
    )


def _request(**state: Any) -> Any:
    """A Request-shaped stub carrying ``app.state``."""
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(**state)))


# ── GET /api/models ─────────────────────────────────────────────────────────


async def test_models_list_all_keeps_the_loop_running(slow_flm_list: _SlowFlmList) -> None:
    """The issue's reproduction: a heartbeat keeps ticking during the probe."""
    from hal0.services import models_service

    registry = types.SimpleNamespace(list=lambda: [])
    upstreams = types.SimpleNamespace(list=lambda: [])

    out, ticks = await _heartbeat_ticks(
        models_service.list_all(registry=registry, upstreams=upstreams, cache={}, update_state=None)
    )

    slow_flm_list.assert_ran_off_loop()
    # ~_PROBE_BLOCK_S / _HEARTBEAT_S ticks when the loop is free; 0-1 if blocked.
    assert ticks >= 5, f"event loop stalled during `flm list` (ticks={ticks})"
    assert "qwen3-0.6b-FLM" in {m["id"] for m in out["models"]}


# ── capabilities ────────────────────────────────────────────────────────────


def _orch(tmp_path: Path) -> CapabilityOrchestrator:
    """Orchestrator over an empty capabilities.toml and no slots."""
    caps = tmp_path / "capabilities.toml"
    caps.write_text("", encoding="utf-8")
    return CapabilityOrchestrator(slot_manager=_NoSlots(), config_path=caps)  # type: ignore[arg-type]


async def test_capabilities_get_state_probes_off_loop(
    slow_flm_list: _SlowFlmList, tmp_path: Path, tmp_hal0_home: str
) -> None:
    """GET /api/capabilities builds NPU picker rows from ``flm list``."""
    state = await _orch(tmp_path).get_state()

    slow_flm_list.assert_ran_off_loop()
    assert "embed-gemma:300m" in {row["id"] for row in state["catalogs"]["embed"]["embed"]}


async def test_capability_apply_validation_probes_off_loop(
    slow_flm_list: _SlowFlmList, tmp_path: Path, tmp_hal0_home: str
) -> None:
    """POST /api/capabilities/{slot}/{child} validates through the catalog."""
    with pytest.raises(Hal0Error):
        await _orch(tmp_path).apply(
            "embed", "embed", {"device": "npu", "provider": "flm", "model": "no-such:1b"}
        )

    slow_flm_list.assert_ran_off_loop()


# ── NPU occupancy + hardware live stats + capacity ──────────────────────────


async def test_npu_occupancy_probes_off_loop(
    slow_flm_list: _SlowFlmList, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/npu/occupancy reads FLM footprints for each FLM slot."""
    import hal0.api.routes.hardware as hw_mod
    import hal0.providers.npu_columns as npu_columns
    from hal0.api.routes import npu as npu_routes

    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(npu_columns, "cached_aie_columns", AsyncMock(return_value=None))
    monkeypatch.setattr(npu_routes, "slot_token_for", AsyncMock(return_value="npu"), raising=False)
    sm = types.SimpleNamespace(list=AsyncMock(return_value=[_flm_slot()]))

    body = await npu_routes.npu_occupancy(_request(slot_manager=sm))  # type: ignore[arg-type]

    slow_flm_list.assert_ran_off_loop()
    assert body["slots"][0]["gb"] == 1.2


async def test_hardware_npu_status_probes_off_loop(
    slow_flm_list: _SlowFlmList, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardware live stats size a live FLM slot from ``flm list``."""
    import hal0.api.routes.hardware as hw_mod

    info = types.SimpleNamespace(model_dump=lambda mode="python": {"npu": {"present": True}})
    monkeypatch.setattr(hw_mod, "load_hardware_info", lambda: info)
    sm = types.SimpleNamespace(list=AsyncMock(return_value=[_flm_slot()]))

    out = await hw_mod._npu_status(_request(slot_manager=sm, model_registry=None))  # type: ignore[arg-type]

    slow_flm_list.assert_ran_off_loop()
    assert out is not None
    assert out["model_mb"] > 0


async def test_capacity_build_per_slot_probes_off_loop(slow_flm_list: _SlowFlmList) -> None:
    """The per-slot memory map sizes FLM slots from ``flm list``."""
    from hal0.slots import capacity

    out = await capacity.build_per_slot([_flm_slot()], gpu_capable=False)

    slow_flm_list.assert_ran_off_loop()
    assert out["npu"]["mem_mb"] > 0


# ── slots routes ────────────────────────────────────────────────────────────


async def test_slots_flm_models_probes_off_loop(
    slow_flm_list: _SlowFlmList, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/slots/flm/models falls back to host ``flm list`` when cold."""
    from hal0.api.routes import slots as slots_routes
    from hal0.slots import flm_catalog as slots_flm_catalog

    monkeypatch.setattr(slots_flm_catalog, "_from_container", lambda: None)

    body = await slots_routes.list_flm_models(_request())  # type: ignore[arg-type]

    slow_flm_list.assert_ran_off_loop()
    assert {m["model"] for m in body["models"]} == {"embed-gemma:300m", "qwen3:0.6b"}


@pytest.mark.parametrize("handler", ["load_slot", "swap_slot"])
async def test_slot_load_and_swap_resolve_flm_ids_off_loop(
    slow_flm_list: _SlowFlmList, handler: str
) -> None:
    """Load/swap validate a ``-FLM`` id against the installed FLM catalog."""
    from hal0.api.routes import slots as slots_routes
    from hal0.registry.store import ModelNotFound

    request = _request(
        slot_manager=_NoSlots(),
        model_registry=types.SimpleNamespace(has=lambda _mid: False),
    )
    request.json = AsyncMock(return_value={"model_id": "ghost-1b-FLM"})

    with pytest.raises(ModelNotFound):
        await getattr(slots_routes, handler)("npu", request)

    slow_flm_list.assert_ran_off_loop()


# ── pulls ───────────────────────────────────────────────────────────────────


async def test_pull_enqueue_detects_flm_tags_off_loop(
    slow_flm_list: _SlowFlmList, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/models/{id}/pull routes FLM tags by asking ``flm list``."""
    from hal0.registry import pull_jobs

    start = AsyncMock(return_value={"id": "j1", "model_id": "qwen3:0.6b", "state": "queued"})
    monkeypatch.setattr(pull_jobs, "start_flm_pull", start)

    out = await pull_jobs.enqueue(_request(model_pull_jobs={}), model_id="qwen3:0.6b")  # type: ignore[arg-type]

    slow_flm_list.assert_ran_off_loop()
    assert out["id"] == "j1"
    start.assert_awaited_once()


@pytest.mark.parametrize("empty_at_start", [False, True])
async def test_run_flm_pull_probes_off_loop(
    slow_flm_list: _SlowFlmList,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    empty_at_start: bool,
) -> None:
    """``run_flm_pull`` reads the catalog at start, per tick and after the pull.

    With ``empty_at_start`` the install-path probe at pull start gets an empty
    ``flm list``, so the per-tick retry is what resolves it.
    """
    import sys

    from hal0.registry import pull as pull_mod
    from hal0.registry.pull import PullJob, run_flm_pull

    host_models_dir = str(tmp_path)
    fake_pull = (
        "import os, sys, time; os.makedirs(os.path.join(sys.argv[1], 'Qwen3-0.6B-NPU2'),"
        " exist_ok=True); time.sleep(1.2)"
    )
    monkeypatch.setattr(
        flm_mod,
        "flm_pull_command",
        lambda tag: ([sys.executable, "-c", fake_pull, host_models_dir], host_models_dir),
    )
    monkeypatch.setattr(flm_mod, "ensure_host_flm_store_link", lambda: host_models_dir)
    monkeypatch.setattr(flm_mod, "flm_host_async_spawn", lambda argv: (argv, {}))
    monkeypatch.setattr(pull_mod, "_register_flm_pulled", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "reset_flm_image_present_cache", lambda: None)

    slow_flm_list.empty_answers = 1 if empty_at_start else 0
    job = PullJob(job_id="j1", model_id="qwen3:0.6b")
    await run_flm_pull(job, tag="qwen3:0.6b", registry=object())

    assert job.state == "completed", job
    slow_flm_list.assert_ran_off_loop()
    assert job.path == str(tmp_path / "Qwen3-0.6B-NPU2")


# ── sync callers keep working ───────────────────────────────────────────────


def test_sync_callers_still_probe_inline(slow_flm_list: _SlowFlmList) -> None:
    """The CLI and other sync callers keep the plain synchronous API."""
    assert {m["tag"] for m in flm_mod.flm_served_models()} == {"embed-gemma:300m", "qwen3:0.6b"}
    assert slow_flm_list.on_loop == [False]
    assert threading.current_thread() is threading.main_thread()
