"""hal0-api's refresh-policy task: the reconcile and scheduled consolidation (ADR-0060).

One background task (:class:`RefreshPolicyRunner`) started by the
``background_tasks`` boot phase. It

* runs the refresh-policy reconcile (:func:`hal0.memory.refresh_policy.apply_refresh_policy`)
  once the engine answers after boot, again whenever a ``memory.refresh.*``
  setting is saved (:meth:`RefreshPolicyRunner.kick`, called by the settings
  PUT), and every :data:`RECONCILE_EVERY_S` to catch a page written straight
  to the engine;
* when ``[memory.refresh] consolidation = "scheduled"``, starts one
  consolidation of each of this box's banks when
  ``consolidation_schedule`` comes due. The reconcile has turned the
  engine's own consolidation off for those banks. The last run is kept in
  :func:`state_path`, so a run missed while hal0-api was down runs at the
  next start, once; switching to scheduled starts the clock then, so the
  switch itself starts no run.

The config is read on every tick, never held, and nothing here raises out
of the loop: a failed pass is logged and the next one tries again.
``HAL0_MEMORY_REFRESH_POLICY=0`` keeps the task from starting (the test
suite sets it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from hal0.config.cron import CronError, previous_fire
from hal0.memory.refresh_policy import EngineCall, PolicyReport, apply_refresh_policy

log = structlog.get_logger(__name__)

#: How often the loop wakes when nothing kicks it.
TICK_S = 60.0
#: How often the reconcile runs again when nothing changed.
RECONCILE_EVERY_S = 6 * 3600.0
#: How soon a reconcile that could not reach the engine is tried again.
RETRY_S = 60.0
#: Per-request budget for the engine calls this task makes.
ENGINE_TIMEOUT_S = 60.0
#: Tries of one bank's scheduled consolidation that failed before it was sent
#: (a refused connection, 429, 5xx) before that run gives up on the bank.
CONSOLIDATE_ATTEMPTS = 4
#: A request that timed out may have reached the engine: it is sent again this
#: many times, then taken as sent.
TIMEOUT_RETRIES = 1
#: Wait before retrying a run, doubled per try, capped.
CONSOLIDATE_BACKOFF_S = 60.0
CONSOLIDATE_BACKOFF_CAP_S = 15 * 60.0
#: Environment switch: "0", "false" or "no" keeps the task from starting.
ENABLE_ENV = "HAL0_MEMORY_REFRESH_POLICY"


def state_path() -> Path:
    """``/var/lib/hal0/state/memory-consolidation-schedule.json`` (under ``HAL0_HOME`` in a sandbox)."""
    from hal0.config.paths import var_lib

    return var_lib() / "state" / "memory-consolidation-schedule.json"


def enabled_by_env() -> bool:
    return os.environ.get(ENABLE_ENV, "1").strip().lower() not in ("0", "false", "no")


def provider_engine(provider: Any) -> EngineCall | None:
    """An :data:`EngineCall` over a live Hindsight provider's client, or ``None``
    when there is no engine (memory off, the in-memory fallback, a boot
    that has not healed yet)."""
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is None or not hasattr(client, "request_json"):
        return None

    async def call(
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        return await client.request_json(
            method, path, params=params, json_body=json_body, timeout_s=ENGINE_TIMEOUT_S
        )

    return call


def provider_banks(provider: Any) -> list[str]:
    """The banks the policy covers: this box's platform bank, and its private
    bank on a two-bank store (the banks :func:`hal0.memory.bank_seed.seed_targets` names)."""
    from hal0.memory.bank_seed import seed_targets

    try:
        return list(seed_targets(provider)) if provider is not None else []
    except Exception:  # pragma: no cover - defensive
        return []


class RefreshPolicyRunner:
    """The loop. Every collaborator is a callable read on use, so a test
    drives it with fakes and hal0-api with ``app.state``."""

    def __init__(
        self,
        *,
        policy: Callable[[], Any],
        engine: Callable[[], EngineCall | None],
        banks: Callable[[], list[str]],
        state_file: Path | None = None,
        clock: Callable[[], float] = time.time,
        tick_s: float = TICK_S,
        reconcile_every_s: float = RECONCILE_EVERY_S,
        retry_s: float = RETRY_S,
    ) -> None:
        self._policy = policy
        self._engine = engine
        self._banks = banks
        self._state_file = state_file
        self._clock = clock
        self._tick_s = tick_s
        self._reconcile_every_s = reconcile_every_s
        self._retry_s = retry_s
        self._kick = asyncio.Event()
        self._reconcile_pending = True
        self._next_reconcile_at = 0.0
        self._state: dict[str, Any] | None = None
        self.last_report: PolicyReport | None = None
        self.last_consolidation: dict[str, Any] | None = None

    # ── public ─────────────────────────────────────────────────────────

    def kick(self) -> None:
        """Run the reconcile on the next pass, now (a ``memory.refresh.*`` save)."""
        self._reconcile_pending = True
        self._kick.set()

    async def run(self) -> None:
        """Loop until cancelled."""
        log.info("hal0.memory.refresh_runner_started")
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - step() catches its own
                log.warning(
                    "hal0.memory.refresh_runner_failed", error=f"{type(exc).__name__}: {exc}"
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._kick.wait(), timeout=self._tick_s)

    async def step(self) -> None:
        """One pass: the reconcile when due, then the consolidation schedule."""
        now = self._clock()
        if self._reconcile_pending or now >= self._next_reconcile_at:
            await self._reconcile(now)
        await self._consolidation_tick(now)

    # ── the reconcile ──────────────────────────────────────────────────

    async def _reconcile(self, now: float) -> None:
        self._kick.clear()
        call = self._engine()
        if call is None:
            # No engine yet; the boot heal or a restart brings one.
            self._next_reconcile_at = now + self._retry_s
            return
        self._reconcile_pending = False
        try:
            report = await apply_refresh_policy(call, self._banks(), self._policy())
        except Exception as exc:
            log.warning("hal0.memory.refresh_policy_failed", error=f"{type(exc).__name__}: {exc}")
            self._next_reconcile_at = now + self._retry_s
            return
        self.last_report = report
        unreachable = report.deferred is not None or any(
            b.skipped and b.list_error for b in report.banks
        )
        self._next_reconcile_at = now + (self._retry_s if unreachable else self._reconcile_every_s)

    # ── scheduled consolidation ────────────────────────────────────────
    #
    # The state (``self._state``, mirrored to :func:`state_path`):
    #
    # * ``mode`` and ``schedule``: what the clock was started for. Either one
    #   differing from the config (a save, or an edit while hal0-api was down)
    #   restarts the clock, so a change never fires a catch-up run.
    # * ``last_run_at``: when the last run finished (or the clock started). A
    #   fire after it is due.
    # * ``due_at``, ``done``, ``failed``, ``attempts``, ``timeouts``,
    #   ``next_try_at``: the run in progress. A bank in ``done`` is never sent
    #   again for that ``due_at``; one in ``failed`` was given up on.
    #
    # Memory is authoritative: a state file that cannot be written is logged,
    # and the run still advances, so a failed write never repeats a run.

    def _path(self) -> Path:
        return self._state_file or state_path()

    def _load_state(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("last_run_at"), (int, float)):
            return None
        return data

    def _save_state(self) -> None:
        path = self._path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("hal0.memory.consolidation_schedule_unsaved", error=str(exc))

    def _start_clock(self, now: float, mode: str, schedule: str, reason: str) -> None:
        self._state = {
            "mode": mode,
            "schedule": schedule,
            "last_run_at": now,
            "reason": reason,
            "due_at": None,
        }
        self._save_state()

    async def _consolidation_tick(self, now: float) -> None:
        policy = self._policy()
        mode = str(getattr(policy, "consolidation", "continuous"))
        schedule = str(getattr(policy, "consolidation_schedule", ""))
        if self._state is None:
            self._state = self._load_state()
            if self._state is not None:
                # A file written before the mode and schedule were recorded counts as this config.
                self._state.setdefault("mode", mode)
                self._state.setdefault("schedule", schedule)
        state = self._state
        if state is None:
            self._start_clock(now, mode, schedule, "clock started")
            return
        if state.get("mode") != mode or (mode == "scheduled" and state.get("schedule") != schedule):
            # A new mode or schedule starts the clock: the first run is its next fire.
            self._start_clock(now, mode, schedule, "config changed")
            return
        if mode != "scheduled":
            return
        try:
            due = previous_fire(schedule, datetime.fromtimestamp(now, UTC)).timestamp()
        except CronError as exc:
            log.warning("hal0.memory.consolidation_schedule_invalid", error=str(exc))
            return
        in_progress = state.get("due_at")
        if isinstance(in_progress, (int, float)) and due > in_progress:
            log.warning(
                "hal0.memory.scheduled_consolidation_superseded",
                due_at=in_progress,
                pending=self._pending(state),
            )
            in_progress = None
        if not isinstance(in_progress, (int, float)):
            if due <= float(state["last_run_at"]):
                return
            state.update(due_at=due, done=[], failed=[], attempts={}, timeouts={}, next_try_at=0.0)
        if now < float(state.get("next_try_at") or 0.0):
            return
        call = self._engine()
        if call is None:
            return
        from hal0.memory.write_gate import read_gate

        if read_gate() is not None:
            return  # maintenance holds the engine; the next tick tries again
        await self._run_due(call, state, now)

    def _pending(self, state: Mapping[str, Any]) -> list[str]:
        settled = {*state.get("done", []), *state.get("failed", [])}
        return [b for b in dict.fromkeys(self._banks()) if b not in settled]

    async def _run_due(self, call: EngineCall, state: dict[str, Any], now: float) -> None:
        results: dict[str, Any] = {}
        attempts: dict[str, int] = state.setdefault("attempts", {})
        timeouts: dict[str, int] = state.setdefault("timeouts", {})
        done: list[str] = state.setdefault("done", [])
        failed: list[str] = state.setdefault("failed", [])
        for bank in self._pending(state):
            path = f"/v1/default/banks/{quote(bank, safe='')}/consolidate"
            try:
                out = await call("POST", path, json_body=None)
            except httpx.TimeoutException as exc:
                # The request may have reached the engine: retry it once, then
                # take it as sent rather than queue consolidation after consolidation.
                timeouts[bank] = timeouts.get(bank, 0) + 1
                if timeouts[bank] > TIMEOUT_RETRIES:
                    done.append(bank)
                    results[bank] = {"assumed_sent": f"{type(exc).__name__}"}
                else:
                    results[bank] = {"retry": f"{type(exc).__name__}"}
                continue
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    failed.append(bank)  # the engine refused it; sending it again changes nothing
                    results[bank] = {"error": f"HTTP {status}"}
                    continue
                error = f"HTTP {status}"
            except Exception as exc:  # connect errors and the like: not sent
                error = f"{type(exc).__name__}: {exc}"
            else:
                done.append(bank)
                results[bank] = {
                    "operation_id": out.get("operation_id") if isinstance(out, dict) else None,
                    "deduplicated": bool(out.get("deduplicated"))
                    if isinstance(out, dict)
                    else False,
                }
                continue
            attempts[bank] = attempts.get(bank, 0) + 1
            if attempts[bank] >= CONSOLIDATE_ATTEMPTS:
                failed.append(bank)
                results[bank] = {"error": error, "gave_up": True}
                log.error(
                    "hal0.memory.scheduled_consolidation_gave_up",
                    bank=bank,
                    due_at=state.get("due_at"),
                    attempts=attempts[bank],
                    error=error,
                )
            else:
                results[bank] = {"retry": error}
        pending = self._pending(state)
        self.last_consolidation = {
            "at": now,
            "due_at": state.get("due_at"),
            "banks": results,
            "done": list(done),
            "failed": list(failed),
            "pending": pending,
        }
        log.info(
            "hal0.memory.scheduled_consolidation",
            due_at=state.get("due_at"),
            banks=results,
            pending=pending,
        )
        if pending:
            tries = max(
                [attempts.get(b, 0) for b in pending] + [timeouts.get(b, 0) for b in pending]
            )
            state["next_try_at"] = now + min(
                CONSOLIDATE_BACKOFF_S * (2 ** max(tries - 1, 0)), CONSOLIDATE_BACKOFF_CAP_S
            )
        else:
            state.update(last_run_at=now, due_at=None, last_result=results)
            for key in ("done", "failed", "attempts", "timeouts", "next_try_at"):
                state.pop(key, None)
        self._save_state()


def start_for_app(app: Any) -> tuple[RefreshPolicyRunner | None, asyncio.Task[None] | None]:
    """Build the runner over ``app.state`` and start it; ``(None, None)`` when
    :data:`ENABLE_ENV` turns it off. The runner is ``app.state.refresh_policy_runner``."""
    if not enabled_by_env():
        app.state.refresh_policy_runner = None
        return None, None

    def _policy() -> Any:
        cfg = getattr(app.state, "hal0_config", None)
        memory = getattr(cfg, "memory", None)
        refresh = getattr(memory, "refresh", None)
        if refresh is None:
            from hal0.config.schema import MemoryRefreshConfig

            return MemoryRefreshConfig()
        return refresh

    def _provider() -> Any:
        return getattr(app.state, "memory_provider", None)

    runner = RefreshPolicyRunner(
        policy=_policy,
        engine=lambda: provider_engine(_provider()),
        banks=lambda: provider_banks(_provider()),
    )
    app.state.refresh_policy_runner = runner
    task = asyncio.create_task(runner.run(), name="memory-refresh-policy")
    return runner, task


__all__ = [
    "ENABLE_ENV",
    "RECONCILE_EVERY_S",
    "RefreshPolicyRunner",
    "enabled_by_env",
    "provider_banks",
    "provider_engine",
    "start_for_app",
    "state_path",
]
