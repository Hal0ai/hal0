"""Push-driven failure detector (P3-slots §1b-watchdog).

``SlotWatchdog`` polls a live slot's container unit + model-server health
every ``_FAIL_WATCH_INTERVAL_S`` and flips state to ERROR/OFFLINE when it
notices the unit died or the health probe fails repeatedly — so a dead
slot surfaces within ~2s instead of waiting for the next ``status()``
poll. Architecturally distinct from the idle/eviction reaper (§1b): this
is a failure DETECTOR, not capacity policy, sharing nothing with reaper.py
except "a background task per slot".

Highest-risk P3-slots extraction (spec §9 risk 1) — the self-cancel-via-
own-transition semantics in :meth:`SlotWatchdog.update` (was
``_update_fail_watcher``) are subtle: when the watcher's OWN coroutine
fires an ERROR/OFFLINE transition, ``_transition``'s tail calls back into
``update()``, which must recognise "the task calling me right now IS the
task I would otherwise cancel" and let it finish naturally instead of
calling ``task.cancel()`` on itself (which would raise ``CancelledError``
on the await it just completed).

``SlotManager.__init__`` constructs ``self._watchdog = SlotWatchdog(self)``.
``_transition``'s tail calls ``self._watchdog.update(name, to_state)``.
Every previously-public name survives on ``SlotManager`` as a thin
delegator: ``container_readiness_check`` (called by
``dispatcher/router.py``), plus the private ``_update_fail_watcher``/
``_fail_watch_loop``/``_is_active``/``_probe_health`` (called internally by
core ``status``/``load``/``_maybe_adopt_running_slot``, and directly by
``tests/slots/test_health_probe_cfg.py``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from hal0.slots._cfg_helpers import _cfg_port
from hal0.slots.npu.trio import is_npu_trio_shadow
from hal0.slots.state import DISPATCHABLE_STATES, SlotState

if TYPE_CHECKING:
    from hal0.slots.manager import Slot
    from hal0.slots.state import SlotStateRecord

log = logging.getLogger(__name__)

# Push-driven failure detector. While a slot is in a "live" state
# (READY / SERVING / IDLE) a background task polls is_active every
# _FAIL_WATCH_INTERVAL_S seconds. When the slot's container unit goes
# inactive underneath us the watcher flips state and emits an SSE
# frame within ~1s.
_FAIL_WATCH_INTERVAL_S: float = 2.0
#: The "live" states the fail-watcher polls: the dispatchable ready-set
#: (container up and usable, aliased from the one canonical set — DR-8) PLUS
#: WARMING. WARMING is watched because a health-wait timeout leaves the slot
#: parked there indefinitely (see ``_await_ready``); without a watcher a unit
#: that later dies is never noticed. WARMING gets softer treatment inside the
#: loop: only the systemd is-active check can strike it (never /health), so a
#: slot legitimately still loading a large model is never killed.
_FAIL_WATCH_LIVE_STATES: frozenset[SlotState] = DISPATCHABLE_STATES | {SlotState.WARMING}
# #783/B4: an active unit is not necessarily healthy. The watcher also probes
# the model server's /health; a crashed-but-active server (active unit, failing
# /health) is demoted to ERROR — but only after this many CONSECUTIVE failures,
# so a single transient blip doesn't trigger a disruptive model reload.
_HEALTH_FAIL_STRIKES: int = 2
# WARMING-only: consecutive is-active failures before a warming slot is
# declared dead. Higher than _HEALTH_FAIL_STRIKES because a freshly-spawned
# unit can legitimately read "activating"/inactive to the probe for a beat or
# two while podman brings the container up — a warming slot must only be
# flipped when the unit is *stably* down, never while a big model loads.
_WARMING_INACTIVE_STRIKES: int = 3
# WARMING-only staleness watchdog. /health is deliberately NOT probed while a
# slot is WARMING (a large NPU/GGUF model can legitimately hold /health down
# for minutes during a cold load — see ``_await_ready`` and its ceiling
# ``providers.container._HEALTH_TIMEOUT_S`` = 180s per attempt), so a unit that
# stays *active* forever while its model server never converges would otherwise
# sit in WARMING indefinitely — 503ing every /v1/embeddings and NPU-trio
# consumer with ``npu.trio_unavailable`` and never self-healing (a wedged FLM
# NPU chat-anchor). This bound catches that: once a slot has been WARMING
# longer than this, treat it as wedged and auto-recover (unload → load). It
# MUST sit well ABOVE the legitimate cold-load ceiling so a slow-but-healthy
# load is never demoted — 900s (15 min) is ~5x the 180s per-attempt health-wait
# ceiling, comfortably clear of any real large-model load.
_WARMING_STALE_AFTER_S: float = 900.0
# READY-side stall ceiling (#3282). A provider's ``"stalled"`` /health answer
# (a read timeout or its engine-silent 503) is never a strike, because tearing
# down a slow engine leaks its GPU memory. The flip side is a front end that
# hangs forever, which would sit READY indefinitely. So the slot goes ERROR
# (no SIGKILL; the unit is left alone) once the stall has run BOTH this many
# CONSECUTIVE probes AND this long without one answer in between. Any answered
# probe resets both. 900 s matches the warming watchdog above. A probe takes at
# most ~40 s (the 35 s read timeout plus the poll interval), so 900 s already
# implies at least 22 probes and the 10-strike condition never binds on its own;
# it is a floor for a stub that answers instantly, where time alone would
# otherwise decide.
_STALL_CEILING_S: float = 900.0
_STALL_CEILING_STRIKES: int = 10
# How often a slot already at the stall ceiling re-probes from status()/list().
# Each probe can wait out the readiness budget, and GET /api/slots, the metrics
# sampler and preload eviction all go through status(), so an unthrottled probe
# would slow every one of those calls for as long as the engine stays silent.
_STALL_REPROBE_INTERVAL_S: float = 30.0


class WatchdogHost(Protocol):
    """Narrow seam :class:`SlotWatchdog` needs from ``SlotManager``."""

    _fail_watchers: dict[int, asyncio.Task[None]]
    _states: dict[int, SlotStateRecord]
    _serving_count: dict[int, int]

    def _current_state(self, name: str) -> SlotState: ...
    def _key(self, name: str) -> int: ...
    def _lock(self, name: str) -> asyncio.Lock: ...
    async def _maybe_load_config(self, name: str) -> dict[str, Any] | None: ...
    async def _transition(self, name: str, to_state: SlotState, **kw: Any) -> SlotStateRecord: ...
    async def load(self, name: str) -> Slot: ...
    async def unload(self, name: str) -> Slot: ...


class SlotWatchdog:
    """Push-driven failure detector, against a narrow host seam."""

    def __init__(self, host: WatchdogHost) -> None:
        self._host = host
        # Last re-probe of a stall-ceiling ERROR, by slot key (#3282). Only ever
        # holds a probe that did not clear the ceiling: a clear removes the entry.
        self._stall_reprobed_at: dict[int, float] = {}

    def update(self, name: str, new_state: SlotState) -> None:
        """Spawn or cancel the per-slot fail-watcher to match ``new_state``.

        Live states (READY/SERVING/IDLE/WARMING) → ensure a watcher task is
        running. Any other state → cancel the watcher if present.

        Self-cancellation is a no-op: when the watcher itself fires the
        transition to ERROR, we let it return naturally rather than calling
        ``task.cancel()`` on the currently-executing coroutine (which would
        raise CancelledError on the await it just completed).
        """
        host = self._host
        key = host._key(name)
        if new_state in _FAIL_WATCH_LIVE_STATES:
            existing = host._fail_watchers.get(key)
            if existing is not None and not existing.done():
                return
            try:
                host._fail_watchers[key] = asyncio.create_task(
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

        existing = host._fail_watchers.pop(key, None)
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

    async def _stamp_locked(
        self,
        slot_name: str,
        to_state: SlotState,
        *,
        message: str,
        still_faulted: Callable[[], Awaitable[bool]],
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Flip a confirmed-faulted slot to ``to_state`` under the per-slot lock.

        ``load``/``unload``/``restart`` hold ``host._lock(name)`` for the whole
        of their run, so taking it here serialises the watchdog against an
        in-flight load: the watcher can never stamp ERROR/OFFLINE onto a slot
        whose load is mid-flight — which used to race load's own final
        ``WARMING → READY`` into an illegal transition (a warming unit that read
        inactive for a beat during a big model's cold-load got flipped, and the
        load then failed/409'd). Once we hold the lock the load has settled, so
        we re-verify BOTH the live state AND the fault itself (``still_faulted``)
        before stamping — a load that converged healthy while the watcher waited
        for the lock is left untouched.

        Returns whether it stamped: a caller that keeps watching must tell a
        declined stamp (the slot is still live) from one that landed.
        """
        host = self._host
        async with host._lock(slot_name):
            if host._current_state(slot_name) not in _FAIL_WATCH_LIVE_STATES:
                return False
            try:
                faulted = await still_faulted()
            except Exception as exc:
                log.warning(
                    "slot.fail_watch_recheck_failed",
                    extra={"slot": slot_name, "error": str(exc)},
                )
                return False
            if not faulted:
                # Fault cleared while we waited for the lock (a concurrent
                # load/restart converged the slot) — leave it alone.
                return False
            try:
                await host._transition(
                    slot_name,
                    to_state,
                    message=message,
                    extra=extra or {},
                    force=True,
                )
            except Exception as exc:
                log.warning(
                    "slot.fail_watch_transition_failed",
                    extra={"slot": slot_name, "error": str(exc)},
                )
                return False
            return True

    async def _probe_unhealthy(self, slot_name: str) -> bool:
        """True when the model server still fails its /health probe."""
        return not await self.probe_health(slot_name)

    async def _probe_stalled(self, slot_name: str) -> bool:
        """True when /health still reads ``stalled`` and nothing is being served.

        A long in-flight run holds the engine's PING down by itself, so a slot
        with requests in flight is never stamped on a stall (#3282).
        """
        if self._host._serving_count.get(self._host._key(slot_name), 0) > 0:
            return False
        healthy, stalled = await self._probe_health_detail(slot_name)
        return healthy and stalled

    async def _unit_inactive(self, slot_name: str) -> bool:
        """True when the container unit still reads inactive to systemd."""
        return not await self.is_active(slot_name)

    async def _fail_watch_loop(self, slot_name: str) -> None:
        """Poll the container unit's is-active and flip state when it dies.

        Runs as a background task while the slot is in READY/SERVING/IDLE —
        or WARMING (the post-health-timeout blind spot): a warming slot is
        judged ONLY on the unit's is-active (``_WARMING_INACTIVE_STRIKES``
        consecutive failures), never on /health, so a dead unit is caught
        while a slot legitimately still loading a large model is left alone.
        Detection latency = up to one poll interval (~2s). Exits cleanly
        once the slot leaves the live-state set, by self-cancel via the
        ERROR transition, or via outer ``task.cancel()``.
        """
        host = self._host
        health_failures = 0
        warming_inactive = 0
        stall_probes = 0
        stalled_since: float | None = None
        try:
            while True:
                await asyncio.sleep(_FAIL_WATCH_INTERVAL_S)
                # First gate: did the slot leave live-state from underneath
                # us?  ``update`` already cancels in that case but this
                # defends against the race where the watcher wakes before
                # the cancel lands.
                current = host._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                try:
                    active = await self.is_active(slot_name)
                except Exception as exc:
                    # Probe failure is unusual — log and keep polling.
                    log.warning(
                        "slot.fail_watch_is_active_failed",
                        extra={"slot": slot_name, "error": str(exc)},
                    )
                    continue
                if active:
                    warming_inactive = 0
                    if current is SlotState.WARMING:
                        # Warming slots are still loading — /health failing is
                        # the EXPECTED condition, not a crash signal. Only the
                        # unit's liveness may strike a warming slot, so skip
                        # the health probe entirely (a big-model load can hold
                        # /health down for minutes without being wedged).
                        health_failures = 0
                        # …but an active unit whose model server never converges
                        # would otherwise sit in WARMING forever, 503ing every
                        # embed/STT consumer (a wedged FLM NPU anchor). Bound the
                        # WARMING dwell: once it exceeds the cold-load ceiling,
                        # treat the slot as wedged and auto-recover. WARMING →
                        # STARTING is an illegal transition, so recovery is
                        # unload → load (as ``restart()`` does), not a direct
                        # reload.
                        rec = host._states.get(host._key(slot_name))
                        warming_elapsed = time.time() - rec.updated_at if rec is not None else 0.0
                        if warming_elapsed <= _WARMING_STALE_AFTER_S:
                            continue
                        log.warning(
                            "slot.fail_watch_warming_stale",
                            extra={
                                "slot": slot_name,
                                "warming_elapsed_s": round(warming_elapsed, 1),
                                "stale_after_s": _WARMING_STALE_AFTER_S,
                            },
                        )
                        try:
                            await host.unload(slot_name)
                            await host.load(slot_name)
                        except Exception as exc:
                            log.warning(
                                "slot.fail_watch_warming_recover_failed",
                                extra={"slot": slot_name, "error": str(exc)},
                            )
                        # unload → load re-stamps ``updated_at`` (restarting the
                        # staleness clock) and re-arms a fresh fail-watcher via
                        # its own transitions, so this now-orphaned watcher
                        # returns cleanly instead of racing the new one or
                        # tight-looping recovery every tick.
                        return
                    # #783/B4: active is necessary but not sufficient. Probe
                    # the model server's /health — a crashed/wedged server is
                    # active to systemd while /health fails, so an is-active-
                    # only watcher leaves it lying as dispatchable READY.
                    healthy, stalled = await self._probe_health_detail(slot_name)
                    if healthy and stalled:
                        # Inconclusive, still tolerated — but only up to the
                        # stall ceiling (#3282).
                        health_failures = 0
                        stall_probes += 1
                        now = time.monotonic()
                        if stalled_since is None:
                            stalled_since = now
                        if (
                            stall_probes < _STALL_CEILING_STRIKES
                            or now - stalled_since < _STALL_CEILING_S
                        ):
                            continue
                        log.warning(
                            "slot.fail_watch_stall_ceiling",
                            extra={
                                "slot": slot_name,
                                "stalled_probes": stall_probes,
                                "stalled_s": round(now - stalled_since, 1),
                            },
                        )
                        stamped = await self._stamp_locked(
                            slot_name,
                            SlotState.ERROR,
                            message=(
                                f"model server /health stalled past the stall ceiling "
                                f"({_STALL_CEILING_S:.0f} s, {_STALL_CEILING_STRIKES} "
                                "consecutive probes)"
                            ),
                            still_faulted=lambda: self._probe_stalled(slot_name),
                            # ``stall_ceiling`` makes this ERROR stick: adoption
                            # and the lazy load() both refuse it (#3282).
                            extra={"health_ok": False, "stall_ceiling": True},
                        )
                        if stamped:
                            # A fresh ERROR gets a fresh re-probe.
                            self._stall_reprobed_at.pop(host._key(slot_name), None)
                            return
                        # Declined (the engine answered, or a request is in
                        # flight): the slot is still live, so keep watching with
                        # a fresh count rather than leave it unwatched.
                        stall_probes = 0
                        stalled_since = None
                        continue
                    stall_probes = 0
                    stalled_since = None
                    if healthy:
                        health_failures = 0
                        continue
                    health_failures += 1
                    if health_failures < _HEALTH_FAIL_STRIKES:
                        # Tolerate a transient blip; a real crash fails again.
                        continue
                    # Confirmed unhealthy → ERROR (red dot, operator cue). The
                    # health endpoint (#783 cr1) then reports degraded and
                    # hal0_slot_up reads health_ok=False (#791). Recoverable —
                    # the dispatcher reloads on the next request. Stamped under
                    # the per-slot lock + fault re-verification so it can never
                    # clobber a concurrent load/reload (see _stamp_locked).
                    await self._stamp_locked(
                        slot_name,
                        SlotState.ERROR,
                        message="model server failed /health probe",
                        still_faulted=lambda: self._probe_unhealthy(slot_name),
                        extra={"health_ok": False},
                    )
                    return
                # The container unit went inactive while we believed it
                # was live. Re-check state once more — load/unload may
                # have moved us legitimately during the probe.
                current = host._current_state(slot_name)
                if current not in _FAIL_WATCH_LIVE_STATES:
                    return
                if current is SlotState.WARMING:
                    # A warming slot tolerates a few inactive reads (a
                    # freshly-spawned unit can look inactive to the probe
                    # for a beat) but a *stably* dead unit is a real load
                    # failure — flip to ERROR so the red dot cues the
                    # operator instead of the slot lying in WARMING forever.
                    warming_inactive += 1
                    if warming_inactive < _WARMING_INACTIVE_STRIKES:
                        continue
                    # Under the per-slot lock + re-verification: a big model whose
                    # unit reads inactive for a beat mid-cold-load must not be
                    # flipped to ERROR while its load holds the lock (that raced
                    # load's WARMING→READY into an illegal error→ready). See
                    # _stamp_locked.
                    await self._stamp_locked(
                        slot_name,
                        SlotState.ERROR,
                        message="container unit died while warming",
                        still_faulted=lambda: self._unit_inactive(slot_name),
                        extra={"health_ok": False},
                    )
                    return
                # A stopped unit (GPU arbiter handoff, systemd stop,
                # OOM-kill with Restart= pending) is a clean not-loaded
                # state from the slot's perspective — the dispatcher
                # lazy-loads on the next request. Reflect that as OFFLINE
                # (grey dot) rather than ERROR (red dot, operator-
                # investigation cue), reserving ERROR for the real
                # failures: spawn/health/load exceptions.
                # Under the per-slot lock + re-verification (still inactive): a
                # concurrent load that just brought the unit up must not be
                # clobbered to OFFLINE mid-flight (OFFLINE→READY is illegal too).
                await self._stamp_locked(
                    slot_name,
                    SlotState.OFFLINE,
                    message="container stopped (auto-reloads on next request)",
                    still_faulted=lambda: self._unit_inactive(slot_name),
                )
                return
        except asyncio.CancelledError:
            # Normal shutdown path — slot left live-state cleanly.
            raise

    async def is_active(self, slot_name: str) -> bool:
        """Is the slot's container unit live? (systemctl is-active probe).

        Synchronous probe, runs in an executor. Probe errors are coerced
        to False so status()'s drift reconciler runs.

        The **config** is handed to the provider, never the bare name: the unit
        to probe is keyed by the slot's instance token, which is the durable id
        on a post-migration box (#1417).
        """
        cfg = await self._host._maybe_load_config(slot_name)
        if not cfg:
            return False

        from hal0.providers.container import container_provider

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, container_provider().is_active, cfg
            )
        except Exception as exc:
            # The docstring contract: probe errors coerce to False so the
            # status() drift reconciler runs instead of 500ing /api/slots.
            log.warning(
                "slot.is_active_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return False

    async def probe_health(self, slot_name: str, *, strict: bool = False) -> bool:
        """Probe the slot's model-server ``/health`` (#783/B4).

        Returns ``False`` only on a *definitive* not-ok response. Anything
        inconclusive — missing config, no port, an NPU trio shadow (whose
        /health would target a non-existent child port), or a probe
        exception — returns ``True`` so the fail-watch never demotes a slot
        it cannot actually judge. The watcher's strike counter handles
        transient single failures; this method only reports one probe.

        ``strict=True`` inverts that bias for callers that PROMOTE on a
        healthy answer (#2442's adopted-WARMING poll): there, only a
        conclusive ``ok`` counts, and anything inconclusive reads as
        not-healthy-yet.

        A provider's ``"stalled"`` answer is inconclusive too: it can take the
        probe's whole read timeout to arrive, which the watcher's inline await
        tolerates (a dead unit is only noticed later during a stall).
        """
        healthy, _stalled = await self._probe_health_detail(slot_name, strict=strict)
        return healthy

    async def _probe_health_detail(
        self, slot_name: str, *, strict: bool = False
    ) -> tuple[bool, bool]:
        """:meth:`probe_health` plus whether the answer was a provider stall.

        Returns ``(healthy, stalled)``. ``stalled`` is only ever ``True`` on a
        provider's explicit ``"stalled"`` answer, which the caller counts toward
        the stall ceiling (:data:`_STALL_CEILING_S`).
        """
        cfg = await self._host._maybe_load_config(slot_name)
        if not cfg:
            return (not strict, False)
        if is_npu_trio_shadow(cfg):
            return (True, False)
        port = _cfg_port(cfg)
        if not port:
            return (not strict, False)

        from hal0.providers.container import container_provider

        try:
            # Pass the slot config so FLM slots get the Tier-1 real-inference
            # probe (health() delegates to FLMProvider.health when the cfg
            # resolves to FLM) instead of the weak /v1/models fallback. The
            # probe distinguishes "up but still loading" (ok=False) from
            # "dead"; WARMING slots are never health-struck by the
            # fail-watcher (is-active only), so a loading FLM model is safe.
            health = await container_provider().health(port, cfg)
        except Exception as exc:
            # Inconclusive — a transport error is not proof the model server
            # is dead. Don't demote; the next poll re-probes.
            log.warning(
                "slot.health_probe_failed",
                extra={"slot": slot_name, "error": str(exc)},
            )
            return (not strict, False)
        if health.get("status") == "stalled":
            return (not strict, True)
        return (bool(health.get("ok")), False)

    async def stall_ceiling_cleared(self, slot_name: str, cfg: dict[str, Any]) -> bool:
        """True only when a stall-ceiling ERROR's engine demonstrably answers again.

        Strict: a stall, a transport error, a missing port and a probe that
        outlasts the readiness budget all read as "not cleared", so the ERROR
        stays. Bounded and throttled because status() and list() are the callers
        (:data:`_STALL_REPROBE_INTERVAL_S`). The timestamp is taken before the
        probe so concurrent callers skip it instead of queueing behind it; the
        cost is that a recovered engine can read ERROR for up to one interval.
        """
        from hal0.providers.container import readiness_probe_budget_s

        key = self._host._key(slot_name)
        now = time.monotonic()
        last = self._stall_reprobed_at.get(key)
        if last is not None and now - last < _STALL_REPROBE_INTERVAL_S:
            return False
        self._stall_reprobed_at[key] = now
        try:
            healthy, stalled = await asyncio.wait_for(
                self._probe_health_detail(slot_name, strict=True),
                readiness_probe_budget_s(cfg),
            )
        except TimeoutError:
            return False
        if not healthy or stalled:
            return False
        self._stall_reprobed_at.pop(key, None)
        return True

    async def readiness_check(self, slot_name: str) -> tuple[bool, str]:
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
        cfg = await self._host._maybe_load_config(slot_name)
        if cfg is None:
            return False, "config_missing"

        from hal0.providers.container import container_provider, readiness_probe_budget_s

        # 1) systemctl is-active (synchronous — run in executor). The config
        # carries the instance token the unit is actually keyed by (#1417); a
        # bare name made this gate reject every healthy slot on an id-keyed box
        # with slot.loading / slot.load_failed.
        active = await asyncio.get_event_loop().run_in_executor(
            None, container_provider().is_active, cfg
        )
        if not active:
            return False, "inactive"

        # 2) /health probe (only meaningful when the unit is active). The
        # slot config is passed so FLM slots use the Tier-1 real-inference
        # probe (see ContainerProvider.health) — a still-loading FLM reports
        # ok=False here, which maps to the retryable "starting" reason. So does
        # a stalled engine: this runs per request, so a provider whose probe can
        # outlast one bounds it here (the fail-watcher waits it out instead).
        port = _cfg_port(cfg)
        if port:
            try:
                health = await asyncio.wait_for(
                    container_provider().health(port, cfg), readiness_probe_budget_s(cfg)
                )
            except TimeoutError:
                return False, "starting"
            if not health.get("ok") or health.get("status") == "stalled":
                return False, "starting"

        return True, "ready"


__all__ = [
    "_FAIL_WATCH_INTERVAL_S",
    "_FAIL_WATCH_LIVE_STATES",
    "_HEALTH_FAIL_STRIKES",
    "_STALL_CEILING_S",
    "_STALL_CEILING_STRIKES",
    "_STALL_REPROBE_INTERVAL_S",
    "_WARMING_INACTIVE_STRIKES",
    "_WARMING_STALE_AFTER_S",
    "SlotWatchdog",
    "WatchdogHost",
]
