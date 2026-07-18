"""Slot lifecycle manager (container runtime).

SlotManager owns every aspect of slot lifecycle: load, unload, swap,
status, restart, create, delete. Every state-changing call dispatches
through :class:`ContainerProvider` — each slot runs as a podman
container under its ``hal0-slot@<name>.service`` systemd unit.

State transitions are persisted atomically to
``/var/lib/hal0/slots/<name>/state.json`` (see :mod:`hal0.slots.state`).

Architectural boundaries (ARCHITECTURE.md "Key boundaries"):
  - All public methods return :class:`Slot` snapshots, never dicts.
    Errors raise typed Hal0Error subclasses.
  - This module does NOT import from :mod:`hal0.dispatcher`.

The v0.1.x public surface (``load`` / ``unload`` / ``swap`` /
``status`` / ``create`` / ``update_config`` / …) is preserved
verbatim so api/routes, dispatcher, and orchestrator callers do not
need to migrate in PR-10.

New in PR-10: :data:`SEEDED_SLOTS`, :data:`NPU_SEEDED_SLOTS`, plus
routing helpers (:meth:`SlotManager.default_slot_for`,
:meth:`SlotManager.route_for_request`,
:meth:`SlotManager.add_slot`, :meth:`SlotManager.remove_slot`).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.registry import fallback as _registry_fallback
from hal0.slot_config import (
    fold_ctx_size_alias,
    slot_write_lock,
    write_slot_toml,
)
from hal0.slots._cfg_helpers import _cfg_port, _cfg_provider, _cfg_to_dict, _model_default
from hal0.slots.config_write import (
    _base_profile_for_backend,
    _cfg_effective_backend,
    _reconcile_device_profile,
    check_default_uniqueness,
    check_npu_exclusivity,
    reconcile_and_guard_slot_config,
    reconcile_slot_updates,
)
from hal0.slots.drift import _CONFIG_DRIFT_KEYS, _argv_values
from hal0.slots.drift import compute_config_drift as _compute_config_drift
from hal0.slots.npu.trio import is_npu_trio_shadow
from hal0.slots.npu.trio import reconcile_trio_slots as _npu_reconcile_trio_slots
from hal0.slots.profile_adopt import (
    apply_preferred_profile as _profile_adopt_apply_preferred_profile,
)
from hal0.slots.profile_adopt import (
    apply_preferred_runner as _profile_adopt_apply_preferred_runner,
)
from hal0.slots.profile_adopt import (
    defuse_stale_mtp_on_swap as _profile_adopt_defuse_stale_mtp_on_swap,
)
from hal0.slots.profile_adopt import (
    preferred_profile_for as _profile_adopt_preferred_profile_for,
)
from hal0.slots.profile_adopt import (
    preferred_runner_for as _profile_adopt_preferred_runner_for,
)
from hal0.slots.profile_adopt import profile_fits_slot as _profile_adopt_profile_fits_slot
from hal0.slots.profile_adopt import runner_fits_slot as _profile_adopt_runner_fits_slot
from hal0.slots.reaper import _EVICT_AFTER_S, _IDLE_AFTER_S, _IDLE_MONITOR_INTERVAL_S, SlotReaper
from hal0.slots.reaper import is_pinned as _reaper_is_pinned
from hal0.slots.reaper import probe_host_free_mb as _reaper_probe_host_free_mb
from hal0.slots.reaper import probe_host_total_mb as _reaper_probe_host_total_mb
from hal0.slots.routing import (
    _VALID_SLOT_TYPES,
    NPU_SEEDED_SLOTS,
    SEEDED_SLOTS,
    SLOT_ALIASES,
    LoadedSlot,
    loaded_slot_from_config,
)
from hal0.slots.routing import add_slot as _routing_add_slot
from hal0.slots.routing import default_slot_for as _routing_default_slot_for
from hal0.slots.routing import loaded_slot as _routing_loaded_slot
from hal0.slots.routing import remove_slot as _routing_remove_slot
from hal0.slots.routing import resolve_for_request as _routing_resolve_for_request
from hal0.slots.routing import route_for_request as _routing_route_for_request
from hal0.slots.routing import seeded_slots as _routing_seeded_slots
from hal0.slots.state import (
    DISPATCHABLE_STATES,
    IllegalSlotTransition,
    SlotConfigError,
    SlotNotFound,
    SlotPinned,
    SlotState,
    SlotStateRecord,
    is_transition_legal,
    provider_requires_model,
    read_state,
    write_state_atomic,
)
from hal0.slots.watchdog import (
    _FAIL_WATCH_INTERVAL_S,
    _FAIL_WATCH_LIVE_STATES,
    _HEALTH_FAIL_STRIKES,
    _WARMING_INACTIVE_STRIKES,
    _WARMING_STALE_AFTER_S,
    SlotWatchdog,
)

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig
    from hal0.slots.arbiter import GpuArbiter

log = logging.getLogger(__name__)


class RegistryUnavailableError(Hal0Error):
    """The model registry could not be consulted (outage, not a miss).

    Raised by the model-cache check when the registry lookup fails for a
    reason other than "model not registered" — e.g. an unreadable registry
    directory.  Distinct from ``model.not_found`` (404): the model may well
    exist, we just cannot know right now, so 503 is the honest answer.
    Surfaced instead of silently treating the model as cached (which
    skipped PULLING and crash-looped the container on a missing file).
    """

    code = "model.registry_unavailable"
    status = 503


# ── Seeded slot catalogue + routing (P3-slots §1i: moved to slots/routing.py)
#
# SEEDED_SLOTS / NPU_SEEDED_SLOTS / SLOT_ALIASES / LoadedSlot now live in
# hal0.slots.routing (imported at module top); re-exported below so every
# existing ``from hal0.slots.manager import X`` caller keeps working
# unchanged (dispatcher._capability_resolve, api/__init__, omni_router,
# stacks/apply, tests/slots/test_loaded_slot.py, tests/omni_router/conftest.py).


# ── Tunables ─────────────────────────────────────────────────────────────────

# NOTE: fail-watch tunables (_FAIL_WATCH_INTERVAL_S, _FAIL_WATCH_LIVE_STATES,
# _HEALTH_FAIL_STRIKES, _WARMING_INACTIVE_STRIKES, _WARMING_STALE_AFTER_S)
# moved to hal0.slots.watchdog (P3-slots §1b-watchdog) — imported + re-exported below.
#
# NOTE: idle-monitor tunables (_IDLE_AFTER_S, _IDLE_MONITOR_INTERVAL_S,
# _EVICT_AFTER_S) and _PINNED_BY_DEFAULT moved to hal0.slots.reaper
# (P3-slots §1b) — imported + re-exported below.


# ── Hook protocols ───────────────────────────────────────────────────────────
#
# Slot loading optionally fans out to a model-pull step when the model file
# isn't on disk yet.  The pull engine itself lives in ``hal0.registry.pull``;
# the SlotManager only sees an injectable callable so it stays out of HF /
# I/O concerns.  ``PullRunner`` must raise on failure so ``load()`` can flip
# the slot to ERROR with a meaningful message.

PullRunner = Callable[[str], Awaitable[None]]
"""Async hook invoked while the slot is in PULLING.

Receives the resolved model id; must ``await`` until the model is on disk
and resolvable through :class:`hal0.registry.store.ModelRegistry`, or raise
on hard failure."""

ModelCacheCheck = Callable[[str], bool]
"""Sync predicate: True when ``model_id`` is already on disk + registered.

The default check consults :class:`ModelRegistry`; tests inject stubs to
force-trigger or skip the PULLING transition deterministically."""


# ── Slot snapshot ────────────────────────────────────────────────────────────


class Slot:
    """Runtime handle for a single inference slot.

    Carries the slot name, current state, and any live metadata returned
    by the last health probe.  Immutable snapshot — SlotManager is the
    authoritative mutable source.
    """

    def __init__(
        self,
        name: str,
        state: SlotState = SlotState.OFFLINE,
        port: int = 0,
        model_id: str | None = None,
        backend: str | None = None,
        metadata: dict[str, Any] | None = None,
        last_used_at: float | None = None,
    ) -> None:
        self.name = name
        self.state = state
        self.port = port
        self.model_id = model_id
        self.backend = backend
        self.metadata: dict[str, Any] = metadata or {}
        # Wall-clock epoch (seconds) of the most recent request served by
        # this slot. ``None`` when the slot hasn't served since hal0-api
        # started — surfaces on /api/slots so the dashboard can render the
        # "recently live within 1h" indicator (see ui/src/dash/slots.jsx
        # ``slotIndicator``). Persistence is intentionally process-local:
        # on restart the dashboard renders the slot as "loaded but stale"
        # (yellow) until the first request lands, which matches operator
        # intuition — we don't actually know if it was hit during downtime.
        self.last_used_at: float | None = last_used_at

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for API responses."""
        return {
            "name": self.name,
            "state": self.state.value,
            "port": self.port,
            "model_id": self.model_id,
            "backend": self.backend,
            "metadata": self.metadata,
            "last_used_at": self.last_used_at,
        }


# ── Manager ──────────────────────────────────────────────────────────────────


class SlotManager:
    """Manages the lifecycle of all hal0 inference slots.

    Each public method corresponds to a CLI subcommand and an API route.
    All methods are async so they can be awaited from FastAPI route handlers
    and from the Typer CLI via asyncio.run().

    The state machine (hal0.slots.state) replaces the ad-hoc status strings
    in haloai's original.  Every state transition is:
      1. Validated against ``LEGAL_TRANSITIONS`` (illegal → IllegalSlotTransition).
      2. Persisted atomically to /var/lib/hal0/slots/<name>/state.json.
      3. Pushed onto in-memory async queues for any state_stream() subscribers.
    """

    # Class-level alias for module-level :data:`SEEDED_SLOTS`. Kept
    # spelt the same as v0.1.x so caller code (and the test
    # ``BUILTIN_SLOTS in SlotManager`` check) keeps working.
    # ``seeded_slots()`` is the source of truth — it composes
    # SEEDED_SLOTS with NPU_SEEDED_SLOTS when an FLM runtime is
    # present.
    BUILTIN_SLOTS: tuple[str, ...] = SEEDED_SLOTS

    def __init__(
        self,
        *,
        pull_runner: PullRunner | None = None,
        model_cache_check: ModelCacheCheck | None = None,
        idle_after_s: float = _IDLE_AFTER_S,
        evict_after_s: float = _EVICT_AFTER_S,
        evict_pressure_mb: float = 8192.0,
        evict_pressure_pct: float | None = None,
        idle_monitor_interval_s: float = _IDLE_MONITOR_INTERVAL_S,
        event_bus: Any | None = None,
        upstreams_registry: Any | None = None,
    ) -> None:
        # Optional EventBus for footer/dashboard observability. Not part
        # of the slot state machine — purely a side-channel so the
        # dashboard footer can render transitions without polling. None
        # in CLI / unit-test contexts; wired by the FastAPI lifespan.
        self._event_bus = event_bus
        # Live UpstreamRegistry injected by the API lifespan so ContainerProvider
        # slots can auto-register/deregister kind="remote" entries at load/unload
        # time.  None in test contexts (container upstream wiring is skipped).
        self._upstreams_registry = upstreams_registry
        # Per-slot locks to prevent concurrent load/unload/restart races.
        self._locks: dict[str, asyncio.Lock] = {}
        # Parsed slot-TOML cache: canonical slot name → (mtime_ns, size,
        # parsed dict).  ``resolve_for_request`` calls ``iter_configs``
        # twice per routed request, which used to re-read + re-parse every
        # slot TOML from disk each time.  The cache is keyed on the file's
        # (st_mtime_ns, st_size) so external edits are picked up on the
        # next call (we still stat per read — the win is skipping the
        # parse, not the stat).  Same-process writers additionally
        # invalidate eagerly via :meth:`_invalidate_cfg_cache` so even a
        # coarse-mtime filesystem can't serve a stale parse after our own
        # write.  Callers receive deep copies — the cached dict is never
        # handed out for mutation.
        self._cfg_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        # In-memory copy of the latest state per slot (mirrors state.json).
        self._states: dict[str, SlotStateRecord] = {}
        # SSE subscribers: list of queues; one per active state_stream().
        self._subscribers: list[asyncio.Queue[SlotStateRecord]] = []
        # Idle-tracking — last request timestamp per slot.
        self._last_used: dict[str, float] = {}
        # Per-slot background tasks that poll the container unit's
        # is-active state and push a transition when it drops out from
        # underneath us. Keyed by slot name; only present while the
        # slot is in a live state. Owned here (not by SlotWatchdog) since
        # `_transition` needs to check/mutate it synchronously.
        self._fail_watchers: dict[str, asyncio.Task[None]] = {}
        # Push-driven failure detector (P3-slots §1b-watchdog) — see watchdog.py.
        self._watchdog: SlotWatchdog = SlotWatchdog(self)
        # PULLING — optional model-pull hook + cache predicate.  When
        # ``pull_runner`` is unset, load() never enters PULLING (the model
        # is treated as already present, matching the legacy
        # offline→starting path).  See task #10 in PLAN.md.
        self._pull_runner: PullRunner | None = pull_runner
        self._model_cache_check: ModelCacheCheck = (
            model_cache_check or self._default_model_cache_check
        )
        # SERVING — per-slot in-flight request counter.  ``serving()`` is
        # an async context manager that flips READY/IDLE → SERVING on the
        # first concurrent entry and back to READY on the last exit.  A
        # single asyncio.Lock guards the counter to prevent toggle storms
        # when N concurrent requests arrive in the same tick.
        self._serving_count: dict[str, int] = {}
        self._serving_lock: asyncio.Lock = asyncio.Lock()
        # DR-2 — per-slot committed-dispatch tickets.  ``Dispatcher.forward``
        # takes a ticket synchronously right after the image-mode guard and
        # BEFORE its first await, closing the window where a request has
        # passed the guard but not yet entered ``serving()``.  ``in_flight_count``
        # sums tickets + serving so the GpuArbiter drain never unloads a slot
        # out from under a request that is already committed to dispatch.
        self._dispatch_tickets: dict[str, int] = {}
        # IDLE — background sweeper task that demotes READY→IDLE after
        # ``idle_after_s`` seconds of inactivity.  Started explicitly via
        # ``start_idle_monitor()`` (the API lifespan owns the lifecycle so
        # tests can inject shorter intervals).
        self._idle_after_s: float = idle_after_s
        # Hard-eviction TTL default (#902): a slot idle past its resolved
        # idle_timeout_s is unloaded, not just relabeled.  Per-slot TOML
        # idle_timeout_s overrides this global default.
        self._evict_after_s: float = evict_after_s
        # Pressure-eviction floor (#903): when host MemAvailable drops below
        # this value (MiB), idle lru-eligible slots are evicted in LRU order
        # until free RAM recovers.  0 disables pressure eviction.
        self._evict_pressure_mb: float = float(evict_pressure_mb)
        # §21.10 threshold_pct: when set, the pressure floor is instead
        # ``evict_pressure_pct`` percent of total GTT-aware capacity,
        # re-derived every sweep (total capacity can shift as the amdgpu
        # GTT pool grows/shrinks). None (default) keeps the absolute
        # ``evict_pressure_mb`` floor above — purely additive.
        self._evict_pressure_pct: float | None = evict_pressure_pct
        self._idle_monitor_interval_s: float = idle_monitor_interval_s
        # Idle/eviction background loop (P3-slots §1b) — SlotReaper owns its
        # own task handle; see reaper.py.
        self._reaper: SlotReaper = SlotReaper(self)
        # GpuArbiter (Phase D, spec §7) — constructed lazily on first
        # ``.arbiter`` access so CLI/test contexts that never touch image
        # mode pay nothing. See the ``arbiter`` property below.
        self._arbiter: GpuArbiter | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _register_container_upstream(self, slot_name: str, port: int) -> None:
        """Add a kind="remote" upstream for a container slot's loopback port.

        Idempotent via upsert — if the slot was already registered (e.g. a
        restart), the entry is refreshed with the current port.
        """
        if self._upstreams_registry is None:
            log.debug(
                "container.upstream_registry_unavailable",
                extra={"slot": slot_name},
            )
            return
        from hal0.upstreams.registry import Upstream

        upstream = Upstream(
            name=slot_name,
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            auth_style="none",
            warmup_strategy="none",
            advertise_models=True,
            slot_name=slot_name,  # marks this remote as container-backed (for dispatcher preflight)
        )
        self._upstreams_registry.upsert(upstream)
        log.info(
            "container.upstream_registered",
            extra={"slot": slot_name, "url": upstream.url},
        )

    def _deregister_container_upstream(self, slot_name: str) -> None:
        """Remove the kind="remote" upstream for a container slot."""
        if self._upstreams_registry is None:
            return
        removed = self._upstreams_registry.remove(slot_name)
        if removed:
            log.info("container.upstream_deregistered", extra={"slot": slot_name})

    async def reconcile_container_upstreams(self) -> list[str]:
        """Re-register upstreams for containers that outlived the process (#732).

        Per-slot ``kind="remote"`` upstreams exist only in the in-memory
        registry and die with the api process, while the podman containers
        (and their loaded models) survive a ``systemctl restart hal0-api``.
        Pre-fix, every restart left "ready" slots returning
        ``dispatch.no_route`` until an operator unload+load sweep.

        Called once from the api lifespan after startup. A slot is restored
        when its persisted state is dispatchable AND its unit is live
        (``is_active`` probe) — a stale state.json must never register a
        dead upstream. Trio shadows are skipped (no container of their own;
        the npu anchor serves them). Returns the restored slot names.
        """
        restored: list[str] = []
        if self._upstreams_registry is None:
            return restored
        try:
            cfgs = await self.iter_configs()
        except Exception as exc:
            log.warning("container.upstream_reconcile_failed", extra={"error": str(exc)})
            return restored
        from hal0.providers.container import container_provider

        for cfg in cfgs:
            name = str(cfg.get("name", ""))
            if not name or is_npu_trio_shadow(cfg):
                continue
            port = _cfg_port(cfg)
            if not port:
                continue
            try:
                active = await asyncio.get_event_loop().run_in_executor(
                    None, container_provider().is_active, name
                )
            except Exception:
                continue
            # A stale state.json must never register a dead upstream.
            if not active:
                continue
            state = self._current_state(name)
            if state in (SlotState.READY, SlotState.SERVING, SlotState.IDLE):
                # Already dispatchable — just restore the in-memory route.
                pass
            elif state in (SlotState.OFFLINE, SlotState.ERROR):
                # Inverse drift: the container survived the api restart (or
                # was started out-of-band) but state.json reads OFFLINE.
                # Pre-fix this slot was skipped, so it stayed unrouted AND
                # the dashboard reported it "offline" over a live, serving
                # container until a later /api/slots poll happened to adopt
                # it. Adopt it here so reconciliation is the single point
                # that heals the drift at startup.
                adopted = await self._maybe_adopt_running_slot(name, cfg)
                if adopted is None:
                    # Nothing to adopt (e.g. no model configured) — leave it.
                    continue
            else:
                # Transitional (pulling/starting/warming/unloading): a load
                # is already in flight and will register on completion.
                continue
            self._register_container_upstream(name, port)
            restored.append(name)
        if restored:
            log.info("container.upstreams_reconciled", extra={"slots": restored})
        return restored

    def _lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    @staticmethod
    def _resolve_alias(name: str) -> str:
        """Map a back-compat alias to its canonical slot name.

        Aliases (``primary`` → ``chat``, ``agent-hermes`` → ``agent``) are
        accepted by every public method but never stored on disk and never
        returned by :meth:`list` or :meth:`iter_configs`.  Callers that
        want to know whether the name was remapped can compare
        ``_resolve_alias(name) != name``.
        """
        return SLOT_ALIASES.get(name, name)

    def _state_file(self, name: str) -> Path:
        return paths.slot_data_dir(name) / "state.json"

    def _config_file(self, name: str) -> Path:
        return paths.slots_config_dir() / f"{name}.toml"

    def _all_configured_slot_names(self) -> list[str]:
        """Enumerate slots by listing /etc/hal0/slots/*.toml."""
        cfg_dir = paths.slots_config_dir()
        if not cfg_dir.exists():
            return []
        return sorted(p.stem for p in cfg_dir.glob("*.toml") if not p.name.startswith("."))

    def _ensure_known(self, name: str) -> None:
        """Raise SlotNotFound if no config and no state for this slot."""
        if name in self._states:
            return
        if self._config_file(name).exists():
            return
        # Check state.json as a final fallback (slot may have been create()'d
        # in-memory only during tests).
        if self._state_file(name).exists():
            return
        raise SlotNotFound(
            f"slot {name!r} is not configured",
            details={"slot": name},
        )

    # ── public readiness interface (issue #696) ─────────────────────────────

    #: States in which a slot is safe to dispatch inference requests to.
    #: Single source of truth per #696 / DR-8 — aliases the one canonical set
    #: in ``hal0.slots.state`` so this class never re-declares the literal.
    #: Sync read so call sites in the hot dispatch path pay zero await overhead.
    _DISPATCHABLE_STATES: frozenset[SlotState] = DISPATCHABLE_STATES

    def state(self, name: str) -> SlotState:
        """Return the current :class:`SlotState` for *name*.

        Locked public interface (issue #696):
          - Cache-first: returns the in-memory record when present.
          - State.json fallback: reads ``/var/lib/hal0/slots/<name>/state.json``
            on a cache miss and populates the cache.
          - OFFLINE default: unknown slot → ``SlotState.OFFLINE``, never raises.

        Synchronous by design — the dispatch hot path (router.py) reads
        state without awaiting; async callers can call it directly.

        Resolves back-compat aliases transparently (e.g. ``primary`` →
        ``chat``) so callers never need to pre-resolve.
        """
        return self._current_state(self._resolve_alias(name))

    def is_ready_for_dispatch(self, name: str) -> bool:
        """Return ``True`` when *name* is in the dispatchable ready-set.

        Ready set (issue #696): ``READY | SERVING | IDLE``.

        This is the single authoritative implementation — all three
        previously-duplicated inline checks in ``dispatcher/router.py``
        and ``dispatcher/flm_trio.py`` delegate here.  A future state
        addition that is NOT dispatchable will be caught automatically
        by the ``test_is_ready_for_dispatch_parametrized`` test.
        """
        return self.state(name) in self._DISPATCHABLE_STATES

    # ── state machine ────────────────────────────────────────────────────────

    def _current_state(self, name: str) -> SlotState:
        rec = self._states.get(name)
        if rec is None:
            # Try disk.
            rec = read_state(self._state_file(name))
            if rec is None:
                return SlotState.OFFLINE
            self._states[name] = rec
        return rec.state

    async def _transition(
        self,
        name: str,
        to_state: SlotState,
        *,
        model_id: str | None = None,
        port: int = 0,
        message: str = "",
        extra: dict[str, Any] | None = None,
        force: bool = False,
    ) -> SlotStateRecord:
        """Move a slot from its current state to ``to_state``.

        Raises IllegalSlotTransition if the transition is not in
        LEGAL_TRANSITIONS (unless ``force=True``, reserved for error
        recovery paths that need to drop straight to OFFLINE).
        """
        current = self._current_state(name)
        if current == to_state:
            # Idempotent — refresh metadata only.
            pass
        elif not force and not is_transition_legal(current, to_state):
            raise IllegalSlotTransition(
                f"slot {name!r}: illegal transition {current} → {to_state}",
                details={"slot": name, "from": current.value, "to": to_state.value},
            )

        prior = self._states.get(name)
        # Carry prior extras forward (backend / provider stamped at create
        # time should survive starting→warming→ready transitions). Caller-
        # supplied keys override, missing keys inherit.
        carried_extra: dict[str, Any] = dict(prior.extra) if prior else {}
        if extra:
            carried_extra.update(extra)
        effective_model_id = (
            model_id if model_id is not None else (prior.model_id if prior else None)
        )
        # Belt-and-suspenders: never persist READY/SERVING with no model
        # when the provider needs one.  The state.json files on hal0-test
        # showed exactly this shape — state=ready, model_id="" — when
        # adoption + force-restart paths bypassed the normal lifecycle.
        if to_state in (SlotState.READY, SlotState.SERVING) and not effective_model_id:
            provider_hint = (
                carried_extra.get("provider")
                or (extra or {}).get("provider")
                or (prior.extra.get("provider") if prior else None)
            )
            if provider_hint and provider_requires_model(str(provider_hint)):
                log.warning(
                    "slot.modelless_ready_blocked",
                    extra={
                        "slot": name,
                        "from": current.value,
                        "requested": to_state.value,
                        "provider": provider_hint,
                    },
                    stack_info=False,
                )
                to_state = SlotState.IDLE
                carried_extra["modelless_ready_blocked"] = True
        record = SlotStateRecord(
            name=name,
            state=to_state,
            model_id=effective_model_id,
            port=port or (prior.port if prior else 0),
            updated_at=time.time(),
            message=message,
            extra=carried_extra,
        )
        # Persist atomically before broadcasting — readers via state_stream
        # observe state.json on disk after they read the queue (Tier 3).
        write_state_atomic(self._state_file(name), record)
        self._states[name] = record
        log.info(
            "slot.transition", extra={"slot": name, "from": current.value, "to": to_state.value}
        )
        # Structured ERROR audit trail (separate from the info-level
        # transition log) so operators can `journalctl -u hal0-api |
        # grep slot.error` to see every red-dot transition with its
        # cause. Pairs with the event_bus emit below; the bus is
        # transient SSE, this is durable journald.
        if to_state == SlotState.ERROR and current != to_state:
            # NOTE: ``extra=`` MUST NOT use "message" as a key — that's
            # a reserved LogRecord attribute and stdlib logging raises
            # KeyError when it collides. Use ``reason`` instead.
            log.error(
                "slot.error",
                extra={
                    "slot": name,
                    "from": current.value,
                    "reason": message or "(no message)",
                    "model_id": record.model_id or "",
                },
            )
        await self._broadcast(record)
        # Footer event bus — best-effort emit. Skip when current == to_state
        # (idempotent refresh, no real transition) so the footer doesn't
        # show redundant rows.
        if self._event_bus is not None and current != to_state:
            severity = "error" if to_state == SlotState.ERROR else "info"
            payload: dict[str, Any] = {
                "slot": name,
                "from": current.value,
                "to": to_state.value,
            }
            if record.model_id:
                payload["model_id"] = record.model_id
            if message:
                payload["error" if severity == "error" else "message"] = message
            with contextlib.suppress(Exception):
                await self._event_bus.emit(
                    "slot.state",
                    severity,
                    f"slot:{name}",
                    f"{name}: {current.value} → {to_state.value}",
                    data=payload,
                )
        # TIER1: spawn/cancel the push-driven fail-watcher to match the new
        # state.  Done after broadcast so the SSE frame for the transition
        # itself lands before any watcher-induced follow-up frame.
        self._watchdog.update(name, to_state)
        return record

    async def _broadcast(self, record: SlotStateRecord) -> None:
        """Push a record onto every active SSE subscriber queue."""
        dead: list[asyncio.Queue[SlotStateRecord]] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(record)
            except asyncio.QueueFull:
                # Subscriber is too slow — drop it; SSE client will
                # reconnect.  Never block the state machine on a stuck
                # consumer.  TIER1: no swallowed errors elsewhere, but
                # this drop is intentional and logged.
                log.warning("slot.subscriber_dropped", extra={"slot": record.name})
                dead.append(q)
        for q in dead:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    async def state_stream(self) -> AsyncIterator[SlotStateRecord]:
        """Async generator yielding every slot state transition as it happens.

        Used by the SSE endpoint that powers the dashboard's real-time
        slot card updates (PLAN.md §6).  Each subscriber gets its own
        queue; transitions are fan-out broadcast.

        TIER3: Replaces haloai's polling-based status refresh.
        """
        # Buffer size 64 — comfortably larger than the number of expected
        # in-flight transitions across all slots.
        queue: asyncio.Queue[SlotStateRecord] = asyncio.Queue(maxsize=64)
        self._subscribers.append(queue)
        try:
            while True:
                rec = await queue.get()
                yield rec
        finally:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(queue)

    # ── fail-watcher (push-driven failure detector) ──────────────────────────
    #
    # P3-slots §1b-watchdog: logic lives in hal0.slots.watchdog.SlotWatchdog
    # (self._watchdog, constructed in __init__). Every method below is a
    # thin delegator; `_transition`'s tail calls `self._watchdog.update`
    # directly (see above) rather than through `_update_fail_watcher`.

    def _update_fail_watcher(self, name: str, new_state: SlotState) -> None:
        """See :meth:`hal0.slots.watchdog.SlotWatchdog.update`."""
        self._watchdog.update(name, new_state)

    async def _fail_watch_loop(self, slot_name: str) -> None:
        """See :meth:`hal0.slots.watchdog.SlotWatchdog._fail_watch_loop`."""
        await self._watchdog._fail_watch_loop(slot_name)

    async def _is_active(self, slot_name: str) -> bool:
        """See :meth:`hal0.slots.watchdog.SlotWatchdog.is_active`."""
        return await self._watchdog.is_active(slot_name)

    async def _probe_health(self, slot_name: str) -> bool:
        """See :meth:`hal0.slots.watchdog.SlotWatchdog.probe_health`."""
        return await self._watchdog.probe_health(slot_name)

    async def container_readiness_check(self, slot_name: str) -> tuple[bool, str]:
        """See :meth:`hal0.slots.watchdog.SlotWatchdog.readiness_check`."""
        return await self._watchdog.readiness_check(slot_name)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def load(self, slot_name: str, model_id: str | None = None) -> Slot:
        """Load a model into a slot.  Transitions: offline → starting → warming → ready.

        If model_id is None, uses the model assigned in the slot's TOML config.
        """
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        async with self._lock(slot_name):
            cfg = await self._load_slot_config(slot_name)
            resolved_model = model_id or _model_default(cfg)

            current = self._current_state(slot_name)
            if current in (SlotState.READY, SlotState.SERVING, SlotState.IDLE):
                # Already loaded — return snapshot without restarting.
                # Re-register the upstream first (#732): the registry is
                # in-memory and dies with the api process while the
                # container survives, so post-restart a "ready" slot is
                # unroutable. Loading a ready slot must restore the
                # route, not silently no-op. Idempotent via upsert.
                if not is_npu_trio_shadow(cfg):
                    port = _cfg_port(cfg)
                    if port:
                        self._register_container_upstream(slot_name, port)
                return await self.status(slot_name)

            # Configuration check: a slot with no resolvable model is
            # NOT an ERROR (which would render red and flag for operator
            # investigation). It's an unconfigured slot — render grey
            # with a CTA. Bail before dispatching the load, whose
            # ValueError would otherwise stamp the slot ERROR every
            # tick the reconciler runs. The user fixes it by picking a
            # model in the dashboard dropdown; Fix #1 persists the
            # choice to TOML so the slot never re-enters this branch.
            if not resolved_model:
                await self._transition(
                    slot_name,
                    SlotState.OFFLINE,
                    port=_cfg_port(cfg),
                    message="no default model — pick one from the dropdown",
                    force=True,
                )
                return await self.status(slot_name)

            # Seed/default may pin a model id that never landed locally under
            # that exact id (e.g. a catalog id like ``gemma-4-12b-it`` while the
            # scanned gguf registered as ``gemma-4-12b-it-ud-q4-k-xl``). Rather
            # than crash-loop on a non-servable id, fall back to a locally
            # registered model matching the slot's capability. No-op for FLM/NPU
            # (tag-served), already-local ids, and pullable catalog ids.
            resolved_model = self._resolve_servable_model(resolved_model, cfg)

            # NPU FLM trio shadow (stt/embed, device=npu): the chat anchor's
            # single FLM process serves these via the anchor's [npu] toggles.
            # They are NOT independently loadable on the busy single-tenant
            # NPU. Treat as a read-only
            # shadow of the anchor: skip both the spawn and the readiness probe
            # (which targets this slot's own — non-existent — child port) and
            # mark READY. The /api/slots enrichment derives the live shadow
            # state from the anchor; trio inference requests are routed to the
            # anchor's FLM process by the dispatcher, not to this slot's port.
            if is_npu_trio_shadow(cfg):
                await self._transition(
                    slot_name,
                    SlotState.READY,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                    message="served by NPU FLM anchor (trio shadow)",
                    force=True,
                )
                return await self.status(slot_name)

            try:
                # PULLING — gate the model download behind an explicit
                # state so dashboards can show "downloading model"
                # separately from "container starting".  If the model is
                # already on disk (or no pull hook is wired), skip
                # straight to STARTING — both edges are legal.
                if resolved_model and self._needs_pull(resolved_model):
                    await self._transition(
                        slot_name,
                        SlotState.PULLING,
                        model_id=resolved_model,
                        port=_cfg_port(cfg),
                    )
                    assert self._pull_runner is not None  # _needs_pull guards
                    await self._pull_runner(resolved_model)
                await self._transition(
                    slot_name,
                    SlotState.STARTING,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                )
                await self._spawn_locked(slot_name, cfg, resolved_model)
                await self._transition(
                    slot_name,
                    SlotState.WARMING,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                )
                # _await_ready returns READY when the upstream has a
                # model loaded and serves inference, or IDLE when the
                # process is up but ``/v1/models`` is empty (issue #31:
                # llama-server --model "" lands here). Either is a
                # successful load — callers downstream pick READY slots
                # for routing and IDLE slots for "ready to accept a
                # model" UX.
                resolved_state = await self._await_ready(slot_name, _cfg_port(cfg))
                await self._transition(
                    slot_name,
                    resolved_state,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                )
                # Persist explicit model_id to TOML so reconciliation
                # after an api restart doesn't drift back to "no
                # model.default" ERROR. Only fires when caller passed
                # model_id (i.e. swap() / explicit /load body), not on
                # plain reload of the existing default. Best-effort:
                # a write failure is logged but doesn't fail the load —
                # the slot is already running with the right model.
                if model_id and model_id != _model_default(cfg):
                    try:
                        await self._persist_model_default(slot_name, model_id)
                    except Exception as exc:
                        log.warning(
                            "slot.persist_model_default_failed",
                            extra={
                                "slot": slot_name,
                                "model_id": model_id,
                                "error": str(exc),
                            },
                        )
            except Exception as exc:
                # TIER1: never swallow — record ERROR with details, re-raise.
                await self._transition(
                    slot_name,
                    SlotState.ERROR,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                    message=str(exc),
                    force=True,
                )
                raise
            return await self.status(slot_name)

    async def unload(self, slot_name: str) -> Slot:
        """Gracefully unload a slot.  Transitions: → unloading → offline."""
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        async with self._lock(slot_name):
            current = self._current_state(slot_name)
            if current == SlotState.OFFLINE:
                return await self.status(slot_name)
            try:
                await self._transition(slot_name, SlotState.UNLOADING, force=True)
                await self.terminate(slot_name)
                await self._transition(slot_name, SlotState.OFFLINE, force=True)
            except Exception as exc:
                await self._transition(
                    slot_name,
                    SlotState.ERROR,
                    message=str(exc),
                    force=True,
                )
                raise
            self._last_used.pop(slot_name, None)
            return await self.status(slot_name)

    async def restart(self, slot_name: str) -> Slot:
        """Restart a slot without changing its model assignment.

        A slot wedged in ERROR must NOT go through the graceful ``unload()``
        drain: its systemd unit may be ``failed``, where the stop path can
        hang and leave the CLI ReadTimeout'ing with the unit never relaunched
        (#1224). For an errored slot, force a best-effort terminate (which now
        also ``reset-failed``s the unit) and drop straight to OFFLINE, then run
        the full load — never short-circuiting on an "already loaded" state,
        which ERROR is not.
        """
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        if self._current_state(slot_name) == SlotState.ERROR:
            async with self._lock(slot_name):
                # Best-effort cleanup of the wedged unit; never let a stuck
                # stop wedge the restart itself. ``terminate`` resets the
                # failed unit so the subsequent ``load`` isn't blocked by
                # systemd's StartLimit.
                with contextlib.suppress(Exception):
                    await self.terminate(slot_name)
                await self._transition(slot_name, SlotState.OFFLINE, force=True)
            return await self.load(slot_name)
        await self.unload(slot_name)
        return await self.load(slot_name)

    async def start(self, slot_name: str) -> Slot:
        """Idempotent start.  Equivalent to load() when slot is offline.

        Mirrors haloai's slots.start() (lib/slots.py:644) so callers like
        the dispatcher wake-on-request path can share the contract.
        """
        slot_name = self._resolve_alias(slot_name)
        current = self._current_state(slot_name)
        if current in (SlotState.READY, SlotState.SERVING, SlotState.IDLE):
            self.bump_last_used(slot_name)
            return await self.status(slot_name)
        return await self.load(slot_name)

    async def swap(self, slot_name: str, new_model_id: str) -> Slot:
        """Hot-swap a slot's model: unload current, load new (container restart)."""
        if not new_model_id:
            raise SlotConfigError("swap requires a non-empty model id")
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        # Q1 (model profiles): adopt the new model's preferred profile — when it
        # fits the slot — BEFORE the reload, so the container comes up on the
        # right image. A no-op when the model has no preference or it doesn't
        # fit; a write failure must not block the swap, so it soft-fails.
        try:
            await self._apply_preferred_profile(slot_name, new_model_id)
        except SlotConfigError as exc:
            log.warning(
                "slot.preferred_profile_swap_failed",
                extra={"slot": slot_name, "model_id": new_model_id, "error": str(exc)},
            )
        # §7.1b / ML-4: adopt the new model's preferred runner image — same
        # soft-fail contract as the preferred-profile hook above (a write
        # failure must not block the swap; the dynamic model_info tier in
        # _resolve_image_ref means the right image still launches even if
        # this persist is skipped).
        try:
            await self._apply_preferred_runner(slot_name, new_model_id)
        except SlotConfigError as exc:
            log.warning(
                "slot.preferred_runner_swap_failed",
                extra={"slot": slot_name, "model_id": new_model_id, "error": str(exc)},
            )
        # MTP defuse: a forced mtp=true pointing at a model with no MTP heads
        # makes llama-server exit at load ("model doesn't contain MTP layers"),
        # so a swap onto an ineligible model clears exactly that override
        # (→ AUTO). Force-off and force-on-for-eligible survive swaps; an
        # unresolvable model is left alone (can't judge). Soft-fails like the
        # preferred-profile hook — a write failure must not block the swap.
        try:
            await self._defuse_stale_mtp_on_swap(slot_name, new_model_id)
        except Exception as exc:
            log.warning(
                "slot.mtp_defuse_swap_failed",
                extra={"slot": slot_name, "model_id": new_model_id, "error": str(exc)},
            )
        await self.unload(slot_name)
        slot = await self.load(slot_name, model_id=new_model_id)
        # Refresh Hermes's live-context files so a model swap is visible to
        # the agent on its next session (detached; never blocks the swap).
        from hal0.agents.hermes_refresh import spawn_context_refresh

        spawn_context_refresh()
        return slot

    # ── queries ──────────────────────────────────────────────────────────────

    async def status(self, slot_name: str, *, include_config_drift: bool = False) -> Slot:
        """Return a snapshot of the current slot state.

        Combines the persisted state.json with a live "is the container
        unit active?" probe. Reconciliation runs in both directions:

          - state.json says READY/SERVING/IDLE but the unit is inactive
            → transition to OFFLINE so the dashboard reflects reality.
          - state.json says OFFLINE / ERROR (or is missing) but the
            unit is active → adopt the running slot into READY. Covers
            the case where another process started the unit
            out-of-band.
        """
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        rec = self._states.get(slot_name) or read_state(self._state_file(slot_name))
        active = await self._is_active(slot_name)
        if rec is None:
            # No state.json yet — but the TOML may exist (configured slot
            # that hasn't been loaded). Synthesize an OFFLINE snapshot
            # carrying the on-disk backend/provider so the dashboard chips
            # render correctly before the first load.
            cfg = await self._maybe_load_config(slot_name)
            # ISSUE #30: if the TOML exists AND the unit is somehow
            # already running, run an adoption probe before returning
            # OFFLINE.  Without this, a slot started by an external
            # orchestrator never surfaces as ready in /api/slots.
            if active and cfg:
                adopted = await self._maybe_adopt_running_slot(slot_name, cfg)
                if adopted is not None:
                    return adopted
            # W3: surface the EFFECTIVE backend (derived from ``device``),
            # not the stale legacy ``backend`` TOML field — see
            # ``_cfg_effective_backend``.
            eff_backend = _cfg_effective_backend(cfg) if cfg else None
            return Slot(
                name=slot_name,
                state=SlotState.OFFLINE,
                port=int(cfg.get("port") or 0) if cfg else 0,
                backend=eff_backend,
                metadata={
                    "provider": cfg.get("provider"),
                    "backend": eff_backend,
                }
                if cfg
                else {},
            )
        # Reconcile with unit reality.
        observed = rec.state
        if observed in (SlotState.READY, SlotState.SERVING, SlotState.IDLE) and not active:
            # The unit is inactive; record reflects ready. This is drift
            # but NOT a slot-config error — units stop legitimately (GPU
            # arbiter handoff, systemd stop, idle policies) and the
            # dispatcher lazy-loads on the next request. Surface as
            # OFFLINE so the card chip shows the neutral "not loaded"
            # state rather than red ERROR.
            await self._transition(
                slot_name,
                SlotState.OFFLINE,
                message="container stopped (auto-reloads on next request)",
                force=True,
            )
            observed = SlotState.OFFLINE
        elif observed in (SlotState.OFFLINE, SlotState.ERROR) and active:
            # Inverse drift — state.json says we're not running, but
            # the unit is active. Adoption picks the slot up.
            cfg = await self._maybe_load_config(slot_name)
            if cfg:
                adopted = await self._maybe_adopt_running_slot(slot_name, cfg)
                if adopted is not None:
                    return adopted
        # W3 truth fix: the displayed ``backend`` must equal the EFFECTIVE
        # backend that will actually run — i.e. the token derived from the
        # slot's authoritative ``device`` field. We deliberately
        # do NOT trust ``rec.extra.get("backend")`` here: that mirror is
        # seeded at create-time and drifts the instant a user flips backend
        # via POST /api/slots/{name}/backend (which rewrites ``device`` only)
        # or whenever it predates the device migration. Deriving from the
        # live TOML ``device`` means declared-vs-actual can never silently
        # thrash to a stale seeded default. Fall back to the carried extra
        # only when the TOML is unreadable, so the chip degrades gracefully
        # rather than showing 'unknown'.
        cfg = await self._maybe_load_config(slot_name)
        backend = _cfg_effective_backend(cfg) if cfg else None
        if backend is None:
            backend = rec.extra.get("backend")
        # Surface the truthful value in both the top-level field and the
        # metadata mirror; override any stale ``extra.backend`` so the
        # dashboard never reads the seeded token out of metadata.
        meta = {
            "updated_at": rec.updated_at,
            "message": rec.message,
            **rec.extra,
        }
        if backend:
            meta["backend"] = backend
        if include_config_drift:
            config_drift = await self.compute_config_drift(slot_name, cfg=cfg, active=active)
            if config_drift is not None:
                meta["config_drift"] = config_drift
        return Slot(
            name=slot_name,
            state=observed,
            port=rec.port,
            model_id=rec.model_id,
            backend=backend,
            metadata=meta,
            last_used_at=self._last_used.get(slot_name),
        )

    async def compute_config_drift(
        self,
        slot_name: str,
        *,
        cfg: dict[str, Any] | None = None,
        active: bool | None = None,
    ) -> dict[str, Any] | None:
        """See :func:`hal0.slots.drift.compute_config_drift`.

        Kept (not deleted) — P3-slots §6 investigation found a live drift
        source (see slots/drift.py module docstring): a slot can run stale
        argv after a TOML edit without a restart, which
        ``test_real_drift_still_detected_across_spellings`` exercises.
        """
        return await _compute_config_drift(self, slot_name, cfg=cfg, active=active)

    async def _maybe_load_config(self, slot_name: str) -> dict[str, Any] | None:
        """Read the slot's TOML if it exists, swallowing parse errors.

        Used by ``status()`` to re-hydrate the top-level ``backend`` field
        on snapshots whose state.json predates the extras-carry change.
        Returns ``None`` when the TOML is missing or invalid — callers
        treat that as "no override available" rather than a hard failure.
        """
        path = self._config_file(slot_name)
        if not path.exists():
            return None
        try:
            return await self._load_slot_config(slot_name)
        except SlotConfigError:
            # Don't let a malformed slot TOML take out the status snapshot —
            # /api/slots is supposed to be best-effort. The error will
            # surface elsewhere (load/start/restart paths re-raise).
            return None

    async def list(self) -> list[Slot]:
        """Return snapshots for all configured slots, concurrently."""
        names = self._all_configured_slot_names()
        # Slots that only exist in memory (test injection) also show up.
        for n in self._states:
            if n not in names:
                names.append(n)
        if not names:
            return []
        return list(await asyncio.gather(*(self.status(n) for n in names)))

    async def iter_configs(self) -> list[dict[str, Any]]:
        """Return raw slot config dicts for every configured slot.

        Lightweight — reads TOML only, no live probes. Intended
        for startup hooks (e.g. ``lifespan`` auto-registering slots as
        upstreams) that need slot metadata before any real lifecycle
        interaction.  Also the routing hot path
        (:meth:`resolve_for_request` calls this twice per request):
        parses are served from the per-file mtime cache in
        :meth:`_load_slot_config`, and the directory glob per call keeps
        added/removed slot TOMLs visible immediately.

        Returns:
            One dict per slot, in stable order. Each dict carries at
            least ``name`` and ``port``; the rest of the SlotConfig
            shape (``backend``, ``provider``, …) round-trips verbatim.
        """
        out: list[dict[str, Any]] = []
        for name in self._all_configured_slot_names():
            try:
                cfg = await self._load_slot_config(name)
            except SlotConfigError as exc:
                log.warning(
                    "slot.config_skipped",
                    extra={"slot": name, "error": str(exc)},
                )
                continue
            out.append(cfg)
        return out

    # ── PR-10: seeded slot catalogue + routing helpers ──────────────────────
    #
    # P3-slots §1i: the actual logic lives in hal0.slots.routing (pure
    # config-query, no state-machine coupling). Every method below is a
    # thin delegator so the public surface (and every existing caller /
    # test) is unchanged.

    @staticmethod
    def seeded_slots(*, include_npu: bool | None = None) -> tuple[str, ...]:
        """See :func:`hal0.slots.routing.seeded_slots`."""
        return _routing_seeded_slots(include_npu=include_npu)

    async def default_slot_for(self, slot_type: str) -> str | None:
        """See :func:`hal0.slots.routing.default_slot_for`."""
        return await _routing_default_slot_for(self, slot_type)

    def _loaded_slot_from_config(self, cfg: dict[str, Any]) -> LoadedSlot | None:
        """See :func:`hal0.slots.routing.loaded_slot_from_config`."""
        return loaded_slot_from_config(cfg)

    async def loaded_slot(self, name: str) -> LoadedSlot | None:
        """See :func:`hal0.slots.routing.loaded_slot`."""
        return await _routing_loaded_slot(self, name)

    async def resolve_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> LoadedSlot | None:
        """See :func:`hal0.slots.routing.resolve_for_request`."""
        return await _routing_resolve_for_request(self, slot_type, required_labels=required_labels)

    async def route_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> str | None:
        """See :func:`hal0.slots.routing.route_for_request`."""
        return await _routing_route_for_request(self, slot_type, required_labels=required_labels)

    async def add_slot(
        self,
        name: str,
        *,
        type: str,
        model: str,
        device: str = "gpu-rocm",
        port: int = 8081,
    ) -> Slot:
        """See :func:`hal0.slots.routing.add_slot`."""
        return await _routing_add_slot(self, name, type=type, model=model, device=device, port=port)

    async def remove_slot(self, name: str) -> None:
        """See :func:`hal0.slots.routing.remove_slot`."""
        await _routing_remove_slot(self, name)

    # ── low-level lifecycle ──────────────────────────────────────────────────

    async def spawn(self, slot_name: str, slot_cfg: SlotConfig | dict[str, Any]) -> Slot:
        """Low-level: render + start this slot's container unit.

        Called by load() after the model is confirmed present in the
        registry. Public for tests + the installer's first-run path.
        Acquires the per-slot lock; ``load()``'s callers can use
        ``_spawn_locked`` directly.
        """
        async with self._lock(slot_name):
            await self._spawn_locked(slot_name, slot_cfg, _model_default(slot_cfg))
        return await self.status(slot_name)

    async def _spawn_locked(
        self,
        slot_name: str,
        slot_cfg: SlotConfig | dict[str, Any],
        model_id: str | None,
    ) -> None:
        """Spawn body — caller already holds the per-slot lock.

        Dispatches through :class:`ContainerProvider`: writes the
        ``hal0-slot@<name>`` unit and starts it.

        ``model_id`` (when set) overrides the slot config's
        ``model.default`` for swap semantics.

        Any exception is let through as-is; the calling ``load()``
        ``except Exception -> ERROR`` branch records a stable error envelope.
        """
        model_info = await self._resolve_model_info(model_id)

        cfg = _cfg_to_dict(slot_cfg)
        if model_id:
            existing_model = cfg.get("model")
            base_model = existing_model if isinstance(existing_model, dict) else {}
            cfg = {**cfg, "model": {**base_model, "default": model_id}}

        # Container path: write + start the podman systemd unit.
        from hal0.providers.container import container_provider

        port = int(cfg.get("port", 0))
        await asyncio.get_event_loop().run_in_executor(
            None, container_provider().load_sync, cfg, model_info
        )
        # Register loopback upstream so the dispatcher can route to this slot.
        self._register_container_upstream(slot_name, port)

    async def terminate(self, slot_name: str, *, timeout_s: float = 30.0) -> None:
        """Stop the slot's container unit and deregister its upstream.

        Idempotent — stopping an already-stopped unit is a no-op.

        Public because callers that need to release VRAM directly can
        do so without going through ``unload()``'s state-machine
        ceremony. ``timeout_s`` is preserved in the signature for
        caller compatibility; the systemd stop is synchronous.
        """
        cfg = await self._maybe_load_config(slot_name)
        # Resilient to the slot config being missing — terminate should
        # never fail just because someone deleted the TOML between load
        # and unload. Synthesise an empty cfg so the provider's
        # no-model-to-unload branch fires.
        if cfg is None:
            cfg = {"name": slot_name}

        # Stop the systemd unit + deregister upstream.
        from hal0.providers.container import container_provider

        await asyncio.get_event_loop().run_in_executor(
            None, container_provider().unload_sync, _cfg_to_dict(cfg)
        )
        self._deregister_container_upstream(slot_name)

    # ── slot CRUD ────────────────────────────────────────────────────────────

    async def create(
        self,
        slot_name: str,
        slot_cfg: SlotConfig | dict[str, Any],
    ) -> Slot:
        """Create a new dynamic slot's persistent on-disk state.

        Writes ``/etc/hal0/slots/<name>.toml`` and an initial
        ``state.json`` (OFFLINE). Does NOT start the slot — that's
        ``load()``'s job.

        Does not render the systemd unit — that happens on first
        ``load()``. The TOML is the only on-disk artefact at create
        time.

        PR-11 (plan §5.3): rejects a second ``device=npu,
        type=llm, enabled=true`` slot — the AMDXDNA hardware context
        admits exactly one NPU LLM at a time. Disabled NPU LLM slots
        coexist; only the live anchor count is bounded.

        TOML serialisation routes through
        :func:`hal0.slot_config.write_slot_toml` — the single
        slots/*.toml write path (issue #697).
        """
        cfg_dict = _cfg_to_dict(slot_cfg)
        # #585: canonicalize a ctx_size alias from the create modal too —
        # same single fold (:func:`hal0.slot_config.fold_ctx_size_alias`)
        # the merge/update path uses.
        fold_ctx_size_alias(cfg_dict)
        # Persist a concrete context window when the operator left it unset, so
        # the TOML, the dashboard, and the running container all agree. The
        # provider's load-path derive is the belt-and-suspenders fallback; this
        # makes the chosen window visible at create time (chat@4096 incident).
        model_tbl = cfg_dict.get("model")
        if isinstance(model_tbl, dict) and model_tbl.get("context_size") is None:
            from hal0.providers.container import _resolve_context_size

            model_info = await self._resolve_model_info(model_tbl.get("default"))
            model_tbl["context_size"] = _resolve_context_size(None, model_info)
        # Q1 (model profiles): a new slot bound to a model but with NO explicit
        # profile adopts the model's preferred profile when it fits the slot's
        # device/type. An operator's explicit create-time profile is left
        # untouched (only an empty profile is filled) — the reconcile below then
        # validates device/profile coherence as usual.
        if isinstance(model_tbl, dict) and model_tbl.get("default") and not cfg_dict.get("profile"):
            preferred = await self._preferred_profile_for(model_tbl.get("default"))
            if preferred and self._profile_fits_slot(preferred, cfg_dict):
                cfg_dict["profile"] = preferred
        # §7.1b / ML-4: mirrors the profile adoption immediately above — a
        # new slot bound to a model but with NO explicit image override
        # adopts the model's preferred runner's image when it fits the
        # slot's device/backend. An operator's explicit create-time image
        # is left untouched (only an empty image is filled).
        if isinstance(model_tbl, dict) and model_tbl.get("default") and not cfg_dict.get("image"):
            preferred_runner = await self._preferred_runner_for(model_tbl.get("default"))
            if preferred_runner and self._runner_fits_slot(preferred_runner, cfg_dict):
                from hal0.runners import get_runner, resolve_runner_image

                cfg_dict["image"] = resolve_runner_image(get_runner(preferred_runner))
        # Reject (or normalize) an incoherent device/profile backend pairing
        # before it ever lands on disk — the door the dashboard left open for
        # the utility slot (vulkan device + rocm-dnse profile). Every field is
        # "new" at create time, so a conflicting device+profile is an explicit
        # operator error and raises.
        _reconcile_device_profile(cfg_dict, set(cfg_dict.keys()))
        await self._check_npu_exclusivity(slot_name, cfg_dict)
        # SC-4: refuse a second default=true slot of the same type before
        # the TOML lands on disk (belt to default_slot_for's routing-time
        # suspenders). Fast fast path when this write isn't a new default.
        await self._check_default_uniqueness(slot_name, cfg_dict)
        cfg_path = self._config_file(slot_name)
        # TOCTOU fix: the exists-check + write below used to run unlocked, so
        # two concurrent create() calls (all callers are async — api routes,
        # orchestrator, stacks _create_missing_slots) could both pass the
        # check and the loser clobbered the winner's TOML. Serialize
        # in-process on the per-slot asyncio lock and cross-process on the
        # coarse slot-TOML file lock so the SC-5 guard is race-free.
        async with self._lock(slot_name):
            with slot_write_lock():
                # SC-5: refuse to clobber an existing slot's config. Without
                # this, a duplicate create() overwrote the on-disk TOML and
                # force-reset state.json to OFFLINE, orphaning any running
                # container. Most internal reconcile callers pre-check
                # cfg_path.exists() before create() (orchestrator, backends,
                # stacks._create_missing_slots), so they never reach this
                # guard; install/orchestrate does NOT pre-check but wraps
                # create() in a best-effort except, so a duplicate degrades
                # to a per-slot error rather than a crash. add_slot and
                # POST /api/slots surface the conflict to the caller.
                if cfg_path.exists():
                    raise SlotConfigError(
                        f"slot {slot_name!r} already exists; use update to modify it",
                        details={"slot": slot_name, "config": str(cfg_path)},
                    )
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    write_slot_toml(cfg_path, cfg_dict)
                except OSError as exc:
                    raise SlotConfigError(
                        f"failed to write slot config {cfg_path}: {exc}",
                        details={"slot": slot_name},
                    ) from exc
                self._invalidate_cfg_cache(slot_name)

            # Initialise state.
            await self._transition(
                slot_name,
                SlotState.OFFLINE,
                port=_cfg_port(cfg_dict),
                model_id=_model_default(cfg_dict) or None,
                extra={
                    # W3: seed the device-derived effective backend, not a
                    # hardcoded "vulkan" default — the slot's ``device`` is what
                    # will actually run. ``status()`` re-derives from the TOML on
                    # every read, so this is only a fallback, but keeping it
                    # honest avoids a transient lie before the first status call.
                    "backend": _cfg_effective_backend(cfg_dict) or "vulkan",
                    "provider": cfg_dict.get("provider", "llama-server"),
                },
                force=True,
            )
        return await self.status(slot_name)

    async def delete(self, slot_name: str, *, force: bool = False) -> None:
        """Delete a slot. Seeded + pinned slots are protected unless ``force=True``.

        A seeded slot (``primary`` / ``embed`` / … + the NPU trio) is normally
        undeletable — disable it via ``capabilities.toml`` instead. ``force``
        overrides that guard so an operator can remove a seeded slot outright;
        note an install/update reconcile may re-seed it later, and the name stays
        reserved (``create`` still rejects it) until then.

        §21.10 operator-pin hardening: a pinned slot (default-pinned anchor
        or ``SlotConfig.pinned = true``) is likewise refused without
        ``force=True`` — HTTP 409 ``slot.pinned`` — so an accidental delete
        can't take down an always-warm anchor.
        """
        slot_name = self._resolve_alias(slot_name)
        if not force:
            if slot_name in self.seeded_slots():
                raise SlotConfigError(
                    f"cannot delete seeded slot {slot_name!r} — disable it via "
                    "capabilities.toml, or pass force to delete it anyway",
                    details={"slot": slot_name, "seeded": True},
                )
            if await self.is_pinned(slot_name):
                raise SlotPinned(
                    f"slot {slot_name!r} is pinned — pass force=true to delete it anyway",
                    details={"slot": slot_name, "pinned": True},
                )
        self._ensure_known(slot_name)
        # Make sure it's stopped first.
        current = self._current_state(slot_name)
        if current != SlotState.OFFLINE:
            await self.unload(slot_name)

        # Remove state.json and the slot config.
        for path in (
            self._state_file(slot_name),
            self._config_file(slot_name),
        ):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        # Drop in-memory bookkeeping last.
        self._states.pop(slot_name, None)
        self._locks.pop(slot_name, None)
        self._last_used.pop(slot_name, None)
        self._invalidate_cfg_cache(slot_name)

    async def update_config(
        self,
        slot_name: str,
        updates: dict[str, Any],
    ) -> Slot:
        """Apply partial updates to a slot's TOML.

        Rewriting the TOML is enough — the container unit is re-rendered
        from it on the next load/restart.
        """
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        cfg_path = self._config_file(slot_name)
        # The whole read→merge→guard→write below is one cross-process
        # critical section (slot_write_lock): a concurrent CLI / stacks /
        # capabilities writer can no longer interleave its own RMW and drop
        # this update. Everything awaited inside is sync at heart (plain
        # file IO) so the event loop never actually suspends while the
        # advisory lock is held — do not introduce real awaits here.
        with slot_write_lock():
            cfg = await self._load_slot_config(slot_name)
            cfg_dict = _cfg_to_dict(cfg)
            # One-level deep merge for nested TOML tables ([model], [server])
            # plus the #585 ctx_size→context_size fold and device↔profile
            # backend coherence, via the shared ``reconcile_slot_updates``
            # pipeline — the SAME projection the SlotConfigStore and the
            # stacks apply engine use, so the writers can't silently diverge.
            #
            # A bare shallow ``dict.update`` replaced a sub-table wholesale,
            # so a partial ``PATCH /defaults`` body like ``{"model":
            # {"ctx_size": N}}`` silently dropped sibling keys — most
            # damagingly ``[model].default`` (the model name). The merge
            # touches only the fields the update carries; scalars and lists
            # still replace wholesale. Device/profile coherence: a profile
            # switch re-derives device (the drawer path that previously left
            # a vulkan device under a rocm-dnse profile), a cross-backend
            # device flip re-points the profile (the POST /backend path,
            # which writes device only), and an explicit conflicting pair
            # raises. Only the field(s) the caller changed drive
            # reconciliation. Returns a fresh dict — rebind ``cfg_dict``.
            cfg_dict = reconcile_slot_updates(cfg_dict, updates)

            # PR-11: re-run the NPU exclusivity guard whenever the merged
            # config could land a second device=npu, type=llm anchor (plan
            # §5.3). Cheap when no NPU LLM is involved — the helper short-
            # circuits on the merged cfg's own device/type.
            await self._check_npu_exclusivity(slot_name, cfg_dict)

            # SC-4: a PATCH that flips default=true (even when ``type`` lives
            # only on the pre-existing config) is checked against the merged
            # cfg_dict — the authoritative post-merge state, same as the NPU
            # guard. The peer walk excludes slot_name, so re-persisting the
            # sole default never self-conflicts.
            await self._check_default_uniqueness(slot_name, cfg_dict)

            try:
                write_slot_toml(cfg_path, cfg_dict)
            except OSError as exc:
                raise SlotConfigError(
                    f"failed to rewrite {cfg_path}: {exc}",
                ) from exc
            self._invalidate_cfg_cache(slot_name)

        # Issue #359: invalidate stale top-level metadata in state.json
        # whenever the operator's update changes a field that's also
        # carried in ``extra``. ``_transition()`` shallow-merges extras
        # (intentional — provider/backend stamped at create-time survive
        # start→warm→ready), so without an explicit fix here the persisted
        # ``extra.backend`` survives a ``POST /api/slots/{name}/backend``
        # forever. ``status()`` short-circuits to this stale value as
        # long as the unit stays active (the adoption probe never
        # re-runs once ``rec`` exists).
        #
        # Only touch keys the caller actually changed — leave the rest of
        # ``extra`` (adopted flag, modelless_ready_blocked, etc.) alone.
        rec = self._states.get(slot_name) or read_state(self._state_file(slot_name))
        if rec is not None:
            mirrored = {"backend", "provider"}
            dirty = mirrored & updates.keys()
            new_extra = dict(rec.extra)
            for key in dirty:
                new_extra[key] = cfg_dict.get(key)
            # W3: a ``device`` change (the canonical path —
            # POST /api/slots/{name}/backend rewrites ``device`` only, never
            # ``backend``) must re-derive the mirrored ``extra.backend`` token
            # too, or state.json keeps advertising the stale seeded backend
            # forever. Without this the chip thrashes back to the old value
            # on the next status read that trusts the mirror. A ``profile``
            # change can also move the effective backend now (it re-derives
            # ``device`` via _reconcile_device_profile), so refresh the mirror
            # for either trigger off the reconciled cfg.
            if "device" in updates or "profile" in updates:
                eff = _cfg_effective_backend(cfg_dict)
                if eff is not None:
                    new_extra["backend"] = eff
                    dirty = dirty | {"backend"}
            if dirty:
                refreshed = SlotStateRecord(
                    name=rec.name,
                    state=rec.state,
                    model_id=rec.model_id,
                    port=rec.port,
                    updated_at=time.time(),
                    message=rec.message,
                    extra=new_extra,
                )
                write_state_atomic(self._state_file(slot_name), refreshed)
                self._states[slot_name] = refreshed

        # #599 follow-up: ``[image].idle_restore_minutes`` is read into the
        # GpuArbiter once at lazy construction, so a config write alone left
        # the live idle-restore window stale until the next api restart — the
        # Settings control silently under-delivered. Push the new value into
        # the already-constructed arbiter (if any) so the idle loop's next
        # tick honours it immediately. Fail-soft: the arbiter reads
        # ``idle_restore_minutes`` fresh each tick, so a bad value can't wedge
        # it, and an absent arbiter picks the value up at construction anyway.
        if self._arbiter is not None and "image" in updates:
            from hal0.slots.arbiter import gpu_exclusive_group

            if gpu_exclusive_group(cfg_dict) == "img":
                image = cfg_dict.get("image") or cfg_dict.get("image_gen") or {}
                val = image.get("idle_restore_minutes") if isinstance(image, dict) else None
                if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
                    if val != self._arbiter.idle_restore_minutes:
                        log.info(
                            "gpu_arbiter.idle_restore_minutes_hot_reload",
                            extra={
                                "slot": slot_name,
                                "old": self._arbiter.idle_restore_minutes,
                                "new": val,
                            },
                        )
                    self._arbiter.idle_restore_minutes = val

        return await self.status(slot_name)

    async def reconcile_unconfigured_slots(self) -> None:
        """One-shot startup pass: clear stuck ERROR on unconfigured slots.

        Before the empty-default short-circuit in :meth:`load`, slots
        with no ``model.default`` would get stamped ERROR every time
        the reconciler dispatched a load with an empty model name.
        Existing state.json snapshots from that era persist the red
        dot even after this fix lands. This pass rewrites them to
        OFFLINE with a "pick a model" message so the dashboard
        re-renders correctly without requiring the operator to click
        each slot.

        Best-effort — failures are logged and don't block startup.

        Reads in-memory state + state.json directly. Deliberately
        avoids :meth:`list` (which would trigger adoption probes) and
        :meth:`_maybe_adopt_running_slot` (which would flip slots to
        READY without a load) — this pass is a state-machine cleanup,
        not a fresh status check.
        """
        # Walk slot configs on disk. Hydrate state.json into _states
        # the same way _current_state does, but without going through
        # status() (no adoption probes).
        try:
            slot_dir = paths.slots_config_dir()
            cfg_files = sorted(slot_dir.glob("*.toml")) if slot_dir.exists() else []
        except OSError as exc:
            log.warning(
                "slot.reconcile_unconfigured_dir_failed",
                extra={"error": str(exc)},
            )
            return
        for cfg_path in cfg_files:
            slot_name = cfg_path.stem
            try:
                rec = self._states.get(slot_name) or read_state(self._state_file(slot_name))
                if rec is None or rec.state != SlotState.ERROR:
                    continue
                msg = (rec.message or "").lower()
                # Cache the hydrated record so _transition compares
                # against the right baseline.
                self._states[slot_name] = rec
                # Pre-fix "no model.default set" ERRORs → OFFLINE+CTA.
                if "no model.default set" in msg:
                    cfg = await self._maybe_load_config(slot_name)
                    if cfg is not None and _model_default(cfg):
                        # TOML now has a default — leave the ERROR
                        # alone so the operator sees that something
                        # else went wrong.
                        continue
                    await self._transition(
                        slot_name,
                        SlotState.OFFLINE,
                        message="no default model — pick one from the dropdown",
                        force=True,
                    )
                    continue
            except Exception as exc:
                log.warning(
                    "slot.reconcile_unconfigured_failed",
                    extra={"slot": slot_name, "error": str(exc)},
                )

    async def reconcile_npu_trio_slots(self) -> int:
        """See :func:`hal0.slots.npu.trio.reconcile_trio_slots`."""
        return await _npu_reconcile_trio_slots(self)

    async def _persist_model_default(self, slot_name: str, model_id: str) -> None:
        """Write ``[model] default = <model_id>`` into the slot's TOML.

        Preserves every other key — only the ``model.default`` field is
        rewritten. Used by :meth:`load` after a successful explicit-
        model load (i.e. swap path) so the next reconciliation pass
        reads the right default instead of drifting back to the empty
        seed value that produced the "no model.default set" ERROR.

        Atomic via :func:`hal0.slot_config.write_slot_toml` — the single
        slots/*.toml write path (issue #697). Failures bubble up so the
        caller can log + soft-fail without affecting the live load state.
        """
        # Cross-process RMW guard: read + rewrite under the shared slot-TOML
        # lock so a concurrent update_config / stacks apply can't be dropped.
        with slot_write_lock():
            cfg = await self._load_slot_config(slot_name)
            cfg_dict = _cfg_to_dict(cfg)
            existing_model = cfg_dict.get("model")
            base_model = existing_model if isinstance(existing_model, dict) else {}
            cfg_dict = {**cfg_dict, "model": {**base_model, "default": model_id}}

            cfg_path = self._config_file(slot_name)
            try:
                write_slot_toml(cfg_path, cfg_dict)
            except OSError as exc:
                raise SlotConfigError(
                    f"failed to persist model.default to {cfg_path}: {exc}",
                    details={"slot": slot_name, "model_id": model_id},
                ) from exc
            self._invalidate_cfg_cache(slot_name)

    # ── model preferred profile (Q1: profile loads with the model) ────────────
    #
    # P3-slots §1g: logic lives in hal0.slots.profile_adopt; every method
    # below is a thin delegator.

    async def _preferred_profile_for(self, model_id: str | None) -> str | None:
        """See :func:`hal0.slots.profile_adopt.preferred_profile_for`."""
        return await _profile_adopt_preferred_profile_for(self, model_id)

    _profile_fits_slot = staticmethod(_profile_adopt_profile_fits_slot)

    async def _apply_preferred_profile(self, slot_name: str, model_id: str) -> bool:
        """See :func:`hal0.slots.profile_adopt.apply_preferred_profile`."""
        return await _profile_adopt_apply_preferred_profile(self, slot_name, model_id)

    async def _defuse_stale_mtp_on_swap(self, slot_name: str, model_id: str) -> bool:
        """See :func:`hal0.slots.profile_adopt.defuse_stale_mtp_on_swap`."""
        return await _profile_adopt_defuse_stale_mtp_on_swap(self, slot_name, model_id)

    # ── model preferred runner (§7.1b / ML-4: image loads with the model) ─────
    #
    # Sibling of the preferred-profile hooks above; logic lives in
    # hal0.slots.profile_adopt, this class is a thin delegator.

    async def _preferred_runner_for(self, model_id: str | None) -> str | None:
        """See :func:`hal0.slots.profile_adopt.preferred_runner_for`."""
        return await _profile_adopt_preferred_runner_for(self, model_id)

    _runner_fits_slot = staticmethod(_profile_adopt_runner_fits_slot)

    async def _apply_preferred_runner(self, slot_name: str, model_id: str) -> bool:
        """See :func:`hal0.slots.profile_adopt.apply_preferred_runner`."""
        return await _profile_adopt_apply_preferred_runner(self, slot_name, model_id)

    async def _check_npu_exclusivity(
        self,
        slot_name: str,
        cfg_dict: dict[str, Any],
    ) -> None:
        """Reject a write that would land a second NPU LLM anchor.

        Plan §5.3: the AMDXDNA hardware context admits
        ONE ``device=npu, type=llm`` slot at a time. Disabled NPU LLM
        slots may coexist with another disabled (or enabled) one, but
        two enabled anchors cannot be configured. This guard runs on
        every ``create()`` and ``update_config()`` so the constraint
        holds before any TOML hits disk.

        Cheap fast paths:
          - the slot being written is not ``device=npu, type=llm`` →
            no possible violation, return.
          - the slot being written is not ``enabled`` → at most one
            enabled NPU LLM can survive (the OTHER one, if any),
            return.

        On the slow path we walk the other configured slots to see
        whether any pre-existing NPU LLM is already enabled. Reading
        the writer's own slot from disk is skipped — the in-memory
        ``cfg_dict`` IS the authoritative new state.

        Thin async wrapper (kept for the public method surface and test
        monkeypatchability) over the module-level sync
        :func:`check_npu_exclusivity`, which the stacks apply engine
        shares so its writes obey the same guard.
        """
        check_npu_exclusivity(slot_name, cfg_dict)

    async def _check_default_uniqueness(
        self,
        slot_name: str,
        cfg_dict: dict[str, Any],
    ) -> None:
        """Reject a write that would land a second ``default=true`` per type.

        SC-4 (CONTEXT.md §defaults): exactly one ``default = true`` slot
        is allowed per ``type``. :meth:`default_slot_for` already raises
        at routing time when two defaults slip onto disk; this guard is
        the belt to that suspenders — it refuses the offending write on
        every ``create()`` and ``update_config()`` so the second default
        never reaches disk.

        Cheap fast path: the write only introduces a conflict when the
        slot being written is itself ``default=true``. A write that
        clears or leaves the default alone can't create a new collision,
        so it returns immediately (mirrors the NPU guard's "not the
        constrained kind → return"). We deliberately do NOT retroactively
        reject writes to unrelated slots just because two stale defaults
        already exist on disk — that's the routing check's job, and
        firing here would brick legitimate edits.

        On the slow path we walk the other configured slots, keying on
        the merged ``cfg_dict``'s own ``type``, and collect any peer of
        the same type that is already ``default=true``. Reading the
        writer's own slot is skipped — the in-memory ``cfg_dict`` IS the
        authoritative new state.

        Thin async wrapper (kept for the public method surface and test
        monkeypatchability) over the module-level sync
        :func:`check_default_uniqueness`, which the stacks apply engine
        shares so its writes obey the same guard.
        """
        check_default_uniqueness(slot_name, cfg_dict)

    # ── idle / wake-on-request ───────────────────────────────────────────────

    def bump_last_used(self, slot_name: str) -> None:
        """Record activity on a slot — called from request dispatch paths.

        Tier-2 idle-management: the idle monitor (see
        :meth:`start_idle_monitor`) polls these timestamps and transitions
        long-idle READY slots to IDLE.  The dispatcher's ``serving()``
        context also bumps on every request boundary so a steady stream
        keeps the slot READY.
        """
        self._last_used[slot_name] = time.time()

    def last_used(self, slot_name: str) -> float | None:
        return self._last_used.get(slot_name)

    # ── PULLING ──────────────────────────────────────────────────────────────
    #
    # ``load()`` consults ``_needs_pull`` before the STARTING transition.
    # The default cache check looks the model up in ``ModelRegistry`` and
    # verifies the file at ``Model.path`` exists on disk.  Tests inject a
    # custom predicate to force-trigger or skip PULLING deterministically.

    def _needs_pull(self, model_id: str) -> bool:
        """True when load() must flip through PULLING before STARTING.

        Returns ``False`` if no ``pull_runner`` was wired — the legacy
        offline→starting path is preserved for callers that handle their
        own model staging (installer, integration tests).

        Raises:
            RegistryUnavailableError: When the cache check could not
                consult the registry at all (registry outage).  This is
                NOT swallowed: silently treating an unreachable registry
                as "cached" skips PULLING and launches a container
                against a maybe-missing file, which then crash-loops with
                an unrelated-looking error.  The structured
                ``model.registry_unavailable`` code surfaces the real
                cause to the API caller instead.
        """
        if self._pull_runner is None:
            return False
        try:
            return not bool(self._model_cache_check(model_id))
        except RegistryUnavailableError:
            raise
        except Exception as exc:
            # Defensive: a buggy *injected* cache check must not break
            # load().  Log and treat as "cached" so we fall through to
            # STARTING and the slot's own probe surfaces the real failure.
            # (The default check never lands here: it maps registry
            # outages to RegistryUnavailableError above.)
            log.warning(
                "slot.cache_check_failed",
                extra={"model_id": model_id, "error": str(exc)},
            )
            return False

    def _resolve_servable_model(self, model_id: str, cfg: SlotConfig | dict[str, Any]) -> str:
        """Resolve a slot's configured model id to one that can actually serve.

        THIN DELEGATOR (ML-2/ML-3 — the deferred P3-slots extraction): the
        actual heuristic lives in
        :func:`hal0.registry.fallback.resolve_servable_model` now — this
        method exists only so every existing call site
        (``self._resolve_servable_model(...)``) and test
        (``SlotManager._resolve_servable_model``) keeps working unchanged.
        See that function's docstring for the full contract.
        """
        return _registry_fallback.resolve_servable_model(model_id, cfg)

    @staticmethod
    def _fallback_local_model(capability: str, configured_id: str = ""):
        """THIN DELEGATOR to :func:`hal0.registry.fallback.fallback_local_model`
        (ML-2/ML-3 — the deferred P3-slots extraction). See that function's
        docstring for the full contract."""
        return _registry_fallback.fallback_local_model(capability, configured_id=configured_id)

    @staticmethod
    def _default_model_cache_check(model_id: str) -> bool:
        """THIN DELEGATOR to
        :func:`hal0.registry.fallback.default_model_cache_check` (ML-2/ML-3
        — the deferred P3-slots extraction). Still raises this module's
        :class:`RegistryUnavailableError` on a genuine registry outage —
        ``fallback.default_model_cache_check`` imports that exact class
        back from here (deferred) so ``except RegistryUnavailableError``
        elsewhere in this class keeps matching. See that function's
        docstring for the full contract.
        """
        return _registry_fallback.default_model_cache_check(model_id)

    # ── SERVING ──────────────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def serving(self, slot_name: str) -> AsyncIterator[None]:
        """Mark ``slot_name`` as SERVING for the duration of one request.

        Concurrency-safe: a per-manager asyncio.Lock guards an in-flight
        counter.  The first concurrent entry flips READY/IDLE → SERVING;
        the last exit flips SERVING → READY.  ``IllegalSlotTransition``
        from races (e.g. the slot got unloaded mid-request) is swallowed
        so request paths never crash because of state-machine drift.

        ``bump_last_used`` fires on both entry and exit so the idle
        monitor's clock resets every time a request lands.

        # NOTE: callers wire this through ``Dispatcher.forward``; the
        # single-flight prefetch path does NOT enter this context — it
        # only touches /v1/models, never a real inference request, so
        # the slot stays READY for cold-cache fanouts.
        """
        await self._serving_enter(slot_name)
        try:
            yield
        finally:
            await self._serving_exit(slot_name)

    async def _serving_enter(self, slot_name: str) -> None:
        async with self._serving_lock:
            prev = self._serving_count.get(slot_name, 0)
            self._serving_count[slot_name] = prev + 1
            self.bump_last_used(slot_name)
            if prev > 0:
                return
            current = self._current_state(slot_name)
            if current not in (SlotState.READY, SlotState.IDLE):
                return
            try:
                await self._transition(slot_name, SlotState.SERVING)
            except IllegalSlotTransition:
                log.debug(
                    "slot.serving_enter_illegal_transition",
                    extra={"slot": slot_name, "from": current.value},
                )

    async def _serving_exit(self, slot_name: str) -> None:
        async with self._serving_lock:
            remaining = self._serving_count.get(slot_name, 1) - 1
            if remaining > 0:
                self._serving_count[slot_name] = remaining
                self.bump_last_used(slot_name)
                return
            self._serving_count.pop(slot_name, None)
            self.bump_last_used(slot_name)
            current = self._current_state(slot_name)
            if current != SlotState.SERVING:
                return
            try:
                await self._transition(slot_name, SlotState.READY)
            except IllegalSlotTransition:
                log.debug(
                    "slot.serving_exit_illegal_transition",
                    extra={"slot": slot_name, "from": current.value},
                )

    def in_flight_count(self, slot_name: str) -> int:
        """Return the number of in-flight requests for ``slot_name``.

        Sums active ``serving()`` contexts AND committed dispatch tickets
        (DR-2): a request that has passed ``Dispatcher.forward``'s image-mode
        guard but has not yet entered ``serving()`` still holds a ticket, so
        the GpuArbiter drain sees it and never unloads the slot out from under
        an in-flight request.  Temporary overlap while both counters hold the
        same request is harmless — both reach 0 only when the request ends.
        """
        return self._serving_count.get(slot_name, 0) + self._dispatch_tickets.get(slot_name, 0)

    def enter_dispatch(self, slot_name: str) -> None:
        """Synchronously commit a dispatch ticket for ``slot_name`` (DR-2).

        Called by ``Dispatcher.forward`` immediately after the image-mode
        guard and BEFORE the first await.  Being sync (no lock, no await) is
        the whole point: nothing — not even ``GpuArbiter.ensure_img`` — can
        interleave between the guard read and the ticket take, so any request
        that passed the guard registers before the drain's next poll.
        """
        self._dispatch_tickets[slot_name] = self._dispatch_tickets.get(slot_name, 0) + 1

    def exit_dispatch(self, slot_name: str) -> None:
        """Release the dispatch ticket taken by :meth:`enter_dispatch` (DR-2).

        Balanced on EVERY exit path of ``Dispatcher.forward``'s slot branches
        (success, typed raises, ``UpstreamUnavailable``, cancellation).
        """
        remaining = self._dispatch_tickets.get(slot_name, 1) - 1
        if remaining > 0:
            self._dispatch_tickets[slot_name] = remaining
        else:
            self._dispatch_tickets.pop(slot_name, None)

    # ── GpuArbiter (Phase D, spec §7) ────────────────────────────────────────

    @property
    def arbiter(self) -> GpuArbiter:
        """Lazily-constructed exclusive-GPU arbiter (llm ⇄ img groups).

        State persists under the same var-lib root the slot state files
        use (``paths.var_lib()``, HAL0_HOME-redirected in tests).
        ``idle_restore_minutes`` comes from the img slot's ``[image]``
        section when one is configured (D1), default 60.
        """
        if self._arbiter is None:
            from hal0.slots.arbiter import GpuArbiter

            self._arbiter = GpuArbiter(
                self,
                state_path=paths.var_lib() / "gpu_arbiter.json",
                idle_restore_minutes=self._img_idle_restore_minutes(),
            )
        return self._arbiter

    def _img_idle_restore_minutes(self) -> int:
        """Read ``[image].idle_restore_minutes`` from the img-group slot TOML.

        Synchronous direct TOML scan, mirroring ``idle_timeout_by_model``
        (the ``arbiter`` property can't await). The first slot whose config
        derives to the ``img`` exclusive group wins; missing/invalid values
        (negatives, bools, non-ints) fall back to the default of 60
        minutes. ``0`` is VALID and means manual-only restore (#599 schema)
        — the arbiter's idle loop never auto-restores on a zero window.
        """
        import tomllib

        from hal0.slots.arbiter import gpu_exclusive_group

        for name in self._all_configured_slot_names():
            path = self._config_file(name)
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if gpu_exclusive_group(data) != "img":
                continue
            image = data.get("image") or data.get("image_gen") or {}
            val = image.get("idle_restore_minutes") if isinstance(image, dict) else None
            if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
                return val
            return 60
        return 60

    # ── IDLE monitor ─────────────────────────────────────────────────────────
    #
    # P3-slots §1b: logic lives in hal0.slots.reaper.SlotReaper
    # (self._reaper, constructed in __init__). Every method below is a thin
    # delegator so the public surface is unchanged.

    async def start_idle_monitor(
        self,
        *,
        idle_after_s: float | None = None,
        evict_after_s: float | None = None,
        evict_pressure_mb: float | None = None,
        evict_pressure_pct: float | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Start the background sweeper that demotes READY → IDLE and evicts.

        Idempotent — calling twice while the task is alive is a no-op.
        Callers in the API lifespan invoke this once at startup (wiring
        ``evict_after_s`` from ``slots.idle_timeout_s``); tests construct a
        SlotManager with shorter intervals and start the monitor explicitly.

        ``evict_pressure_pct`` (§21.10, new): expresses the pressure floor
        as a percentage of total GTT-aware capacity instead of an absolute
        MiB value. ``None`` (default) leaves ``evict_pressure_mb`` in
        charge — purely additive.
        """
        await self._reaper.start(
            idle_after_s=idle_after_s,
            evict_after_s=evict_after_s,
            evict_pressure_mb=evict_pressure_mb,
            evict_pressure_pct=evict_pressure_pct,
            interval_s=interval_s,
        )

    async def stop_idle_monitor(self) -> None:
        """Cancel the idle-monitor task if running.  Idempotent."""
        await self._reaper.stop()

    async def _idle_monitor_loop(self) -> None:
        """See :meth:`hal0.slots.reaper.SlotReaper._loop`."""
        await self._reaper._loop()

    async def _evict_timeout_for(self, slot_name: str) -> float | None:
        """See :meth:`hal0.slots.reaper.SlotReaper.evict_timeout_for`."""
        return await self._reaper.evict_timeout_for(slot_name)

    def _sweep_candidates(self) -> dict[str, float]:
        """See :meth:`hal0.slots.reaper.SlotReaper.sweep_candidates`."""
        return self._reaper.sweep_candidates()

    async def _sweep_idle_once(self) -> None:
        """See :meth:`hal0.slots.reaper.SlotReaper.sweep_idle_once`."""
        await self._reaper.sweep_idle_once()

    def _probe_host_free_mb(self) -> float:
        """Return free host memory in MiB, GTT-aware where possible (§21.10).

        See :func:`hal0.slots.reaper.probe_host_free_mb`. Kept as an
        overridable instance method (rather than calling the module
        function directly from the reaper) so
        ``tests/slots/test_pressure_eviction.py``'s
        ``monkeypatch.setattr(sm, "_probe_host_free_mb", ...)`` pattern
        keeps working unchanged — the reaper always calls back through
        ``self._host._probe_host_free_mb()``.
        """
        return _reaper_probe_host_free_mb()

    def _probe_host_total_mb(self) -> float:
        """Return total host memory in MiB, GTT-aware where possible (§21.10).

        Only consulted when ``evict_pressure_pct`` is set. See
        :func:`hal0.slots.reaper.probe_host_total_mb`.
        """
        return _reaper_probe_host_total_mb()

    async def _pressure_evict_once(self) -> None:
        """See :meth:`hal0.slots.reaper.SlotReaper.pressure_evict_once`."""
        await self._reaper.pressure_evict_once()

    async def is_pinned(self, slot_name: str) -> bool:
        """True when *slot_name* is exempt from eviction (§21.10 operator pin).

        Combines the default-pinned anchor set (``agent``/``utility``/
        ``npu``) with an explicit ``SlotConfig.pinned = true``. Resolves
        the alias first; a missing/unreadable config is treated as "not
        pinned" beyond the default set (same fail-open contract as
        :meth:`hal0.slots.reaper.SlotReaper.evict_timeout_for`). Used by
        :meth:`delete` and by ``api/routes/slots.py``'s manual-unload
        guard (both require ``force=true`` on a pinned slot, HTTP 409
        ``slot.pinned``).
        """
        canonical = self._resolve_alias(slot_name)
        cfg = await self._maybe_load_config(canonical)
        return _reaper_is_pinned(canonical, cfg)

    async def get_config(self, slot_name: str) -> dict[str, Any]:
        """Return the slot's TOML config as a plain dict (read-only view).

        Public counterpart to ``_load_slot_config``: same semantics, but
        callable from API routes without reaching past the underscore.
        """
        return await self._load_slot_config(slot_name)

    # ── private helpers ──────────────────────────────────────────────────────

    async def _load_slot_config(self, slot_name: str) -> dict[str, Any]:
        """Read /etc/hal0/slots/<name>.toml as a raw dict.

        TIER1: surfaces a typed SlotConfigError on missing / malformed
        TOML.  Replaces haloai's silent `except Exception: pass` at
        lib/slots.py:296 et al.
        """
        try:
            import tomllib
        except ImportError:  # py<3.11
            import tomli as tomllib  # type: ignore[no-redef]

        # Resolve back-compat aliases (primary→chat, agent-hermes→agent) so a
        # config read by an old slot name lands on the canonical TOML. This is
        # the single chokepoint for config reads; callers that already resolved
        # are unaffected (canonical names pass through unchanged).
        slot_name = self._resolve_alias(slot_name)
        path = self._config_file(slot_name)
        if not path.exists():
            # In-memory-only slot (test injection) — fall back to the
            # state.json record.  Real callers should always have a TOML.
            rec = self._states.get(slot_name)
            if rec is None:
                # Issue #35: no TOML and no in-memory state means the slot
                # simply doesn't exist — raise the 404-shaped SlotNotFound so
                # the API surfaces 'slot.not_found' instead of the misleading
                # 400 'slot.config_error'. A real config-parse failure on an
                # existing slot still raises SlotConfigError below.
                raise SlotNotFound(
                    f"slot {slot_name!r} is not configured "
                    f"(no config at {path} and no in-memory state)",
                    details={"slot": slot_name, "path": str(path)},
                )
            # W3: no hardcoded "vulkan" fallback — carry whatever backend the
            # state record actually recorded (itself device-derived at
            # create/adopt time). When absent, omit the key so downstream
            # consumers (``_cfg_effective_backend``) honestly report
            # "unknown" / derive from ``device`` instead of a stale lie.
            fallback: dict[str, Any] = {
                "name": slot_name,
                "port": rec.port,
                "provider": rec.extra.get("provider", "llama-server"),
                "model": {"default": rec.model_id or ""},
            }
            rec_backend = rec.extra.get("backend")
            if rec_backend:
                fallback["backend"] = rec_backend
            return fallback
        # ── mtime-keyed parse cache ──────────────────────────────────────
        # Stat BEFORE reading: if the file changes mid-read we may cache
        # the newer content under the older key, and the next write bumps
        # the mtime again so the entry self-corrects on the following call.
        # (st_mtime_ns, st_size) as the key survives coarse-mtime
        # filesystems better than mtime alone; same-process writers also
        # invalidate eagerly (see _invalidate_cfg_cache).
        try:
            st = path.stat()
            cache_key: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            cache_key = None
        if cache_key is not None:
            cached = self._cfg_cache.get(slot_name)
            if cached is not None and (cached[0], cached[1]) == cache_key:
                # Deep-copy: callers freely mutate the returned dict
                # (merge pipelines, _paths injection) and must never
                # write through into the cache.
                return copy.deepcopy(cached[2])
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except OSError as exc:
            raise SlotConfigError(
                f"cannot read slot config {path}: {exc}",
                details={"slot": slot_name, "path": str(path)},
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            # A malformed TOML must not be served from a stale cache entry
            # on subsequent calls either — drop whatever we had.
            self._cfg_cache.pop(slot_name, None)
            raise SlotConfigError(
                f"slot config {path} is not valid TOML: {exc}",
                details={"slot": slot_name, "path": str(path)},
            ) from exc
        # PR #754 follow-up: the on-disk slot TOML nests fields under a
        # [slot] table (the shape config.loader / capabilities / profiles
        # consume). The flat readers in this module (load_sync, _cfg_*
        # helpers) expect those keys at the top level, so hoist the [slot]
        # table up while leaving the sibling [model]/[image]/[npu]/[server]
        # tables in place. A no-op for already-flat configs.
        slot_tbl = data.pop("slot", None)
        if isinstance(slot_tbl, dict):
            for _k, _v in slot_tbl.items():
                data[_k] = _v
        if "name" not in data:
            data["name"] = slot_name
        if cache_key is not None:
            self._cfg_cache[slot_name] = (
                cache_key[0],
                cache_key[1],
                copy.deepcopy(data),
            )
        return data

    def _invalidate_cfg_cache(self, slot_name: str) -> None:
        """Drop the parsed-TOML cache entry for *slot_name* (post-write hook).

        Called by every in-process slot-TOML writer (create / update_config /
        _persist_model_default / _apply_preferred_profile / delete) right
        after the write lands.  Belt to the (mtime_ns, size) suspenders in
        :meth:`_load_slot_config` — guarantees our own writes are never
        masked even on a filesystem with coarse mtime resolution.
        """
        self._cfg_cache.pop(self._resolve_alias(slot_name), None)

    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]:
        """Look up model metadata from the registry.

        Returns an empty dict when model_id is None or the registry isn't
        wired yet.  NOTE: codes against the registry-subtree's expected
        ``get(model_id) -> Model`` API; if that lands as ``get_model``,
        the lookup below adjusts.
        """
        if not model_id:
            return {}

        # Stamp _model_key / flm_tag onto every model_info, registry-hit or
        # miss. Providers that look at these (currently FLM, where the
        # "model_id" is a FastFlowLM tag like ``qwen3.5:4b`` rather than a
        # local-file model) use them as the canonical lookup key. Mirrors
        # haloai's haloai-launch behaviour.
        #
        # FLM's ``serve`` only accepts the native ``family:size`` tag
        # (``gemma4-it:e2b``), but slots persist the hal0 catalog id
        # (``gemma4-it-e2b-FLM``). Translate so the FLM provider serves the
        # right tag instead of passing the ``-FLM`` id straight through, which
        # makes FLM answer "Model not found" and the slot crash-loop. Falls
        # back to the raw id when the catalog can't resolve it.
        flm_tag = model_id
        try:
            from hal0.providers.flm import flm_id_to_tag
        except ImportError:
            flm_id_to_tag = None  # type: ignore[assignment]
        if flm_id_to_tag is not None:
            resolved_tag = flm_id_to_tag(model_id)
            if resolved_tag:
                flm_tag = resolved_tag
        info: dict[str, Any] = {"_model_key": model_id, "flm_tag": flm_tag}

        try:
            from hal0.registry.store import ModelNotFound, ModelRegistry
        except ImportError:
            log.warning("slot.registry_unavailable", extra={"model_id": model_id})
            return info
        try:
            reg = ModelRegistry()
            model = reg.get(model_id)
        except ModelNotFound:
            # Not fatal — the slot manager is not the authoritative gate
            # on "is this model installed"; the toolbox will surface its
            # own load error if the path is wrong.
            log.warning("slot.model_not_in_registry", extra={"model_id": model_id})
            return info
        except NotImplementedError:
            log.warning("slot.registry_stub", extra={"model_id": model_id})
            return info

        registry_dump = model.model_dump() if hasattr(model, "model_dump") else dict(model)
        info.update(registry_dump)
        return info

    # ── health probe (TIER1 tightened) ───────────────────────────────────────

    async def _await_ready(self, slot_name: str, port: int) -> SlotState:
        """Resolve the slot's final readiness state after spawning.

        Polls GET /health on the slot's container port until 200.

        Returns:
            SlotState.READY when the container is serving. On a health-wait
            timeout, resolves to SlotState.WARMING (non-dispatchable) rather
            than a lying READY: WARMING keeps the slot retryable so the fail
            watcher / next-request reload governs recovery, instead of live
            traffic being forwarded to a wedged server on a false READY.
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return SlotState.READY  # nothing more to verify

        # Wait for /health 200 on the container port.
        slot_port = port or int(_cfg_to_dict(cfg).get("port", 0))
        from hal0.providers.container import _spec_provider_for, container_provider

        try:
            await container_provider().wait_ready(slot_port)
        except TimeoutError as exc:
            log.warning(
                "slot.container_await_ready_timeout",
                extra={"slot": slot_name, "port": slot_port, "error": str(exc)},
            )
            # DR-3: the inference server never answered /health, so do NOT
            # advertise a dispatchable READY. WARMING is not in
            # _DISPATCHABLE_STATES, so _check_slot_ready_for_dispatch raises
            # the retryable SlotLoading (503 + Retry-After) and the next
            # request re-drives load() — the fail watcher / reload governs
            # recovery instead of live traffic hitting a wedged server.
            return SlotState.WARMING

        # One-shot inference gate for FLM/NPU slots. The hot health paths (2s
        # fail-watch + per-request readiness) use only the cheap /v1/models
        # liveness probe — a repeating/overlapping real completion double-frees
        # the single NPU context (SIGABRT, status 134). We still verify real
        # inferability ONCE, here, where there is no contention: the slot is
        # not yet dispatchable, this load holds the slot lock, and the warming
        # fail-watcher skips /health. A wedged NPU that lists models but can't
        # infer resolves to retryable WARMING instead of a lying READY.
        from hal0.providers.flm import FLMProvider

        provider = _spec_provider_for(cfg)
        if isinstance(provider, FLMProvider):
            # Pick the sentinel by which modality the slot actually serves.
            # An embed/STT-primary slot ([npu].chat=false) has no chat model,
            # so the chat completion sentinel would fail and wedge it in
            # WARMING forever. Probe the real role instead.
            npu_cfg = cfg.get("npu") or (cfg.get("extra") or {}).get("npu") or {}
            chat_on = npu_cfg.get("chat", True) is not False
            embed_on = bool(npu_cfg.get("embed"))
            if chat_on:
                # Probe the slot's ASSIGNED model, not FLM's models[0]. FLM's
                # /v1/models lists the whole installed catalogue, so models[0]
                # is an arbitrary OTHER model; probing it reloads the wrong
                # weights onto the single NPU context and deadlocks the load,
                # wedging the slot in WARMING forever (#1171). Translate the
                # persisted default to the served colon tag the same way the
                # serve path does (build_env uses flm_tag), so the expected id
                # matches what FLM advertises regardless of whether the config
                # stores the "-FLM" id or the native tag.
                expected_model = _model_default(cfg)
                try:
                    from hal0.providers.flm import flm_id_to_tag

                    resolved_tag = flm_id_to_tag(expected_model)
                    if resolved_tag:
                        expected_model = resolved_tag
                except ImportError:
                    pass
                verdict = await provider.verify_inference(
                    slot_port, expected_model=expected_model or None
                )
            elif embed_on:
                verdict = await provider.verify_embed(slot_port)
            else:
                # ASR-only (no chat, no embed): a transcription sentinel needs
                # an audio upload, so fall back to the cheap /v1/models liveness
                # — the slot is ready once it lists its served model.
                verdict = await provider.health(slot_port)
            if not verdict.get("ok"):
                log.warning(
                    "slot.flm_inference_gate_failed",
                    extra={
                        "slot": slot_name,
                        "port": slot_port,
                        "status": verdict.get("status"),
                        "detail": verdict.get("detail"),
                    },
                )
                return SlotState.WARMING
        return SlotState.READY

    # ── adoption / drift reconcile (ISSUE #30) ───────────────────────────────

    async def _maybe_adopt_running_slot(self, slot_name: str, cfg: dict[str, Any]) -> Slot | None:
        """Adopt a slot whose unit is live but whose state.json is stale.

        Checks systemctl is-active (via _is_active). Returns the
        post-adoption Slot snapshot, or ``None`` when the slot is not
        running — caller falls back to the on-disk record.
        """
        port = _cfg_port(cfg)
        model_id = _model_default(cfg) or None
        if model_id is None:
            # No model configured → nothing to adopt.
            return None

        active = await self._is_active(slot_name)
        if not active:
            return None

        # #790: an active unit is not necessarily ready. A still-loading or
        # wedged container is active to systemd while its model server isn't
        # answering /health — adopting it straight to READY publishes it as
        # dispatchable and live traffic 502s. is_active is already confirmed
        # above, so only the /health half remains: probe it and adopt to
        # WARMING (not READY) on a definitive not-ok. _probe_health degrades
        # gracefully (inconclusive → True) so a probe transport error never
        # 500s the best-effort /api/slots list, and short-circuits NPU trio
        # shadows (no own model server) to healthy.
        healthy = await self._probe_health(slot_name)
        resolved = SlotState.READY if healthy else SlotState.WARMING
        extras: dict[str, Any] = {
            # W3: mirror the device-derived EFFECTIVE backend, not a
            # hardcoded "vulkan" fallback — the slot's ``device`` (or its
            # legacy ``backend`` field, folded by _cfg_effective_backend)
            # is what is actually running.
            "backend": _cfg_effective_backend(cfg) or cfg.get("backend"),
            "provider": cfg.get("provider", "llama-server"),
            "adopted": True,
            # Record the probe result so /api/health + hal0_slot_up can fold
            # in real readiness rather than trusting FSM state alone (#791).
            "health_ok": healthy,
        }
        detail = "container unit active" if healthy else "container active, model server not ready"
        # ``force=True`` is required: the legal-transition map does not
        # contain offline→ready. Adoption is the exception — the state
        # machine is recovering from drift, not following load().
        await self._transition(
            slot_name,
            resolved,
            model_id=model_id,
            port=port,
            message=f"adopted running slot ({detail})",
            extra=extras,
            force=True,
        )
        # Start the idle clock: adopted slots were invisible to the idle-TTL
        # and pressure sweeps because nothing ever bumped ``_last_used`` for
        # them (both sweeps key off it), so a container that outlived an api
        # restart could squat on RAM forever. Adoption counts as activity —
        # the TTL runs from now, and the sweeps' state.json fallback covers
        # any dispatchable slot that still slips through in-memory tracking.
        self.bump_last_used(slot_name)
        log.info(
            "slot.adopted",
            extra={
                "slot": slot_name,
                "port": port,
                "resolved": resolved.value,
                "detail": detail,
            },
        )
        # Build the Slot snapshot directly from the just-written record.
        rec = self._states[slot_name]
        return Slot(
            name=slot_name,
            state=resolved,
            port=rec.port,
            model_id=rec.model_id,
            backend=rec.extra.get("backend"),
            metadata={
                "updated_at": rec.updated_at,
                "message": rec.message,
                **rec.extra,
            },
        )


# ── module-level helpers ─────────────────────────────────────────────────────

# NOTE (ML-2/ML-3 — the deferred P3-slots extraction): the model-fallback
# heuristics that used to live here — `_SLOT_TYPE_TO_CAPABILITY`, the
# diffusion/non-text guard (`_looks_diffusion_or_nontext` + its token
# tables), and the id-token-overlap ranking helpers (`_id_tokens`,
# `_leading_token_overlap`) — moved to `hal0.registry.fallback` (imported
# above as `_registry_fallback`). They belong to the registry/discovery
# layer, not slot lifecycle orchestration. `SlotManager._resolve_servable_model`
# / `_fallback_local_model` / `_default_model_cache_check` stay as thin
# delegators for call-site/test compatibility (see those methods above).

# NOTE: _argv_values / _resolve_drift_flags / _config_drift_values_equal /
# _CONFIG_DRIFT_KEYS / compute_config_drift moved to hal0.slots.drift
# (P3-slots §1c — investigated + KEPT, not deleted; see that module's
# docstring). Imported + re-exported above.

# NOTE: _cfg_effective_backend / _base_profile_for_backend /
# _reconcile_device_profile / _read_slot_toml_dict / _iter_peer_configs /
# check_npu_exclusivity / check_default_uniqueness / reconcile_slot_updates /
# reconcile_and_guard_slot_config moved to hal0.slots.config_write
# (P3-slots §1f) — imported + re-exported above (see module docstring's
# "New in P3-slots" note and __all__ below).


__all__ = [
    "NPU_SEEDED_SLOTS",
    "SEEDED_SLOTS",
    "SLOT_ALIASES",
    "_CONFIG_DRIFT_KEYS",
    "_FAIL_WATCH_INTERVAL_S",
    "_FAIL_WATCH_LIVE_STATES",
    "_HEALTH_FAIL_STRIKES",
    "_VALID_SLOT_TYPES",
    "_WARMING_INACTIVE_STRIKES",
    "_WARMING_STALE_AFTER_S",
    "LoadedSlot",
    "Slot",
    "SlotManager",
    "_argv_values",
    "_base_profile_for_backend",
    "_cfg_effective_backend",
    "_cfg_port",
    "_cfg_provider",
    "_model_default",
    "check_default_uniqueness",
    "check_npu_exclusivity",
    "is_npu_trio_shadow",
    "reconcile_and_guard_slot_config",
    "reconcile_slot_updates",
]
