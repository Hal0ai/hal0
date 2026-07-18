"""T2 per-slot sampler -- background asyncio task, one tick per interval.

Reuses the existing, already-hardened readers rather than re-implementing
sensor access:

  * :func:`hal0.slots.capacity.build_per_slot` for per-slot VRAM/RAM
    attribution (the max-pool-against-UMA-underreport logic already lives
    there -- see that module's docstring).
  * :func:`hal0.hardware.gpu_view.sample` for the box-wide GPU memory /
    utilization / GTT reading.
  * :func:`hal0.api.routes.power._probe_power` for hwmon power/thermal.
  * :func:`hal0.api.routes.slots._scrape_llama_metrics` for per-slot
    ``inflight``/``kv_used`` (llama-server ``/metrics`` + ``/slots``).

On a shared-GPU (UMA, e.g. Strix Halo) box there is exactly one physical
GPU serving every slot, so GPU utilization/power/temp/GTT are not
per-slot attributable today -- those numbers are written once per tick as
a synthetic ``slot_id='__fleet__'`` row (see 002_metrics.sql's slot_sample
comment) rather than duplicated (or worse, guessed) onto every slot row.
Per-slot rows carry the per-slot-attributable fields only: state,
vram/ram bytes (from capacity), inflight/kv_used (from the llama scrape).

Missing-sensor discipline (plan §13.5 / #791): every field that has no
reading is written as SQL NULL, never a synthesized 0.

Slot lifecycle (``slot_event``): OBS-1 does not hook
``SlotManager.set_state`` directly (that lane is out of this build's
touch-list); instead the sampler diffs each slot's observed state against
its own previous-tick memory and writes a ``transition`` row when it
changes. This gives ``duration_ms=None`` (tick-granularity, not exact) --
documented in 002_metrics.sql; a follow-up wiring straight into
``SlotManager.set_state`` gives exact timing without changing this row
shape.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import structlog

from hal0.db.repository import now_iso
from hal0.hardware import gpu_view

if TYPE_CHECKING:
    from hal0.metrics.writer import MetricsWriter
    from hal0.slots.manager import SlotManager

log = structlog.get_logger("hal0.metrics.sampler")

_FLEET_SLOT_ID = "__fleet__"
_SLOT_SAMPLE_TABLE = "slot_sample"
_SLOT_EVENT_TABLE = "slot_event"


async def _scrape_llama(port: int) -> dict[str, Any]:
    """Reuse the existing per-slot llama-server scrape (best-effort, degrades to {})."""
    if port <= 0:
        return {}
    try:
        from hal0.api.routes.slots import _scrape_llama_metrics

        return await _scrape_llama_metrics(port)
    except Exception:  # pragma: no cover -- defensive, sampler must never crash
        return {}


def _mb_to_bytes(mb: float | int | None) -> int | None:
    if mb is None:
        return None
    try:
        return int(float(mb) * 1024 * 1024)
    except (TypeError, ValueError):
        return None


async def _probe_power_snapshot() -> dict[str, float | None]:
    try:
        from hal0.api.routes.power import _probe_power

        return await asyncio.to_thread(_probe_power)
    except Exception:  # pragma: no cover -- defensive
        return {"gpu_power_w": None, "gpu_temp_c": None}


class SlotSampler:
    """One background task; one tick = one ``slot_sample``/``slot_event`` write set."""

    def __init__(
        self,
        *,
        slot_manager: SlotManager,
        writer: MetricsWriter,
        interval_s: float = 5.0,
        registry: Any | None = None,
    ) -> None:
        self._slot_manager = slot_manager
        self._writer = writer
        self._interval_s = max(0.5, interval_s)
        self._registry = registry
        self._task: asyncio.Task[None] | None = None
        self._last_state: dict[str, str] = {}

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="metrics-slot-sampler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # pragma: no cover -- defensive
                log.warning(
                    "metrics.sampler_tick_failed", error=str(exc), error_type=type(exc).__name__
                )
            await asyncio.sleep(self._interval_s)

    async def tick(self) -> None:
        """Run exactly one sample cycle. Exposed directly for tests."""
        try:
            slots = await self._slot_manager.list()
        except Exception:
            slots = []

        ts = now_iso()

        # Fleet-wide GPU + power/thermal reading -- once per tick, never
        # per-slot (see module docstring).
        try:
            gpu_sample = await asyncio.to_thread(gpu_view.sample)
        except Exception:  # pragma: no cover -- defensive
            gpu_sample = None
        power = await _probe_power_snapshot()

        fleet_row = {
            "ts": ts,
            "slot_id": _FLEET_SLOT_ID,
            "state": "n/a",
            "vram_bytes": _mb_to_bytes(gpu_sample.vram_used_mb) if gpu_sample else None,
            "gtt_bytes": _mb_to_bytes(gpu_sample.gtt_used_mb) if gpu_sample else None,
            "ram_bytes": None,
            "gpu_util": gpu_sample.gpu_busy if gpu_sample else None,
            "npu_util": None,
            "power_w": power.get("gpu_power_w"),
            "temp_c": power.get("gpu_temp_c"),
            "inflight": None,
            "kv_used": None,
        }
        self._writer.enqueue(_SLOT_SAMPLE_TABLE, fleet_row)

        if not slots:
            return

        try:
            from hal0.slots.capacity import build_per_slot

            per_slot_mem = await build_per_slot(slots, registry=self._registry)
        except Exception:  # pragma: no cover -- defensive
            per_slot_mem = {}

        for slot in slots:
            mem = per_slot_mem.get(slot.name, {})
            llm = await _scrape_llama(getattr(slot, "port", 0) or 0)
            state_value = getattr(slot.state, "value", str(slot.state))

            row = {
                "ts": ts,
                "slot_id": slot.name,
                "state": state_value,
                "vram_bytes": _mb_to_bytes(mem.get("vram_mb")),
                "gtt_bytes": None,
                "ram_bytes": _mb_to_bytes(mem.get("ram_mb")),
                "gpu_util": None,
                "npu_util": None,
                "power_w": None,
                "temp_c": None,
                "inflight": (
                    int(llm["requests_processing"]) if "requests_processing" in llm else None
                ),
                "kv_used": (
                    int(llm["kv_cache_usage"] * 1_000_000) if "kv_cache_usage" in llm else None
                ),
            }
            self._writer.enqueue(_SLOT_SAMPLE_TABLE, row)

            prev = self._last_state.get(slot.name)
            if prev is not None and prev != state_value:
                self._writer.enqueue(
                    _SLOT_EVENT_TABLE,
                    {
                        "ts": ts,
                        "slot_id": slot.name,
                        "event": "transition",
                        "from_state": prev,
                        "to_state": state_value,
                        "duration_ms": None,
                        "reason": "sampler-observed transition (tick-granularity)",
                    },
                )
            self._last_state[slot.name] = state_value


__all__ = ["SlotSampler"]
