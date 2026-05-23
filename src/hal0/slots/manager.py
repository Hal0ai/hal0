"""Slot lifecycle manager.

SlotManager owns every aspect of slot lifecycle: spawn, terminate, load,
unload, restart, swap, create, delete.  It talks to systemd via asyncio
subprocesses, reads and writes slot env files via
hal0.config.env.write_env_atomic, and persists state transitions to
/var/lib/hal0/slots/<name>/state.json.

Port target: haloai lib/slots.py (1082 lines).
Refactored for the state machine defined in hal0.slots.state (PLAN.md §5 Tier 3).

Architectural boundaries (ARCHITECTURE.md "Key boundaries"):
  - This module is *pure systemd*.  It does not import providers.  It does
    not make HTTP calls except for the health probe (which is a slot
    *lifecycle* concern, not a routing concern).
  - It depends on hal0.config.paths, hal0.config.env, and the rendering
    helpers in hal0.slots.unit_template.  It does NOT import from
    hal0.dispatcher.
  - All public methods return :class:`Slot` snapshots, never dicts —
    haloai's ``{"ok": False, "error": "..."}`` return shape is replaced
    by typed Hal0Error subclasses (Tier 1).

See PLAN.md §3 (module port plan) and PLAN.md §5 (reliability work).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from hal0.config import paths
from hal0.slots.state import (
    IllegalSlotTransition,
    SlotConfigError,
    SlotHealthFailed,
    SlotNotFound,
    SlotSpawnFailed,
    SlotState,
    SlotStateRecord,
    is_transition_legal,
    provider_requires_model,
    read_state,
    write_state_atomic,
)
from hal0.slots.unit_template import (
    override_path,
    render_override,
    write_slot_env,
)

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig

log = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────────

# TIER1: Replaces haloai's hardcoded 2s cold-boot probe at lib/upstreams.py:500.
# Adaptive backoff: (0.5s, 1s, 2s, 5s, 10s) with ±20% jitter.  Total grace
# capped at HEALTH_GRACE_TOTAL_S.  Per-slot override may live in
# hardware.json once the hardware-probe agent's contract lands.
_HEALTH_BACKOFF_S: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0)
_HEALTH_GRACE_TOTAL_S: float = 180.0
_HEALTH_PROBE_TIMEOUT_S: float = 5.0
_SENTINEL_PROMPT: str = "ping"

# How long systemctl operations get before we treat them as hung.
_SYSTEMCTL_TIMEOUT_S: float = 30.0

# TIER1: Push-driven failure detector.  While a slot is in a "live" state
# (READY / SERVING / IDLE) a background task polls ``systemctl is-active``
# every _FAIL_WATCH_INTERVAL_S seconds.  When the unit unexpectedly dies
# (OOM, segfault, mid-warmup pull failure, …) the watcher flips state to
# ERROR and emits the SSE frame within ~1s of detection, instead of
# waiting for the next ``status()`` poll or for ``_await_ready``'s 180s
# grace to expire.  See task #11.
_FAIL_WATCH_INTERVAL_S: float = 2.0
_FAIL_WATCH_LIVE_STATES: frozenset[SlotState] = frozenset(
    {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
)

# Idle-monitor defaults.  A READY slot whose last activity is older than
# _IDLE_AFTER_S gets demoted to IDLE so dashboards / unload heuristics can
# distinguish "warm but quiet" from "warm and serving".  Per task #10, the
# default matches haloai's 300s; constructor args + start_idle_monitor()
# accept overrides for tests and ops tuning.
_IDLE_AFTER_S: float = 300.0
_IDLE_MONITOR_INTERVAL_S: float = 30.0

# ISSUE #31: How long ``_await_ready`` waits in the "alive but no model"
# regime before resolving the slot to IDLE instead of continuing to wait
# for a model to appear.  Kept short so a llama-server launched with
# ``--model ""`` doesn't sit in WARMING for the full 180s grace window
# before the dashboard sees an actionable state.
_IDLE_STABILISE_S: float = 3.0


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
    ) -> None:
        self.name = name
        self.state = state
        self.port = port
        self.model_id = model_id
        self.backend = backend
        self.metadata: dict[str, Any] = metadata or {}

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for API responses."""
        return {
            "name": self.name,
            "state": self.state.value,
            "port": self.port,
            "model_id": self.model_id,
            "backend": self.backend,
            "metadata": self.metadata,
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

    BUILTIN_SLOTS: tuple[str, ...] = ("primary", "embed", "stt", "tts", "img")

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
        # TIER1: per-slot background tasks that poll systemctl is-active and
        # push a READY→ERROR transition the instant the unit dies.  Keyed by
        # slot name; only present while the slot is in a live state.
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

    def _service_name(self, name: str) -> str:
        """systemd template-unit instance for this slot."""
        return f"hal0-slot@{name}.service"

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
        """Poll ``systemctl is-active`` and flip to ERROR on unit death.

        Runs as a background task while the slot is in READY/SERVING/IDLE.
        Detection latency = up to one poll interval (~2s).  Exits cleanly
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
                    # systemctl failure is unusual — log and keep polling.
                    log.warning(
                        "slot.fail_watch_is_active_failed",
                        extra={"slot": slot_name, "error": str(exc)},
                    )
                    continue
                if active:
                    continue
                # Unit went inactive/failed while we believed it was live.
                # Re-check state once more — load/unload may have moved us
                # legitimately during the is-active call.
                current = self._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                try:
                    await self._transition(
                        slot_name,
                        SlotState.ERROR,
                        message="systemd unit died unexpectedly",
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

    # ── systemctl wrapper ────────────────────────────────────────────────────

    async def _systemctl(self, *args: str) -> tuple[int, str, str]:
        """Run a systemctl command and return (rc, stdout, stderr).

        TIER1: returns the full triple — callers decide how to react.
        haloai's bool-only return at lib/slots.py:177-186 hid the stderr
        needed to surface a meaningful error envelope.
        """
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SYSTEMCTL_TIMEOUT_S
            )
        except TimeoutError as exc:
            proc.kill()
            with contextlib.suppress(asyncio.CancelledError):
                await proc.wait()
            raise SlotSpawnFailed(
                f"systemctl {' '.join(args)} timed out after {_SYSTEMCTL_TIMEOUT_S}s",
                details={"args": list(args)},
            ) from exc
        rc = proc.returncode if proc.returncode is not None else -1
        return (
            rc,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    async def _is_active(self, name: str) -> bool:
        # v0.2 (ADR-0008 §6): under Lemonade there is no per-slot systemd
        # unit; "is-active" maps to "is the slot's model in lemond's
        # loaded[]". Failures of the lemond probe are treated as
        # inactive so the status() reconciler doesn't lock a slot into
        # a stale READY when lemond is down.
        if _lemonade_active():
            return await self._lemonade_is_active(name)
        rc, _, _ = await self._systemctl("is-active", self._service_name(name))
        return rc == 0

    async def _lemonade_is_active(self, slot_name: str) -> bool:
        """Lemonade-mode replacement for systemctl is-active.

        True when the slot's configured model appears in lemond's
        ``/v1/health.loaded[]``. Probe errors are coerced to False so
        ``status()``'s drift reconciler runs (re-load on next request).
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
        """Hot-swap a slot's model: terminate → rewrite env → spawn → await."""
        if not new_model_id:
            raise SlotConfigError("swap requires a non-empty model id")
        self._ensure_known(slot_name)
        await self.unload(slot_name)
        return await self.load(slot_name, model_id=new_model_id)

    # ── queries ──────────────────────────────────────────────────────────────

    async def status(self, slot_name: str) -> Slot:
        """Return a snapshot of the current slot state.

        Combines the persisted state.json with a live systemd `is-active`
        check.  Reconciliation runs in BOTH directions:

          - state.json says READY/SERVING/IDLE but the unit is dead →
            transition to ERROR so the dashboard reflects reality.
          - state.json says OFFLINE / ERROR (or is missing) but the unit
            is *active* and a fast health probe succeeds → adopt the
            running slot into READY/IDLE.  This is the issue #30 fix: a
            kokoro / moonshine slot started outside ``load()`` (e.g. via
            ``systemctl enable --now`` on boot, or by an external
            orchestrator) used to sit at OFFLINE forever because the
            state writer was only driven by the load() path.
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
            return Slot(
                name=slot_name,
                state=SlotState.OFFLINE,
                port=int(cfg.get("port") or 0) if cfg else 0,
                backend=cfg.get("backend") if cfg else None,
                metadata={
                    "provider": cfg.get("provider"),
                    "backend": cfg.get("backend"),
                }
                if cfg
                else {},
            )
        # Reconcile with systemd reality.
        observed = rec.state
        if observed in (SlotState.READY, SlotState.SERVING, SlotState.IDLE) and not active:
            # systemd says dead; record reflects ready — drift.
            await self._transition(
                slot_name,
                SlotState.ERROR,
                message="systemd unit not active",
                force=True,
            )
            observed = SlotState.ERROR
        elif observed in (SlotState.OFFLINE, SlotState.ERROR) and active:
            # ISSUE #30: the inverse drift — state.json says we're not
            # running, but systemd has a live unit.  Adoption probe
            # picks the appropriate ready/idle state for the running
            # process.  Failure of the probe leaves the record alone so
            # the next status() call retries.
            cfg = await self._maybe_load_config(slot_name)
            if cfg:
                adopted = await self._maybe_adopt_running_slot(slot_name, cfg)
                if adopted is not None:
                    return adopted
        elif observed in (SlotState.READY, SlotState.SERVING) and active and not rec.model_id:
            # Migration / self-healing: a stale state.json from before the
            # adoption guard could have READY/SERVING with model_id="".
            # Re-run adoption when the provider actually needs a model so
            # the record either picks up a live model_id from /v1/models
            # or demotes to IDLE.  Gate on the empty-model-id check so the
            # hot path stays cheap on healthy records.
            provider_hint = rec.extra.get("provider")
            if provider_hint and provider_requires_model(str(provider_hint)):
                cfg = await self._maybe_load_config(slot_name)
                if cfg:
                    adopted = await self._maybe_adopt_running_slot(slot_name, cfg)
                    if adopted is not None:
                        return adopted
        # Re-hydrate the top-level backend from the slot's TOML when the
        # state.json record predates the extras-carry change (older state
        # files were written without ``extra.backend``). The dashboard's
        # SlotCard chips key off ``slot.backend`` directly — without this
        # they'd show 'slot' (unknown) until the user re-loaded the slot.
        backend = rec.extra.get("backend")
        if backend is None:
            cfg = await self._maybe_load_config(slot_name)
            if cfg:
                backend = cfg.get("backend")
        return Slot(
            name=slot_name,
            state=observed,
            port=rec.port,
            model_id=rec.model_id,
            backend=backend,
            metadata={
                "updated_at": rec.updated_at,
                "message": rec.message,
                **rec.extra,
                **({"backend": backend} if backend and "backend" not in rec.extra else {}),
            },
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

        Lightweight — reads TOML only, never calls systemctl.  Intended
        for startup hooks (e.g. ``lifespan`` auto-registering slots as
        upstreams) that need slot metadata before any real systemd
        interaction.

        Returns:
            One dict per slot, in stable order.  Each dict carries at
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
                    slot=name,
                    error=str(exc),
                )
                continue
            out.append(cfg)
        return out

    # ── low-level lifecycle ──────────────────────────────────────────────────

    async def spawn(self, slot_name: str, slot_cfg: SlotConfig | dict[str, Any]) -> Slot:
        """Low-level: render env + override, then systemctl start.

        Called by load() after the model is confirmed present in the
        registry.  Public for tests and for the installer's first-run path.
        Acquires the per-slot lock; load() callers can use _spawn_locked.
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

        v0.2 (ADR-0008 §6): when ``HAL0_BACKEND=lemonade`` we delegate
        to :meth:`LemonadeProvider.load` instead of rendering an
        override.conf + running ``systemctl start``. The per-slot
        systemd template ``hal0-slot@.service`` no longer exists (PR-9
        retired it); the lemond daemon owns process lifecycle and
        ``/v1/load`` is the single control-plane entry point.

        Legacy (v0.1.x) toolbox path is preserved unchanged for any
        deployment still on the per-modality container model. PR-10
        retires that path along with the SlotManager simplification.
        """
        model_info = await self._resolve_model_info(model_id)

        if _lemonade_active():
            await self._spawn_via_lemonade(slot_name, slot_cfg, model_id, model_info)
            return

        # TIER1: atomic env write — write_slot_env() delegates to
        # write_env_atomic (tmpfile + os.replace).  haloai's
        # lib/slots.py:551-622 was rewritten here.
        write_slot_env(slot_name, slot_cfg, model_info, model_id_override=model_id)

        # Render the per-slot drop-in.  We rewrite on every spawn so a
        # backend/port edit picked up via update_config takes effect on
        # next start.
        override = render_override(slot_name, slot_cfg, model_info)
        op = override_path(slot_name)
        try:
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(override, encoding="utf-8")
        except OSError as exc:
            raise SlotSpawnFailed(
                f"failed to write override.conf for {slot_name!r}: {exc}",
                details={"slot": slot_name, "path": str(op)},
            ) from exc

        # daemon-reload then start.  Tolerant of "no such unit" only if
        # daemon-reload itself fails for a hard reason.
        rc, _, stderr = await self._systemctl("daemon-reload")
        if rc != 0:
            raise SlotSpawnFailed(
                f"systemctl daemon-reload failed: {stderr.strip()}",
                details={"slot": slot_name},
            )
        rc, _, stderr = await self._systemctl("start", self._service_name(slot_name))
        if rc != 0:
            raise SlotSpawnFailed(
                f"systemctl start hal0-slot@{slot_name} failed: {stderr.strip()}",
                details={"slot": slot_name, "stderr": stderr.strip()},
            )

    async def _spawn_via_lemonade(
        self,
        slot_name: str,
        slot_cfg: SlotConfig | dict[str, Any],
        model_id: str | None,
        model_info: dict[str, Any],
    ) -> None:
        """Lemonade-mode spawn: POST /v1/load through LemonadeProvider.

        Lemond is shared across every hal0 slot; the slot abstraction
        survives only as identity + state. ``model_id`` (when set)
        overrides the slot config's ``model.default`` for swap semantics
        — :meth:`LemonadeProvider.load` resolves ``model.default`` itself,
        so we synthesise a transient override on the cfg dict.

        Any :class:`LemonadeError` subclass is wrapped as
        :class:`SlotSpawnFailed` so the caller's ``except Exception ->
        ERROR`` branch in :meth:`load` records a stable error envelope
        with ``slot.spawn_failed`` semantics.
        """
        # Re-import locally so the module-level import order stays
        # clean (providers/__init__.py imports from this subtree's
        # config/registry modules).
        from hal0.lemonade.errors import LemonadeError
        from hal0.providers import lemonade_provider

        cfg = _cfg_to_dict(slot_cfg)
        if model_id:
            # Honour the model override the same way the toolbox path
            # does via ``write_slot_env(..., model_id_override=...)``.
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
            # surface as a typed slot error consistent with the toolbox
            # path's missing-config behaviour.
            raise SlotConfigError(
                f"lemonade load for {slot_name!r} rejected: {exc}",
                details={"slot": slot_name},
            ) from exc

    async def terminate(self, slot_name: str, *, timeout_s: float = 30.0) -> None:
        """Low-level: issue stop and wait for the slot to be unloaded.

        Public because the dispatcher's idle-monitor calls it directly to
        release VRAM without going through unload()'s state-machine
        ceremony.  TIER1: bubbles stderr on failure.

        v0.2 (ADR-0008): under Lemonade we POST /v1/unload via
        :meth:`LemonadeProvider.unload` instead of calling
        ``systemctl stop``. Mirrors the spawn branch above.
        """
        if _lemonade_active():
            await self._terminate_via_lemonade(slot_name, timeout_s=timeout_s)
            return

        rc, _, stderr = await self._systemctl("stop", self._service_name(slot_name))
        if rc != 0:
            raise SlotSpawnFailed(
                f"systemctl stop hal0-slot@{slot_name} failed: {stderr.strip()}",
                details={"slot": slot_name, "stderr": stderr.strip()},
            )
        # Poll for is-active to flip to inactive — systemctl stop returns
        # before the container actually exits when Type=simple.
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not await self._is_active(slot_name):
                return
            await asyncio.sleep(0.5)
        raise SlotSpawnFailed(
            f"slot {slot_name!r} still active {timeout_s}s after systemctl stop",
            details={"slot": slot_name},
        )

    async def _terminate_via_lemonade(self, slot_name: str, *, timeout_s: float) -> None:
        """Lemonade-mode terminate: POST /v1/unload through LemonadeProvider.

        Idempotent — LemonadeProvider.unload returns a noop dict when no
        model is assigned, and Lemonade itself treats unload of an
        unloaded model as a no-op. We still poll ``_is_active`` to
        match the toolbox path's "stop returns before exit" contract;
        in practice /v1/unload is synchronous so the first check
        succeeds.
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

        # Confirm with a single is-active probe before returning so the
        # surrounding state transitions see a consistent view. We don't
        # busy-loop the full timeout_s budget for Lemonade because the
        # unload call is synchronous — but we keep the parameter in
        # the signature so callers don't have to special-case backends.
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

        Writes the slot TOML to /etc/hal0/slots/<name>.toml, the override
        drop-in, the env file, and the initial state.json (OFFLINE).  Does
        NOT start the slot — that's load()'s job.

        NOTE: TOML serialisation depends on tomli_w which is in
        pyproject.toml; this keeps the slot config writable without a
        cross-subtree dependency on config/loader.py.
        """
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise SlotConfigError(
                "tomli_w not installed — required for slot config writes",
            ) from exc

        cfg_dict = _cfg_to_dict(slot_cfg)
        cfg_path = self._config_file(slot_name)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cfg_path.write_bytes(tomli_w.dumps(cfg_dict).encode("utf-8"))
        except OSError as exc:
            raise SlotConfigError(
                f"failed to write slot config {cfg_path}: {exc}",
                details={"slot": slot_name},
            ) from exc

        # Pre-render env + override so the slot is ready to start.
        write_slot_env(slot_name, cfg_dict, model_info=None)
        op = override_path(slot_name)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(render_override(slot_name, cfg_dict, model_info=None), encoding="utf-8")

        # Initialise state.
        await self._transition(
            slot_name,
            SlotState.OFFLINE,
            port=_cfg_port(cfg_dict),
            model_id=_model_default(cfg_dict) or None,
            extra={
                "backend": cfg_dict.get("backend", "vulkan"),
                "provider": cfg_dict.get("provider", "llama-server"),
            },
            force=True,
        )
        return await self.status(slot_name)

    async def delete(self, slot_name: str) -> None:
        """Delete a dynamic slot.  Built-in slots cannot be deleted."""
        if slot_name in self.BUILTIN_SLOTS:
            raise SlotConfigError(
                f"cannot delete built-in slot {slot_name!r}",
                details={"slot": slot_name},
            )
        self._ensure_known(slot_name)
        # Make sure it's stopped first.
        current = self._current_state(slot_name)
        if current != SlotState.OFFLINE:
            await self.unload(slot_name)

        # Remove override drop-in, env, state.json, and the slot config.
        for path in (
            override_path(slot_name),
            paths.slot_data_dir(slot_name) / "env",
            self._state_file(slot_name),
            self._config_file(slot_name),
        ):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        # Drop in-memory bookkeeping last.
        self._states.pop(slot_name, None)
        self._locks.pop(slot_name, None)
        self._last_used.pop(slot_name, None)
        # daemon-reload so systemd notices the missing drop-in.
        await self._systemctl("daemon-reload")

    async def update_config(
        self,
        slot_name: str,
        updates: dict[str, Any],
    ) -> Slot:
        """Apply partial updates to a slot's TOML.  Re-renders override+env."""
        self._ensure_known(slot_name)
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise SlotConfigError("tomli_w not installed") from exc

        cfg = await self._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        # Shallow merge — nested dicts are replaced wholesale to keep update
        # semantics predictable.  Callers wanting a partial nested update
        # build the sub-dict on their side first.
        cfg_dict.update(updates)

        cfg_path = self._config_file(slot_name)
        try:
            cfg_path.write_bytes(tomli_w.dumps(cfg_dict).encode("utf-8"))
        except OSError as exc:
            raise SlotConfigError(
                f"failed to rewrite {cfg_path}: {exc}",
            ) from exc

        # Re-render env + override so the next start picks up changes.
        write_slot_env(slot_name, cfg_dict, model_info=None)
        op = override_path(slot_name)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(render_override(slot_name, cfg_dict, model_info=None), encoding="utf-8")

        return await self.status(slot_name)

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
                "provider": rec.extra.get("provider", "llama-server"),
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
        """Block until the slot's HTTP health probe converges, return final state.

        Returns:
            SlotState.READY when the upstream is reachable AND can fulfil an
            inference request (sentinel passes / model_loaded=true / etc.).
            SlotState.IDLE when the upstream is reachable but cannot serve
            requests right now — for example llama-server / FLM /
            kokoro launched with no model. Issue #31: ``ready`` would be
            an active UX trap here because routers pick ready slots first
            and immediately 4xx on every inference call.

        Raises:
            SlotHealthFailed: the upstream never became reachable at all
            within the grace window (transport errors, timeouts).

        TIER1 fix for haloai lib/slots.py:899-920 and lib/upstreams.py:500-520:
          - Adaptive backoff (0.5, 1, 2, 5, 10s).
          - Total grace window of 180s, no per-probe 2s hardcap.
          - FLM / vLLM-style providers (anything OpenAI-compatible without
            a /health endpoint) require a *non-empty* /v1/models response
            AND a tiny /v1/chat/completions with max_tokens=1 before we
            declare READY.  Empty /v1/models is no longer "good enough".

        ISSUE #31: After the upstream stabilises (probe stops returning
        transport errors), an empty ``/v1/models`` or ``model_loaded=false``
        body resolves the slot to IDLE rather than retrying until the
        grace window expires.  This lets dashboards distinguish
        "container running with no model" from "container failing to
        start".

        v0.2 (ADR-0008 §3 + §6): under Lemonade the load is synchronous —
        ``POST /v1/load`` blocks until the model is paged into the
        backend (or returns a documented error). When ``_spawn_locked``
        returned 2xx via :meth:`LemonadeProvider.load`, the model is by
        construction ready to serve. Short-circuit to READY rather than
        opening an HTTP probe against a slot port that doesn't host a
        per-slot process under Lemonade.
        """
        if _lemonade_active():
            return await self._lemonade_await_ready(slot_name, provider)
        deadline = time.monotonic() + _HEALTH_GRACE_TOTAL_S
        # Once the upstream returns ANY structured response (even one
        # indicating no model), we know it's alive.  After ``_IDLE_STABILISE_S``
        # of consecutive "alive but empty" responses we resolve to IDLE
        # instead of waiting the full grace window for a model to appear.
        idle_observation_since: float | None = None
        attempt = 0
        last_error: str = "no probe yet"
        url = f"http://127.0.0.1:{port}"

        def _observed_idle() -> SlotState | None:
            """Promote a steady stream of "alive-but-no-model" observations to IDLE.

            Closure over ``idle_observation_since`` so the caller can clear
            the timer if the slot recovers (e.g. a model finishes loading
            mid-warmup).
            """
            nonlocal idle_observation_since
            if idle_observation_since is None:
                idle_observation_since = time.monotonic()
                return None
            if (time.monotonic() - idle_observation_since) >= _IDLE_STABILISE_S:
                return SlotState.IDLE
            return None

        while time.monotonic() < deadline:
            backoff = _HEALTH_BACKOFF_S[min(attempt, len(_HEALTH_BACKOFF_S) - 1)]
            jitter = backoff * 0.2 * (random.random() * 2 - 1)
            wait_s = max(0.1, backoff + jitter)
            try:
                async with httpx.AsyncClient(timeout=_HEALTH_PROBE_TIMEOUT_S) as client:
                    strategy = _provider_health_strategy(provider)
                    if strategy == "chat_sentinel":
                        # FLM / vLLM path — providers that advertise
                        # models before inference works.
                        resp = await client.get(f"{url}/v1/models")
                        if resp.status_code == 200:
                            data = (
                                resp.json()
                                if "json" in resp.headers.get("content-type", "")
                                else {}
                            )
                            models = data.get("data", []) if isinstance(data, dict) else []
                            if models:
                                idle_observation_since = None
                                if await _sentinel_inference(client, url, models[0]):
                                    return SlotState.READY
                                last_error = "sentinel inference failed"
                            else:
                                # ISSUE #31: /v1/models is empty but the
                                # endpoint is alive — this is the canonical
                                # llama-server --model "" / FLM no-model
                                # shape.  Stabilise to IDLE rather than
                                # treat as a transient failure.
                                last_error = "/v1/models returned empty data array"
                                idle = _observed_idle()
                                if idle is not None:
                                    return idle
                        else:
                            idle_observation_since = None
                            last_error = f"/v1/models HTTP {resp.status_code}"
                    elif strategy == "health_with_model_loaded":
                        # Moonshine: /health stays 200 while model is
                        # loading; require model_loaded=true in body.
                        resp = await client.get(f"{url}/health")
                        if resp.status_code == 200:
                            try:
                                body = resp.json()
                            except Exception:
                                body = {}
                            if isinstance(body, dict) and body.get("model_loaded"):
                                idle_observation_since = None
                                return SlotState.READY
                            # ISSUE #31: /health returns 200 but the
                            # body advertises no loaded model — the
                            # container is up, just empty.
                            last_error = f"/health 200 but model_loaded != true (body={body!r})"
                            idle = _observed_idle()
                            if idle is not None:
                                return idle
                        else:
                            idle_observation_since = None
                            last_error = f"/health HTTP {resp.status_code}"
                    else:
                        # llama-server / kokoro — /health 2xx is authoritative.
                        # ISSUE #31: pair /health with a /v1/models probe
                        # so a llama-server launched with --model "" lands
                        # in IDLE instead of READY.  /health alone reports
                        # 200 even when no model is loaded.
                        resp = await client.get(f"{url}/health")
                        if resp.status_code == 200:
                            try:
                                models_resp = await client.get(f"{url}/v1/models")
                            except httpx.HTTPError:
                                # /v1/models may not exist on every
                                # /health-based provider (comfyui, custom
                                # backends).  /health 2xx is sufficient.
                                idle_observation_since = None
                                return SlotState.READY
                            if models_resp.status_code != 200:
                                # Same: provider doesn't expose /v1/models.
                                idle_observation_since = None
                                return SlotState.READY
                            try:
                                models_data = models_resp.json()
                            except Exception:
                                models_data = {}
                            models = (
                                models_data.get("data", []) if isinstance(models_data, dict) else []
                            )
                            if models:
                                idle_observation_since = None
                                return SlotState.READY
                            # /health=200 but /v1/models is empty:
                            # container is up with no model loaded.
                            last_error = "/health 200 but /v1/models empty"
                            idle = _observed_idle()
                            if idle is not None:
                                return idle
                        else:
                            idle_observation_since = None
                            last_error = f"/health HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                # Transport error → upstream genuinely unreachable; do
                # not let prior idle observations carry over.
                idle_observation_since = None
                last_error = f"http error: {exc}"
            except Exception as exc:
                # TIER1: log + retain message; never silent.
                log.warning(
                    "slot.health_probe_unexpected_error",
                    extra={"slot": slot_name, "error": str(exc)},
                )
                idle_observation_since = None
                last_error = f"unexpected: {exc}"
            attempt += 1
            await asyncio.sleep(wait_s)

        raise SlotHealthFailed(
            f"slot {slot_name!r} did not become healthy within {_HEALTH_GRACE_TOTAL_S}s",
            details={"slot": slot_name, "last_error": last_error},
        )

    async def _lemonade_await_ready(self, slot_name: str, provider: str) -> SlotState:
        """Lemonade-mode readiness resolution.

        ``_spawn_locked``'s Lemonade branch calls /v1/load which blocks
        until lemond has the model loaded; any failure raises before we
        get here. So the only question this method answers is "did the
        model actually end up in lemond's loaded[]?". A confirming
        :meth:`LemonadeProvider.status` call separates the
        "loaded — go READY" path from the "modelless slot — go IDLE"
        path that the toolbox provider's _await_ready handles for
        --model "" launches.

        The probe is best-effort: if lemond is briefly unreachable
        (transient daemon hiccup) we still resolve READY because /v1/load
        already returned 2xx. Demotion to ERROR happens via the fail
        watcher's next tick if the model genuinely isn't loaded.
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return SlotState.READY  # nothing more to verify
        model_name = _model_default(cfg)
        if not model_name:
            return SlotState.IDLE  # modelless slot; mirrors toolbox path
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
        """Adopt a running-but-OFFLINE slot into READY/IDLE if its probe passes.

        Returns the post-adoption Slot snapshot, or ``None`` when the slot
        is not (yet) adoptable — caller falls back to the on-disk record.

        Called from ``status()`` when ``state.json`` reports OFFLINE/ERROR
        but ``systemctl is-active`` reports the unit is up.  Resolves
        issue #30: kokoro / moonshine slots started outside the hal0
        ``load()`` lifecycle used to sit at OFFLINE forever.
        """
        port = _cfg_port(cfg)
        provider = _cfg_provider(cfg)
        if port <= 0:
            return None
        ok, resolved, detail = await self._probe_once(port, provider)
        if not ok or resolved is None:
            log.debug(
                "slot.adoption_probe_skipped",
                extra={"slot": slot_name, "port": port, "detail": detail},
            )
            return None
        model_id = _model_default(cfg) or None
        extras: dict[str, Any] = {
            "backend": cfg.get("backend", "vulkan"),
            "provider": cfg.get("provider", "llama-server"),
            "adopted": True,
        }
        # Modelless adoption to READY is the original bug: a slot with
        # no [model] default and an empty /v1/models would land at READY
        # with model_id=None.  For providers that need an explicit model
        # we either sniff one from /v1/models or demote to IDLE.
        detected_model: str | None = None
        if resolved == SlotState.READY and model_id is None and provider_requires_model(provider):
            detected_model = await _fetch_first_concrete_model(port)
            if detected_model:
                model_id = detected_model
                extras["detected_model"] = detected_model
            else:
                resolved = SlotState.IDLE
                extras["degraded_to_idle"] = True
                detail = f"{detail}; no model_id available — demoted to idle"
        # ``force=True`` is required: the legal-transition map does not
        # contain offline→ready or offline→idle.  Adoption is the
        # exception — the state machine is recovering from drift, not
        # following the normal load() path.
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
                "detected_model": detected_model,
                "degraded_to_idle": extras.get("degraded_to_idle", False),
            },
        )
        # Build the Slot snapshot directly from the just-written record.
        # We deliberately do NOT recurse through ``status()`` here — the
        # caller already checked is-active, and another full pass would
        # re-probe systemctl + re-run the reconciler.
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

    async def _probe_once(self, port: int, provider: str) -> tuple[bool, SlotState | None, str]:
        """One-shot health probe — no retries, no backoff.

        Used by ``status()`` to adopt an already-running slot (issue #30):
        when state.json reports OFFLINE but ``systemctl is-active`` says
        the unit is up, a single round-trip to the provider's health
        surface decides whether to promote the slot to READY/IDLE or
        leave it alone.

        Returns:
            ``(ok, resolved, detail)``:
              - ``ok=True``  + ``resolved=READY``: model loaded and
                serving (kokoro/llama-server /health 2xx + non-empty
                /v1/models, moonshine /health body model_loaded=true,
                FLM sentinel passes).
              - ``ok=True``  + ``resolved=IDLE``: process reachable but
                no model loaded — analogous to the warming→idle case in
                ``_await_ready``.
              - ``ok=False`` + ``resolved=None``: not yet adoptable.
                ``detail`` carries a human-readable last_error so the
                caller can log or surface it.
        """
        url = f"http://127.0.0.1:{port}"
        strategy = _provider_health_strategy(provider)
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_PROBE_TIMEOUT_S) as client:
                if strategy == "chat_sentinel":
                    resp = await client.get(f"{url}/v1/models")
                    if resp.status_code != 200:
                        return False, None, f"/v1/models HTTP {resp.status_code}"
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    models = data.get("data", []) if isinstance(data, dict) else []
                    if not models:
                        # Process alive, no model — IDLE, not OFFLINE.
                        return True, SlotState.IDLE, "/v1/models empty"
                    # Don't run a sentinel inference here — adoption must
                    # be cheap.  A successful /v1/models with at least one
                    # entry is enough to call the slot READY for status()
                    # purposes; if the next real request 5xxs, the
                    # fail-watcher / status reconciler will demote it.
                    return True, SlotState.READY, "/v1/models non-empty"
                if strategy == "health_with_model_loaded":
                    resp = await client.get(f"{url}/health")
                    if resp.status_code != 200:
                        return False, None, f"/health HTTP {resp.status_code}"
                    try:
                        body = resp.json()
                    except Exception:
                        body = {}
                    if isinstance(body, dict) and body.get("model_loaded"):
                        return True, SlotState.READY, "model_loaded"
                    return True, SlotState.IDLE, "model_loaded != true"
                # /health-based providers: pair /health with /v1/models
                # so a slot started with no model resolves to IDLE.
                resp = await client.get(f"{url}/health")
                if resp.status_code != 200:
                    return False, None, f"/health HTTP {resp.status_code}"
                try:
                    models_resp = await client.get(f"{url}/v1/models")
                except httpx.HTTPError:
                    return True, SlotState.READY, "/health 2xx (no /v1/models)"
                if models_resp.status_code != 200:
                    return True, SlotState.READY, "/health 2xx (/v1/models not exposed)"
                try:
                    models_data = models_resp.json()
                except Exception:
                    models_data = {}
                models = models_data.get("data", []) if isinstance(models_data, dict) else []
                if models:
                    return True, SlotState.READY, "/v1/models non-empty"
                return True, SlotState.IDLE, "/v1/models empty"
        except httpx.HTTPError as exc:
            return False, None, f"http error: {exc}"
        except Exception as exc:
            log.warning(
                "slot.adoption_probe_unexpected_error",
                extra={"port": port, "provider": provider, "error": str(exc)},
            )
            return False, None, f"unexpected: {exc}"


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
    return str(d.get("provider") or d.get("slot", {}).get("provider") or "llama-server")


def _model_default(cfg: SlotConfig | dict[str, Any]) -> str:
    d = _cfg_to_dict(cfg)
    model = d.get("model") or {}
    if isinstance(model, dict):
        return str(model.get("default") or "")
    return ""


def _lemonade_active() -> bool:
    """Return True when SlotManager should dispatch through Lemonade.

    Thin re-export of :func:`hal0.providers.lemonade.lemonade_active`
    so manager-internal call sites don't have to reach into the
    providers subtree. Re-import on each call keeps the gating
    monkey-patchable in tests without touching this module's
    namespace.
    """
    from hal0.providers.lemonade import lemonade_active

    return lemonade_active()


def _provider_health_strategy(provider: str) -> str:
    """Pick the readiness probe shape for a provider.

    Returns one of:
      - ``"health"``: GET /health → 2xx is sufficient. Used by
        llama-server (canonical) and kokoro (whose /health body is
        either empty or ``{"status":"ok"}``).
      - ``"health_with_model_loaded"``: GET /health → 2xx + JSON body
        with ``model_loaded == true``. Used by moonshine, whose /health
        returns ``{model_loaded, model_id, model_arch}`` and stays 200
        even while the model is still loading.
      - ``"chat_sentinel"``: GET /v1/models must be non-empty AND a
        ``max_tokens=1`` POST /v1/chat/completions must 2xx. Used by FLM
        and vLLM, both of which advertise models before inference works.
    """
    p = provider.lower()
    if p in ("llama-server", "llama_server", "llamacpp", "kokoro", "comfyui"):
        # comfyui's /system_stats is the health surface; the provider
        # health() method already validates it. No chat sentinel applies
        # (image-gen models are too expensive to probe per readiness).
        return "health"
    if p in ("moonshine",):
        return "health_with_model_loaded"
    return "chat_sentinel"


# Duplicated from hal0.api.routes.models._ALIAS_NAMES to avoid pulling the
# FastAPI surface into the pure-systemd slot layer (ARCHITECTURE.md "Key
# boundaries"). Keep in sync.
_ROUTING_ALIAS_NAMES: frozenset[str] = frozenset(
    {
        "primary",
        "medium",
        "tiny",
        "embed",
        "rerank",
        "npu",
        "coding",
        "coder",
        "whisper",
        "moonshine",
        "vibevoice",
        "kokoro",
        "tts-1",
        "tts-1-hd",
        "bge-reranker",
        "nomic-embed",
    }
)


def _is_routing_alias(model_id: str) -> bool:
    """Drop haloai:* + known routing shortcuts when sniffing real model ids."""
    if not model_id:
        return True
    if model_id.startswith("haloai:"):
        return True
    return model_id in _ROUTING_ALIAS_NAMES


async def _fetch_first_concrete_model(port: int) -> str | None:
    """Best-effort: GET /v1/models on the slot and return the first non-alias id."""
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_PROBE_TIMEOUT_S) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    entries = data.get("data", []) if isinstance(data, dict) else []
    for entry in entries:
        mid = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(mid, str) and not _is_routing_alias(mid):
            return mid
    return None


async def _sentinel_inference(
    client: httpx.AsyncClient,
    url: str,
    model_entry: dict[str, Any],
) -> bool:
    """Send a max_tokens=1 sentinel /v1/chat/completions and accept any 2xx.

    TIER1: An OpenAI-compatible /v1/models is necessary but not sufficient
    — we have seen FLM advertise models that immediately 500 on first
    inference.  This sentinel exercises the inference path so READY
    actually means ready.
    """
    model_id = (model_entry.get("id") if isinstance(model_entry, dict) else None) or "sentinel"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": _SENTINEL_PROMPT}],
        "max_tokens": 1,
        "temperature": 0.0,
        "stream": False,
    }
    try:
        resp = await client.post(f"{url}/v1/chat/completions", json=payload)
    except httpx.HTTPError:
        return False
    return 200 <= resp.status_code < 300


__all__ = [
    "Slot",
    "SlotManager",
]
