"""BridgeService: the delegated-run lifecycle behind the bridge's run tools (ADR-0043).

ADR-0043 decision 9 lets a bridge client start a run on a Hermes profile, wait
for it, poll it, steer it, stop it, list its runs and replay their events. This
module is that behaviour, once: the ``/api/bridge`` run routes and the
``hal0-agents`` MCP tools are thin projections of :class:`BridgeService`, so
both enforce the same rules:

* **Ownership.** A bridge caller sees only its own runs; the admin key sees all.
  A run another client owns is reported exactly like a run that does not exist
  (404 ``bridge.run_not_found``, never 403), so a client cannot probe for ids.
* **Namespacing.** A client's ``session_key`` and ``idempotency_key`` are sent to
  Hermes as ``hal0-bridge:<client>:<key>``, so one client cannot bind another's
  conversation or replay another's run. Should Hermes still answer an
  idempotent replay with a run the ledger holds for another client, the start
  is refused with 409 ``bridge.idempotency_conflict``.
* **Concurrency.** ``[bridge].max_concurrent_runs_per_client`` is checked before
  the run is posted; admin runs count under the client name ``admin``.
* **Waiting.** ``wait_run`` defaults to 120 s and is capped at 600 s. It returns
  when the run ends or needs the caller (a clarification is pending); an
  approval is the operator's to give in the inbox, so it does not end a wait.
* **Shutdown.** After :meth:`BridgeService.close`, starting a run or re-attaching
  is 503 ``bridge.unavailable``: a watcher started then could not be stopped.

The :class:`~hal0.bridge.watcher.RunWatcher` keeps each live run's ledger row
current and calls back on every change, which wakes waiters here.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from hal0.agents.hermes_api import (
    DEFAULT_PROFILE,
    api_server_base,
    model_id_for,
    probe_status,
    profile_health,
    served_profiles,
)
from hal0.bridge.caller import Caller
from hal0.bridge.errors import BridgeError
from hal0.bridge.hermes_client import HermesGateway, HermesRefused, HermesUnavailable
from hal0.bridge.store import TERMINAL_STATUSES, BridgeStore, RunRow
from hal0.bridge.watcher import (
    HERMES_STATUSES,
    ConfigSource,
    RunWatcher,
    WatcherTiming,
    pending_of,
)
from hal0.config.schema import BridgeConfig

if TYPE_CHECKING:
    from hal0.events import EventBus
    from hal0.mcp.approval_queue import ApprovalQueue

__all__ = ["DEFAULT_WAIT_S", "MAX_WAIT_S", "BridgeService", "Caller", "clamp_wait"]

log = structlog.get_logger(__name__)

#: ``wait_run``'s default and its cap (ADR-0043 decision 9).
DEFAULT_WAIT_S = 120.0
MAX_WAIT_S = 600.0

#: Characters of a run's input kept in the ledger and shown in lists.
INPUT_PREVIEW_CHARS = 200

#: Events a finished ``wait_run`` carries.
WAIT_TAIL_EVENTS = 20

#: ``X-Hermes-Session-Key`` is at most 256 characters; the idempotency key gets the same cap.
_MAX_HEADER_KEY = 256

#: Visible ASCII: a key travels in an HTTP header.
_HEADER_KEY = re.compile(r"[\x21-\x7e]+")

_MAX_LIST = 200
_MAX_EVENTS = 500
_BUS_PREVIEW_CHARS = 80

#: RunView keys, in order.
_VIEW_KEYS = (
    "run_id",
    "agent",
    "profile",
    "client",
    "status",
    "created_at",
    "updated_at",
    "input_preview",
    "output",
    "error",
    "usage",
    "pending",
    "last_seq",
)


def clamp_wait(timeout_s: float) -> float:
    """``timeout_s`` clamped to [0, 600]; a value that is not a number means the default."""
    if not math.isfinite(timeout_s):
        return DEFAULT_WAIT_S
    return min(max(float(timeout_s), 0.0), MAX_WAIT_S)


def _namespaced(client: str, key: str | None, *, code: str, what: str) -> str | None:
    """``hal0-bridge:<client>:<key>`` (ADR-0043 decision 9), or 422 ``code`` for a bad key."""
    if key is None:
        return None
    prefix = f"hal0-bridge:{client}:"
    if not _HEADER_KEY.fullmatch(key) or len(prefix) + len(key) > _MAX_HEADER_KEY:
        raise BridgeError(
            code,
            f"{what} must be 1 to {_MAX_HEADER_KEY - len(prefix)} visible ASCII characters "
            "with no spaces",
            status=422,
        )
    return prefix + key


def _view(row: RunRow) -> dict[str, Any]:
    body = row.as_dict()
    return {key: body[key] for key in _VIEW_KEYS}


def _not_found(run_id: str) -> BridgeError:
    # The same words as the store's, so a foreign run reads exactly like a missing one.
    return BridgeError("bridge.run_not_found", f"no run {run_id!r}")


def _wait_over(row: RunRow) -> bool:
    pending = pending_of(row)
    return row.status in TERMINAL_STATUSES or (
        pending is not None and pending.get("event") == "clarify.request"
    )


class BridgeService:
    """The run lifecycle of the agent bridge; see the module docstring.

    ``config`` is the ``[bridge]`` section, or a callable returning the current
    one so the limits apply to the next request without a restart.
    ``approval_queue`` and ``events`` may be None (no inbox mirroring, no bus
    events). ``memory_dispatcher`` is held for the teach tools (decision 10).
    ``timing`` scales the watcher's clocks (tests) and ``probe`` replaces the HTTP
    status probe :meth:`list_agents` uses.
    """

    def __init__(
        self,
        *,
        store: BridgeStore,
        gateway: HermesGateway,
        config: ConfigSource,
        approval_queue: ApprovalQueue | None,
        events: EventBus | None,
        memory_dispatcher: Any | None = None,
        timing: WatcherTiming | None = None,
        probe: Callable[[str], int] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._config = config
        self._events = events
        self.memory_dispatcher = memory_dispatcher
        self._probe = probe
        self._changed: dict[str, asyncio.Event] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._admitting: set[asyncio.Task[None]] = set()
        self._closed = False
        self._watcher = RunWatcher(
            store=store,
            gateway=gateway,
            config=config,
            approval_queue=approval_queue,
            events=events,
            on_change=self._on_change,
            timing=timing,
        )

    # -- change notification -----------------------------------------------------------

    def change_signal(self, run_id: str) -> asyncio.Event:
        """An event set at the next change to ``run_id``'s ledger row or events.

        Take the signal *before* reading the run, then wait on it: a change that
        lands between the read and the wait still wakes the waiter. Each signal
        fires once; take a new one after it does. The run event stream route
        follows runs this way.
        """
        signal = self._changed.get(run_id)
        if signal is None:
            signal = self._changed[run_id] = asyncio.Event()
        return signal

    def _on_change(self, run_id: str) -> None:
        signal = self._changed.pop(run_id, None)
        if signal is not None:
            signal.set()

    def watching(self) -> list[str]:
        """Run ids the watcher is following."""
        return self._watcher.watched()

    # -- agents ------------------------------------------------------------------------

    async def list_agents(self) -> dict[str, Any]:
        """The bundled agents: Hermes with each served profile, and Pi (not runnable).

        Each profile carries its ``/health`` status, whether hal0 holds its API
        key, and the model id Hermes advertises for it. Never includes a key.
        """
        home = self._gateway.home
        base_probe = self._probe if self._probe is not None else probe_status
        seen: dict[str, int] = {}

        def probe(url: str) -> int:
            if url not in seen:
                seen[url] = base_probe(url)
            return seen[url]

        def gather() -> list[tuple[str, int, str]]:
            out: list[tuple[str, int, str]] = []
            for name in served_profiles(home, probe=probe):
                if name == DEFAULT_PROFILE:
                    health = probe(f"{api_server_base(home)}/health")
                else:
                    health = profile_health(home, name, probe=probe)
                out.append((name, health, model_id_for(name, home)))
            return out

        profiles = []
        for name, health, model_id in await asyncio.to_thread(gather):
            profiles.append(
                {
                    "name": name,
                    "agent_id": "hermes" if name == DEFAULT_PROFILE else f"hermes/{name}",
                    "health": health,
                    "keyed": self._keyed(name),
                    "model_id": model_id,
                }
            )
        return {
            "agents": [
                {"id": "hermes", "kind": "daemon", "runnable": True, "profiles": profiles},
                {"id": "pi", "kind": "cli", "runnable": False},
            ]
        }

    def _keyed(self, profile: str) -> bool:
        try:
            self._gateway.key_for(profile)
        except BridgeError:
            return False
        return True

    # -- runs --------------------------------------------------------------------------

    async def start_run(
        self,
        caller: Caller,
        *,
        agent: str,
        input: str,  # Hermes's own field name
        session_key: str | None = None,
        instructions: str | None = None,
        reasoning_effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a run on a Hermes profile for ``caller``; returns its RunView.

        ``agent`` is ``hermes`` (the default profile) or ``hermes/<profile>``. An
        idempotent replay of the caller's own run returns that run unchanged.
        """
        self._check_open()
        config = self._config_now()
        if not config.enabled:
            raise BridgeError("bridge.disabled", "the agent bridge is disabled ([bridge].enabled)")
        profile = self._gateway.profile_of(agent)
        client = caller.name
        sent_session = _namespaced(
            client, session_key, code="bridge.invalid_session_key", what="session_key"
        )
        sent_idempotency = _namespaced(
            client, idempotency_key, code="bridge.invalid_idempotency_key", what="idempotency_key"
        )
        async with self._start_lock(client):
            live = await asyncio.to_thread(self._store.count_live_runs, client)
            limit = config.max_concurrent_runs_per_client
            if live >= limit:
                raise BridgeError(
                    "bridge.too_many_runs",
                    f"{client} already has {live} run(s) in flight (limit {limit}); "
                    "wait for one to finish or stop one",
                )
            try:
                started = await self._gateway.start_run(
                    profile,
                    input=input,
                    instructions=instructions,
                    reasoning_effort=reasoning_effort,
                    session_key=sent_session,
                    idempotency_key=sent_idempotency,
                )
            except HermesRefused as exc:
                if exc.hermes_code == "idempotency_key_conflict":
                    raise BridgeError(
                        "bridge.idempotency_conflict",
                        "idempotency_key was already used for a run with different input",
                    ) from exc
                raise
            run_id = started.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise HermesUnavailable(
                    f"Hermes profile {profile!r} accepted the run but sent no run id"
                )
            if started.get("replayed"):
                existing = await asyncio.to_thread(self._store.get_run, run_id)
                if existing is not None:
                    if existing.client != client:
                        log.warning("bridge.idempotency_foreign_run", run_id=run_id, client=client)
                        raise BridgeError(
                            "bridge.idempotency_conflict",
                            "idempotency_key matches a run that belongs to another client",
                        )
                    return _view(existing)
            status = started.get("status")
            now = time.time()
            row = RunRow(
                run_id=run_id,
                client=client,
                agent=agent,
                profile=profile,
                session_key=session_key,
                # Hermes answers "started"; until an event says otherwise it is running.
                status=status if status in HERMES_STATUSES else "running",
                created_at=now,
                updated_at=now,
                input_preview=input[:INPUT_PREVIEW_CHARS],
                output=None,
                error=None,
                usage_json=None,
                admission_id=_str_or_none(started.get("admission_id")),
                execution_generation=None,
                pending_request_json=None,
                last_seq=0,
            )
            # Hermes has accepted the run: record and watch it even if the caller
            # goes away now, or it would hold a concurrency slot unwatched.
            preview = " ".join(input.split())[:_BUS_PREVIEW_CHARS]
            admit = asyncio.create_task(self._admit(row, f"{client} → {agent}: {preview}"))
            self._admitting.add(admit)
            admit.add_done_callback(self._admitting.discard)
            await asyncio.shield(admit)
        return _view(row)

    async def _admit(self, row: RunRow, message: str) -> None:
        await asyncio.to_thread(self._store.insert_run, row)
        self._watcher.start(row)
        await self._emit("agents.run_started", "info", row, message)

    async def get_run(self, caller: Caller, run_id: str) -> dict[str, Any]:
        """The run's current RunView."""
        return _view(await self._owned(caller, run_id))

    async def list_runs(
        self,
        caller: Caller,
        *,
        status: str | None = None,
        limit: int = 20,
        client: str | None = None,
    ) -> list[dict[str, Any]]:
        """The caller's runs (the admin key: every client's), newest first.

        ``client`` narrows the admin view to one client's runs. A bridge caller
        only ever sees its own, so a ``client`` naming anyone else matches nothing.
        """
        if caller.tier == "admin":
            scope = client
        elif client is not None and client != caller.name:
            return []
        else:
            scope = caller.name
        rows = await asyncio.to_thread(
            self._store.list_runs,
            client=scope,
            status=status,
            limit=min(max(int(limit), 1), _MAX_LIST),
        )
        return [_view(row) for row in rows]

    async def run_events(
        self, caller: Caller, run_id: str, *, since_seq: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The run's stored events after ``since_seq``, oldest first (deltas as ``message.text``)."""
        await self._owned(caller, run_id)
        return await asyncio.to_thread(
            self._store.events,
            run_id,
            since_seq=max(int(since_seq), 0),
            limit=min(max(int(limit), 1), _MAX_EVENTS),
        )

    async def wait_run(
        self, caller: Caller, run_id: str, *, timeout_s: float = DEFAULT_WAIT_S
    ) -> dict[str, Any]:
        """Block until the run ends, needs the caller (a clarification), or the timeout passes.

        ``timeout_s`` is clamped to [0, 600]. The view adds ``events_tail`` (the
        last 20 events) and ``timed_out``.
        """
        timeout = clamp_wait(timeout_s)
        row = await self._owned(caller, run_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            signal = self.change_signal(run_id)
            row = await self._owned(caller, run_id)
            if _wait_over(row):
                timed_out = False
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(signal.wait(), remaining)
        view = _view(row)
        view["events_tail"] = await asyncio.to_thread(
            self._store.last_events, run_id, limit=WAIT_TAIL_EVENTS
        )
        view["timed_out"] = timed_out
        return view

    async def answer_run(
        self, caller: Caller, run_id: str, *, request_id: str, answer: str
    ) -> dict[str, Any]:
        """Answer the run's pending clarification ``request_id``; the run resumes.

        A run with no such clarification pending is 409 ``bridge.no_pending_request``;
        Hermes's own 409s (``stale_generation``, ``clarify_not_pending``) come back
        as ``bridge.agent_refused`` with Hermes's code in the message.
        """
        row = await self._owned(caller, run_id)
        pending = pending_of(row)
        if (
            row.status in TERMINAL_STATUSES
            or pending is None
            or pending.get("event") != "clarify.request"
            or pending.get("request_id") != request_id
        ):
            raise BridgeError(
                "bridge.no_pending_request",
                f"run {run_id!r} has no pending clarification {request_id!r}",
            )
        generation = pending.get("execution_generation")
        if not isinstance(generation, int) or isinstance(generation, bool):
            generation = row.execution_generation
        if generation is None:
            raise BridgeError(
                "bridge.no_pending_request",
                f"clarification {request_id!r} of run {run_id!r} carries no execution generation",
            )
        await self._gateway.clarify(
            row.profile,
            run_id,
            request_id=request_id,
            execution_generation=generation,
            answer=answer,
        )
        updated = await self._watcher.settle(run_id, request_id)
        return await self._fresh_view(run_id, updated)

    async def steer_run(
        self,
        caller: Caller,
        run_id: str,
        *,
        input: str,  # Hermes's own field name
    ) -> dict[str, Any]:
        """Add guidance to a run that is ``running``; anything else is 409 ``bridge.run_not_running``."""
        row = await self._owned(caller, run_id)
        if row.status != "running":
            raise _not_running(run_id, row.status, "steering needs a running run")
        try:
            await self._gateway.steer(row.profile, run_id, input)
        except HermesRefused as exc:
            if exc.hermes_code == "run_not_running":
                raise BridgeError("bridge.run_not_running", exc.message) from exc
            raise
        return await self._fresh_view(run_id, None)

    async def stop_run(self, caller: Caller, run_id: str) -> dict[str, Any]:
        """Ask Hermes to stop the run; the view shows ``stopping`` until it ends.

        Hermes's answer sets the status only when it is a live one: the run ends
        when the watcher sees its terminal event, which also withdraws the inbox
        row and tells the bus. A pending approval or clarification is cleared at
        once, with the clarify timer.
        """
        row = await self._owned(caller, run_id)
        if row.status in TERMINAL_STATUSES:
            raise _not_running(run_id, row.status, "only a live run can be stopped")
        answer = await self._gateway.stop(row.profile, run_id)
        reported = answer.get("status")
        live = reported in HERMES_STATUSES and reported not in TERMINAL_STATUSES
        updated = await self._watcher.stopping(run_id, str(reported) if live else "stopping")
        return await self._fresh_view(run_id, updated)

    # -- process lifecycle -------------------------------------------------------------

    async def reattach(self) -> int:
        """Resume watching every live run in the ledger (hal0-api start); returns how many.

        A run Hermes no longer knows (404) is marked ``unknown``. A run whose
        profile cannot be reached now is watched anyway: the watcher keeps trying.
        Overlapping calls are safe: each run gets one watcher.
        """
        self._check_open()
        started = 0
        for row in await asyncio.to_thread(self._store.live_runs):
            if self._closed:  # close() ran meanwhile: nothing more may be watched
                break
            try:
                await self._gateway.get_run(row.profile, row.run_id)
            except HermesRefused as exc:
                if exc.hermes_status == 404:
                    await self._watcher.mark_unknown(
                        row, "Hermes no longer knows this run (it ended or Hermes restarted)"
                    )
                    continue
            except BridgeError:
                pass
            if await self._watcher.attach(row):
                started += 1
        return started

    async def close(self) -> None:
        """Stop every watcher (hal0-api shutdown); the ledger keeps the runs for ``reattach``.

        Afterwards the service takes no new runs and attaches nothing (503
        ``bridge.unavailable``). Closing again is harmless.
        """
        self._closed = True
        if self._admitting:
            await asyncio.gather(*self._admitting, return_exceptions=True)
        await self._watcher.stop_all()

    # -- helpers -----------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise BridgeError("bridge.unavailable", "the agent bridge is shutting down")

    def _config_now(self) -> BridgeConfig:
        return self._config if isinstance(self._config, BridgeConfig) else self._config()

    def _start_lock(self, client: str) -> asyncio.Lock:
        lock = self._start_locks.get(client)
        if lock is None:
            lock = self._start_locks[client] = asyncio.Lock()
        return lock

    async def _owned(self, caller: Caller, run_id: str) -> RunRow:
        """The run, if ``caller`` may see it; otherwise 404, never 403 (module docstring)."""
        row = await asyncio.to_thread(self._store.get_run, run_id)
        if row is None or (caller.tier != "admin" and row.client != caller.name):
            raise _not_found(run_id)
        return row

    async def _fresh_view(self, run_id: str, row: RunRow | None) -> dict[str, Any]:
        if row is None:
            row = await asyncio.to_thread(self._store.get_run, run_id)
            if row is None:
                raise _not_found(run_id)
        return _view(row)

    async def _emit(self, type_: str, severity: str, row: RunRow, message: str) -> None:
        if self._events is None:
            return
        data = {
            "run_id": row.run_id,
            "agent": row.agent,
            "client": row.client,
            "status": row.status,
        }
        try:
            await self._events.emit(type_, severity, f"bridge:{row.client}", message, data=data)
        except Exception:
            log.warning("bridge.event_emit_failed", run_id=row.run_id, exc_info=True)


def _not_running(run_id: str, status: str, why: str) -> BridgeError:
    return BridgeError("bridge.run_not_running", f"run {run_id!r} is {status}; {why}")


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
