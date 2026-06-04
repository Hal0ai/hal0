"""Slot lifecycle manager (v0.2 — Lemonade-only).

SlotManager owns every aspect of slot lifecycle: load, unload, swap,
status, restart, create, delete. It dispatches every state-changing
call through :class:`LemonadeProvider` (PR-10 retired the legacy
docker/systemd path; ADR-0008 §1 makes Lemonade the sole backend).

State transitions are persisted atomically to
``/var/lib/hal0/slots/<name>/state.json`` (see :mod:`hal0.slots.state`).
The state machine survives v0.2 unchanged — only the side effects of
each transition changed (from "systemctl start hal0-slot@..." to
"POST /v1/load on lemond").

Architectural boundaries (ARCHITECTURE.md "Key boundaries"):
  - SlotManager talks to lemond exclusively via
    :func:`hal0.providers.lemonade_provider`. No subprocess spawning,
    no per-slot HTTP probing — the daemon is the source of truth.
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
import logging
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.config import paths
from hal0.slots.state import (
    IllegalSlotTransition,
    NpuExclusivityViolation,
    SlotConfigError,
    SlotNotFound,
    SlotSpawnFailed,
    SlotState,
    SlotStateRecord,
    is_transition_legal,
    provider_requires_model,
    read_state,
    write_state_atomic,
)

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig

log = logging.getLogger(__name__)


# ── Seeded slot catalogue (PR-10, plan §4.2 + §10.2) ────────────────────────

#: Slots that exist on every hal0 install regardless of hardware. The
#: dashboard creates these as empty cards at first run; the bundle
#: picker (Phase 5) populates their ``model.default`` fields.
SEEDED_SLOTS: tuple[str, ...] = ("primary", "embed", "rerank", "stt", "tts", "img")

#: NPU slots seeded only when the FastFlowLM ``.deb`` is installed
#: (``shutil.which('flm')`` truthy). These back the AMDXDNA hardware
#: context's trio mode — chat + ASR + embed coresident in one FLM
#: process (ADR-0008 §5). Opt-in enabled at Pro+ bundle tier.
NPU_SEEDED_SLOTS: tuple[str, ...] = ("agent", "stt-npu", "embed-npu")

#: Lemonade-vocabulary slot types (plan §4.1).
_VALID_SLOT_TYPES: frozenset[str] = frozenset(
    {"llm", "embedding", "reranking", "transcription", "tts", "image"}
)

#: Slot-name policy: kebab-case, max 32 chars, leading alphanumeric.
_SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ── Tunables ─────────────────────────────────────────────────────────────────

# Push-driven failure detector. While a slot is in a "live" state
# (READY / SERVING / IDLE) a background task polls is_active every
# _FAIL_WATCH_INTERVAL_S seconds. When the slot drops out of lemond's
# loaded[] (model evicted, lemond died) the watcher flips state to
# ERROR and emits an SSE frame within ~1s.
_FAIL_WATCH_INTERVAL_S: float = 2.0
_FAIL_WATCH_LIVE_STATES: frozenset[SlotState] = frozenset(
    {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
)

# Idle-monitor defaults. A READY slot whose last activity is older than
# _IDLE_AFTER_S gets demoted to IDLE so dashboards / unload heuristics
# can distinguish "warm but quiet" from "warm and serving".
_IDLE_AFTER_S: float = 300.0
_IDLE_MONITOR_INTERVAL_S: float = 30.0


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
        idle_monitor_interval_s: float = _IDLE_MONITOR_INTERVAL_S,
        event_bus: Any | None = None,
    ) -> None:
        # Optional EventBus for footer/dashboard observability. Not part
        # of the slot state machine — purely a side-channel so the
        # dashboard footer can render transitions without polling. None
        # in CLI / unit-test contexts; wired by the FastAPI lifespan.
        self._event_bus = event_bus
        # Per-slot locks to prevent concurrent load/unload/restart races.
        self._locks: dict[str, asyncio.Lock] = {}
        # In-memory copy of the latest state per slot (mirrors state.json).
        self._states: dict[str, SlotStateRecord] = {}
        # SSE subscribers: list of queues; one per active state_stream().
        self._subscribers: list[asyncio.Queue[SlotStateRecord]] = []
        # Idle-tracking — last request timestamp per slot.
        self._last_used: dict[str, float] = {}
        # Per-slot background tasks that poll lemond's loaded[] list
        # and push a READY→ERROR transition when the model drops out
        # (eviction, lemond restart, etc). Keyed by slot name; only
        # present while the slot is in a live state.
        self._fail_watchers: dict[str, asyncio.Task[None]] = {}
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
        # IDLE — background sweeper task that demotes READY→IDLE after
        # ``idle_after_s`` seconds of inactivity.  Started explicitly via
        # ``start_idle_monitor()`` (the API lifespan owns the lifecycle so
        # tests can inject shorter intervals).
        self._idle_after_s: float = idle_after_s
        self._idle_monitor_interval_s: float = idle_monitor_interval_s
        self._idle_monitor_task: asyncio.Task[None] | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

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
        self._update_fail_watcher(name, to_state)
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

    def _update_fail_watcher(self, name: str, new_state: SlotState) -> None:
        """Spawn or cancel the per-slot fail-watcher to match ``new_state``.

        Live states (READY/SERVING/IDLE) → ensure a watcher task is running.
        Any other state → cancel the watcher if present.

        Self-cancellation is a no-op: when the watcher itself fires the
        transition to ERROR, we let it return naturally rather than calling
        ``task.cancel()`` on the currently-executing coroutine (which would
        raise CancelledError on the await it just completed).
        """
        if new_state in _FAIL_WATCH_LIVE_STATES:
            existing = self._fail_watchers.get(name)
            if existing is not None and not existing.done():
                return
            try:
                self._fail_watchers[name] = asyncio.create_task(
                    self._fail_watch_loop(name),
                    name=f"hal0-slot-fail-watch-{name}",
                )
            except RuntimeError:
                # No running loop (sync-context test of _transition with
                # force=True called outside asyncio). Skip — the watcher
                # only matters when the slot is actually live in an event
                # loop.
                log.debug("slot.fail_watch_no_loop", extra={"slot": name})
            return

        existing = self._fail_watchers.pop(name, None)
        if existing is None or existing.done():
            return
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if existing is current_task:
            # Watcher self-cancel via its own transition — let it finish.
            return
        existing.cancel()

    async def _fail_watch_loop(self, slot_name: str) -> None:
        """Poll lemond's loaded[] and flip to ERROR on model eviction.

        Runs as a background task while the slot is in READY/SERVING/IDLE.
        Detection latency = up to one poll interval (~2s). Exits cleanly
        once the slot leaves the live-state set, by self-cancel via the
        ERROR transition, or via outer ``task.cancel()``.
        """
        try:
            while True:
                await asyncio.sleep(_FAIL_WATCH_INTERVAL_S)
                # First gate: did the slot leave live-state from underneath
                # us?  ``_update_fail_watcher`` already cancels in that case
                # but this defends against the race where the watcher wakes
                # before the cancel lands.
                current = self._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                try:
                    active = await self._is_active(slot_name)
                except Exception as exc:
                    # Probe failure is unusual — log and keep polling.
                    log.warning(
                        "slot.fail_watch_is_active_failed",
                        extra={"slot": slot_name, "error": str(exc)},
                    )
                    continue
                if active:
                    continue
                # Model dropped out of lemond while we believed it was
                # live. Re-check state once more — load/unload may have
                # moved us legitimately during the probe.
                current = self._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                # Lemonade routinely evicts loaded models (idle-TTL,
                # nuclear-evict on a sibling load failure, max_models
                # pressure). From the slot's perspective this is a clean
                # unload — the next inference request hot-reloads the
                # model. Reflect that as OFFLINE (grey dot, "evicted —
                # auto-reloads on next request") rather than ERROR (red
                # dot, operator-investigation cue), reserving ERROR for
                # the real failures: spawn/health/load exceptions.
                try:
                    await self._transition(
                        slot_name,
                        SlotState.OFFLINE,
                        message="model evicted from lemond (auto-reloads on next request)",
                        force=True,
                    )
                except Exception as exc:
                    log.warning(
                        "slot.fail_watch_transition_failed",
                        extra={"slot": slot_name, "error": str(exc)},
                    )
                return
        except asyncio.CancelledError:
            # Normal shutdown path — slot left live-state cleanly.
            raise

    async def _is_active(self, slot_name: str) -> bool:
        """Lemonade-mode "is the slot live" probe.

        v0.2 (ADR-0008 §1/§6): there is no per-slot systemd unit; the
        question "is the slot active?" maps to "does the slot's
        configured model appear in lemond's ``/v1/health.loaded[]``?".
        Probe errors are coerced to ``False`` so ``status()``'s drift
        reconciler runs (and re-loads on the next request).
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return False
        model_name = _model_default(cfg)
        if not model_name:
            return False
        try:
            from hal0.providers import lemonade_provider

            snap = await lemonade_provider().status({"name": slot_name, **cfg})
        except Exception as exc:
            log.warning(
                "slot.lemonade_is_active_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return False
        return bool(snap.get("loaded", False))

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def load(self, slot_name: str, model_id: str | None = None) -> Slot:
        """Load a model into a slot.  Transitions: offline → starting → warming → ready.

        If model_id is None, uses the model assigned in the slot's TOML config.
        """
        self._ensure_known(slot_name)
        async with self._lock(slot_name):
            cfg = await self._load_slot_config(slot_name)
            resolved_model = model_id or _model_default(cfg)

            current = self._current_state(slot_name)
            if current in (SlotState.READY, SlotState.SERVING, SlotState.IDLE):
                # Already loaded — return snapshot without restarting.
                return await self.status(slot_name)

            # Configuration check: a slot with no resolvable model is
            # NOT an ERROR (which would render red and flag for operator
            # investigation). It's an unconfigured slot — render grey
            # with a CTA. Bail before calling lemonade.load(), whose
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
                resolved_state = await self._await_ready(
                    slot_name, _cfg_port(cfg), _cfg_provider(cfg)
                )
                await self._transition(
                    slot_name,
                    resolved_state,
                    model_id=resolved_model,
                    port=_cfg_port(cfg),
                )
                # Persist explicit model_id to TOML so reconciliation
                # after a Lemonade restart doesn't drift back to "no
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
        """Restart a slot without changing its model assignment."""
        self._ensure_known(slot_name)
        await self.unload(slot_name)
        return await self.load(slot_name)

    async def recover_evicted_slot(self, slot_name: str) -> Slot:
        """Resync a slot whose child died unexpectedly (Lemonade silent eviction).

        The dispatcher calls this when ``_forward_direct`` hits a
        ConnectError on a slot upstream — hal0's state.json said the
        slot was READY but the upstream port is dead.  Two common cases
        both land here:

          - **Lemonade-initiated eviction** (idle/OOM/auto-evict-all):
            lemond's ``loaded[]`` table no longer contains the model.
            A bare ``/v1/load`` actually re-spawns the child.
          - **Orphan process** (lemond's supervisor missed the death):
            ``loaded[]`` still claims the model is loaded on a now-dead
            PID/port.  A bare ``/v1/load`` is a no-op — lemond returns
            success without re-spawning.  The slot stays dead.

        To cover both, we drive a full unload + load cycle: ``/v1/unload``
        forces lemond to drop its ``loaded[]`` claim (and is a no-op
        when the model is genuinely gone, per the LemonadeProvider
        contract), then ``/v1/load`` actually re-spawns on the slot's
        configured port.  The slot's per-method locks serialize concurrent
        recoveries.

        Best-effort: if ``unload()`` itself raises (rare — usually means
        lemond is hard-down), we still try ``load()`` to give recovery
        the best shot at succeeding.  A subsequent retry-time
        ConnectError will surface as ``UpstreamUnavailable`` as before.
        """
        self._ensure_known(slot_name)
        log.info("slot.recover_evicted_dispatched", extra={"slot": slot_name})
        try:
            await self.unload(slot_name)
        except Exception as exc:
            # Log but don't bail — load() may still succeed if lemond
            # was just confused about state, not actually broken.
            log.warning(
                "slot.recover_unload_failed",
                extra={
                    "slot": slot_name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        return await self.load(slot_name)

    async def start(self, slot_name: str) -> Slot:
        """Idempotent start.  Equivalent to load() when slot is offline.

        Mirrors haloai's slots.start() (lib/slots.py:644) so callers like
        the dispatcher wake-on-request path can share the contract.
        """
        current = self._current_state(slot_name)
        if current in (SlotState.READY, SlotState.SERVING, SlotState.IDLE):
            self.bump_last_used(slot_name)
            return await self.status(slot_name)
        return await self.load(slot_name)

    async def swap(self, slot_name: str, new_model_id: str) -> Slot:
        """Hot-swap a slot's model: unload current, load new via Lemonade."""
        if not new_model_id:
            raise SlotConfigError("swap requires a non-empty model id")
        self._ensure_known(slot_name)
        await self.unload(slot_name)
        slot = await self.load(slot_name, model_id=new_model_id)
        # Refresh Hermes's live-context files so a model swap is visible to
        # the agent on its next session (detached; never blocks the swap).
        from hal0.agents.hermes_refresh import spawn_context_refresh

        spawn_context_refresh()
        return slot

    # ── queries ──────────────────────────────────────────────────────────────

    async def status(self, slot_name: str) -> Slot:
        """Return a snapshot of the current slot state.

        Combines the persisted state.json with a live "is the model
        in lemond's loaded[]?" probe. Reconciliation runs in both
        directions:

          - state.json says READY/SERVING/IDLE but the model has been
            evicted from lemond → transition to ERROR so the dashboard
            reflects reality.
          - state.json says OFFLINE / ERROR (or is missing) but the
            model is live in lemond → adopt the running slot into
            READY. Covers the case where another process or a manual
            ``/v1/load`` populated lemond out-of-band.
        """
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
        # Reconcile with lemond reality.
        observed = rec.state
        if observed in (SlotState.READY, SlotState.SERVING, SlotState.IDLE) and not active:
            # lemond says the model is not loaded; record reflects ready.
            # This is drift but NOT a slot-config error — Lemonade evicts
            # models on its own (LRU per-type budget, nuclear evict on a
            # different model's load failure, idle-unload driver, etc.).
            # Demoting to ERROR was the old "slot broken" semantics from
            # the per-slot-systemd model; under Lemonade, an evicted model
            # is a perfectly recoverable state — the dispatcher reloads on
            # next request. Surface as OFFLINE so the card chip shows the
            # neutral "not loaded" state rather than red ERROR.
            await self._transition(
                slot_name,
                SlotState.OFFLINE,
                message="model evicted from lemond (auto-reloads on next request)",
                force=True,
            )
            observed = SlotState.OFFLINE
        elif observed in (SlotState.OFFLINE, SlotState.ERROR) and active:
            # Inverse drift — state.json says we're not running, but
            # lemond holds the model. Adoption picks the slot up.
            cfg = await self._maybe_load_config(slot_name)
            if cfg:
                adopted = await self._maybe_adopt_running_slot(slot_name, cfg)
                if adopted is not None:
                    return adopted
        # W3 truth fix: the displayed ``backend`` must equal the EFFECTIVE
        # backend that will actually run — i.e. the token derived from the
        # slot's authoritative ``device`` field (which flows through to
        # Lemonade's per-load ``llamacpp_backend`` override). We deliberately
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
        return Slot(
            name=slot_name,
            state=observed,
            port=rec.port,
            model_id=rec.model_id,
            backend=backend,
            metadata=meta,
            last_used_at=self._last_used.get(slot_name),
        )

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

        Lightweight — reads TOML only, never touches lemond. Intended
        for startup hooks (e.g. ``lifespan`` auto-registering slots as
        upstreams) that need slot metadata before any real lifecycle
        interaction.

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

    def idle_timeout_by_model(self) -> dict[str, float]:
        """Map each slot's lemond ``model_name`` → its ``idle_timeout_s``.

        Issue #414: the Lemonade idle-unload driver evicts by lemond
        ``model_name``, but the per-slot ``idle_timeout_s`` (config
        source of truth) was never plumbed through, so every model used
        the driver's hardcoded 300s global. This synchronous reader
        builds the per-model TTL map the driver consumes once per tick.

        Synchronous on purpose: the driver's resolver runs inside the
        running event loop and can't await. We read each slot's TOML
        directly (the same files ``iter_configs`` reads), which is a
        cheap local-disk op. Slots with an empty ``[model] default`` or
        an unreadable / malformed config are skipped — the driver falls
        back to its global default for any model absent from the map.

        A value of ``idle_timeout_s == 0`` is preserved (maps to 0.0);
        the driver treats it as "never evict this model".
        """
        try:
            import tomllib
        except ImportError:  # py<3.11
            import tomli as tomllib  # type: ignore[no-redef]

        out: dict[str, float] = {}
        for name in self._all_configured_slot_names():
            path = self._config_file(name)
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.warning(
                    "slot.idle_ttl_skipped",
                    extra={"slot": name, "error": str(exc)},
                )
                continue
            model = data.get("model") or {}
            model_name = ""
            if isinstance(model, dict):
                model_name = str(model.get("default") or "")
            if not model_name:
                continue
            ttl = data.get("idle_timeout_s")
            if isinstance(ttl, (int, float)) and not isinstance(ttl, bool):
                out[model_name] = float(ttl)
        return out

    # ── PR-10: seeded slot catalogue + routing helpers ──────────────────────

    @staticmethod
    def seeded_slots(*, include_npu: bool | None = None) -> tuple[str, ...]:
        """Return the seeded slot list, optionally including the NPU trio.

        :data:`SEEDED_SLOTS` lands on every hal0 install. The NPU trio
        (``agent`` / ``stt-npu`` / ``embed-npu``) only seeds when the
        FastFlowLM ``.deb`` is installed (``shutil.which('flm')``
        truthy). Per plan §10.2 + §4.2 + ADR-0008 §5.

        Args:
            include_npu: ``None`` (default) detects FLM presence at
                runtime; ``True`` forces inclusion (tests + the bundle
                picker's preview mode); ``False`` forces exclusion.
        """
        if include_npu is None:
            include_npu = bool(shutil.which("flm"))
        if include_npu:
            return SEEDED_SLOTS + NPU_SEEDED_SLOTS
        return SEEDED_SLOTS

    async def default_slot_for(self, slot_type: str) -> str | None:
        """Return the name of the slot with ``type=slot_type`` and ``default=true``.

        Plan §4.4 step 1. Exactly one ``default = true`` per type is
        allowed; two defaults raise :class:`SlotConfigError` so the
        misconfiguration surfaces at the routing call site instead of
        silently picking one. Returns ``None`` when no slot of the
        type has ``default = true`` (the caller is expected to
        fall-through to the first enabled slot — see
        :meth:`route_for_request`).
        """
        candidates: list[str] = []
        for cfg in await self.iter_configs():
            if cfg.get("type") != slot_type:
                continue
            if cfg.get("default") is True:
                candidates.append(str(cfg.get("name", "")))
        if len(candidates) > 1:
            raise SlotConfigError(
                f"slot type {slot_type!r} has multiple default=true slots: "
                f"{candidates}; exactly one is allowed",
                details={"type": slot_type, "candidates": candidates},
            )
        return candidates[0] if candidates else None

    async def route_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> str | None:
        """Resolve a request of type ``slot_type`` to a concrete slot name.

        Plan §4.4 four-step routing:

          1. **Type match + default.** If a slot of ``type=slot_type``
             carries ``default = true``, prefer it.
          2. **Label filter overlay.** When ``required_labels`` is
             non-empty, the chosen slot's model must advertise every
             required label (sourced from the slot's
             ``model.labels`` list). The default is dropped if it
             can't satisfy the overlay.
          3. **Fall-through.** Otherwise pick the first ``enabled =
             true`` slot of ``slot_type`` in TOML declaration order
             (still satisfying the label overlay if any).
          4. ``None`` when nothing matches.
        """

        def _labels_of(cfg: dict[str, Any]) -> set[str]:
            model = cfg.get("model") or {}
            if isinstance(model, dict):
                raw = model.get("labels", ())
                if isinstance(raw, (list, tuple)):
                    return {str(x) for x in raw}
            return set()

        def _satisfies(cfg: dict[str, Any]) -> bool:
            if not required_labels:
                return True
            return set(required_labels).issubset(_labels_of(cfg))

        configs = [c for c in await self.iter_configs() if c.get("type") == slot_type]

        # Step 1+2: try the default first.
        default_name = await self.default_slot_for(slot_type)
        if default_name is not None:
            default_cfg = next((c for c in configs if c.get("name") == default_name), None)
            if (
                default_cfg is not None
                and default_cfg.get("enabled", True)
                and _satisfies(default_cfg)
            ):
                return default_name

        # Step 3: fall-through to first enabled + label-matching slot.
        for cfg in configs:
            if not cfg.get("enabled", True):
                continue
            if not _satisfies(cfg):
                continue
            return str(cfg.get("name", ""))

        return None

    async def add_slot(
        self,
        name: str,
        *,
        type: str,
        model: str,
        device: str = "gpu-rocm",
        group: str = "custom",
        port: int = 8081,
    ) -> Slot:
        """Programmatic ``hal0 slot add`` (plan §4.3).

        Validates kebab-case name, rejects seeded-name collisions,
        rejects unknown slot types. The default ``port`` matches the
        lemond control-plane port — Lemonade ignores per-slot ports
        but the SlotConfig schema requires one in the 8081-8099 range.

        Args:
            name: Kebab-case identifier; must not collide with a
                seeded slot (``SEEDED_SLOTS`` plus ``NPU_SEEDED_SLOTS``,
                independently of whether FLM is installed).
            type: One of ``llm | embedding | reranking | transcription
                | tts | image``.
            model: Lemonade model name to load by default.
            device: Hardware preference (``gpu-rocm | gpu-vulkan | cpu
                | npu``); see ``map_backend_to_device``. Default
                ``gpu-rocm`` matches Strix Halo seed semantics.
            group: Dashboard rollup group (default ``custom``).
            port: SlotConfig.port — kept for schema validity even
                though Lemonade doesn't use per-slot ports.
        """
        if not _SLOT_NAME_RE.match(name):
            raise SlotConfigError(
                f"slot name {name!r}: use lowercase alphanumeric, hyphens, underscores; "
                f"start with alphanumeric; max 32 chars",
                details={"slot": name},
            )
        # Reject collisions with ALL seeded slots (include the NPU trio
        # regardless of FLM presence — the names are reserved).
        reserved = set(SEEDED_SLOTS) | set(NPU_SEEDED_SLOTS)
        if name in reserved:
            raise SlotConfigError(
                f"slot {name!r} collides with a seeded slot; pick a different name",
                details={"slot": name, "reserved": sorted(reserved)},
            )
        if type not in _VALID_SLOT_TYPES:
            raise SlotConfigError(
                f"slot type {type!r} is not one of {sorted(_VALID_SLOT_TYPES)}",
                details={"slot": name, "type": type},
            )
        cfg = {
            "name": name,
            "port": port,
            "type": type,
            "device": device,
            "provider": "lemonade",
            "enabled": True,
            "group": group,
            "model": {"default": model},
        }
        return await self.create(name, cfg)

    async def remove_slot(self, name: str) -> None:
        """Programmatic ``hal0 slot remove`` (plan §4.3).

        Rejects seeded-slot names (use :meth:`unload` or
        ``capabilities.toml`` to disable a seeded slot, not delete it).
        No side effect on the underlying Lemonade model — the model
        stays in lemond's catalog.
        """
        reserved = set(SEEDED_SLOTS) | set(NPU_SEEDED_SLOTS)
        if name in reserved:
            raise SlotConfigError(
                f"slot {name!r} is seeded; cannot remove (disable it via capabilities.toml)",
                details={"slot": name, "reserved": sorted(reserved)},
            )
        await self.delete(name)

    # ── low-level lifecycle ──────────────────────────────────────────────────

    async def spawn(self, slot_name: str, slot_cfg: SlotConfig | dict[str, Any]) -> Slot:
        """Low-level: dispatch a Lemonade ``/v1/load`` for this slot.

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

        v0.2 (ADR-0008 §1/§6): every slot dispatches through
        :meth:`LemonadeProvider.load`. The per-slot systemd template
        ``hal0-slot@.service`` retired in PR-9; the lemond daemon owns
        process lifecycle and ``/v1/load`` is the single control-plane
        entry point.

        ``model_id`` (when set) overrides the slot config's
        ``model.default`` for swap semantics —
        :meth:`LemonadeProvider.load` resolves ``model.default`` itself,
        so we synthesise a transient override on the cfg dict.

        Any :class:`LemonadeError` subclass is wrapped as
        :class:`SlotSpawnFailed` so the caller's ``except Exception ->
        ERROR`` branch in :meth:`load` records a stable error envelope.
        """
        from hal0.lemonade.errors import LemonadeError
        from hal0.providers import lemonade_provider

        model_info = await self._resolve_model_info(model_id)

        cfg = _cfg_to_dict(slot_cfg)
        if model_id:
            existing_model = cfg.get("model")
            base_model = existing_model if isinstance(existing_model, dict) else {}
            cfg = {**cfg, "model": {**base_model, "default": model_id}}

        try:
            await lemonade_provider().load(cfg, model_info)
        except LemonadeError as exc:
            raise SlotSpawnFailed(
                f"lemonade /v1/load for {slot_name!r} failed: {exc}",
                details={
                    "slot": slot_name,
                    "model_id": model_id or _model_default(cfg),
                    "error_type": type(exc).__name__,
                },
            ) from exc
        except ValueError as exc:
            # LemonadeProvider raises ValueError for "no model set" —
            # surface as a typed slot error.
            raise SlotConfigError(
                f"lemonade load for {slot_name!r} rejected: {exc}",
                details={"slot": slot_name},
            ) from exc

    async def terminate(self, slot_name: str, *, timeout_s: float = 30.0) -> None:
        """Tell Lemonade to unload the slot's model and wait for confirmation.

        v0.2 (ADR-0008 §1/§6): every slot dispatches through
        :meth:`LemonadeProvider.unload`. Idempotent — the provider
        returns a noop dict when no model is assigned, and Lemonade
        itself treats unload-of-unloaded as a no-op.

        Public because the dispatcher's idle-monitor calls it directly
        to release VRAM without going through ``unload()``'s
        state-machine ceremony. ``timeout_s`` is preserved in the
        signature for caller compatibility; in practice
        ``/v1/unload`` is synchronous and the first ``_is_active``
        check succeeds.
        """
        from hal0.lemonade.errors import LemonadeError
        from hal0.providers import lemonade_provider

        cfg = await self._maybe_load_config(slot_name)
        # Resilient to the slot config being missing — terminate should
        # never fail just because someone deleted the TOML between load
        # and unload. Synthesise an empty cfg so the provider's
        # no-model-to-unload branch fires.
        if cfg is None:
            cfg = {"name": slot_name}

        try:
            await lemonade_provider().unload(cfg)
        except LemonadeError as exc:
            raise SlotSpawnFailed(
                f"lemonade /v1/unload for {slot_name!r} failed: {exc}",
                details={"slot": slot_name, "error_type": type(exc).__name__},
            ) from exc

        # Confirm with a single is-active probe so surrounding state
        # transitions see a consistent view.
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not await self._is_active(slot_name):
                return
            await asyncio.sleep(0.5)
        # Fallthrough: log + return cleanly. Failure here would mean
        # lemond reports the model as still loaded after /v1/unload
        # returned 2xx — that's a lemond bug, not a slot-state issue.
        log.warning(
            "slot.lemonade_unload_still_active",
            extra={"slot": slot_name, "timeout_s": timeout_s},
        )

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

        v0.2 (PR-10): no per-slot systemd override or env file is
        rendered — lemond owns process lifecycle. The TOML is the only
        on-disk artefact.

        PR-11 (plan §5.3 + ADR-0008 §5): rejects a second ``device=npu,
        type=llm, enabled=true`` slot — the AMDXDNA hardware context
        admits exactly one NPU LLM at a time. Disabled NPU LLM slots
        coexist; only the live anchor count is bounded.

        TOML serialisation depends on ``tomli_w`` (declared in
        pyproject.toml); kept inline so this module doesn't pull in
        ``config/loader.py``.
        """
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise SlotConfigError(
                "tomli_w not installed — required for slot config writes",
            ) from exc

        cfg_dict = _cfg_to_dict(slot_cfg)
        await self._check_npu_exclusivity(slot_name, cfg_dict)
        cfg_path = self._config_file(slot_name)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cfg_path.write_bytes(tomli_w.dumps(cfg_dict).encode("utf-8"))
        except OSError as exc:
            raise SlotConfigError(
                f"failed to write slot config {cfg_path}: {exc}",
                details={"slot": slot_name},
            ) from exc

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
                "provider": cfg_dict.get("provider", "lemonade"),
            },
            force=True,
        )
        return await self.status(slot_name)

    async def delete(self, slot_name: str) -> None:
        """Delete a dynamic slot. Seeded slots cannot be deleted."""
        if slot_name in self.seeded_slots():
            raise SlotConfigError(
                f"cannot delete seeded slot {slot_name!r}",
                details={"slot": slot_name},
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

    async def update_config(
        self,
        slot_name: str,
        updates: dict[str, Any],
    ) -> Slot:
        """Apply partial updates to a slot's TOML.

        v0.2 (PR-10): rewriting the TOML is enough — lemond reads the
        runtime config on the next ``/v1/load`` call. No per-slot
        systemd override or env file to re-render.
        """
        self._ensure_known(slot_name)
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise SlotConfigError("tomli_w not installed") from exc

        cfg = await self._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        # Shallow merge — nested dicts are replaced wholesale to keep update
        # semantics predictable. Callers wanting a partial nested update
        # build the sub-dict on their side first.
        cfg_dict.update(updates)

        # PR-11: re-run the NPU exclusivity guard whenever the merged
        # config could land a second device=npu, type=llm anchor (plan
        # §5.3). Cheap when no NPU LLM is involved — the helper short-
        # circuits on the merged cfg's own device/type.
        await self._check_npu_exclusivity(slot_name, cfg_dict)

        cfg_path = self._config_file(slot_name)
        try:
            cfg_path.write_bytes(tomli_w.dumps(cfg_dict).encode("utf-8"))
        except OSError as exc:
            raise SlotConfigError(
                f"failed to rewrite {cfg_path}: {exc}",
            ) from exc

        # Issue #359: invalidate stale top-level metadata in state.json
        # whenever the operator's update changes a field that's also
        # carried in ``extra``. ``_transition()`` shallow-merges extras
        # (intentional — provider/backend stamped at create-time survive
        # start→warm→ready), so without an explicit fix here the persisted
        # ``extra.backend`` survives a ``POST /api/slots/{name}/backend``
        # forever. ``status()`` short-circuits to this stale value as long
        # as the model is still in lemond's ``loaded[]`` (the adoption
        # probe never re-runs once ``rec`` exists).
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
            # on the next status read that trusts the mirror.
            if "device" in updates:
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

        return await self.status(slot_name)

    async def reconcile_unconfigured_slots(self) -> None:
        """One-shot startup pass: clear stuck ERROR on unconfigured slots.

        Before the empty-default short-circuit in :meth:`load`, slots
        with no ``model.default`` would get stamped ERROR every time
        the reconciler called lemonade.load() with an empty model name.
        Existing state.json snapshots from that era persist the red
        dot even after this fix lands. This pass rewrites them to
        OFFLINE with a "pick a model" message so the dashboard
        re-renders correctly without requiring the operator to click
        each slot.

        Best-effort — failures are logged and don't block startup.

        Reads in-memory state + state.json directly. Deliberately
        avoids :meth:`list` (which would trigger Lemonade adoption
        probes) and :meth:`_maybe_adopt_running_slot` (which would
        flip slots to READY without calling /v1/load) — this pass is
        a state-machine cleanup, not a fresh status check.
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
                # Pre-fix "model dropped from lemond unexpectedly"
                # ERRORs → OFFLINE+evicted. The fail-watcher now
                # treats eviction as a clean unload (Fix #3); existing
                # state.json snapshots from before that change
                # persist the red dot until cleared.
                if "model dropped from lemond" in msg:
                    await self._transition(
                        slot_name,
                        SlotState.OFFLINE,
                        message="model evicted from lemond (auto-reloads on next request)",
                        force=True,
                    )
                    continue
            except Exception as exc:
                log.warning(
                    "slot.reconcile_unconfigured_failed",
                    extra={"slot": slot_name, "error": str(exc)},
                )

    async def _persist_model_default(self, slot_name: str, model_id: str) -> None:
        """Write ``[model] default = <model_id>`` into the slot's TOML.

        Preserves every other key — only the ``model.default`` field is
        rewritten. Used by :meth:`load` after a successful explicit-
        model load (i.e. swap path) so the next reconciliation pass
        reads the right default instead of drifting back to the empty
        seed value that produced the "no model.default set" ERROR.

        Atomic via the same ``write_bytes`` pattern as :meth:`update_config`.
        Failures bubble up so the caller can log + soft-fail without
        affecting the live load state.
        """
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise SlotConfigError("tomli_w not installed") from exc

        cfg = await self._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        existing_model = cfg_dict.get("model")
        base_model = existing_model if isinstance(existing_model, dict) else {}
        cfg_dict = {**cfg_dict, "model": {**base_model, "default": model_id}}

        cfg_path = self._config_file(slot_name)
        try:
            cfg_path.write_bytes(tomli_w.dumps(cfg_dict).encode("utf-8"))
        except OSError as exc:
            raise SlotConfigError(
                f"failed to persist model.default to {cfg_path}: {exc}",
                details={"slot": slot_name, "model_id": model_id},
            ) from exc

    async def _check_npu_exclusivity(
        self,
        slot_name: str,
        cfg_dict: dict[str, Any],
    ) -> None:
        """Reject a write that would land a second NPU LLM anchor.

        Plan §5.3 + ADR-0008 §5: the AMDXDNA hardware context admits
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
        """
        device = cfg_dict.get("device")
        type_ = cfg_dict.get("type")
        if device != "npu" or type_ != "llm":
            return
        # The merged write is for an NPU LLM slot. If it isn't being
        # enabled, no collision is possible — at most one OTHER slot
        # could still be enabled, and that's the legal state we want.
        if cfg_dict.get("enabled") is False:
            return
        # Walk peers. Use _all_configured_slot_names() so we see slots
        # whose TOML exists but whose in-memory state hasn't been hit
        # yet (e.g. installer-seeded slots before first poll).
        peer_names = [n for n in self._all_configured_slot_names() if n != slot_name]
        offenders: list[str] = []
        for name in peer_names:
            try:
                peer = await self._load_slot_config(name)
            except SlotConfigError:
                # Malformed TOML doesn't block the user's legitimate
                # write — surface the malformed-slot warning via the
                # usual path instead of conflating it here.
                continue
            except SlotNotFound:
                continue
            if peer.get("device") != "npu" or peer.get("type") != "llm":
                continue
            if peer.get("enabled") is False:
                continue
            offenders.append(name)
        if offenders:
            raise NpuExclusivityViolation(
                "only one NPU LLM slot may be enabled at a time "
                f"(slot {slot_name!r} would conflict with {offenders[0]!r})",
                details={
                    "slot": slot_name,
                    "conflicting_slots": sorted(offenders),
                    "hint": "disable the existing NPU LLM slot before enabling another",
                },
            )

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
        """
        if self._pull_runner is None:
            return False
        try:
            return not bool(self._model_cache_check(model_id))
        except Exception as exc:
            # Defensive: a buggy cache check must not break load().  Log
            # and treat as "cached" so we fall through to STARTING and
            # the slot's own probe surfaces the real failure.
            log.warning(
                "slot.cache_check_failed",
                extra={"model_id": model_id, "error": str(exc)},
            )
            return False

    @staticmethod
    def _default_model_cache_check(model_id: str) -> bool:
        """Default predicate: registered + path-on-disk → cached.

        Imports the registry lazily so test fixtures that haven't wired
        ``HAL0_HOME`` still load the module.  Missing registry / model →
        not cached → caller flips through PULLING (where the pull hook
        either materialises the file or raises).
        """
        try:
            from hal0.registry.store import ModelNotFound, ModelRegistry
        except ImportError:
            return True
        try:
            model = ModelRegistry().get(model_id)
        except ModelNotFound:
            return False
        except Exception:
            return True
        path = getattr(model, "path", "") or ""
        if not path:
            return False
        try:
            return Path(path).exists()
        except OSError:
            return False

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
        """Return the number of currently-active ``serving()`` contexts."""
        return self._serving_count.get(slot_name, 0)

    # ── IDLE monitor ─────────────────────────────────────────────────────────

    async def start_idle_monitor(
        self,
        *,
        idle_after_s: float | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Start the background sweeper that demotes READY → IDLE.

        Idempotent — calling twice while the task is alive is a no-op.
        Callers in the API lifespan invoke this once at startup; tests
        construct a SlotManager with shorter intervals and start the
        monitor explicitly.
        """
        if idle_after_s is not None:
            self._idle_after_s = idle_after_s
        if interval_s is not None:
            self._idle_monitor_interval_s = interval_s
        existing = self._idle_monitor_task
        if existing is not None and not existing.done():
            return
        try:
            self._idle_monitor_task = asyncio.create_task(
                self._idle_monitor_loop(),
                name="hal0-slot-idle-monitor",
            )
        except RuntimeError:
            # No running loop (sync-context test).  Defer until callers
            # are in an async context.
            log.debug("slot.idle_monitor_no_loop")

    async def stop_idle_monitor(self) -> None:
        """Cancel the idle-monitor task if running.  Idempotent."""
        task = self._idle_monitor_task
        self._idle_monitor_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _idle_monitor_loop(self) -> None:
        """Periodically sweep READY slots for idle-timeout."""
        try:
            while True:
                await asyncio.sleep(self._idle_monitor_interval_s)
                try:
                    await self._sweep_idle_once()
                except Exception as exc:  # never let the monitor die quietly
                    log.warning("slot.idle_sweep_failed", extra={"error": str(exc)})
        except asyncio.CancelledError:
            raise

    async def _sweep_idle_once(self) -> None:
        """One pass: flip any READY slot past idle-timeout to IDLE."""
        now = time.time()
        for slot_name, ts in list(self._last_used.items()):
            if (now - ts) < self._idle_after_s:
                continue
            if self._serving_count.get(slot_name, 0) > 0:
                continue
            if self._current_state(slot_name) != SlotState.READY:
                continue
            try:
                await self._transition(
                    slot_name,
                    SlotState.IDLE,
                    message=f"idle for {now - ts:.0f}s",
                )
            except IllegalSlotTransition:
                # Raced with an unload — fine; next sweep will skip it.
                continue

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
            return {
                "name": slot_name,
                "port": rec.port,
                "backend": rec.extra.get("backend", "vulkan"),
                "provider": rec.extra.get("provider", "lemonade"),
                "model": {"default": rec.model_id or ""},
            }
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except OSError as exc:
            raise SlotConfigError(
                f"cannot read slot config {path}: {exc}",
                details={"slot": slot_name, "path": str(path)},
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            raise SlotConfigError(
                f"slot config {path} is not valid TOML: {exc}",
                details={"slot": slot_name, "path": str(path)},
            ) from exc
        if "name" not in data:
            data["name"] = slot_name
        return data

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
        info: dict[str, Any] = {"_model_key": model_id, "flm_tag": model_id}

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

    async def _await_ready(self, slot_name: str, port: int, provider: str) -> SlotState:
        """Resolve the slot's final readiness state after a Lemonade load.

        v0.2 (ADR-0008 §1/§6): ``_spawn_locked`` calls Lemonade's
        ``/v1/load`` which blocks until lemond has the model loaded.
        Any failure raises before we get here. So this method only
        answers "did the model actually end up in lemond's
        ``loaded[]``?". A confirming :meth:`LemonadeProvider.status`
        call separates the "loaded → READY" path from the
        "modelless slot → IDLE" path.

        ``port`` and ``provider`` parameters survive in the signature
        for caller compatibility; under Lemonade neither matters
        (lemond is on a fixed port, every slot speaks OpenAI). They're
        accepted and ignored.

        The probe is best-effort: if lemond is briefly unreachable
        (transient daemon hiccup) we still resolve READY because
        ``/v1/load`` already returned 2xx. Demotion to ERROR happens
        via the fail watcher's next tick if the model genuinely isn't
        loaded.

        Returns:
            SlotState.READY when the model is in ``/v1/health.loaded[]``.
            SlotState.IDLE when the slot has no model assigned (modelless).
        """
        del port, provider  # accepted for signature compatibility only

        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return SlotState.READY  # nothing more to verify
        model_name = _model_default(cfg)
        if not model_name:
            return SlotState.IDLE  # modelless slot
        try:
            from hal0.providers import lemonade_provider

            snap = await lemonade_provider().status({"name": slot_name, **cfg})
        except Exception as exc:
            log.warning(
                "slot.lemonade_await_ready_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return SlotState.READY
        if snap.get("loaded"):
            return SlotState.READY
        # Loaded == False after a successful /v1/load is unusual — the
        # safest landing state is IDLE so the dashboard surfaces the
        # discrepancy without erroring the slot outright.
        log.warning(
            "slot.lemonade_loaded_check_failed",
            extra={"slot": slot_name, "model_name": model_name, "snapshot": snap},
        )
        return SlotState.IDLE

    # ── adoption / drift reconcile (ISSUE #30) ───────────────────────────────

    async def _maybe_adopt_running_slot(self, slot_name: str, cfg: dict[str, Any]) -> Slot | None:
        """Adopt a slot whose model is live in lemond but whose state.json is stale.

        Returns the post-adoption Slot snapshot, or ``None`` when the
        slot's model is not in ``/v1/health.loaded[]`` — caller falls
        back to the on-disk record.

        v0.2 (ADR-0008): drift detection collapses to "is the slot's
        configured model in lemond's ``loaded[]``?". A slot whose
        model was loaded out-of-band (e.g. by another hal0-api
        instance or a manual /v1/load call) gets recognised the next
        time the dashboard polls ``status()``.
        """
        port = _cfg_port(cfg)
        model_id = _model_default(cfg) or None
        if model_id is None:
            # No model configured → nothing to adopt. Modelless slots
            # under Lemonade resolve as IDLE via the normal status path.
            return None
        try:
            from hal0.providers import lemonade_provider

            snap = await lemonade_provider().status({"name": slot_name, **cfg})
        except Exception as exc:
            log.debug(
                "slot.adoption_probe_skipped",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return None
        if not snap.get("loaded"):
            return None

        resolved = SlotState.READY
        extras: dict[str, Any] = {
            "backend": cfg.get("backend", "vulkan"),
            "provider": cfg.get("provider", "lemonade"),
            "adopted": True,
        }
        detail = "model present in /v1/health.loaded[]"
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


def _cfg_to_dict(cfg: SlotConfig | dict[str, Any]) -> dict[str, Any]:
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump()  # type: ignore[no-any-return]
    if isinstance(cfg, dict):
        return dict(cfg)
    raise SlotConfigError(f"unsupported slot cfg type {type(cfg).__name__}")


def _cfg_port(cfg: SlotConfig | dict[str, Any]) -> int:
    d = _cfg_to_dict(cfg)
    port = d.get("port") or d.get("slot", {}).get("port") or 0
    return int(port)


def _cfg_provider(cfg: SlotConfig | dict[str, Any]) -> str:
    d = _cfg_to_dict(cfg)
    return str(d.get("provider") or d.get("slot", {}).get("provider") or "lemonade")


def _model_default(cfg: SlotConfig | dict[str, Any]) -> str:
    d = _cfg_to_dict(cfg)
    model = d.get("model") or {}
    if isinstance(model, dict):
        return str(model.get("default") or "")
    return ""


def _cfg_effective_backend(cfg: SlotConfig | dict[str, Any]) -> str | None:
    """Derive the EFFECTIVE runtime backend token from a slot config.

    W3 truth fix: ``device`` is the v0.2 authoritative hardware-intent
    field; ``LemonadeProvider.load`` maps it to the per-load
    ``llamacpp_backend`` that Lemonade actually honors (overriding
    config.json's global ``llamacpp.backend`` for that model). The
    dashboard's SlotCard backend chip must therefore reflect what
    ``device`` will run — NOT the legacy, never-resynced ``backend``
    TOML field which drifts the moment a user flips backend (which only
    rewrites ``device``).

    Returns the normalized token ``rocm`` | ``vulkan`` | ``cpu`` |
    ``flm`` (NPU → ``flm``), or ``None`` when neither ``device`` nor a
    legacy ``backend`` is set so callers can fall through to "unknown".
    Pure/synchronous — safe on the status hot path.
    """
    d = _cfg_to_dict(cfg)
    device = d.get("device")
    if not device:
        # Legacy TOMLs may carry only ``backend``; promote it the same way
        # SlotConfig._promote_backend_to_device would, so we still emit the
        # device-derived token rather than the raw legacy string.
        legacy = d.get("backend")
        if not legacy:
            return None
        from hal0.config.schema import map_backend_to_device

        device = map_backend_to_device(str(legacy))
    # Reuse the single device→(recipe, llamacpp_backend) mapping so the
    # displayed token can never diverge from what gets sent on /v1/load.
    from hal0.providers.lemonade import device_to_backend

    recipe, llamacpp_backend = device_to_backend(str(device))
    # NPU → recipe="flm" with no llamacpp_backend; surface "flm".
    return llamacpp_backend or (recipe if recipe == "flm" else None)


__all__ = [
    "NPU_SEEDED_SLOTS",
    "SEEDED_SLOTS",
    "Slot",
    "SlotManager",
]
