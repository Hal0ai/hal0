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
  the run is posted; admin runs count under the client name ``admin``. A start
  is admitted under its client's lock, which is held until the run is in the
  ledger even when the caller goes away mid-start, so two starts can never
  both pass the cap. A start whose idempotency key the ledger already holds
  for that client and profile is answered from the ledger before the cap is
  checked: the same request is that run, another request with the same key is
  409 ``bridge.idempotency_conflict`` (what Hermes answers itself).
* **Ledger failures.** If the ledger cannot record a run Hermes has just
  accepted, hal0 asks Hermes to stop it (best effort, so it holds no slot
  unwatched) and the start is 503 ``bridge.store_unavailable``.
* **Waiting.** ``wait_run`` defaults to 120 s and is capped at 600 s. It returns
  when the run ends or needs the caller (a clarification is pending); an
  approval is the operator's to give in the inbox, so it does not end a wait.
* **Shutdown.** After :meth:`BridgeService.close`, starting a run, a teach call
  or re-attaching is 503 ``bridge.unavailable``: a watcher started then could
  not be stopped.
* **Restart.** :meth:`BridgeService.reattach` probes every live run at once,
  each with a short timeout (:data:`REATTACH_PROBE_S`), so one hung profile
  cannot hold up the others; a run whose profile does not answer in time is
  watched anyway and the watcher's own reconnect loop keeps trying.

The :class:`~hal0.bridge.watcher.RunWatcher` keeps each live run's ledger row
current and calls back on every change, which wakes waiters here.

The teach methods (ADR-0043 decision 10) let a bridge client leave knowledge
behind for a Hermes profile:

* **Memory.** ``memory_add`` and ``memory_search`` act in the agent's own
  namespace, forced by :meth:`MemoryDispatcher.scoped` (private mode on, identity
  fixed to ``hermes`` or ``hermes__<profile>``), or in ``shared`` through
  :meth:`MemoryDispatcher.shared` (private mode off). Neither trusts the live
  request's headers. Every write carries ``source:bridge:<client>``. The memory
  layer reads ownership and visibility off a note's *tags* (``agent:<id>``,
  ``visibility:private``, ``project:<id>``) and trusts what the writer put there
  (issue #3136), so a client's own tags are filtered first: any tag starting
  ``agent:``, ``visibility:``, ``project:`` or ``source:bridge:`` (compared
  case-insensitively, after stripping) is dropped. Ownership and provenance are
  hal0's to stamp, never the client's.
* **Skills.** Reads are direct. ``skill_put`` and ``skill_delete`` validate, then
  queue the change in the approval inbox (tools ``agent_skill_put`` and
  ``agent_skill_delete``); nothing is written until the operator approves, and
  a request that could never succeed (invalid, or a shipped skill's name) is
  refused before anything is queued. A put and a delete of one name can both be
  pending. Each client's change is its own row: a repeat by the same client is
  that row, and an identical put or delete by another client is that client's
  own. For a put, an edited re-push by the *same client* withdraws its older row,
  so approving always writes what the card shows, and an edited push while
  *another client's* put of that name is pending is 409
  ``bridge.skill_pending_elsewhere``. A client holds at most
  :data:`MAX_PENDING_SKILL_CHANGES` pending skill changes (429
  ``bridge.too_many_pending``); the queue lets go of a resolved row's executor,
  which is what holds the pushed content. A push must be reviewable on its card
  in full: ``SKILL.md`` at most 16 KiB, the whole push at most 48 KiB, all of it
  UTF-8 text with no secret-shaped value that is not a placeholder and no control,
  format or invisible character (in a file, a file name or the skill name); the
  card carries ``SKILL.md`` and every file's content exactly as approval writes
  them, never cut. A bigger skill is the operator's to drop in by hand.
* **Capabilities.** :func:`hal0.bridge.capabilities.capabilities_view`, off the
  event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from hal0.agents.hermes_api import (
    DEFAULT_PROFILE,
    api_server_base,
    model_id_for,
    probe_status,
    profile_dirs,
    profile_health,
    served_profiles,
)
from hal0.agents.hermes_provision import SKILLS_EXTERNAL_DIRS, profile_agent_id
from hal0.bridge.caller import Caller
from hal0.bridge.capabilities import FetchJson, capabilities_view
from hal0.bridge.errors import BridgeError
from hal0.bridge.hermes_client import HermesGateway, HermesRefused, HermesUnavailable
from hal0.bridge.skills import SkillLibrary
from hal0.bridge.store import TERMINAL_STATUSES, BridgeStore, RunRow
from hal0.bridge.watcher import (
    HERMES_STATUSES,
    ConfigSource,
    RunWatcher,
    WatcherTiming,
    pending_of,
)
from hal0.config.schema import BridgeConfig
from hal0.redaction import redact_log_line, redact_secret_shapes, secret_spans

if TYPE_CHECKING:
    from hal0.events import EventBus
    from hal0.mcp.approval_queue import ApprovalQueue

__all__ = [
    "DEFAULT_WAIT_S",
    "MAX_PENDING_SKILL_CHANGES",
    "MAX_PUSH_SKILL_MD_BYTES",
    "MAX_PUSH_TOTAL_BYTES",
    "MAX_WAIT_S",
    "BridgeService",
    "Caller",
    "clamp_wait",
]

log = structlog.get_logger(__name__)

#: ``wait_run``'s default and its cap (ADR-0043 decision 9).
DEFAULT_WAIT_S = 120.0
MAX_WAIT_S = 600.0

#: Characters of a run's input kept in the ledger and shown in lists.
INPUT_PREVIEW_CHARS = 200

#: Events a finished ``wait_run`` carries.
WAIT_TAIL_EVENTS = 20

#: How long :meth:`BridgeService.reattach` waits for Hermes to answer for one run.
REATTACH_PROBE_S = 10.0

#: Streamed text read on each side of an events window to mask a secret split over
#: events (:meth:`BridgeService.run_events_window`): at most this many characters,
#: from at most :data:`_CONTEXT_EVENTS` stored events.
TEXT_CONTEXT_CHARS = 8192
_CONTEXT_EVENTS = 256

#: ``X-Hermes-Session-Key`` is at most 256 characters; the idempotency key gets the same cap.
_MAX_HEADER_KEY = 256

#: Visible ASCII: a key travels in an HTTP header.
_HEADER_KEY = re.compile(r"[\x21-\x7e]+")

_MAX_LIST = 200
_MAX_EVENTS = 500

#: The agent id of the shared memory namespace (``agent="shared"`` in the memory tools).
SHARED_AGENT = "shared"

#: The most hits ``memory_search`` asks for; the memory layer itself allows 200.
_MAX_MEMORY_HITS = 50

#: A bridge push must be reviewable on its inbox card, in full: ``SKILL.md`` at most
#: 16 KiB and the whole push (``SKILL.md`` plus supporting files) at most 48 KiB of
#: UTF-8 text. The library's own limits (256 KiB a file, 16 files) stay the on-disk
#: rule; a bigger skill is the operator's to drop in by hand (ADR-0043 decision 10).
MAX_PUSH_SKILL_MD_BYTES = 16 * 1024
MAX_PUSH_TOTAL_BYTES = 48 * 1024

_NOT_REVIEWABLE = "bridge pushes are limited to reviewable text (SKILL.md ≤ 16 KiB, total ≤ 48 KiB)"

#: Unicode categories of characters a reader does not see as written on the inbox
#: card while the model still reads them: controls (Cc: C0, DEL and C1), format
#: characters (Cf: zero-width and joiner characters, bidirectional marks, embeddings,
#: overrides and isolates, tag characters, the soft hyphen, the BOM), private use
#: (Co), unassigned code points (Cn, by this Python's Unicode database) and the line
#: and paragraph separators (Zl, Zp: U+2028 and U+2029, which break a line in some
#: viewers and not in others). Tab, newline and carriage return are the only
#: controls a skill may hold; NUL is refused earlier, as not text at all.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Zl", "Zp"})
_SHOWN_CONTROLS = frozenset("\t\n\r")
#: Invisible marks outside those categories: the variation selectors (U+FE00-U+FE0F,
#: U+E0100-U+E01EF), which change how the character before them is drawn and show
#: nothing themselves, and the combining grapheme joiner (U+034F).
_INVISIBLE_MARKS = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF), (0x034F, 0x034F))
_HIDDEN_TEXT_KINDS = "control, format or invisible characters"

#: A value a skill may show where the log redactor would mask a secret: it names one
#: rather than being one (``Bearer <token>``, ``$ENV_VAR``, ``HF_TOKEN=...``). Matched
#: whole once quotes and whitespace are trimmed (:func:`_is_placeholder`), ignoring
#: case except where marked ``(?-i:...)``. A ``<...>`` holds no ``$(`` or backtick (a
#: command is not a placeholder). A ``$VAR`` or ``${VAR}`` ends the value (``${VAR``:
#: the redactor's span stops before the brace), and its name is in one case
#: (``$API_KEY``, ``$api_key``), so an opaque mixed-case value behind a ``$`` is not
#: taken for a name. ``YOUR_KEY`` is upper case.
_PLACEHOLDER = re.compile(
    r"<(?:(?!\$\()[^<>`]){1,64}>"
    r"|\$(?:\{(?-i:[A-Z_][A-Z0-9_]*|[a-z_][a-z0-9_]*)\}?"
    r"|(?-i:[A-Z_][A-Z0-9_]*|[a-z_][a-z0-9_]*))\Z"
    r"|(?-i:YOUR_[A-Z0-9_]+)"
    r"|\.{3}|\N{HORIZONTAL ELLIPSIS}"
    r"|x{3,}|\*{3,}"
    r"|(?:TODO|TBD|CHANGEME|REPLACE[_-]?ME|PLACEHOLDER|EXAMPLE|REDACTED)"
    r"|[<\[{(]?(?:your|my|the)[ _-][a-z _-]{1,40}[>\]})]?",
    re.IGNORECASE,
)

#: What a masked value is trimmed of before it is judged (backticks: skills are Markdown).
_PLACEHOLDER_TRIM = " \t\r\n\"'`"

_USE_A_PLACEHOLDER = "use a placeholder such as <token> or $ENV_VAR"

#: Why a pending skill row was retired when a newer push of the same skill arrived.
_SUPERSEDED = "superseded by a newer push of this skill"

#: Most pending ``agent_skill_put`` / ``agent_skill_delete`` rows one client may hold in the inbox.
MAX_PENDING_SKILL_CHANGES = 8

_SKILL_TOOLS = frozenset({"agent_skill_put", "agent_skill_delete"})

#: Tag prefixes a client may not write: the memory layer treats these as hal0's own
#: (ownership, visibility, project scope, provenance); see the module docstring and #3136.
_RESERVED_TAG_PREFIXES = ("agent:", "visibility:", "project:", "source:bridge:")

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
    events; with no inbox a skill change is refused, since nobody could approve
    it). ``memory_dispatcher`` is the app-level one the memory teach tools pin
    with ``scoped`` / ``shared`` (None when memory is off). ``skills`` is the skill
    library (default: hal0's shipped mirror and the drop-in directory from
    ``SKILLS_EXTERNAL_DIRS``). ``timing`` scales the watcher's clocks (tests),
    ``probe`` replaces the HTTP status probe :meth:`list_agents` uses and
    ``detail_fetch`` the ``GET /health/detailed`` request :meth:`capabilities` makes.
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
        skills: SkillLibrary | None = None,
        timing: WatcherTiming | None = None,
        probe: Callable[[str], int] | None = None,
        detail_fetch: FetchJson | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._config = config
        self._events = events
        self._approvals = approval_queue
        self.memory_dispatcher = memory_dispatcher
        self._skills = skills if skills is not None else _default_skills()
        self._skill_gate = asyncio.Lock()
        self._probe = probe
        self._detail_fetch = detail_fetch
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
        fingerprint = _request_digest(
            input=input,
            instructions=instructions,
            reasoning_effort=reasoning_effort,
            session_key=session_key,
        )
        async with self._start_lock(client):
            if sent_idempotency is not None:
                # A retried start is answered from the ledger before the cap is checked:
                # the run it names is already admitted and counts once.
                known = await asyncio.to_thread(
                    self._store.run_for_idempotency_key, client, profile, sent_idempotency
                )
                if known is not None:
                    if known.request_sha256 not in (None, fingerprint):
                        raise BridgeError(
                            "bridge.idempotency_conflict",
                            "idempotency_key was already used for a run with different input",
                        )
                    return _view(known)
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
                # Masked before it is cut, so the cut cannot leave half a token unmasked.
                input_preview=redact_log_line(input)[:INPUT_PREVIEW_CHARS],
                output=None,
                error=None,
                usage_json=None,
                admission_id=_str_or_none(started.get("admission_id")),
                execution_generation=None,
                pending_request_json=None,
                last_seq=0,
                idempotency_key=sent_idempotency,
                request_sha256=fingerprint,
            )
            # Hermes has accepted the run: record and watch it even if the caller
            # goes away now, or it would hold a concurrency slot unwatched.
            # The bus message names who and where, never the task: activity.db is
            # world-readable (0644) and run text belongs in the 0600 ledger.
            admit = asyncio.create_task(self._admit(row, f"{client} → {agent}"))
            self._admitting.add(admit)
            admit.add_done_callback(self._admitting.discard)
            try:
                await asyncio.shield(admit)
            finally:
                # A cancelled caller must not release the client's lock while the
                # shielded admission is still writing: the next start would count
                # the live runs before this one is in the ledger.
                await _until_done(admit)
        return _view(row)

    async def _admit(self, row: RunRow, message: str) -> None:
        """Record and watch a run Hermes accepted; 503 ``bridge.store_unavailable`` if it cannot.

        A ledger that cannot take the row would leave the run unwatched, holding a
        Hermes slot with nobody to report it: hal0 asks Hermes to stop it (best
        effort) before refusing the start.
        """
        try:
            await asyncio.to_thread(self._store.insert_run, row)
        except Exception as exc:
            log.error(
                "bridge.ledger_insert_failed",
                run_id=row.run_id,
                client=row.client,
                error=redact_log_line(f"{type(exc).__name__}: {exc}"),
            )
            try:
                await self._gateway.stop(row.profile, row.run_id)
            except Exception:
                log.warning("bridge.unrecorded_run_stop_failed", run_id=row.run_id, exc_info=True)
            raise BridgeError(
                "bridge.store_unavailable",
                "the run ledger could not record the run, so it was stopped; try again",
                status=503,
            ) from None
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

    async def run_events_window(
        self, caller: Caller, run_id: str, *, since_seq: int = 0, limit: int = 100
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]] | None]:
        """:meth:`run_events` with its streamed-text context: ``(before, events, after)``.

        ``before`` is the run of stored ``message.text`` events just before the
        window and ``after`` the run just after it, oldest first, each at most
        :data:`TEXT_CONTEXT_CHARS` of text (fewer at either end of the run, or where
        another event interrupts the text). ``after`` is None when nothing at all
        is stored after the window: the window ends at the run's live edge. The
        ``hal0-agents`` tools mask streamed text across event boundaries with them,
        so a secret split over several ``message.text`` events, or over pages, is
        still caught, and one cut off at the live edge is masked from its prefix.
        """
        await self._owned(caller, run_id)
        since = max(int(since_seq), 0)
        size = min(max(int(limit), 1), _MAX_EVENTS)
        events = await asyncio.to_thread(
            self._store.events, run_id, since_seq=since, limit=size + _CONTEXT_EVENTS
        )
        window, rest = events[:size], events[size:]
        before = await self._text_before(run_id, since + 1) if since > 0 else []
        return before, window, (_text_run(rest) if rest else None)

    async def events_before(self, caller: Caller, run_id: str, seq: int) -> list[dict[str, Any]]:
        """The stored ``message.text`` run just before ``seq`` (a wait tail's left context)."""
        await self._owned(caller, run_id)
        return await self._text_before(run_id, int(seq))

    async def _text_before(self, run_id: str, seq: int) -> list[dict[str, Any]]:
        newest_first = await asyncio.to_thread(
            self._store.events_before, run_id, seq, limit=_CONTEXT_EVENTS
        )
        return list(reversed(_text_run(newest_first)))

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

    # -- teach: memory -------------------------------------------------------------------

    async def memory_add(
        self, caller: Caller, *, agent: str, text: str, tags: list[str] | None
    ) -> dict[str, Any]:
        """Remember ``text`` for ``agent`` (``hermes``, ``hermes/<profile>`` or ``shared``).

        The note lands in the agent's own private namespace (``private:hermes`` or
        ``private:hermes__<profile>``), or in ``shared``, and is tagged
        ``source:bridge:<client>``; the namespace is fixed by the dispatcher, not by
        anything in the arguments. The client's own ``tags`` are filtered by
        :func:`_client_tags`: the memory layer takes ownership and visibility from
        ``agent:`` / ``visibility:`` / ``project:`` tags (#3136), so a client cannot
        write any of them. A memory refusal is 422 ``bridge.memory_invalid``
        or 502 ``bridge.memory_failed``, memory off is 503 ``bridge.memory_disabled``.
        """
        self._check_open()
        dispatcher, extra = await self._memory_target(caller, agent)
        own = f"source:bridge:{caller.name}"
        merged = list(dict.fromkeys([*_client_tags(tags), own]))
        return await _memory_call(dispatcher, "memory_add", {"text": text, "tags": merged, **extra})

    async def memory_search(
        self, caller: Caller, *, agent: str, query: str, limit: int = 10
    ) -> dict[str, Any]:
        """Search ``agent``'s memory: its own private namespace and ``shared``; or ``shared`` alone.

        ``limit`` is clamped to 1..50. Returns the memory layer's ``{status, results}``.
        """
        self._check_open()
        dispatcher, extra = await self._memory_target(caller, agent)
        hits = min(max(int(limit), 1), _MAX_MEMORY_HITS)
        return await _memory_call(
            dispatcher, "memory_search", {"query": query, "limit": hits, **extra}
        )

    async def _memory_target(self, caller: Caller, agent: str) -> tuple[Any, dict[str, Any]]:
        """The dispatcher pinned for ``agent`` and the arguments that name its namespace."""
        if agent == SHARED_AGENT:
            client_id: str | None = None
        else:
            profile = await self._known_profile(agent)
            client_id = "hermes" if profile == DEFAULT_PROFILE else profile_agent_id(profile)
        base = self.memory_dispatcher
        if base is None:
            raise BridgeError(
                "bridge.memory_disabled", "memory is not enabled on this box", status=503
            )
        if client_id is None:
            return base.shared(caller.name), {"dataset": SHARED_AGENT}
        return base.scoped(client_id), {}

    async def _known_profile(self, agent: str) -> str:
        """The Hermes profile ``agent`` names, if the box has it; else 404 ``bridge.unknown_agent``."""
        profile = self._gateway.profile_of(agent)
        if profile != DEFAULT_PROFILE:
            known = await asyncio.to_thread(profile_dirs, self._gateway.home)
            if profile not in known:
                raise BridgeError(
                    "bridge.unknown_agent", f"no Hermes profile {profile!r} on this box"
                )
        return profile

    # -- teach: skills -------------------------------------------------------------------

    def skills_list(self) -> list[dict[str, Any]]:
        """Every skill in hal0's shipped mirror and the drop-in directory (see :class:`SkillLibrary`).

        503 ``bridge.unavailable`` once the service is closed, like every teach call.
        """
        self._check_open()
        return self._skills.list()

    def skill_get(self, name: str) -> dict[str, Any]:
        """One skill with its ``SKILL.md`` text and supporting file names (503 once closed)."""
        self._check_open()
        return self._skills.get(name)

    async def skill_put(
        self, caller: Caller, *, name: str, skill_md: str, files: dict[str, str] | None
    ) -> dict[str, Any]:
        """Queue creating or replacing the drop-in skill ``name`` for the operator's approval.

        Validation (the reviewable-text caps, name, frontmatter, sizes, file names)
        and the shipped-name check run first, so a doomed request is refused here,
        before anything is queued: 422 ``bridge.skill_invalid`` or 403
        ``bridge.skill_readonly``. The card names the pushing ``client``; its
        ``preview`` is the whole ``SKILL.md`` and ``files`` maps each supporting file
        to ``{size, sha256, content}``, every text exactly what approval writes and
        never cut (``preview_truncated`` is always false). Returns
        ``{status: "pending_approval", approval_id, name}``; the write happens only
        when the operator approves the ``agent_skill_put`` row. Another client's
        pending put of this name is 409 ``bridge.skill_pending_elsewhere``, unless
        the push is identical to it: then this client gets its own row, and the
        other client's is left as it is. This client already holding
        :data:`MAX_PENDING_SKILL_CHANGES` pending changes is 429
        ``bridge.too_many_pending``.
        """
        self._check_open()
        queue = self._inbox()
        total = _check_reviewable(name, skill_md, files)
        await asyncio.to_thread(self._skills.validate, name, skill_md, files)
        if await asyncio.to_thread(self._skills.is_shipped, name):
            raise _readonly(name, "changed")
        pushed = dict(files) if files else {}
        digest = _skill_digest(skill_md, pushed)
        # Everything the approval writes is on the card, byte for byte and never cut:
        # the operator must be able to read what they approve. The text goes on raw,
        # not through the log redactor: a mask would show the operator something
        # other than what Hermes loads (a quoted command after a secret-shaped name is
        # masked whole). That is safe only because _check_reviewable refused any file
        # holding a secret-shaped value that is not a placeholder: what the redactor
        # would still mask here is a placeholder, which the operator must read as is.
        # ``client`` names the pusher, and being in the args it is part of the
        # queue's dedup digest: an identical push by another client is its own row.
        args: dict[str, Any] = {
            "name": name,
            "client": caller.name,
            "bytes": total,
            "sha256": digest,
            "preview": skill_md,
            "preview_truncated": False,
            "files": {
                fname: {
                    "size": len(pushed[fname].encode()),
                    "sha256": hashlib.sha256(pushed[fname].encode()).hexdigest(),
                    "content": pushed[fname],
                }
                for fname in sorted(pushed)
            },
        }

        async def execute(_args: dict[str, Any]) -> dict[str, Any]:
            row = await asyncio.to_thread(self._skills.put, name, skill_md, pushed)
            return {key: row[key] for key in ("name", "source", "category", "files")}

        client_id = f"bridge:{caller.name}"
        async with self._skill_gate:
            # One pending put per skill, always the newest content: a row for other
            # content would write that content when approved, not what was just pushed.
            # Only the pusher's own row is replaced; another client's is theirs to keep.
            pending = queue.list_pending()
            stale = [
                entry
                for entry in pending
                if entry["tool"] == "agent_skill_put"
                and entry["args"].get("name") == name
                and entry["args"].get("sha256") != digest
            ]
            if any(entry["client_id"] != client_id for entry in stale):
                raise BridgeError(
                    "bridge.skill_pending_elsewhere",
                    f"another client has a pending change to skill {name!r}",
                )
            self._check_pending_cap(pending, client_id, tool="agent_skill_put", name=name)
            for entry in stale:
                await queue.withdraw(entry["id"], _SUPERSEDED)
            approval_id = await queue.enqueue(
                "agent_skill_put",
                args,
                client_id=client_id,
                executor=execute,
                target_args=("name",),
            )
        return {"status": "pending_approval", "approval_id": approval_id, "name": name}

    async def skill_delete(self, caller: Caller, *, name: str) -> dict[str, Any]:
        """Queue removing the drop-in skill ``name`` for the operator's approval.

        A shipped skill is 403 ``bridge.skill_readonly`` and an unknown one 404
        ``bridge.skill_not_found``, both before anything is queued; this client
        already holding :data:`MAX_PENDING_SKILL_CHANGES` pending changes is 429
        ``bridge.too_many_pending``. The card names the pushing ``client``, which is
        part of the queue's dedup digest: a repeat by this client is its one row,
        while another client's delete of the same skill is that client's own row.
        """
        self._check_open()
        queue = self._inbox()
        row = await asyncio.to_thread(self._skills.get, name)
        if row["source"] == "shipped":
            raise _readonly(name, "deleted")

        async def execute(_args: dict[str, Any]) -> dict[str, Any]:
            await asyncio.to_thread(self._skills.delete, name)
            return {"deleted": name}

        client_id = f"bridge:{caller.name}"
        async with self._skill_gate:
            self._check_pending_cap(
                queue.list_pending(), client_id, tool="agent_skill_delete", name=name
            )
            approval_id = await queue.enqueue(
                "agent_skill_delete",
                {"name": name, "client": caller.name},
                client_id=client_id,
                executor=execute,
                target_args=("name",),
            )
        return {"status": "pending_approval", "approval_id": approval_id, "name": name}

    @staticmethod
    def _check_pending_cap(
        pending: list[dict[str, Any]], client_id: str, *, tool: str, name: str
    ) -> None:
        """429 ``bridge.too_many_pending`` when ``client_id`` holds the cap of skill changes.

        The client's pending row for this very ``(tool, name)`` does not count: a re-push
        replaces it (put) or is the same row (delete), so it never adds a row.
        """
        held = sum(
            1
            for entry in pending
            if entry["tool"] in _SKILL_TOOLS
            and entry["client_id"] == client_id
            and not (entry["tool"] == tool and entry["args"].get("name") == name)
        )
        if held >= MAX_PENDING_SKILL_CHANGES:
            raise BridgeError(
                "bridge.too_many_pending",
                f"{MAX_PENDING_SKILL_CHANGES} skill changes are already waiting for approval; "
                "wait for the operator to decide some before pushing more",
            )

    def _inbox(self) -> ApprovalQueue:
        """The approval inbox that gates skill changes; without one nothing is queued."""
        if self._approvals is None:
            raise BridgeError(
                "bridge.unavailable",
                "no approval inbox is running, so a skill change cannot be gated",
            )
        return self._approvals

    # -- teach: capabilities -------------------------------------------------------------

    async def capabilities(self, agent: str) -> dict[str, Any]:
        """What the Hermes profile ``agent`` can do now; secrets never appear (see ``capabilities_view``).

        The profile's API key (the one the run tools use) authenticates the version
        probe; a profile hal0 holds no key for reports no version. The view gets a
        reader for the key, never the key: no frame of this call holds it while
        something can raise (#3137 renders every frame's locals), the same rule as
        :class:`HermesGateway`.
        """
        self._check_open()
        profile = await self._known_profile(agent)
        return await asyncio.to_thread(
            capabilities_view,
            self._gateway.home,
            profile,
            fetch_json=self._detail_fetch,
            read_api_key=functools.partial(self._profile_key, profile),
        )

    def _profile_key(self, profile: str) -> str | None:
        """The API key hal0 holds for ``profile``, or None; returned, never bound to a local."""
        try:
            return self._gateway.key_for(profile)
        except BridgeError:  # bridge.agent_unkeyed: raised before any key is read
            return None

    # -- process lifecycle -------------------------------------------------------------

    async def reattach(self, *, probe_timeout_s: float = REATTACH_PROBE_S) -> int:
        """Resume watching every live run in the ledger (hal0-api start); returns how many.

        Every run is probed at once, each for at most ``probe_timeout_s``, so a hung
        profile cannot hold up the healthy ones. A run Hermes no longer knows (404)
        is marked ``unknown``. A run whose profile cannot be reached, or does not
        answer in time, is watched anyway: the watcher's reconnect loop keeps
        trying. Overlapping calls are safe: each run gets one watcher.
        """
        self._check_open()
        rows = await asyncio.to_thread(self._store.live_runs)
        results = await asyncio.gather(
            *(self._reattach_one(row, probe_timeout_s) for row in rows),
            return_exceptions=True,
        )
        started = 0
        for row, result in zip(rows, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                log.warning("bridge.reattach_failed", run_id=row.run_id, exc_info=result)
            elif result:
                started += 1
        return started

    async def _reattach_one(self, row: RunRow, timeout_s: float) -> bool:
        if self._closed:  # close() ran meanwhile: nothing more may be watched
            return False
        try:
            await asyncio.wait_for(self._gateway.get_run(row.profile, row.run_id), timeout_s)
        except HermesRefused as exc:
            if exc.hermes_status == 404:
                await self._watcher.mark_unknown(
                    row, "Hermes no longer knows this run (it ended or Hermes restarted)"
                )
                return False
        except (BridgeError, TimeoutError):
            pass  # unreachable or slow: the watcher's reconnect loop takes it from here
        if self._closed:
            return False
        return await self._watcher.attach(row)

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


def _text_run(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The leading ``message.text`` events of ``events``, up to :data:`TEXT_CONTEXT_CHARS`."""
    run: list[dict[str, Any]] = []
    chars = 0
    for event in events:
        payload = event.get("payload")
        text = payload.get("text") if isinstance(payload, dict) else None
        if event.get("event") != "message.text" or not isinstance(text, str):
            break
        run.append(event)
        chars += len(text)
        if chars >= TEXT_CONTEXT_CHARS:
            break
    return run


async def _until_done(task: asyncio.Task[None]) -> None:
    """Wait for ``task`` to finish, through any number of cancellations of the waiter.

    The caller's own cancellation (or exception) still propagates once ``task`` is
    done; ``task``'s outcome is the caller's to read, not raised here.
    """
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    if not task.cancelled():
        task.exception()  # read, so a failure is not reported as never retrieved


def _request_digest(
    *, input: str, instructions: str | None, reasoning_effort: str | None, session_key: str | None
) -> str:
    """sha256 of what a start asks Hermes for: the same key with another request is a conflict."""
    body = json.dumps(
        {
            "input": input,
            "instructions": instructions,
            "reasoning_effort": reasoning_effort,
            "session_key": session_key,
        },
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _not_running(run_id: str, status: str, why: str) -> BridgeError:
    return BridgeError("bridge.run_not_running", f"run {run_id!r} is {status}; {why}")


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _default_skills() -> SkillLibrary:
    """The library over hal0's shipped mirror and the drop-in directory Hermes loads."""
    shipped, dropin = SKILLS_EXTERNAL_DIRS
    return SkillLibrary(shipped_root=Path(shipped), dropin_root=Path(dropin))


def _readonly(name: str, verb: str) -> BridgeError:
    return BridgeError("bridge.skill_readonly", f"{name!r} is a shipped skill and cannot be {verb}")


def _check_reviewable(name: Any, skill_md: Any, files: Any) -> int:
    """The push's size in UTF-8 bytes; 422 ``bridge.skill_invalid`` when it is not reviewable text.

    A bridge push is shown whole on its inbox card, so it must be text a person can
    read there: ``SKILL.md`` at most :data:`MAX_PUSH_SKILL_MD_BYTES`, everything at
    most :data:`MAX_PUSH_TOTAL_BYTES`, every file valid UTF-8 with no NUL character.
    Once the sizes pass, no file may carry a secret: every value the log redactor
    would mask (:func:`hal0.redaction.secret_spans`) must be a placeholder
    (:data:`_PLACEHOLDER`: ``<token>``, ``$ENV_VAR``, ``...``, ``YOUR_KEY`` and the
    like), since teaching Hermes to call an authenticated API is what a pushed skill
    is for, and an opaque token is refused. No file, file name or the skill name
    may hold a character the card would not show (:func:`_holds_hidden_text`). The
    card then shows each file exactly as it is written. Anything that is not a
    string is left to :meth:`SkillLibrary.validate`.
    """
    parts: list[tuple[str, Any]] = [("SKILL.md", skill_md)]
    if isinstance(files, dict):
        parts += [(str(key), value) for key, value in files.items()]
    total = 0
    texts: list[tuple[str, str]] = []
    for label, text in parts:
        if not isinstance(text, str):
            continue
        texts.append((label, text))
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise BridgeError(
                "bridge.skill_invalid", f"{_NOT_REVIEWABLE}; {label!r} is not valid UTF-8 text"
            ) from None
        if "\x00" in text:
            raise BridgeError(
                "bridge.skill_invalid", f"{_NOT_REVIEWABLE}; {label!r} is not text (it holds NUL)"
            )
        if label == "SKILL.md" and size > MAX_PUSH_SKILL_MD_BYTES:
            raise BridgeError(
                "bridge.skill_invalid", f"{_NOT_REVIEWABLE}; SKILL.md is {size} bytes"
            )
        total += size
    if total > MAX_PUSH_TOTAL_BYTES:
        raise BridgeError("bridge.skill_invalid", f"{_NOT_REVIEWABLE}; the push is {total} bytes")
    # Only now, with the sizes bounded, is each file read for what the card cannot show.
    if isinstance(name, str) and _holds_hidden_text(name):  # the card shows the name too
        raise BridgeError(
            "bridge.skill_invalid",
            f"{_NOT_REVIEWABLE}: the skill name {name!r} holds {_HIDDEN_TEXT_KINDS}",
        )
    for label, text in texts:
        if _holds_hidden_text(text) or _holds_hidden_text(label):  # the card shows both
            raise BridgeError(
                "bridge.skill_invalid", f"{_NOT_REVIEWABLE}: {label!r} holds {_HIDDEN_TEXT_KINDS}"
            )
        if any(not _is_placeholder(text[start:end]) for start, end in secret_spans(text)):
            raise BridgeError(
                "bridge.skill_invalid",
                f"{_NOT_REVIEWABLE}: {label!r} holds secret-shaped text the inbox card would "
                f"mask; a skill has no business carrying a secret; {_USE_A_PLACEHOLDER}",
            )
    return total


def _holds_hidden_text(text: str) -> bool:
    """Whether ``text`` holds a character the inbox card would not show as the model reads it.

    That is a character of :data:`_HIDDEN_CATEGORIES` other than tab, newline and
    carriage return, or one of :data:`_INVISIBLE_MARKS`. Each distinct character is
    judged once.
    """
    for char in set(text):
        if char in _SHOWN_CONTROLS:
            continue
        point = ord(char)
        if unicodedata.category(char) in _HIDDEN_CATEGORIES or any(
            low <= point <= high for low, high in _INVISIBLE_MARKS
        ):
            return True
    return False


def _is_placeholder(value: str) -> bool:
    """Whether a value the redactor would mask names a secret rather than being one.

    It must match :data:`_PLACEHOLDER` whole once trimmed, and hold no secret shape
    of its own (``$hf_...``, ``<ghp_...>``, ``YOUR_KEYghp_...``): the shape rules find
    nothing in a value that only names a secret.
    """
    if redact_secret_shapes(value) != value:
        return False
    return _PLACEHOLDER.fullmatch(value.strip(_PLACEHOLDER_TRIM)) is not None


def _skill_digest(skill_md: str, files: dict[str, str]) -> str:
    """A short hash of everything a push would write, to tell an edited push from a repeat."""
    h = hashlib.sha256()
    for part in (skill_md, *(f"{key}\0{files[key]}" for key in sorted(files))):
        h.update(part.encode())
        h.update(b"\0\0")
    return h.hexdigest()[:16]


def _client_tags(tags: list[str] | None) -> list[str]:
    """The tags a bridge client sent, minus every reserved namespace (see the module docstring).

    The memory layer reads a note's owner, visibility and project from ``agent:``,
    ``visibility:`` and ``project:`` tags and trusts the writer's (#3136), and
    ``source:bridge:`` is the provenance hal0 stamps. A tag is judged stripped and
    lower-cased; one that survives is passed on as written. Anything that is not a
    string is dropped.
    """
    kept: list[str] = []
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        if tag.strip().lower().startswith(_RESERVED_TAG_PREFIXES):
            continue
        kept.append(tag)
    return kept


async def _memory_call(dispatcher: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a memory tool; the memory layer's error envelope becomes a raised :class:`BridgeError`."""
    result: dict[str, Any] = await dispatcher.dispatch(tool, args)
    if result.get("status") != "error":
        return result
    error = result.get("error")
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "mcp.memory_failed")
    detail = redact_log_line(str(error.get("detail") or code))[:500]
    bridge_code = "bridge.memory_invalid" if code == "mcp.memory_schema" else "bridge.memory_failed"
    raise BridgeError(bridge_code, f"{code}: {detail}")
