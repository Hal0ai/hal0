"""Idle-unload driver for Lemonade-loaded models (ADR-0007 §Related, ADR-0006 §17).

Lemonade has no built-in idle-eviction TTL. hal0 v0.1.x kept its own
300s "demote READY→IDLE and unload" policy via ``SlotManager``'s idle
monitor; v0.2 has to keep that policy alive at the Lemonade layer
because the slot abstraction is no longer the unit of process
lifecycle — Lemonade owns the pool.

This driver runs as a background asyncio task started in hal0-api's
lifespan. Once per tick (30s default — matches the existing hal0
``_IDLE_MONITOR_INTERVAL_S``) it:

  1. Polls ``GET /v1/health`` for ``all_models_loaded[].last_use``
  2. Tracks each model's ``last_use`` value across ticks. When the
     value stays unchanged for more than ``idle_timeout_s`` wall-clock
     seconds (measured against our own injectable clock — NOT against
     the ``last_use`` field directly), calls ``POST /v1/unload``.
  3. Logs the eviction so dashboards can audit.

Why we don't compute ``now - last_use`` directly: Lemonade 10.6 reports
``last_use`` as an opaque monotonic counter, NOT a unix epoch float.
Verified on hal0 LXC 2026-05-25:

  * Fresh load: ``last_use = 1420647618``
  * After one ``/v1/chat/completions``: ``last_use = 1420673741``
    (+26,123 for ~25s wall — clearly not seconds)

If we treated that as epoch seconds the model would always look ~11
years stale and get evicted on the first sweep (which is exactly the
regression that motivated this rewrite — see field-bug report from
2026-05-25). Lemonade ships ~weekly and field semantics drift; the
safe contract is "the value changes when the model is used" and
nothing more. We bump a wall-clock idle clock locally each time the
counter moves, so this driver is correct regardless of the field's
units (or even sign).

Resilience contract:
  * Transient lemond unavailability (``LemonadeUnavailableError`` /
    ``LemonadeTimeoutError``) is logged at WARNING and the driver
    continues on the next tick. We never crash the task on a flaky
    daemon.
  * Cancellation propagates cleanly: the task awaits the sleep, picks
    up the CancelledError, and exits without partial state.
  * Per-model unload failures don't abort the whole sweep; we log and
    move on to the next candidate.
  * Models that disappear from the loaded list have their tracker
    state dropped, so an unload + reload starts a fresh idle clock
    (rather than evicting the reloaded model immediately).

ADR cross-references:
  * ADR-0007 §Related — operational gap that motivated this driver
  * ADR-0006 §17 — "Idle-eviction driver: hal0-owned external"
  * docs/internal/lemonade-spike-findings-2026-05-22.md — confirms
    Lemonade has no TTL of its own
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from typing import Any

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeError,
    LemonadeHTTPError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)

log = logging.getLogger(__name__)


# Defaults intentionally match hal0 v0.1.x conventions (slots/manager.py
# ``_IDLE_AFTER_S`` / ``_IDLE_MONITOR_INTERVAL_S``) so the operator
# behaviour doesn't change across the v0.2 cutover.
DEFAULT_POLL_INTERVAL_S: float = 30.0
DEFAULT_IDLE_TIMEOUT_S: float = 300.0


class IdleDriver:
    """Background poller that unloads idle models from Lemonade.

    Lifecycle:
        driver = IdleDriver(client)
        await driver.start()
        ...
        await driver.stop()

    ``start()`` schedules the poll task on the running event loop and
    returns immediately. ``stop()`` cancels the task and awaits its
    exit — it's idempotent and safe to call from a finally block.

    The driver does NOT own the ``LemonadeClient``; the caller passes
    an open instance and is responsible for closing it after
    ``IdleDriver.stop()`` returns. This mirrors the dispatcher pattern
    where the http client is shared between subsystems.

    See ADR-0007 §Related, ADR-0006 §17.
    """

    def __init__(
        self,
        client: LemonadeClient,
        *,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        clock: Any = time.time,
    ) -> None:
        if idle_timeout_s <= 0:
            raise ValueError("idle_timeout_s must be > 0")
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self._client = client
        self._idle_timeout_s = idle_timeout_s
        self._poll_interval_s = poll_interval_s
        # Injectable clock so tests can fast-forward without
        # monkeypatching the time module globally.
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Per-model idle-clock state:
        #   model_name -> (last_observed_last_use, first_seen_at_wall_clock)
        # We bump first_seen_at whenever last_observed_last_use moves,
        # which is the only signal Lemonade gives us that the model was
        # used. See the module docstring for why we can't trust the
        # field's units.
        self._seen: dict[str, tuple[float, float]] = {}

    # ── lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Schedule the poll task. No-op if already running."""
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="lemonade-idle-driver")
        log.info(
            "lemonade.idle.started",
            extra={
                "idle_timeout_s": self._idle_timeout_s,
                "poll_interval_s": self._poll_interval_s,
            },
        )

    async def stop(self) -> None:
        """Signal the task to exit and await its completion. Idempotent."""
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("lemonade.idle.stopped")

    # ── core loop ──────────────────────────────────────────────────

    async def _run(self) -> None:
        """Poll loop. Survives lemond hiccups; exits on cancellation."""
        try:
            while not self._stopping.is_set():
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover — defensive
                    # Nothing inside tick() should escape — but if it
                    # does, log and continue rather than letting the
                    # driver die silently.
                    log.warning(
                        "lemonade.idle.tick_error",
                        extra={"error": str(exc), "error_type": type(exc).__name__},
                    )
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval_s)
                except TimeoutError:
                    # Normal path — the interval elapsed without a stop signal.
                    continue
        except asyncio.CancelledError:
            # Normal shutdown path. Don't re-raise — the awaiter (stop)
            # already suppresses CancelledError.
            return

    async def tick(self) -> int:
        """Run one eviction sweep. Returns the number of models unloaded.

        Public so tests can drive the loop deterministically without
        sleeping. Production code should call ``start()`` and let
        ``_run`` manage cadence.

        Resilience: never raises. Lemond unreachable / 5xx is logged
        + counted as zero evictions; the next tick retries.
        """
        try:
            health = await self._client.health()
        except (LemonadeUnavailableError, LemonadeTimeoutError) as exc:
            # ADR-0006 operational risk: lemond may restart, drop the
            # connection, or hang. Treat as "no signal this tick" and
            # come back next round — never crash the driver.
            log.warning(
                "lemonade.idle.health_unreachable",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return 0
        except LemonadeHTTPError as exc:
            log.warning(
                "lemonade.idle.health_http_error",
                extra={"status_code": exc.status_code, "body": exc.body},
            )
            return 0
        except LemonadeError as exc:  # pragma: no cover — defensive
            log.warning("lemonade.idle.health_error", extra={"error": str(exc)})
            return 0

        loaded = list(_extract_loaded_models(health))
        # First: drop tracker state for any model that's no longer
        # loaded. An unload + reload must start a fresh idle clock —
        # otherwise a reloaded model would inherit its previous
        # last_use snapshot and look stale immediately.
        live_names = {
            entry.get("model_name")
            for entry in loaded
            if isinstance(entry.get("model_name"), str) and entry.get("model_name")
        }
        stale_state = self._seen.keys() - live_names
        for gone in stale_state:
            self._seen.pop(gone, None)

        if not loaded:
            return 0

        now = float(self._clock())
        evicted = 0
        for entry in loaded:
            name = entry.get("model_name")
            if not isinstance(name, str) or not name:
                continue
            last_use = _coerce_last_use(entry.get("last_use"))
            if last_use is None:
                # No counter → can't decide; leave it alone. Better
                # to skip an eviction than evict a freshly-loaded
                # model whose stats haven't populated yet. We also
                # don't seed tracker state in this case — the next
                # tick with a real counter starts cleanly.
                continue

            prev = self._seen.get(name)
            if prev is None:
                # First sight of this model. Record its counter and
                # the wall-clock moment we noticed it; skip eviction
                # this tick so a freshly-loaded model gets at least
                # one full idle_timeout_s window before it can be
                # culled.
                self._seen[name] = (last_use, now)
                continue

            prev_last_use, first_seen_at = prev
            if last_use != prev_last_use:
                # Counter moved → the model was used since our last
                # observation. Reset the idle clock and skip eviction.
                self._seen[name] = (last_use, now)
                continue

            # Counter unchanged since first_seen_at. Decide on
            # wall-clock age, NOT on the opaque counter's value.
            age = now - first_seen_at
            if age <= self._idle_timeout_s:
                continue
            ok = await self._unload(name, age=age)
            if ok:
                # Drop tracker state proactively so an immediate
                # reload (before the next health poll observes the
                # unload) won't reuse the stale entry.
                self._seen.pop(name, None)
                evicted += 1
        return evicted

    async def _unload(self, model_name: str, *, age: float) -> bool:
        """Call ``POST /v1/unload``. Returns True on success.

        Per-model failure is logged + swallowed so the sweep moves on
        to the next candidate. The next tick will retry whatever we
        missed.
        """
        try:
            await self._client.unload(model_name)
        except LemonadeError as exc:
            log.warning(
                "lemonade.idle.unload_failed",
                extra={
                    "model_name": model_name,
                    "age_s": age,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return False
        log.info(
            "lemonade.idle.unloaded",
            extra={
                "model_name": model_name,
                "age_s": age,
                "idle_timeout_s": self._idle_timeout_s,
            },
        )
        return True


# ── helpers ───────────────────────────────────────────────────────────


def _extract_loaded_models(health: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Pull the loaded-models list out of a ``/v1/health`` payload.

    Lemonade has used two field names across versions:
      * ``all_models_loaded`` (current docs)
      * ``loaded`` (older + LemonadeClient docstring)

    Accept either so a Lemonade upgrade that renames the field doesn't
    silently break the driver. Bad shapes (missing field, wrong type)
    yield an empty iterable — the caller treats that as "nothing to
    do this tick".
    """
    if not isinstance(health, dict):
        return []
    for key in ("all_models_loaded", "loaded"):
        value = health.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_last_use(value: Any) -> float | None:
    """Coerce a ``last_use`` payload field into a comparable float.

    Lemonade 10.6 reports ``last_use`` as an opaque monotonic counter
    (NOT a unix timestamp — verified on hal0 LXC 2026-05-25, see the
    module docstring). The driver only uses this value for equality
    comparison across ticks ("did the counter move?"), so the units
    don't matter — we just need a value we can stash and compare.

    We accept int/float; anything else (None, str, missing) yields
    None which the caller treats as "skip this entry this tick" (no
    tracker state seeded, no eviction).

    bool is an int subclass and is excluded explicitly so True/False
    don't masquerade as 1.0/0.0 counter values.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
