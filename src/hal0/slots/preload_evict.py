"""Pre-load eviction: free memory synchronously BEFORE a load, until it fits.

Today every automatic reclamation path is reactive: TTL eviction and the
``SlotReaper`` pressure sweep (see :mod:`hal0.slots.reaper`) only run on a
timer, AFTER the box is already tight. Nothing stood between
``SlotManager.load()`` and a container spawn that doesn't fit alongside
what's already resident — the box can OOM before the next sweep tick.

This module adds the missing synchronous gate: before a slot starts, estimate
the incoming model's footprint and, if projected free memory is short, evict
idle, non-pinned resident slots — lowest eviction ``priority`` first,
least-recently-used as the tie-break within a tier (spec 2026-08-02) — until
it fits. It deliberately REUSES existing machinery rather than inventing a
second, divergent notion of "fits" or "evictable":

  - Footprint estimate: :func:`hal0.slots.capacity.estimate_file_size_kv_mb`
    (registry file size + coarse KV-cache estimate) — the same formula
    :func:`hal0.slots.capacity.build_per_slot` uses as its baseline.
  - Free-memory probe: :meth:`SlotManager._probe_host_free_mb` (GTT-aware,
    §21.10) — the same probe the pressure sweeper reads.
  - Eviction eligibility: :func:`hal0.slots.reaper.is_pinned` +
    ``serving_count`` + state, ordered by
    :func:`hal0.slots.reaper.eviction_priority` (lower evicts first) — the
    EXACT rules :meth:`hal0.slots.reaper.SlotReaper.pressure_evict_once`
    already applies, just run synchronously at admission time instead of on
    a timer.

Two layers, so the decision logic is unit-testable without spinning
containers:

  - :func:`select_eviction_order` is a PURE function: given a candidate list
    (with pre-computed footprint estimates) and how much is needed, it
    decides which candidates to evict — sorted lowest-priority-first,
    least-recently-used as the tie-break — and whether the result will fit.
    No I/O, no SlotManager.
  - :func:`admit` is the async orchestrator :meth:`SlotManager.load` calls:
    gathers live candidates, calls :func:`select_eviction_order`, executes
    the plan against the real manager (real unloads, re-probing real free
    memory after each — the plan is an ESTIMATE, the probe is ground
    truth), and raises :class:`PreloadEvictionFailed` with the concrete
    evidence (what was freed, what was still short) if it still won't fit.

Concurrent admission (field evidence, box OOM postmortem): several loads
racing independently would each read the same stale free-memory snapshot
and each conclude "it fits", collectively over-committing. ``admit()``
serializes the fit decision through :attr:`SlotManager._admission_lock` and
reserves the incoming model's estimated footprint in
:attr:`SlotManager._preload_reserved_mb` for the duration of the load —
released on success AND failure — so a second concurrent ``admit()`` call
for a DIFFERENT slot sees the first load's reservation subtracted from free
memory, even though the first slot isn't resident (and thus not visible to
the host-memory probe) yet.

NOTE — scope: this closes the gate on every path that calls
``SlotManager.load()`` (interactive load/swap/start, dispatcher
wake-on-request). It does NOT cover slot containers that systemd starts
directly at boot via each Quadlet's ``WantedBy=hal0.target`` (see
``hal0.providers.container``) — those are enabled to auto-start
independent of hal0-api's Python process, so hal0-api only ever ADOPTS them
after the fact (:meth:`SlotManager._maybe_adopt_running_slot`) and this
gate never runs for them. That is a separate, larger change (systemd
sequencing / hal0-api-owned boot admission) tracked as follow-up, not
addressed here.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from hal0.slots.reaper import (
    _DEFAULT_EVICTION_PRIORITY,
    _warn_lru_deprecated,
    eviction_priority,
    is_pinned,
)
from hal0.slots.state import (
    IllegalSlotTransition,
    SlotConfigError,
    SlotError,
    SlotNotFound,
    SlotState,
)

if TYPE_CHECKING:
    import asyncio

log = logging.getLogger(__name__)

# Extra free-memory slack (MiB) required on top of a model's estimated
# footprint before admission considers a load safe. Absorbs error in the
# coarse file-size + KV-cache estimate and runtime/container overhead the
# estimate doesn't capture. Mirrors evict_pressure_mb's role as a floor,
# just applied synchronously at admission instead of on the pressure timer.
_DEFAULT_HEADROOM_MB: float = 1024.0

__all__ = [
    "_DEFAULT_HEADROOM_MB",
    "CandidateSlot",
    "EvictionStep",
    "PreloadEvictHost",
    "PreloadEvictionFailed",
    "PreloadEvictionPlan",
    "admit",
    "select_eviction_order",
]


class PreloadEvictionFailed(SlotError):
    """The incoming model still doesn't fit after evicting every eligible slot.

    ``details`` carries the concrete evidence an operator needs: which
    slots were freed, how much that recovered, and the remaining shortfall
    — never just "out of memory".
    """

    code = "slot.preload_evict_insufficient"
    status = 507


@dataclass(frozen=True, slots=True)
class CandidateSlot:
    """One resident slot considered for pre-load eviction.

    ``eligible=False`` candidates are carried through (rather than
    filtered out before reaching :func:`select_eviction_order`) so the
    planner itself is the thing under test for "never evicts protected
    slots" — a candidate list mixing eligible and ineligible entries is
    the natural unit-test fixture.
    """

    name: str
    last_used: float
    footprint_mb: float
    eligible: bool
    reason: str = ""
    priority: int = _DEFAULT_EVICTION_PRIORITY


@dataclass(frozen=True, slots=True)
class EvictionStep:
    """One eviction actually executed, for the structured log trail."""

    slot: str
    freed_mb: float


@dataclass(frozen=True, slots=True)
class PreloadEvictionPlan:
    """Pure decision output of :func:`select_eviction_order`.

    ``selected`` is the priority-ordered subset of eligible candidates the
    caller should evict; ``projected_free_mb`` is the ESTIMATE after
    evicting all of them (real callers re-probe after each real eviction
    rather than trusting this cumulative estimate — see :func:`admit`).
    """

    needed_mb: float
    headroom_mb: float
    initial_free_mb: float
    projected_free_mb: float
    selected: tuple[CandidateSlot, ...]
    fits: bool


def select_eviction_order(
    candidates: list[CandidateSlot],
    *,
    needed_mb: float,
    headroom_mb: float,
    free_mb: float,
) -> PreloadEvictionPlan:
    """Decide which candidates to evict, lowest priority first, until it fits.

    Pure: no I/O, no SlotManager. Ineligible candidates (``eligible=False``
    — pinned, serving, not resident, or config unavailable) are NEVER
    selected regardless of priority. Eligible candidates are evicted
    lowest-``priority``-first, oldest-``last_used``-first within a tier,
    stopping as soon as the running projected free memory covers
    ``needed_mb + headroom_mb``, matching
    :meth:`hal0.slots.reaper.SlotReaper.pressure_evict_once`'s "stop as
    soon as relieved" behaviour.
    """
    target = needed_mb + headroom_mb
    if free_mb >= target:
        return PreloadEvictionPlan(
            needed_mb=needed_mb,
            headroom_mb=headroom_mb,
            initial_free_mb=free_mb,
            projected_free_mb=free_mb,
            selected=(),
            fits=True,
        )

    eligible = sorted(
        (c for c in candidates if c.eligible), key=lambda c: (c.priority, c.last_used)
    )
    selected: list[CandidateSlot] = []
    projected = free_mb
    for candidate in eligible:
        if projected >= target:
            break
        selected.append(candidate)
        projected += candidate.footprint_mb

    return PreloadEvictionPlan(
        needed_mb=needed_mb,
        headroom_mb=headroom_mb,
        initial_free_mb=free_mb,
        projected_free_mb=projected,
        selected=tuple(selected),
        fits=projected >= target,
    )


class PreloadEvictHost(Protocol):
    """Narrow seam :func:`admit` needs from ``SlotManager``.

    Mirrors :class:`hal0.slots.reaper.ReaperHost` — same style, same
    reasoning: tests monkeypatch these on a real ``SlotManager`` instance
    (see ``tests/slots/test_pressure_eviction.py``), so every read goes
    through ``host.X()``, never computed inline here.
    """

    _serving_count: dict[int, int]
    _preload_evict_enabled: bool
    _preload_evict_headroom_mb: float
    _preload_reserved_mb: dict[str, float]
    _admission_lock: asyncio.Lock

    def _resolve_alias(self, name: str) -> str: ...
    def _key(self, name: str) -> int: ...
    def _current_state(self, name: str) -> SlotState: ...
    def _sweep_candidates(self) -> dict[str, float]: ...
    def _probe_host_free_mb(self) -> float: ...
    async def _load_slot_config(self, name: str) -> dict[str, Any]: ...
    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]: ...
    async def list(self) -> list[Any]: ...
    async def unload(self, name: str) -> Any: ...


async def _estimate_incoming_footprint_mb(
    host: PreloadEvictHost,
    model_id: str,
    *,
    model_info: dict[str, Any] | None = None,
) -> float:
    """Best-effort footprint (MiB) for a model that is NOT yet resident.

    Reuses :func:`hal0.slots.capacity.estimate_file_size_kv_mb` — registry
    file size plus the coarse KV-cache estimate — the exact formula
    :func:`hal0.slots.capacity.build_per_slot` falls back to for a
    resident slot with no live cgroup/FLM figure yet. There is nothing
    more precise available synchronously before spawn: FLM's footprint_gb
    and the container cgroup probe both describe an ALREADY-running
    process, not one about to start.

    ``model_info``: pass the caller's already-resolved
    :meth:`SlotManager._resolve_model_info` result (``load()`` does, so it
    threads through to ``_spawn_locked`` too) to avoid a second registry
    hit for the same model_id. ``None`` resolves it here.

    WEAK SPOT: when the registry has no size_bytes for ``model_id`` (outage,
    not-yet-registered catalog id, stub registry) this returns 0.0, and the
    caller fails OPEN — skips pre-load eviction rather than admitting on a
    bogus near-zero estimate. This mirrors the pressure probe's existing
    fail-safe contract (:func:`hal0.slots.reaper.probe_host_free_mb`
    returns ``inf`` on probe failure so eviction never fires blind) but
    means a genuinely huge model with an unresolvable registry entry gets
    NO pre-load protection — only the reactive pressure sweeper still
    applies. Left as-is rather than guessing a size, per the existing
    "never swallow into a fabricated number" convention in this module tree
    (see capacity.py's Tier-1 fixes).
    """
    from hal0.slots.capacity import estimate_file_size_kv_mb

    info = model_info if model_info is not None else await host._resolve_model_info(model_id)
    if not info:
        return 0.0
    try:
        model_mb = float(info.get("size_bytes") or 0) / (1024.0 * 1024.0)
    except (TypeError, ValueError):
        model_mb = 0.0
    if model_mb <= 0:
        return 0.0
    return estimate_file_size_kv_mb(model_mb, info)


async def _gather_candidates(host: PreloadEvictHost, *, exclude: str) -> list[CandidateSlot]:
    """Build the full candidate list (eligible AND ineligible) for admission.

    Eligibility mirrors :meth:`hal0.slots.reaper.SlotReaper.pressure_evict_once`
    exactly: resident state (READY/IDLE), not currently serving, not
    pinned (:func:`is_pinned`). Every other resident slot is eligible,
    ordered by :func:`hal0.slots.reaper.eviction_priority` (lower evicts
    first); the retired ``lru = true`` TOML key is ignored and warned once
    per slot (:func:`hal0.slots.reaper._warn_lru_deprecated`). The slot
    about to be loaded is always excluded — by construction it can't be
    READY/IDLE yet (``load()`` short-circuits before this point when it
    already is), but the exclusion is explicit here too as a hard
    guarantee, not an accident of state timing.
    """
    sweep = host._sweep_candidates()
    if not sweep:
        return []
    exclude_canonical = host._resolve_alias(exclude)

    registry: Any = None
    with contextlib.suppress(Exception):
        from hal0.registry.store import ModelRegistry

        registry = ModelRegistry()

    from hal0.slots.capacity import build_per_slot

    slots = await host.list()
    per_slot = await build_per_slot(slots, registry=registry)

    out: list[CandidateSlot] = []
    for name, ts in sweep.items():
        canonical = host._resolve_alias(name)
        if name == exclude or canonical == exclude_canonical:
            continue

        reason = ""
        eligible = True
        priority = _DEFAULT_EVICTION_PRIORITY
        if host._serving_count.get(host._key(name), 0) > 0:
            eligible, reason = False, "serving"
        else:
            state = host._current_state(name)
            if state not in (SlotState.READY, SlotState.IDLE):
                eligible, reason = False, "not_resident"
            else:
                try:
                    cfg = await host._load_slot_config(name)
                except (SlotConfigError, SlotNotFound):
                    eligible, reason = False, "config_unavailable"
                else:
                    if is_pinned(canonical, cfg):
                        eligible, reason = False, "pinned"
                    else:
                        _warn_lru_deprecated(canonical, cfg)
                        priority = eviction_priority(cfg)

        footprint_mb = float(per_slot.get(name, {}).get("mem_mb", 0.0) or 0.0)
        out.append(
            CandidateSlot(
                name=name,
                last_used=ts,
                footprint_mb=footprint_mb,
                eligible=eligible,
                reason=reason,
                priority=priority,
            )
        )
    return out


@contextlib.asynccontextmanager
async def admit(
    host: PreloadEvictHost,
    *,
    slot_name: str,
    model_id: str,
    model_info: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Admission gate: free memory (if needed) before ``slot_name`` starts.

    Wrap the spawn section of :meth:`SlotManager.load` in
    ``async with admit(self, slot_name=slot_name, model_id=resolved_model,
    model_info=model_info):``. No-op (disabled via config, or the incoming
    footprint can't be estimated — fail-open) except for the reservation
    bookkeeping used to serialize concurrent admissions. Raises
    :class:`PreloadEvictionFailed` — BEFORE the caller's ``yield`` body
    runs, so nothing is half-loaded — when eviction still leaves it short.

    ``model_info``: pass ``load()``'s already-resolved
    :meth:`SlotManager._resolve_model_info` result so admission and
    ``_spawn_locked`` share one registry lookup instead of two.
    """
    if not host._preload_evict_enabled:
        yield
        return

    needed_mb = await _estimate_incoming_footprint_mb(host, model_id, model_info=model_info)
    if needed_mb <= 0:
        log.debug(
            "slot.preload_evict_unknown_footprint",
            extra={"slot": slot_name, "model_id": model_id},
        )
        yield
        return

    headroom_mb = host._preload_evict_headroom_mb

    async with host._admission_lock:
        reserved_by_others = sum(
            mb for name, mb in host._preload_reserved_mb.items() if name != slot_name
        )
        free_mb = host._probe_host_free_mb() - reserved_by_others
        target = needed_mb + headroom_mb

        executed: list[EvictionStep] = []
        if free_mb < target:
            candidates = await _gather_candidates(host, exclude=slot_name)
            plan = select_eviction_order(
                candidates, needed_mb=needed_mb, headroom_mb=headroom_mb, free_mb=free_mb
            )
            for candidate in plan.selected:
                try:
                    await host.unload(candidate.name)
                except IllegalSlotTransition:
                    log.warning(
                        "slot.preload_evict_race",
                        extra={"slot": slot_name, "candidate": candidate.name},
                    )
                    continue
                except Exception as exc:  # never let one bad eviction sink admission
                    log.warning(
                        "slot.preload_evict_failed",
                        extra={
                            "slot": slot_name,
                            "candidate": candidate.name,
                            "error": str(exc),
                        },
                    )
                    continue
                free_mb = host._probe_host_free_mb() - reserved_by_others
                executed.append(EvictionStep(slot=candidate.name, freed_mb=candidate.footprint_mb))
                log.info(
                    "slot.preload_evicted",
                    extra={
                        "slot": slot_name,
                        "model_id": model_id,
                        "candidate": candidate.name,
                        "freed_mb": round(candidate.footprint_mb, 1),
                        "free_mb": round(free_mb, 1),
                        "needed_mb": round(target, 1),
                    },
                )
                if free_mb >= target:
                    break

        if free_mb < target:
            shortfall_mb = round(target - free_mb, 1)
            evicted_names = [step.slot for step in executed]
            freed_total_mb = round(sum(step.freed_mb for step in executed), 1)
            log.warning(
                "slot.preload_evict_insufficient",
                extra={
                    "slot": slot_name,
                    "model_id": model_id,
                    "evicted": evicted_names,
                    "freed_mb": freed_total_mb,
                    "free_mb": round(free_mb, 1),
                    "needed_mb": round(needed_mb, 1),
                    "headroom_mb": round(headroom_mb, 1),
                    "shortfall_mb": shortfall_mb,
                },
            )
            raise PreloadEvictionFailed(
                f"cannot free enough memory to load {model_id!r} into slot "
                f"{slot_name!r}: evicted {evicted_names} (freed {freed_total_mb} MiB), "
                f"still short {shortfall_mb} MiB "
                f"(need {round(needed_mb, 1)} MiB + {round(headroom_mb, 1)} MiB headroom, "
                f"have {round(free_mb, 1)} MiB free)",
                details={
                    "slot": slot_name,
                    "model_id": model_id,
                    "evicted": evicted_names,
                    "freed_mb": freed_total_mb,
                    "free_mb": round(free_mb, 1),
                    "needed_mb": round(needed_mb, 1),
                    "headroom_mb": round(headroom_mb, 1),
                    "shortfall_mb": shortfall_mb,
                },
            )

        host._preload_reserved_mb[slot_name] = needed_mb

    try:
        yield
    finally:
        async with host._admission_lock:
            host._preload_reserved_mb.pop(slot_name, None)
