"""Idle demotion + TTL/pressure eviction loops (P3-slots §1b).

``SlotReaper`` owns the background sweeper that:
  - demotes a READY slot to IDLE after ``idle_after_s`` of inactivity
    (soft, dashboard-facing only), and
  - hard-evicts (unloads) a slot past its resolved idle TTL (#902) or when
    host memory pressure crosses a floor (#903), skipping pinned anchors
    and in-flight slots.

§21.10 fold (multi-model memory manager gap analysis) landed here:

  - **GTT-aware pressure probe.** The pre-existing probe read raw
    ``/proc/meminfo MemAvailable``, which is blind to amdgpu GTT
    allocations backing resident model weights on UMA hardware (Strix
    Halo) — the "pve GTT hidden memory" blind spot: a chunk of RAM held by
    the GPU driver for a loaded model's weights never shows up in ordinary
    ``free``/``ps`` accounting, so the raw-meminfo probe can report
    "plenty of free RAM" while the box is actually memory-critical.
    :func:`probe_host_free_mb` now prefers the amdgpu DRM sysfs GTT
    counters (``mem_info_gtt_used``/``mem_info_gtt_total`` via
    :func:`hal0.hardware.gpu_view.sample` — the same UMA-aware primitive
    :class:`hal0.slots.capacity.CapacitySnapshot` names as its VRAM/GTT
    source) and only falls back to ``capacity._read_meminfo`` when no UMA
    GTT pool is detected (discrete GPU, or the sysfs counters aren't
    readable) — preserving the historical signal on non-UMA hosts.
  - **threshold_pct.** The eviction floor can now be expressed as a
    percentage of total capacity (``evict_pressure_pct``) instead of only
    an absolute MiB value — purely additive: when unset, behavior is
    identical to before (``evict_pressure_mb`` absolute floor).
  - **Operator pin.** :func:`is_pinned` overlays the new
    ``SlotConfig.pinned`` field onto the pre-existing
    ``_PINNED_BY_DEFAULT`` frozenset so an operator can pin ANY slot, not
    only the three built-in anchors. Both the idle-TTL and pressure-evict
    paths consult it. The companion manual-unload/delete guard (HTTP 409
    ``slot.pinned`` unless ``force=true``) lives on
    :class:`hal0.slots.manager.SlotManager` (``is_pinned`` + the
    ``delete``/``unload`` route guards) since it gates the API surface,
    not the background loop.
  - **Per-modality budget** (``max_loaded_models`` per slot type,
    LRU-evict the oldest over budget) — evaluated and DEFERRED: it needs a
    new config surface (a per-type cap alongside the existing global
    ``[slots].max_slots``) that doesn't yet exist, and the plan flags it
    explicitly as SHOULD/low, "don't block the reaper extraction on it".
    Left as a follow-up.

``SlotManager.__init__`` constructs ``self._reaper = SlotReaper(self)``.
Public ``start_idle_monitor``/``stop_idle_monitor`` are thin delegators
(called from the API lifespan at ``api/__init__.py``) — signatures
unchanged, ``evict_pressure_pct`` added as a new optional kwarg.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from hal0.slots.state import IllegalSlotTransition, SlotConfigError, SlotNotFound, SlotState

if TYPE_CHECKING:
    from hal0.slots.manager import Slot
    from hal0.slots.state import SlotStateRecord

log = logging.getLogger(__name__)

# Idle-monitor defaults. A READY slot whose last activity is older than
# _IDLE_AFTER_S gets demoted to IDLE so dashboards / unload heuristics
# can distinguish "warm but quiet" from "warm and serving".
_IDLE_AFTER_S: float = 300.0
_IDLE_MONITOR_INTERVAL_S: float = 30.0
# Hard-eviction default TTL (#902). A slot idle past this long (resolved
# per-slot: TOML idle_timeout_s overrides, then this global default) is
# *unloaded* — freeing host RAM — not merely relabeled IDLE. 0 disables
# eviction; per-slot idle_timeout_s = 0 pins that slot.
_EVICT_AFTER_S: float = 300.0

# Anchor slots pinned against TTL/pressure eviction *under default config*
# — i.e. when their TOML carries no explicit idle_timeout_s / pinned
# override. Evicting these would defeat always-warm chat, the agent loop,
# and the NPU trio anchor. An explicit per-slot idle_timeout_s in TOML
# still wins for the idle-TTL path (lets an operator opt a named anchor
# back into TTL eviction); the operator-pin field (below) is the
# unconditional override for both TTL and pressure eviction.
_PINNED_BY_DEFAULT: frozenset[str] = frozenset({"agent", "utility", "npu"})


def is_pinned(canonical_name: str, cfg: dict[str, Any] | None) -> bool:
    """True when *canonical_name* is exempt from automatic eviction.

    §21.10 operator pin: an *explicit* ``SlotConfig.pinned`` key in the
    raw TOML dict always wins — ``true`` pins any slot, ``false`` un-pins
    a default anchor (#1367). Only when the key is absent (or the config
    is missing/unreadable) does the default-pinned anchor set (``agent``/
    ``utility``/``npu``) apply. ``cfg`` is the raw TOML dict from
    ``_load_slot_config`` — key presence distinguishes "authored false"
    from "unset". Used by both the idle-TTL and pressure-eviction paths,
    and by :meth:`hal0.slots.manager.SlotManager.is_pinned` (the manual
    unload/delete 409 guard).
    """
    if cfg is not None and cfg.get("pinned") is not None:
        return bool(cfg["pinned"])
    return canonical_name in _PINNED_BY_DEFAULT


def probe_host_free_mb() -> float:
    """Return free host memory in MiB, GTT-aware where possible (§21.10).

    Prefers the amdgpu DRM sysfs GTT counters (UMA hardware, e.g. Strix
    Halo) over raw ``/proc/meminfo MemAvailable`` — the latter is blind to
    GTT allocations backing resident model weights, so it can report
    "plenty of free RAM" while the box is actually memory-critical. Falls
    back to ``/proc/meminfo`` when no UMA GTT pool is detected (discrete
    GPU, or the sysfs counters are unreadable), preserving the historical
    signal there. Returns ``inf`` on total probe failure so the pressure
    guard is fail-SAFE: an unreadable probe reports "plenty of free RAM",
    skipping eviction rather than evicting blindly on a bad reading.
    """
    try:
        from hal0.hardware.gpu_view import sample as gpu_sample

        gpu = gpu_sample()
        if gpu.is_uma and gpu.gtt_total_mb is not None and gpu.gtt_used_mb is not None:
            return max(gpu.gtt_total_mb - gpu.gtt_used_mb, 0.0)
    except Exception as exc:
        log.warning("slot.pressure_probe_gtt_failed", extra={"error": str(exc)})
    try:
        from hal0.slots.capacity import _read_meminfo

        _total_mib, avail_mib = _read_meminfo()
        return avail_mib
    except Exception as exc:
        log.warning("slot.pressure_probe_failed", extra={"error": str(exc)})
        return float("inf")


def probe_host_total_mb() -> float:
    """Return total host memory in MiB, from the same GTT-aware source.

    Only consulted when ``evict_pressure_pct`` is configured (§21.10
    threshold_pct). Returns ``0.0`` on total probe failure — callers treat
    that as "can't compute a percentage floor" and fall back to the
    absolute ``evict_pressure_mb`` floor.
    """
    try:
        from hal0.hardware.gpu_view import sample as gpu_sample

        gpu = gpu_sample()
        if gpu.is_uma and gpu.gtt_total_mb is not None:
            return gpu.gtt_total_mb
    except Exception as exc:
        log.warning("slot.pressure_probe_gtt_total_failed", extra={"error": str(exc)})
    try:
        from hal0.slots.capacity import _read_meminfo

        total_mib, _avail_mib = _read_meminfo()
        return total_mib
    except Exception as exc:
        log.warning("slot.pressure_probe_total_failed", extra={"error": str(exc)})
        return 0.0


class ReaperHost(Protocol):
    """Narrow seam :class:`SlotReaper` needs from ``SlotManager``.

    Deviation from a "pure" narrow protocol: ``_probe_host_free_mb`` /
    ``_probe_host_total_mb`` are included (and always called via
    ``self._host.X()``, never computed inline in the reaper) so that
    ``tests/slots/test_pressure_eviction.py``'s
    ``monkeypatch.setattr(sm, "_probe_host_free_mb", ...)`` pattern — which
    patches the *manager instance* — keeps working unchanged.
    """

    _last_used: dict[int, float]
    _states: dict[int, SlotStateRecord]
    _serving_count: dict[int, int]
    _idle_after_s: float
    _evict_after_s: float
    _evict_pressure_mb: float
    _evict_pressure_pct: float | None
    _idle_monitor_interval_s: float

    def _current_state(self, name: str) -> SlotState: ...
    def _resolve_alias(self, name: str) -> str: ...
    def _key(self, name: str) -> int: ...
    def _name_for_key(self, key: int) -> str: ...
    def _probe_host_free_mb(self) -> float: ...
    def _probe_host_total_mb(self) -> float: ...
    async def _load_slot_config(self, name: str) -> dict[str, Any]: ...
    async def _transition(self, name: str, to_state: SlotState, **kw: Any) -> SlotStateRecord: ...
    async def unload(self, name: str) -> Slot: ...


class SlotReaper:
    """Idle demotion + TTL/pressure eviction, against a narrow host seam."""

    def __init__(self, host: ReaperHost) -> None:
        self._host = host
        self._task: asyncio.Task[None] | None = None

    async def start(
        self,
        *,
        idle_after_s: float | None = None,
        evict_after_s: float | None = None,
        evict_pressure_mb: float | None = None,
        evict_pressure_pct: float | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Start the background sweeper. Idempotent while the task is alive."""
        host = self._host
        if idle_after_s is not None:
            host._idle_after_s = idle_after_s
        if evict_after_s is not None:
            host._evict_after_s = evict_after_s
        if evict_pressure_mb is not None:
            host._evict_pressure_mb = float(evict_pressure_mb)
        if evict_pressure_pct is not None:
            host._evict_pressure_pct = float(evict_pressure_pct)
        if interval_s is not None:
            host._idle_monitor_interval_s = interval_s
        existing = self._task
        if existing is not None and not existing.done():
            return
        try:
            self._task = asyncio.create_task(self._loop(), name="hal0-slot-idle-monitor")
        except RuntimeError:
            # No running loop (sync-context test). Defer until callers are
            # in an async context.
            log.debug("slot.idle_monitor_no_loop")

    async def stop(self) -> None:
        """Cancel the idle-monitor task if running. Idempotent."""
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _loop(self) -> None:
        """Periodically sweep READY slots for idle-timeout and pressure."""
        host = self._host
        try:
            while True:
                await asyncio.sleep(host._idle_monitor_interval_s)
                try:
                    await self.sweep_idle_once()
                except Exception as exc:  # never let the monitor die quietly
                    log.warning("slot.idle_sweep_failed", extra={"error": str(exc)})
                try:
                    await self.pressure_evict_once()
                except Exception as exc:
                    log.warning("slot.pressure_sweep_failed", extra={"error": str(exc)})
        except asyncio.CancelledError:
            raise

    async def evict_timeout_for(self, slot_name: str) -> float | None:
        """Resolve the idle TTL after which a slot is hard-evicted (#902).

        Returns ``None`` when the slot is pinned (never TTL-evicted):
          * an explicit ``idle_timeout_s = 0`` in the slot's TOML,
          * :func:`is_pinned` (default-pinned anchor OR
            ``SlotConfig.pinned = true``) with no explicit per-slot
            ``idle_timeout_s`` override, or
          * a non-positive global default with no explicit per-slot value.

        Otherwise returns the effective TTL in seconds: the per-slot TOML
        ``idle_timeout_s`` when set (overrides the global), else the
        global ``_evict_after_s`` default. ``0`` consistently means
        "disabled" at both levels, matching the config-schema contract.
        """
        host = self._host
        canonical = host._resolve_alias(slot_name)
        try:
            cfg = await host._load_slot_config(canonical)
        except (SlotConfigError, SlotNotFound):
            cfg = {}
        raw = cfg.get("idle_timeout_s")
        if isinstance(raw, bool):  # bool is an int subclass — never a TTL
            raw = None
        if isinstance(raw, int):
            return None if raw <= 0 else float(raw)
        # No explicit per-slot value: pin the named anchors / operator-pinned
        # slots, else fall back to the global default (itself disabled when
        # non-positive).
        if is_pinned(canonical, cfg):
            return None
        return float(host._evict_after_s) if host._evict_after_s > 0 else None

    def sweep_candidates(self) -> dict[str, float]:
        """Slot → last-activity timestamp map for the idle/pressure sweeps.

        ``_last_used`` only tracks slots that served a request (or were
        adopted) during THIS process's lifetime. Dispatchable slots missing
        from it — e.g. adopted before the bump-on-adoption fix, or hydrated
        from a state.json that outlived an api restart — used to be
        invisible to both sweeps and could squat on RAM forever. For those,
        fall back to the state record's ``updated_at`` (the last observed
        transition) as the activity timestamp; the sweeps' own state and
        serving-count guards still apply on top.
        """
        host = self._host
        # ``_last_used`` / ``_states`` are id-keyed (rework §11.1); resolve each
        # handle back to its display name at this boundary so the rest of the
        # reaper (config load, unload, pin check, logging) stays name-based.
        candidates: dict[str, float] = {}
        for key, ts in host._last_used.items():
            name = host._name_for_key(key)
            if name:
                candidates[name] = ts
        for key, rec in list(host._states.items()):
            name = host._name_for_key(key)
            if not name or name in candidates:
                continue
            if rec.state not in (SlotState.READY, SlotState.IDLE):
                continue
            if rec.updated_at:
                candidates[name] = float(rec.updated_at)
        return candidates

    async def sweep_idle_once(self) -> None:
        """One idle-sweep pass over every tracked slot.

        Stage 1 (soft): a READY slot idle past ``_idle_after_s`` is
        relabeled IDLE so dashboards distinguish "warm but quiet" from
        "warm and serving".

        Stage 2 (hard, #902): a slot idle past its resolved per-slot TTL
        (:meth:`evict_timeout_for`) is **unloaded**, freeing host RAM —
        the only way to reclaim it, since llama-server allocates KV
        statically at ``ctx_size``. ``idle_timeout_s = 0`` (or a pinned
        slot) is never evicted. A slot mid-request (``serving_count > 0``)
        is never touched; the dispatcher reloads an evicted slot
        transparently on its next request (wake-on-request), so eviction
        is safe.
        """
        host = self._host
        now = time.time()
        for slot_name, ts in self.sweep_candidates().items():
            idle_for = now - ts
            if host._serving_count.get(host._key(slot_name), 0) > 0:
                continue
            state = host._current_state(slot_name)
            if state not in (SlotState.READY, SlotState.IDLE):
                continue

            # Stage 2 — hard TTL eviction.
            evict_after = await self.evict_timeout_for(slot_name)
            if evict_after is not None and idle_for >= evict_after:
                try:
                    await host.unload(slot_name)
                    log.info(
                        "slot.idle_evicted",
                        extra={"slot": slot_name, "idle_s": round(idle_for)},
                    )
                except IllegalSlotTransition:
                    # Raced with another transition — next sweep retries.
                    pass
                except Exception as exc:  # never let one slot kill the sweep
                    log.warning(
                        "slot.idle_evict_failed",
                        extra={"slot": slot_name, "error": str(exc)},
                    )
                continue

            # Stage 1 — soft demotion READY → IDLE.
            if state == SlotState.READY and idle_for >= host._idle_after_s:
                try:
                    await host._transition(
                        slot_name,
                        SlotState.IDLE,
                        message=f"idle for {idle_for:.0f}s",
                    )
                except IllegalSlotTransition:
                    # Raced with an unload — fine; next sweep will skip it.
                    continue

    async def pressure_evict_once(self) -> None:
        """One pressure-eviction pass (#903).

        Probes host free RAM (GTT-aware, §21.10 — via
        ``host._probe_host_free_mb()``). The floor is either the absolute
        ``_evict_pressure_mb`` (default) or, when ``_evict_pressure_pct``
        is set, that percentage of total capacity (``threshold_pct``,
        §21.10). When free RAM is below the floor, evicts idle,
        ``lru``-eligible, unpinned slots one at a time in
        least-recently-used order (oldest ``last_used`` first) until free
        RAM is back above the floor or no more eligible slots remain.

        Guards:
          - floor <= 0 → pressure eviction is disabled.
          - A slot serving a request (``serving_count > 0``) is never
            evicted.
          - A pinned slot (:func:`is_pinned`: the canonical ``agent``
            anchor and other default-pinned names, or any slot with
            ``SlotConfig.pinned = true``) is never evicted by pressure.
          - Only slots with ``lru = true`` in their TOML are eligible.
        """
        host = self._host
        floor = host._evict_pressure_mb
        pct = host._evict_pressure_pct
        if pct is not None:
            total_mb = host._probe_host_total_mb()
            if total_mb > 0:
                floor = total_mb * (pct / 100.0)
        if floor <= 0:
            return
        free_mb = host._probe_host_free_mb()
        if free_mb >= floor:
            return

        # Build the LRU-ordered candidate list. sweep_candidates() unions
        # _last_used with dispatchable slots known only via state.json
        # (adopted / restart-surviving), timestamped by their last observed
        # transition, so pressure eviction can also reclaim those.
        candidates: list[tuple[float, str]] = []
        for slot_name, ts in self.sweep_candidates().items():
            if host._serving_count.get(host._key(slot_name), 0) > 0:
                continue
            canonical = host._resolve_alias(slot_name)
            state = host._current_state(slot_name)
            if state not in (SlotState.READY, SlotState.IDLE):
                continue
            # Read cfg once — used for both the pin check and the lru flag.
            try:
                cfg = await host._load_slot_config(slot_name)
            except (SlotConfigError, SlotNotFound):
                continue
            if is_pinned(canonical, cfg):
                continue
            if not cfg.get("lru", False):
                continue
            candidates.append((ts, slot_name))

        # Evict oldest-first until pressure is relieved or list is exhausted.
        candidates.sort(key=lambda pair: pair[0])
        for _ts, slot_name in candidates:
            # Re-check serving guard and state — may have changed since
            # the list was built (another coroutine may have started a
            # request or the TTL sweep may have evicted it already).
            if host._serving_count.get(host._key(slot_name), 0) > 0:
                continue
            state = host._current_state(slot_name)
            if state not in (SlotState.READY, SlotState.IDLE):
                continue
            try:
                await host.unload(slot_name)
                log.info(
                    "slot.pressure_evicted",
                    extra={
                        "slot": slot_name,
                        "free_mb": round(free_mb),
                        "floor_mb": round(floor),
                    },
                )
            except IllegalSlotTransition:
                # Raced with another transition — skip, next sweep retries.
                pass
            except Exception as exc:
                log.warning(
                    "slot.pressure_evict_failed",
                    extra={"slot": slot_name, "error": str(exc)},
                )
                continue
            # Re-probe free RAM after each eviction to avoid over-shedding.
            free_mb = host._probe_host_free_mb()
            if free_mb >= floor:
                break


__all__ = [
    "_EVICT_AFTER_S",
    "_IDLE_AFTER_S",
    "_IDLE_MONITOR_INTERVAL_S",
    "_PINNED_BY_DEFAULT",
    "ReaperHost",
    "SlotReaper",
    "is_pinned",
    "probe_host_free_mb",
    "probe_host_total_mb",
]
