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


class WatchdogHost(Protocol):
    """Narrow seam :class:`SlotWatchdog` needs from ``SlotManager``."""

    _fail_watchers: dict[str, asyncio.Task[None]]
    _states: dict[str, SlotStateRecord]

    def _current_state(self, name: str) -> SlotState: ...
    async def _maybe_load_config(self, name: str) -> dict[str, Any] | None: ...
    async def _transition(self, name: str, to_state: SlotState, **kw: Any) -> SlotStateRecord: ...
    async def load(self, name: str) -> Slot: ...
    async def unload(self, name: str) -> Slot: ...


class SlotWatchdog:
    """Push-driven failure detector, against a narrow host seam."""

    def __init__(self, host: WatchdogHost) -> None:
        self._host = host

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
        if new_state in _FAIL_WATCH_LIVE_STATES:
            existing = host._fail_watchers.get(name)
            if existing is not None and not existing.done():
                return
            try:
                host._fail_watchers[name] = asyncio.create_task(
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

        existing = host._fail_watchers.pop(name, None)
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
                        rec = host._states.get(slot_name)
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
                    if await self.probe_health(slot_name):
                        health_failures = 0
                        continue
                    health_failures += 1
                    if health_failures < _HEALTH_FAIL_STRIKES:
                        # Tolerate a transient blip; a real crash fails again.
                        continue
                    # Re-check state in case load/unload moved us mid-probe.
                    current = host._current_state(slot_name)
                    if current not in _FAIL_WATCH_LIVE_STATES:
                        return
                    # Confirmed unhealthy → ERROR (red dot, operator cue). The
                    # health endpoint (#783 cr1) then reports degraded and
                    # hal0_slot_up reads health_ok=False (#791). Recoverable —
                    # the dispatcher reloads on the next request.
                    try:
                        await host._transition(
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
                    try:
                        await host._transition(
                            slot_name,
                            SlotState.ERROR,
                            message="container unit died while warming",
                            extra={"health_ok": False},
                            force=True,
                        )
                    except Exception as exc:
                        log.warning(
                            "slot.fail_watch_transition_failed",
                            extra={"slot": slot_name, "error": str(exc)},
                        )
                    return
                # A stopped unit (GPU arbiter handoff, systemd stop,
                # OOM-kill with Restart= pending) is a clean not-loaded
                # state from the slot's perspective — the dispatcher
                # lazy-loads on the next request. Reflect that as OFFLINE
                # (grey dot) rather than ERROR (red dot, operator-
                # investigation cue), reserving ERROR for the real
                # failures: spawn/health/load exceptions.
                try:
                    await host._transition(
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

    async def is_active(self, slot_name: str) -> bool:
        """Is the slot's container unit live? (systemctl is-active probe).

        Synchronous probe, runs in an executor. Probe errors are coerced
        to False so status()'s drift reconciler runs.
        """
        cfg = await self._host._maybe_load_config(slot_name)
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

    async def probe_health(self, slot_name: str) -> bool:
        """Probe the slot's model-server ``/health`` (#783/B4).

        Returns ``False`` only on a *definitive* not-ok response. Anything
        inconclusive — missing config, no port, an NPU trio shadow (whose
        /health would target a non-existent child port), or a probe
        exception — returns ``True`` so the fail-watch never demotes a slot
        it cannot actually judge. The watcher's strike counter handles
        transient single failures; this method only reports one probe.
        """
        cfg = await self._host._maybe_load_config(slot_name)
        if not cfg or is_npu_trio_shadow(cfg):
            return True
        port = _cfg_port(cfg)
        if not port:
            return True

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
            return True
        return bool(health.get("ok"))

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

        from hal0.providers.container import container_provider

        # 1) systemctl is-active (synchronous — run in executor)
        active = await asyncio.get_event_loop().run_in_executor(
            None, container_provider().is_active, slot_name
        )
        if not active:
            return False, "inactive"

        # 2) /health probe (only meaningful when the unit is active). The
        # slot config is passed so FLM slots use the Tier-1 real-inference
        # probe (see ContainerProvider.health) — a still-loading FLM reports
        # ok=False here, which maps to the retryable "starting" reason.
        port = _cfg_port(cfg)
        if port:
            health = await container_provider().health(port, cfg)
            if not health.get("ok"):
                return False, "starting"

        return True, "ready"


__all__ = [
    "_FAIL_WATCH_INTERVAL_S",
    "_FAIL_WATCH_LIVE_STATES",
    "_HEALTH_FAIL_STRIKES",
    "_WARMING_INACTIVE_STRIKES",
    "_WARMING_STALE_AFTER_S",
    "SlotWatchdog",
    "WatchdogHost",
]
