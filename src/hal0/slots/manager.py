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
import logging
import os
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.config import paths
from hal0.slot_config import write_slot_toml
from hal0.slots.state import (
    IllegalSlotTransition,
    NpuExclusivityViolation,
    SlotConfigError,
    SlotNotFound,
    SlotState,
    SlotStateRecord,
    is_transition_legal,
    provider_requires_model,
    read_state,
    write_state_atomic,
)

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig
    from hal0.slots.arbiter import GpuArbiter

log = logging.getLogger(__name__)


# ── Seeded slot catalogue (PR-10, plan §4.2 + §10.2) ────────────────────────

#: Slots that exist on every hal0 install regardless of hardware. The
#: dashboard creates these as empty cards at first run; the bundle
#: picker (Phase 5) populates their ``model.default`` fields. ``agent``
#: is the GPU MoE chat-role sibling of ``chat`` (moved here from the NPU
#: set in #679 — it is a GPU slot, not the NPU FLM anchor).
SEEDED_SLOTS: tuple[str, ...] = ("chat", "embed", "rerank", "stt", "tts", "img", "vision", "agent")

#: NPU FLM shadow slots seeded only when the FastFlowLM ``.deb`` is
#: installed (``shutil.which('flm')`` truthy): the ASR + embed tags that
#: ride the same coresident FLM process as the NPU chat anchor — which is
#: the separate ``npu`` slot, NOT listed here. ``agent`` was previously
#: (wrongly) in this set; it is a GPU chat-role slot and moved to
#: SEEDED_SLOTS in #679. Opt-in at Pro+ bundle tier (ADR-0008 §5).
NPU_SEEDED_SLOTS: tuple[str, ...] = ("stt-npu", "embed-npu")

#: Back-compat alias map: old slot names → canonical new names.
#: Aliases resolve transparently for dispatch and config lookup but are
#: NEVER stored on disk and NEVER appear in list() / iter_configs() /
#: /api/slots. ``agent-hermes`` maps to ``agent`` (a GPU seed slot, #679)
#: so no new TOML is created — the alias just redirects old references.
SLOT_ALIASES: dict[str, str] = {
    "primary": "chat",
    "agent-hermes": "agent",
}

#: Slot ``type`` vocabulary (plan §4.1).
_VALID_SLOT_TYPES: frozenset[str] = frozenset(
    {"llm", "embedding", "reranking", "transcription", "tts", "image"}
)

#: Slot-name policy: kebab-case, max 32 chars, leading alphanumeric.
_SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ── Tunables ─────────────────────────────────────────────────────────────────

# Push-driven failure detector. While a slot is in a "live" state
# (READY / SERVING / IDLE) a background task polls is_active every
# _FAIL_WATCH_INTERVAL_S seconds. When the slot's container unit goes
# inactive underneath us the watcher flips state and emits an SSE
# frame within ~1s.
_FAIL_WATCH_INTERVAL_S: float = 2.0
_FAIL_WATCH_LIVE_STATES: frozenset[SlotState] = frozenset(
    {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
)
# #783/B4: an active unit is not necessarily healthy. The watcher also probes
# the model server's /health; a crashed-but-active server (active unit, failing
# /health) is demoted to ERROR — but only after this many CONSECUTIVE failures,
# so a single transient blip doesn't trigger a disruptive model reload.
_HEALTH_FAIL_STRIKES: int = 2
# Must match the exact flag spellings emitted by ContainerProvider's llama
# launch renderer; drift checks compare argv text, not llama-server aliases.
_CONFIG_DRIFT_KEYS: tuple[str, ...] = ("--ctx-size", "--model", "--alias", "-b", "-ub")

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

# Anchor slots pinned against TTL eviction *under default config* — i.e.
# when their TOML carries no explicit idle_timeout_s. Evicting these would
# defeat always-warm chat, the agent loop, and the NPU trio anchor. An
# explicit per-slot idle_timeout_s in TOML still wins (lets an operator
# opt a named anchor back into eviction); explicit 0 keeps it pinned.
_PINNED_BY_DEFAULT: frozenset[str] = frozenset({"chat", "agent", "npu"})


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


@dataclass(frozen=True, slots=True)
class LoadedSlot:
    """Typed routing result for an enabled slot.

    Returned by :meth:`SlotManager.resolve_for_request` and
    :meth:`SlotManager.loaded_slot` so callers do not have to route to a
    bare name and then reopen raw slot TOML to recover the model id,
    device, labels, or system prompt.
    """

    name: str
    model_id: str
    slot_type: str
    device: str
    enabled: bool
    labels: frozenset[str]
    system_prompt: str = ""
    profile: str | None = None
    default: bool = False


def is_npu_trio_shadow(cfg: SlotConfig | dict[str, Any]) -> bool:
    """True if *cfg* is an NPU FLM trio **shadow** (stt/embed), not the anchor.

    The NPU runs a single FLM process — the chat anchor (``device=npu
    type=llm``) — which also serves transcription/embedding when the
    anchor's ``[npu]`` toggles are on. The ``stt``/``embed`` slots
    are therefore *shadows*: served by the anchor's process and NOT
    independently loadable. Issuing a standalone ``/v1/load`` for them on the
    busy single-tenant NPU returns HTTP 500, so callers skip the spawn and
    derive their state from the anchor. The anchor itself (``type=llm``) is
    deliberately excluded.
    """
    d = _cfg_to_dict(cfg)
    return d.get("device") == "npu" and d.get("type") in ("transcription", "embedding")


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
        # In-memory copy of the latest state per slot (mirrors state.json).
        self._states: dict[str, SlotStateRecord] = {}
        # SSE subscribers: list of queues; one per active state_stream().
        self._subscribers: list[asyncio.Queue[SlotStateRecord]] = []
        # Idle-tracking — last request timestamp per slot.
        self._last_used: dict[str, float] = {}
        # Per-slot background tasks that poll the container unit's
        # is-active state and push a transition when it drops out from
        # underneath us. Keyed by slot name; only present while the
        # slot is in a live state.
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
        # Hard-eviction TTL default (#902): a slot idle past its resolved
        # idle_timeout_s is unloaded, not just relabeled.  Per-slot TOML
        # idle_timeout_s overrides this global default.
        self._evict_after_s: float = evict_after_s
        self._idle_monitor_interval_s: float = idle_monitor_interval_s
        self._idle_monitor_task: asyncio.Task[None] | None = None
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
            kind="remote",
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
    #: Single source of truth per #696 — never duplicate inline.
    #: Sync read so call sites in the hot dispatch path pay zero await overhead.
    _DISPATCHABLE_STATES: frozenset[SlotState] = frozenset(
        {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
    )

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
        """Poll the container unit's is-active and flip state when it dies.

        Runs as a background task while the slot is in READY/SERVING/IDLE.
        Detection latency = up to one poll interval (~2s). Exits cleanly
        once the slot leaves the live-state set, by self-cancel via the
        ERROR transition, or via outer ``task.cancel()``.
        """
        health_failures = 0
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
                    # #783/B4: active is necessary but not sufficient. Probe
                    # the model server's /health — a crashed/wedged server is
                    # active to systemd while /health fails, so an is-active-
                    # only watcher leaves it lying as dispatchable READY.
                    if await self._probe_health(slot_name):
                        health_failures = 0
                        continue
                    health_failures += 1
                    if health_failures < _HEALTH_FAIL_STRIKES:
                        # Tolerate a transient blip; a real crash fails again.
                        continue
                    # Re-check state in case load/unload moved us mid-probe.
                    current = self._current_state(slot_name)
                    if current not in _FAIL_WATCH_LIVE_STATES:
                        return
                    # Confirmed unhealthy → ERROR (red dot, operator cue). The
                    # health endpoint (#783 cr1) then reports degraded and
                    # hal0_slot_up reads health_ok=False (#791). Recoverable —
                    # the dispatcher reloads on the next request.
                    try:
                        await self._transition(
                            slot_name,
                            SlotState.ERROR,
                            message="model server failed /health probe",
                            extra={"health_ok": False},
                            force=True,
                        )
                    except Exception as exc:
                        log.warning(
                            "slot.fail_watch_transition_failed",
                            extra={"slot": slot_name, "error": str(exc)},
                        )
                    return
                # The container unit went inactive while we believed it
                # was live. Re-check state once more — load/unload may
                # have moved us legitimately during the probe.
                current = self._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                # A stopped unit (GPU arbiter handoff, systemd stop,
                # OOM-kill with Restart= pending) is a clean not-loaded
                # state from the slot's perspective — the dispatcher
                # lazy-loads on the next request. Reflect that as OFFLINE
                # (grey dot) rather than ERROR (red dot, operator-
                # investigation cue), reserving ERROR for the real
                # failures: spawn/health/load exceptions.
                try:
                    await self._transition(
                        slot_name,
                        SlotState.OFFLINE,
                        message="container stopped (auto-reloads on next request)",
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
        """Is the slot's container unit live? (systemctl is-active probe).

        Synchronous probe, runs in an executor. Probe errors are coerced
        to False so status()'s drift reconciler runs.
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return False

        from hal0.providers.container import container_provider

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, container_provider().is_active, slot_name
            )
        except Exception as exc:
            # The docstring contract: probe errors coerce to False so the
            # status() drift reconciler runs instead of 500ing /api/slots.
            log.warning(
                "slot.is_active_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return False

    async def _probe_health(self, slot_name: str) -> bool:
        """Probe the slot's model-server ``/health`` (#783/B4).

        Returns ``False`` only on a *definitive* not-ok response. Anything
        inconclusive — missing config, no port, an NPU trio shadow (whose
        /health would target a non-existent child port), or a probe
        exception — returns ``True`` so the fail-watch never demotes a slot
        it cannot actually judge. The watcher's strike counter handles
        transient single failures; this method only reports one probe.
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg or is_npu_trio_shadow(cfg):
            return True
        port = _cfg_port(cfg)
        if not port:
            return True

        from hal0.providers.container import container_provider

        try:
            health = await container_provider().health(port)
        except Exception as exc:
            # Inconclusive — a transport error is not proof the model server
            # is dead. Don't demote; the next poll re-probes.
            log.warning(
                "slot.health_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return True
        return bool(health.get("ok"))

    async def container_readiness_check(self, slot_name: str) -> tuple[bool, str]:
        """Check whether a container-backed slot is ready to serve requests.

        Performs two live probes:
          1. ``systemctl is-active`` — is the service unit running?
          2. GET /health on the slot's port — has the inference server started?

        Returns:
          ``(True, "ready")`` — both probes passed; safe to forward.
          ``(False, reason)`` — not ready; reason describes the failure
            (e.g. ``"inactive"``, ``"starting"``, ``"health_check_failed"``).

        Called by ``Dispatcher.forward()`` before forwarding to a
        container upstream so that a down/starting container returns a
        structured ``slot.loading`` 503 instead of a raw 502 ConnectError.
        """
        cfg = await self._maybe_load_config(slot_name)
        if cfg is None:
            return False, "config_missing"

        from hal0.providers.container import container_provider

        # 1) systemctl is-active (synchronous — run in executor)
        active = await asyncio.get_event_loop().run_in_executor(
            None, container_provider().is_active, slot_name
        )
        if not active:
            return False, "inactive"

        # 2) /health probe (only meaningful when the unit is active)
        port = _cfg_port(cfg)
        if port:
            health = await container_provider().health(port)
            if not health.get("ok"):
                return False, "starting"

        return True, "ready"

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
        """Restart a slot without changing its model assignment."""
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
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
        """Compare live container argv to the command a restart would render.

        Returns a structured payload when the comparison is meaningful, or
        None when the slot is inactive, lacks a config, is an NPU trio shadow,
        or the provider cannot read either side of the comparison.
        """
        if active is None:
            active = await self._is_active(slot_name)
        if not active:
            return None
        if cfg is None:
            cfg = await self._maybe_load_config(slot_name)
        if not cfg or is_npu_trio_shadow(cfg):
            return None

        model_info = await self._resolve_model_info(_model_default(cfg))
        from hal0.providers.container import container_provider

        provider = container_provider()
        loop = asyncio.get_event_loop()
        running, rendered = await asyncio.gather(
            loop.run_in_executor(None, provider.running_argv, slot_name),
            loop.run_in_executor(None, provider.expected_argv, cfg, model_info),
        )
        if not running or not rendered:
            return None

        running_flags = _argv_values(running, _CONFIG_DRIFT_KEYS)
        rendered_flags = _argv_values(rendered, _CONFIG_DRIFT_KEYS)
        diffs = [
            {"key": key, "running": running_flags.get(key), "rendered": rendered_flags.get(key)}
            for key in _CONFIG_DRIFT_KEYS
            if not _config_drift_values_equal(key, running_flags.get(key), rendered_flags.get(key))
        ]
        return {"drifted": bool(diffs), "diffs": diffs}

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

    def _loaded_slot_from_config(self, cfg: dict[str, Any]) -> LoadedSlot | None:
        """Convert one raw slot config dict into a :class:`LoadedSlot`.

        Returns ``None`` when the config does not describe an enabled slot
        with a model id. The raw TOML shapes are intentionally absorbed here
        so request routers and tool dispatchers consume a typed result.
        """
        name = str(cfg.get("name") or "").strip()
        slot_type = str(cfg.get("type") or "").strip()
        if not name or not slot_type:
            return None
        if cfg.get("enabled") is False:
            return None

        model_section = cfg.get("model") or {}
        model_id = ""
        if isinstance(model_section, dict):
            raw_model = model_section.get("default", "")
            if isinstance(raw_model, str):
                model_id = raw_model.strip()
        if not model_id:
            return None

        from hal0.model_meta import labels_of

        raw_prompt = cfg.get("system_prompt")
        system_prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        if not system_prompt:
            extra = cfg.get("extra")
            if isinstance(extra, dict) and isinstance(extra.get("system_prompt"), str):
                system_prompt = extra["system_prompt"]

        profile = cfg.get("profile")
        return LoadedSlot(
            name=name,
            model_id=model_id,
            slot_type=slot_type,
            device=str(cfg.get("device") or ""),
            enabled=True,
            labels=frozenset(labels_of(cfg)),
            system_prompt=system_prompt,
            profile=profile if isinstance(profile, str) and profile else None,
            default=cfg.get("default") is True,
        )

    async def loaded_slot(self, name: str) -> LoadedSlot | None:
        """Return a typed view of an enabled configured slot, or ``None``.

        Resolves back-compat aliases transparently. This is a read-only
        inventory helper; it does not probe runtime state.
        """
        resolved = self._resolve_alias(name)
        try:
            cfg = await self._load_slot_config(resolved)
        except SlotConfigError:
            return None
        return self._loaded_slot_from_config(cfg)

    async def resolve_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> LoadedSlot | None:
        """Resolve a request of type ``slot_type`` to a loaded slot.

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
        Returning :class:`LoadedSlot` keeps callers from reopening raw slot
        configs to discover the model id, labels, device, or system prompt.
        """

        def _satisfies(slot: LoadedSlot) -> bool:
            if not required_labels:
                return True
            return set(required_labels).issubset(slot.labels)

        slots = [
            slot
            for cfg in await self.iter_configs()
            if cfg.get("type") == slot_type
            for slot in [self._loaded_slot_from_config(cfg)]
            if slot is not None
        ]

        # Step 1+2: try the default first.
        default_name = await self.default_slot_for(slot_type)
        if default_name is not None:
            default_slot = next((slot for slot in slots if slot.name == default_name), None)
            if default_slot is not None and _satisfies(default_slot):
                return default_slot

        # Step 3: fall-through to first enabled + label-matching slot.
        for slot in slots:
            if not _satisfies(slot):
                continue
            return slot

        return None

    async def route_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> str | None:
        """Resolve a request of type ``slot_type`` to a concrete slot name.

        Compatibility wrapper for callers that have not moved to
        :meth:`resolve_for_request` yet.
        """
        slot = await self.resolve_for_request(slot_type, required_labels=required_labels)
        return slot.name if slot is not None else None

    async def add_slot(
        self,
        name: str,
        *,
        type: str,
        model: str,
        device: str = "gpu-rocm",
        port: int = 8081,
    ) -> Slot:
        """Programmatic ``hal0 slot add`` (plan §4.3).

        Validates kebab-case name, rejects seeded-name collisions,
        rejects unknown slot types. The SlotConfig schema requires a
        port in the 8081-8099 range.

        Args:
            name: Kebab-case identifier; must not collide with a
                seeded slot (``SEEDED_SLOTS`` plus ``NPU_SEEDED_SLOTS``,
                independently of whether FLM is installed).
            type: One of ``llm | embedding | reranking | transcription
                | tts | image``.
            model: Model id to load by default.
            device: Hardware preference (``gpu-rocm | gpu-vulkan | cpu
                | npu``); see ``map_backend_to_device``. Default
                ``gpu-rocm`` matches Strix Halo seed semantics.
            port: SlotConfig.port — the container's loopback port.
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
            "provider": "llama-server",
            "enabled": True,
            "model": {"default": model},
        }
        return await self.create(name, cfg)

    async def remove_slot(self, name: str) -> None:
        """Programmatic ``hal0 slot remove`` (plan §4.3).

        Rejects seeded-slot names (use :meth:`unload` or
        ``capabilities.toml`` to disable a seeded slot, not delete it).
        No side effect on the underlying model files — they stay in
        the registry.
        """
        name = self._resolve_alias(name)
        reserved = set(SEEDED_SLOTS) | set(NPU_SEEDED_SLOTS)
        if name in reserved:
            raise SlotConfigError(
                f"slot {name!r} is seeded; cannot remove (disable it via capabilities.toml)",
                details={"slot": name, "reserved": sorted(reserved)},
            )
        await self.delete(name)

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

        PR-11 (plan §5.3 + ADR-0008 §5): rejects a second ``device=npu,
        type=llm, enabled=true`` slot — the AMDXDNA hardware context
        admits exactly one NPU LLM at a time. Disabled NPU LLM slots
        coexist; only the live anchor count is bounded.

        TOML serialisation routes through
        :func:`hal0.slot_config.write_slot_toml` — the single
        slots/*.toml write path (issue #697).
        """
        cfg_dict = _cfg_to_dict(slot_cfg)
        # #585: canonicalize a ctx_size alias from the create modal too.
        _normalize_ctx_key(cfg_dict)
        # Persist a concrete context window when the operator left it unset, so
        # the TOML, the dashboard, and the running container all agree. The
        # provider's load-path derive is the belt-and-suspenders fallback; this
        # makes the chosen window visible at create time (chat@4096 incident).
        model_tbl = cfg_dict.get("model")
        if isinstance(model_tbl, dict) and model_tbl.get("context_size") is None:
            from hal0.providers.container import _resolve_context_size

            model_info = await self._resolve_model_info(model_tbl.get("default"))
            model_tbl["context_size"] = _resolve_context_size(None, model_info)
        # Reject (or normalize) an incoherent device/profile backend pairing
        # before it ever lands on disk — the door the dashboard left open for
        # the utility slot (vulkan device + rocm-dnse profile). Every field is
        # "new" at create time, so a conflicting device+profile is an explicit
        # operator error and raises.
        _reconcile_device_profile(cfg_dict, set(cfg_dict.keys()))
        await self._check_npu_exclusivity(slot_name, cfg_dict)
        cfg_path = self._config_file(slot_name)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_slot_toml(cfg_path, cfg_dict)
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
                "provider": cfg_dict.get("provider", "llama-server"),
            },
            force=True,
        )
        return await self.status(slot_name)

    async def delete(self, slot_name: str) -> None:
        """Delete a dynamic slot. Seeded slots cannot be deleted."""
        slot_name = self._resolve_alias(slot_name)
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

        Rewriting the TOML is enough — the container unit is re-rendered
        from it on the next load/restart.
        """
        slot_name = self._resolve_alias(slot_name)
        self._ensure_known(slot_name)
        cfg = await self._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        # One-level deep merge for nested TOML tables ([model], [server]).
        # A bare shallow ``dict.update`` replaced a sub-table wholesale, so a
        # partial ``PATCH /defaults`` body like ``{"model": {"ctx_size": N}}``
        # silently dropped sibling keys — most damagingly ``[model].default``
        # (the model name), which left the slot unable to resolve a model and
        # turned the dashboard Start button into a silent no-op after a
        # restart. Merge sub-table dicts key-by-key so partial updates only
        # touch the fields they carry; scalars and lists still replace
        # wholesale (predictable, and no caller relies on list-merge).
        for key, value in updates.items():
            existing = cfg_dict.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged = dict(existing)
                merged.update(value)
                cfg_dict[key] = merged
            else:
                cfg_dict[key] = value

        # #585: the dashboard writes [model].ctx_size; canonicalize to
        # context_size so the two keys can't diverge on disk.
        _normalize_ctx_key(cfg_dict)

        # Keep device↔profile backend coherent: a profile switch re-derives
        # device (the drawer path that previously left a vulkan device under a
        # rocm-dnse profile), a cross-backend device flip re-points the profile
        # (the POST /backend path, which writes device only), and an explicit
        # conflicting pair raises. Only the field(s) the caller changed drive
        # reconciliation.
        _reconcile_device_profile(cfg_dict, set(updates.keys()))

        # PR-11: re-run the NPU exclusivity guard whenever the merged
        # config could land a second device=npu, type=llm anchor (plan
        # §5.3). Cheap when no NPU LLM is involved — the helper short-
        # circuits on the merged cfg's own device/type.
        await self._check_npu_exclusivity(slot_name, cfg_dict)

        cfg_path = self._config_file(slot_name)
        try:
            write_slot_toml(cfg_path, cfg_dict)
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

    async def start_idle_monitor(
        self,
        *,
        idle_after_s: float | None = None,
        evict_after_s: float | None = None,
        interval_s: float | None = None,
    ) -> None:
        """Start the background sweeper that demotes READY → IDLE and evicts.

        Idempotent — calling twice while the task is alive is a no-op.
        Callers in the API lifespan invoke this once at startup (wiring
        ``evict_after_s`` from ``slots.idle_timeout_s``); tests construct a
        SlotManager with shorter intervals and start the monitor explicitly.
        """
        if idle_after_s is not None:
            self._idle_after_s = idle_after_s
        if evict_after_s is not None:
            self._evict_after_s = evict_after_s
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

    async def _evict_timeout_for(self, slot_name: str) -> float | None:
        """Resolve the idle TTL after which a slot is hard-evicted (#902).

        Returns ``None`` when the slot is pinned (never TTL-evicted):
          * an explicit ``idle_timeout_s = 0`` in the slot's TOML, or
          * a default-pinned anchor (chat / agent / npu) with no explicit
            per-slot value, or
          * a non-positive global default with no explicit per-slot value.

        Otherwise returns the effective TTL in seconds: the per-slot TOML
        ``idle_timeout_s`` when set (overrides the global), else the global
        ``_evict_after_s`` default.  ``0`` consistently means "disabled" at
        both levels, matching the config-schema contract.
        """
        canonical = self._resolve_alias(slot_name)
        try:
            cfg = await self._load_slot_config(canonical)
        except (SlotConfigError, SlotNotFound):
            cfg = {}
        raw = cfg.get("idle_timeout_s")
        if isinstance(raw, bool):  # bool is an int subclass — never a TTL
            raw = None
        if isinstance(raw, int):
            return None if raw <= 0 else float(raw)
        # No explicit per-slot value: pin the named anchors, else fall back
        # to the global default (itself disabled when non-positive).
        if canonical in _PINNED_BY_DEFAULT:
            return None
        return float(self._evict_after_s) if self._evict_after_s > 0 else None

    async def _sweep_idle_once(self) -> None:
        """One idle-sweep pass over every tracked slot.

        Stage 1 (soft): a READY slot idle past ``_idle_after_s`` is
        relabeled IDLE so dashboards distinguish "warm but quiet" from
        "warm and serving".

        Stage 2 (hard, #902): a slot idle past its resolved per-slot TTL
        (:meth:`_evict_timeout_for`) is **unloaded**, freeing host RAM —
        the only way to reclaim it, since llama-server allocates KV
        statically at ``ctx_size``.  ``idle_timeout_s = 0`` (or a pinned
        anchor) is never evicted.  A slot mid-request
        (``serving_count > 0``) is never touched; the dispatcher reloads an
        evicted slot transparently on its next request (wake-on-request),
        so eviction is safe.
        """
        now = time.time()
        for slot_name, ts in list(self._last_used.items()):
            idle_for = now - ts
            if self._serving_count.get(slot_name, 0) > 0:
                continue
            state = self._current_state(slot_name)
            if state not in (SlotState.READY, SlotState.IDLE):
                continue

            # Stage 2 — hard TTL eviction.
            evict_after = await self._evict_timeout_for(slot_name)
            if evict_after is not None and idle_for >= evict_after:
                try:
                    await self.unload(slot_name)
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
            if state == SlotState.READY and idle_for >= self._idle_after_s:
                try:
                    await self._transition(
                        slot_name,
                        SlotState.IDLE,
                        message=f"idle for {idle_for:.0f}s",
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
            SlotState.READY when the container is serving (or the
            health wait timed out — the fail watcher picks up ongoing
            unhealthiness).
        """
        cfg = await self._maybe_load_config(slot_name)
        if not cfg:
            return SlotState.READY  # nothing more to verify

        # Wait for /health 200 on the container port.
        slot_port = port or int(_cfg_to_dict(cfg).get("port", 0))
        from hal0.providers.container import container_provider

        try:
            await container_provider().wait_ready(slot_port)
            return SlotState.READY
        except TimeoutError as exc:
            log.warning(
                "slot.container_await_ready_timeout",
                extra={"slot": slot_name, "port": slot_port, "error": str(exc)},
            )
            # Let the fail watcher detect ongoing unhealthiness.
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
            "backend": cfg.get("backend", "vulkan"),
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
        d: dict[str, Any] = cfg.model_dump()
    elif isinstance(cfg, dict):
        d = dict(cfg)
    else:
        raise SlotConfigError(f"unsupported slot cfg type {type(cfg).__name__}")
    # An unset context_size (schema default None) must never reach the TOML
    # writer: write_toml_atomic rejects None, and a persisted 4096 was the
    # chat@4096 incident. Drop the key so the load path derives the model's
    # native window instead (see providers.container._resolve_context_size).
    model = d.get("model")
    if isinstance(model, dict) and model.get("context_size") is None:
        model.pop("context_size", None)
    return d


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


def _argv_values(argv: list[str], keys: tuple[str, ...]) -> dict[str, str | None]:
    """Return the last value for each flag key in argv.

    Last value wins because slot ``[server].extra_args`` intentionally follows
    profile flags and can override them.
    """
    wanted = set(keys)
    out: dict[str, str | None] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in wanted:
            out[token] = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
            continue
        for key in keys:
            prefix = f"{key}="
            if token.startswith(prefix):
                out[key] = token[len(prefix) :]
                break
        i += 1
    return out


def _config_drift_values_equal(key: str, running: str | None, rendered: str | None) -> bool:
    if key == "--model" and running is not None and rendered is not None:
        return os.path.realpath(running) == os.path.realpath(rendered)
    return running == rendered


def _normalize_ctx_key(cfg_dict: dict[str, Any]) -> None:
    """Fold the legacy ``[model].ctx_size`` alias into the canonical
    ``context_size`` (SlotConfig's field), in place (#585).

    The dashboard slot-edit panel writes ``ctx_size``; the load path
    reads ``context_size``. Persisting both lets them silently diverge.
    A fresh ``ctx_size`` (the operator's latest UI write) wins over any
    stale ``context_size`` seed, then the alias is dropped so exactly one
    key survives on disk. No-op when ``ctx_size`` is absent.
    """
    model = cfg_dict.get("model")
    if isinstance(model, dict) and "ctx_size" in model:
        model["context_size"] = model.pop("ctx_size")


def _cfg_effective_backend(cfg: SlotConfig | dict[str, Any]) -> str | None:
    """Derive the EFFECTIVE runtime backend token from a slot config.

    W3 truth fix: ``device`` is the authoritative hardware-intent
    field. The dashboard's SlotCard backend chip must reflect what
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
    # displayed token can never diverge from what the load path derives.
    from hal0.model_meta import device_to_backend

    recipe, llamacpp_backend = device_to_backend(str(device))
    # NPU → recipe="flm" with no llamacpp_backend; surface "flm".
    return llamacpp_backend or (recipe if recipe == "flm" else None)


def _base_profile_for_backend(catalog: Any, backend: str) -> str:
    """Pick the canonical (non-MTP) seed profile name for a GPU backend.

    Prefers the seed profile named after the backend (``rocm`` / ``vulkan``);
    falls back to any non-MTP then any profile that declares ``backend``.
    """
    named = catalog.profile.get(backend)
    if named is not None and getattr(named, "backend", None) == backend:
        return backend
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend and not getattr(prof, "mtp", False):
            return str(name)
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend:
            return str(name)
    return backend


def _reconcile_device_profile(cfg_dict: dict[str, Any], changed: set[str]) -> None:
    """Keep a GPU slot's ``device`` and ``profile.backend`` coherent in place.

    A GPU slot implies its backend twice: ``device`` (``gpu-rocm`` /
    ``gpu-vulkan``) drives the llama-server backend, while ``profile`` selects
    the container image + flags. They must agree — a vulkan device under a
    rocm-dnse profile launches a Vulkan binary with ROCm-only MTP draft flags
    (issue: utility slot). The field the operator changed wins; the stale side
    is re-derived. Both changed to conflicting backends → operator error.

    No-ops for slots without a GPU profile (npu/cpu/img profiles declare
    ``backend=None``) and for ``auto`` device (empty) unless the profile
    itself changed. Mutates ``cfg_dict`` in place.
    """
    profile_name = cfg_dict.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        return

    from hal0.config.loader import load_profiles_config

    prof = load_profiles_config().profile.get(profile_name)
    prof_backend = getattr(prof, "backend", None) if prof is not None else None
    if not prof_backend:
        # Non-GPU profile (or unknown profile with no backend) — leave alone.
        return

    from hal0.config.schema import map_backend_to_device
    from hal0.model_meta import device_to_backend

    device = cfg_dict.get("device")
    dev_backend = device_to_backend(str(device))[1] if device else None
    if dev_backend == prof_backend:
        return  # already coherent

    prof_changed = "profile" in changed
    dev_changed = "device" in changed

    if not device:
        # ``auto``/unset device: only adopt the profile's backend when the
        # operator explicitly (re)selected the profile; otherwise leave auto.
        if prof_changed:
            cfg_dict["device"] = map_backend_to_device(prof_backend)
        return

    if prof_changed and not dev_changed:
        cfg_dict["device"] = map_backend_to_device(prof_backend)
    elif dev_changed and not prof_changed and dev_backend is not None:
        catalog = load_profiles_config()
        cfg_dict["profile"] = _base_profile_for_backend(catalog, dev_backend)
    elif prof_changed and dev_changed:
        raise SlotConfigError(
            f"slot device {device!r} (backend {dev_backend!r}) conflicts with "
            f"profile {profile_name!r} (backend {prof_backend!r}); "
            "pick a device and profile with the same backend",
            details={
                "device": device,
                "profile": profile_name,
                "device_backend": dev_backend,
                "profile_backend": prof_backend,
            },
        )
    # neither changed (pre-existing on-disk drift surfaced by an unrelated
    # update): leave both fields untouched so the unrelated edit doesn't
    # silently mutate hardware intent. Drift heals on the next device/profile
    # edit.


__all__ = [
    "NPU_SEEDED_SLOTS",
    "SEEDED_SLOTS",
    "SLOT_ALIASES",
    "Slot",
    "SlotManager",
]
