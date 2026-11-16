"""#3502: a load refused by the pre-load admission gate is re-admitted when
capacity frees, and a request that lands on the refused slot says so.

Before the fix, ``PreloadEvictionFailed`` stamped the slot ERROR, the
crash-loop breaker half-opened, and nothing ever rode it: no supervisor
retried an ERROR slot that never reached WARMING, and capability routing's
rule-9 default answered a bare ``404 dispatch.no_route`` naming the caller's
model. The anchor stayed dead for 11 hours on a real box.

ADR-0063 records the behaviour: the refusal is stamped
``error_reason = "admission_refused"``; the reaper's periodic tick
(:meth:`SlotManager.readmit_refused_once`) retries it only once the breaker
permits a trial AND a dry run of the admission plan says the model would
fit (so a still-short box never pays an eviction for a load that would be
refused again).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hal0.dispatcher.router import Dispatcher, NoRouteFound
from hal0.slots import reaper
from hal0.slots.arbiter import GpuMode
from hal0.slots.manager import SlotManager
from hal0.slots.preload_evict import ADMISSION_REFUSED, PreloadEvictionFailed
from hal0.slots.state import SlotState
from hal0.upstreams.registry import UpstreamRegistry
from tests.slots.conftest import FakeContainerProvider


@pytest.fixture(autouse=True)
def _reset_lru_deprecation_state() -> None:
    reaper._lru_flag_warned.clear()
    yield
    reaper._lru_flag_warned.clear()


def _write_slot(
    root: Path,
    name: str,
    *,
    port: int,
    pinned: bool | None = None,
    autoload: bool | None = None,
) -> None:
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'backend = "vulkan"',
        'provider = "llama-server"',
    ]
    if pinned is not None:
        lines.append(f"pinned = {'true' if pinned else 'false'}")
    if autoload is not None:
        lines.append(f"autoload = {'true' if autoload else 'false'}")
    lines += ["[model]", 'default = "qwen3-4b-q4_k_m"', ""]
    (root / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


def _stub_model_info(size_mb: float) -> Any:
    async def _info(model_id: str | None, *, provider: str | None = None) -> dict[str, Any]:
        return {"size_bytes": int(size_mb * 1024 * 1024)}

    return _info


class _Free:
    """A mutable host-free probe, so a test can 'free capacity' mid-run."""

    def __init__(self, mb: float) -> None:
        self.mb = mb

    def __call__(self) -> float:
        return self.mb


async def _sweep(sm: SlotManager) -> tuple[str, ...]:
    """One reaper tick's re-admission, run to completion.

    ``readmit_refused_once`` only schedules the per-slot retries; this awaits
    them and returns the names that were actually loaded.
    """
    scheduled = await sm.readmit_refused_once()
    tasks = {name: sm._readmit_tasks[sm._key(name)] for name in scheduled}
    done = []
    for name, task in tasks.items():
        if await task:
            done.append(name)
    return tuple(done)


def _age_breaker(sm: SlotManager, name: str, by_s: float = 3600.0) -> None:
    key = sm._key(name)
    failures, last = sm._load_failures[key]
    sm._load_failures[key] = (failures, last - by_s)


async def _refuse(
    slot_root: Path, monkeypatch: pytest.MonkeyPatch, free: _Free, *, pinned_resident: bool = True
) -> SlotManager:
    """Drive the ``agent`` slot into an admission refusal; return the manager."""
    _write_slot(slot_root, "code", port=8085, pinned=pinned_resident)
    _write_slot(slot_root, "agent", port=8082)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("code")

    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(4000.0))
    monkeypatch.setattr(sm, "_probe_host_free_mb", free)
    with pytest.raises(PreloadEvictionFailed):
        await sm.load("agent")
    return sm


async def test_admission_refusal_is_stamped_on_the_error_record(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sm = await _refuse(slot_root, monkeypatch, _Free(100.0))

    slot = await sm.status("agent")
    assert slot.state == SlotState.ERROR
    assert slot.metadata.get("error_reason") == ADMISSION_REFUSED
    assert "cannot free enough memory" in str(slot.metadata.get("message"))


async def test_refused_slot_is_not_retried_while_capacity_is_still_short(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trial that would be refused again is never started: no load attempt,
    no breaker count, no eviction of a resident slot."""
    sm = await _refuse(slot_root, monkeypatch, _Free(100.0))
    _age_breaker(sm, "agent")  # breaker half-open: a trial would be permitted
    failures_before = sm._load_failures[sm._key("agent")][0]
    loads_before = len(container_stub.load_calls)

    readmitted = await _sweep(sm)

    assert readmitted == ()
    assert (await sm.status("agent")).state == SlotState.ERROR
    assert sm._load_failures[sm._key("agent")][0] == failures_before
    assert len(container_stub.load_calls) == loads_before
    assert not container_stub.unload_calls


async def test_refused_slot_is_readmitted_once_capacity_frees(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")

    free.mb = 8000.0  # operator unpinned/unloaded something; the model fits now
    readmitted = await _sweep(sm)

    assert readmitted == ("agent",)
    slot = await sm.status("agent")
    assert slot.state in (SlotState.READY, SlotState.IDLE)
    assert "error_reason" not in slot.metadata
    assert sm._key("agent") not in sm._load_failures


async def test_readmission_counts_evictable_idle_slots_as_capacity(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capacity that the admission gate itself can free (an idle, non-pinned
    resident slot) counts: the dry run mirrors the real plan."""
    free = _Free(100.0)
    # code is serving at the refusal, so it is not an eviction candidate.
    _write_slot(slot_root, "code", port=8085)
    _write_slot(slot_root, "agent", port=8082)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("code")
    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(4000.0))
    monkeypatch.setattr(sm, "_probe_host_free_mb", free)
    sm._serving_count[sm._key("code")] = 1
    with pytest.raises(PreloadEvictionFailed):
        await sm.load("agent")
    _age_breaker(sm, "agent")

    # Still serving → still not a candidate → no retry.
    assert await _sweep(sm) == ()

    # code goes quiet; its 6000 MiB footprint is now freeable by admission.
    sm._serving_count[sm._key("code")] = 0
    sm._last_used[sm._key("code")] = 0.0

    async def _per_slot(slots: Any, registry: Any = None) -> dict[str, dict[str, float]]:
        return {"code": {"mem_mb": 6000.0}}

    monkeypatch.setattr("hal0.slots.capacity.build_per_slot", _per_slot)

    def _evicted_frees() -> float:
        return 100.0 if sm.state("code") in (SlotState.READY, SlotState.IDLE) else 6100.0

    monkeypatch.setattr(sm, "_probe_host_free_mb", _evicted_frees)

    assert await _sweep(sm) == ("agent",)
    assert (await sm.status("agent")).state in (SlotState.READY, SlotState.IDLE)


async def test_readmission_respects_the_breaker_backoff(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    # Not aged: the i4 backoff still holds the slot.
    free.mb = 8000.0

    assert await _sweep(sm) == ()
    assert (await sm.status("agent")).state == SlotState.ERROR


async def test_other_load_failures_are_not_readmitted(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an admission refusal rides capacity. A broken spawn keeps the
    existing contract (operator or request-driven retry)."""
    _write_slot(slot_root, "agent", port=8082)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    container_stub.fail_load = RuntimeError("boom")
    with pytest.raises(Exception, match="boom"):
        await sm.load("agent")
    container_stub.fail_load = None
    assert sm.state("agent") == SlotState.ERROR
    _age_breaker(sm, "agent")
    monkeypatch.setattr(sm, "_probe_host_free_mb", _Free(1e9))

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.ERROR


async def test_operator_unload_retires_the_refusal(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator who unloads the refused slot meant it: no re-admission."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    await sm.unload("agent")
    if sm._key("agent") in sm._load_failures:
        _age_breaker(sm, "agent")
    free.mb = 8000.0

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.OFFLINE


async def test_reaper_tick_drives_readmission(monkeypatch: pytest.MonkeyPatch) -> None:
    """The idle-monitor loop is the supervisor that rides the capacity change."""
    calls: list[str] = []

    class _Host:
        _idle_monitor_interval_s = 0.0

    rp = reaper.SlotReaper(_Host())  # type: ignore[arg-type]

    async def _noop() -> None:
        return None

    async def _readmit() -> tuple[str, ...]:
        calls.append("readmit")
        return ()

    monkeypatch.setattr(rp, "sweep_idle_once", _noop)
    monkeypatch.setattr(rp, "pressure_evict_once", _noop)
    _Host.readmit_refused_once = staticmethod(_readmit)  # type: ignore[attr-defined]

    task = asyncio.create_task(rp._loop())
    for _ in range(20):
        await asyncio.sleep(0)
        if calls:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls, "the reaper tick must call readmit_refused_once"


# ── review findings on #3735 ─────────────────────────────────────────────────


def _set_gpu_mode(sm: SlotManager, mode: str, *, pinned: bool = False) -> None:
    st = sm.arbiter._load_state()
    st["mode"] = mode
    st["pinned"] = pinned
    sm.arbiter._persist()


@pytest.mark.parametrize("pinned", [False, True])
async def test_readmission_never_loads_an_llm_slot_in_image_mode(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    pinned: bool,
) -> None:
    """Finding 1: the arbiter's exclusive image mode holds the GPU, and the
    sweep is not allowed to put an llm-group slot back on it, even when the
    image-mode unloads made the dry run see enough free memory."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    _set_gpu_mode(sm, "img", pinned=pinned)
    free.mb = 8000.0  # the image-mode switch unloaded the LLM set
    loads_before = len(container_stub.load_calls)

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.ERROR
    assert len(container_stub.load_calls) == loads_before

    # Back in LLM mode the same refusal is re-admitted.
    _set_gpu_mode(sm, "llm")
    assert await _sweep(sm) == ("agent",)
    assert sm.state("agent") in (SlotState.READY, SlotState.IDLE)


async def test_readmission_waits_out_a_running_gpu_switch(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1: mid-switch the target mode is not settled; no GPU load."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    free.mb = 8000.0
    async with sm.arbiter._switch_lock:
        assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.ERROR
    assert await _sweep(sm) == ("agent",)


async def test_a_slow_readmit_load_does_not_stall_the_reaper_sweeps(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 2: the tick schedules the retry and returns; the idle and
    pressure sweeps keep running while the load comes up, and a second tick
    does not start a second load of the same slot."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    free.mb = 8000.0

    release = asyncio.Event()
    real_load = sm._load
    load_starts: list[str] = []

    async def _slow_load(name: str, *args: Any, **kwargs: Any) -> Any:
        load_starts.append(name)
        await release.wait()
        return await real_load(name, *args, **kwargs)

    monkeypatch.setattr(sm, "_load", _slow_load)

    sweeps: list[int] = []

    async def _count_sweep() -> None:
        sweeps.append(1)

    async def _noop() -> None:
        return None

    rp = sm._reaper
    monkeypatch.setattr(rp, "sweep_idle_once", _count_sweep)
    monkeypatch.setattr(rp, "pressure_evict_once", _noop)
    sm._idle_monitor_interval_s = 0.0

    # Bounded, so a regression that awaits the load inline fails, not hangs.
    async with asyncio.timeout(10):
        loop_task = asyncio.create_task(rp._loop())
        try:
            for _ in range(200):
                await asyncio.sleep(0)
                if len(sweeps) >= 5 and load_starts:
                    break
            assert load_starts == ["agent"], "one retry, started off the reaper task"
            assert len(sweeps) >= 5, "the sweeps must keep ticking while the load is pending"
            assert not release.is_set()
            release.set()
            task = sm._readmit_tasks.get(sm._key("agent"))
            if task is not None:
                await task
        finally:
            loop_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop_task
    assert load_starts == ["agent"]
    assert sm.state("agent") in (SlotState.READY, SlotState.IDLE)


async def test_stopping_the_idle_monitor_cancels_a_pending_readmission(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    free.mb = 8000.0
    never = asyncio.Event()

    async def _stuck_probe(*_a: Any, **_k: Any) -> Any:
        await never.wait()
        return None

    monkeypatch.setattr("hal0.slots.manager._plan_admission", _stuck_probe)
    assert await sm.readmit_refused_once() == ("agent",)
    task = sm._readmit_tasks[sm._key("agent")]
    await asyncio.sleep(0)
    await sm.stop_idle_monitor()
    assert task.cancelled()
    assert not sm._readmit_tasks


async def test_an_unload_during_the_probe_cancels_the_readmission(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 3: the operator unloads the slot while the dry run awaits."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    free.mb = 8000.0
    loads_before = len(container_stub.load_calls)

    async def _probe_then_unload(*_a: Any, **_k: Any) -> Any:
        await sm.unload("agent")
        return None  # the gate would fail open: "fits"

    monkeypatch.setattr("hal0.slots.manager._plan_admission", _probe_then_unload)

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.OFFLINE
    assert len(container_stub.load_calls) == loads_before


async def test_an_unload_before_the_load_takes_its_lock_cancels_the_readmission(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 3: the last window. ``_load`` awaits the NPU admission before it
    takes the slot lock; an unload that lands there must still win, so the
    re-check has to run under the lock, not before ``load()``."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    free.mb = 8000.0
    loads_before = len(container_stub.load_calls)
    real_admit = sm._admit_npu_for_load

    async def _admit_then_unload(slot_name: str, *args: Any, **kwargs: Any) -> Any:
        await sm.unload("agent")
        return await real_admit(slot_name, *args, **kwargs)

    monkeypatch.setattr(sm, "_admit_npu_for_load", _admit_then_unload)

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.OFFLINE
    assert len(container_stub.load_calls) == loads_before


async def test_the_probe_sizes_the_model_the_load_would_admit(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 4: a stale configured id with no size metadata would fail the
    dry run open; the load then substitutes a model with a known footprint
    that still doesn't fit, and the real gate evicts and refuses again. The
    probe must size the substitute, exactly as ``_load`` does."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")

    monkeypatch.setattr(sm, "_resolve_servable_model", lambda _mid, _cfg: "substitute-24b")

    async def _info(model_id: str | None, *, provider: str | None = None) -> dict[str, Any]:
        if model_id == "substitute-24b":
            return {"size_bytes": 24000 * 1024 * 1024}
        return {}  # the stale configured id: no size metadata

    monkeypatch.setattr(sm, "_resolve_model_info", _info)
    key = sm._key("agent")
    failures_before = sm._load_failures[key][0]
    loads_before = len(container_stub.load_calls)

    assert await _sweep(sm) == ()
    assert sm.state("agent") == SlotState.ERROR
    assert sm._load_failures[key][0] == failures_before, "no refused trial counted"
    assert len(container_stub.load_calls) == loads_before


# ── re-review findings on #3735 ──────────────────────────────────────────────


async def test_a_switch_into_image_mode_waits_for_a_readmission_already_past_its_check(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: once the under-lock check passes, ``_load`` sits in OFFLINE with the
    slot lock held (ERROR teardown, ``_check_load``, the admission evictions)
    before it reaches STARTING. A switch into image mode that starts in that
    window must wait for the load and unload it, not leave an LLM on the GPU
    under ``mode=img``."""
    (slot_root / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                "port = 8188",
                'provider = "comfyui"',
                "[model]",
                'default = "sdxl-turbo"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    container_stub.active.add("img")
    await sm._transition("img", SlotState.READY, force=True)
    _age_breaker(sm, "agent")
    free.mb = 8000.0

    entered = asyncio.Event()
    release = asyncio.Event()
    real_check = sm._check_load

    async def _blocked_check(cfg: Any, *args: Any, **kwargs: Any) -> None:
        if str(cfg.get("name") if isinstance(cfg, dict) else "") == "agent":
            entered.set()
            await release.wait()
        await real_check(cfg, *args, **kwargs)

    monkeypatch.setattr(sm, "_check_load", _blocked_check)

    async with asyncio.timeout(10):
        assert await sm.readmit_refused_once() == ("agent",)
        readmit = sm._readmit_tasks[sm._key("agent")]
        await entered.wait()
        assert sm.state("agent") == SlotState.OFFLINE
        assert sm.is_busy("agent")

        switch = asyncio.create_task(sm.arbiter.ensure_img())
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await switch
        await readmit

    assert sm.arbiter.mode is GpuMode.IMG
    assert sm.state("agent") not in (SlotState.READY, SlotState.IDLE, SlotState.SERVING)


async def test_a_switch_does_not_save_a_busy_slot_that_ends_off_the_gpu(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1 follow-through: a slot taken into the switch only because its lock was
    held, whose load then fails, is not kept in the saved set, so the restore
    does not load what never came up."""
    (slot_root / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                "port = 8188",
                'provider = "comfyui"',
                "[model]",
                'default = "sdxl-turbo"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    container_stub.active.add("img")
    await sm._transition("img", SlotState.READY, force=True)
    _age_breaker(sm, "agent")
    free.mb = 8000.0

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _failing_check(cfg: Any, *args: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()
        raise RuntimeError("runtime refused the load")

    monkeypatch.setattr(sm, "_check_load", _failing_check)

    async with asyncio.timeout(10):
        await sm.readmit_refused_once()
        readmit = sm._readmit_tasks[sm._key("agent")]
        await entered.wait()
        switch = asyncio.create_task(sm.arbiter.ensure_img())
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await switch
        assert await readmit is False

    assert sm.arbiter.mode is GpuMode.IMG
    assert "agent" not in sm.arbiter.saved_llm_slots


async def test_a_lazily_loaded_slot_is_not_readmitted_in_the_background(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N1: only slots meant to be up without a request (the type's default,
    pinned, autoload) are re-admitted. A plain on-demand slot waits for the
    next request that names it."""
    _write_slot(slot_root, "code", port=8085, pinned=True)
    _write_slot(slot_root, "chat", port=8083, autoload=False)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("code")
    free = _Free(100.0)
    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(4000.0))
    monkeypatch.setattr(sm, "_probe_host_free_mb", free)
    with pytest.raises(PreloadEvictionFailed):
        await sm.load("chat")
    _age_breaker(sm, "chat")
    free.mb = 8000.0
    loads_before = len(container_stub.load_calls)

    assert await _sweep(sm) == ()
    assert sm.state("chat") == SlotState.ERROR
    assert len(container_stub.load_calls) == loads_before

    # The same slot with autoload on is re-admitted.
    _write_slot(slot_root, "chat", port=8083, autoload=True)
    sm._cfg_cache.pop(sm._key("chat"), None)
    assert await _sweep(sm) == ("chat",)


async def test_a_plan_the_real_gate_disagrees_with_is_logged(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N2: the dry run said it fits, the real gate refused; say by how much."""
    free = _Free(100.0)
    sm = await _refuse(slot_root, monkeypatch, free)
    _age_breaker(sm, "agent")
    from hal0.slots.preload_evict import PreloadEvictionPlan

    async def _optimistic(*_a: Any, **_k: Any) -> PreloadEvictionPlan:
        return PreloadEvictionPlan(
            needed_mb=4000.0,
            headroom_mb=0.0,
            initial_free_mb=100.0,
            projected_free_mb=4100.0,
            selected=(),
            fits=True,
        )

    monkeypatch.setattr("hal0.slots.manager._plan_admission", _optimistic)
    with caplog.at_level("WARNING", logger="hal0.slots.manager"):
        assert await _sweep(sm) == ()
    rec = next(
        r for r in caplog.records if r.getMessage() == "slot.admission_readmit_plan_mismatch"
    )
    assert rec.planned_free_mb == 4100.0
    assert rec.actual_free_mb == 100.0
    assert rec.shortfall_mb > 3800.0
    assert rec.actual_needed_mb is not None


# ── the 404 names the broken slot ────────────────────────────────────────────


def _request(path: str) -> Any:
    from types import SimpleNamespace

    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


async def test_no_route_on_a_refused_anchor_names_the_slot_and_its_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sm = await _refuse(slot_root, monkeypatch, _Free(100.0))
    dispatcher = Dispatcher(
        upstream_registry=UpstreamRegistry(),
        model_registry=None,
        cached_models=lambda _name: [],
        slot_manager=sm,
    )

    with pytest.raises(NoRouteFound) as excinfo:
        await dispatcher.dispatch(
            _request("/v1/chat/completions"),
            body={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
        )

    err = excinfo.value
    assert err.code == "dispatch.no_route"
    assert err.details["slot"] == "agent"
    assert err.details["state"] == SlotState.ERROR.value
    assert "cannot free enough memory" in err.details["load_error"]
    assert err.details["error_reason"] == ADMISSION_REFUSED
    assert "agent" in err.message and "error" in err.message
    # The original evidence stays for existing readers.
    assert err.details["model"] == "claude-sonnet-5"
    assert "legacy_error" in err.details


async def test_no_route_on_an_offline_anchor_is_unchanged(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    _write_slot(slot_root, "agent", port=8082)
    sm = SlotManager()
    dispatcher = Dispatcher(
        upstream_registry=UpstreamRegistry(),
        model_registry=None,
        cached_models=lambda _name: [],
        slot_manager=sm,
    )
    with pytest.raises(NoRouteFound) as excinfo:
        await dispatcher.dispatch(
            _request("/v1/chat/completions"),
            body={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert "load_error" not in excinfo.value.details
    assert excinfo.value.message.startswith("model 'claude-sonnet-5' not found in registry")
