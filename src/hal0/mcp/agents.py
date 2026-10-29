"""hal0 agents MCP server: the run and teach tools of the agent bridge (ADR-0043 decisions 9-10).

A remote coding agent (Claude Code, Codex, a CI job) holding a bridge key
reaches this server at ``/mcp/agents`` and delegates work to hal0's Hermes
profiles. The server is a projection of :class:`~hal0.bridge.service.BridgeService`:
every rule (ownership, namespacing, the concurrency cap, waiting) lives there,
so the ``/api/bridge`` run routes and these tools cannot disagree.

Tool catalog
------------

::

    agents_list        — the bundled agents and each Hermes profile's health
    agent_run          — start a run; ``wait_s`` > 0 also waits for it
    agent_run_wait     — block until a run ends or needs the caller
    agent_run_status   — a run's current view
    agent_run_events   — a run's stored events after ``since_seq``
    agent_run_answer   — answer the run's pending clarification
    agent_run_steer    — add guidance to a running run
    agent_run_stop     — ask Hermes to stop a run
    agent_runs_list    — the caller's runs, newest first

The teach tools (decision 10) leave knowledge behind for a Hermes profile::

    agent_memory_add     — remember a note in an agent's own (or the shared) memory
    agent_memory_search  — search that memory
    agent_skills_list    — the skills Hermes loads (shipped, read-only; and drop-in)
    agent_skill_get      — one skill's SKILL.md and file names
    agent_skill_put      — propose a drop-in skill: queued for the operator, not written
    agent_skill_delete   — propose removing a drop-in skill: queued likewise
    agent_capabilities   — a sanitised view of what a Hermes profile can do now

Memory is namespaced by hal0 (``private:hermes``, ``private:hermes__<profile>`` or
``shared``), never by the caller's arguments or headers; tags the caller sends that
start ``agent:``, ``visibility:``, ``project:`` or ``source:bridge:`` are dropped, because
the memory layer trusts them for ownership (#3136). A skill write or delete is
gated: the tool returns ``{status: "pending_approval", approval_id}`` and the
change happens only if the operator approves the row in the hal0 inbox (there is no
approve tool). ``agent_capabilities`` never carries a URL, header, key or other
secret.

Who is asking
-------------

Every call resolves ``Caller.from_principal(hooks["principal_resolver"]())``.
The resolver is a transport hook (see :mod:`hal0.api.mcp_mount`); a call with
no bridge or admin principal is refused with ``auth.forbidden`` (403) before
the service is touched. A bridge client sees only its own runs; a run another
client owns is reported as ``bridge.run_not_found``.

Failures
--------

A :class:`~hal0.bridge.errors.BridgeError` is raised, never returned: FastMCP
catches it and marks the result ``isError``. The message is FastMCP's
``Error executing tool <name>: `` followed by the error, so it contains the
stable code as ``bridge.<reason>: <message>``. A client must not mistake a
refusal for a success payload.

Any other exception a tool body raises is a bug or an outage, and its text can
name paths, SQL or addresses a remote machine has no business seeing. It is
logged on hal0-api with its traceback (``hal0.mcp.agents.internal_error``) and
the client gets ``bridge.internal: internal error; see hal0-api logs``.

Output
------

Every agent-written text a result carries (``output``, ``error``,
``input_preview``, the ``pending`` request, ``events_tail`` and event payloads)
passes through the log redactor (:func:`hal0.redaction.redact_log_line`) before
a remote machine sees it, and a string over 16 KiB is cut at that size with
``truncated: true``.

Streamed text is stored as ``message.text`` events cut wherever the watcher
flushed, so a secret can straddle two of them (``Authorization: Bearer `` in
one, the token in the next). Consecutive ``message.text`` events are therefore
masked as one text, together with the stored ``message.text`` run just outside
the window on each side (before ``since_seq``, after the page, before a wait's
tail; up to 8192 characters each), and each event then shows its own part of
the masked text: a secret is masked whichever events, or pages, it is split
over. At the live edge (a page whose last text, with its right context, ends at
the newest stored event, and a wait's tail) the rest of a token may not be
stored yet, so a known token prefix (:func:`hal0.redaction.token_head_at_end`)
whose characters run to the end of the text is masked through the end. The
output of a run that has not finished grows on every flush, so its end is the
live edge too, in a view and in a list's ``output_preview``.

``output`` is paged rather than lost (:mod:`hal0.bridge.output`): a view shows
the 16 KiB window of the masked text that ``agent_run_status``'s ``offset``
names (0 everywhere else), with ``output_offset``, ``output_total`` and
``next_offset`` in bytes (a negative ``offset`` is refused: ``bridge.invalid_offset``,
422). While more follows, ``truncated`` is true and
``more`` names the call that reads the next window,
``agent_run_status(run_id, offset=<next_offset>)``. The events are no way back
to a long answer: the ledger keeps only a run's newest 500 event rows. Any
other text over 16 KiB (``error``, a ``pending`` request, an event in
``events_tail`` or an events page) is cut with ``truncated: true`` and nothing
pages the rest.

Like :mod:`hal0.mcp.memory`, the tool definitions are built once per process
and shared by every server; the bridge service is read off the server's
``state`` at call time, so it may be published after the mount.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from hal0.api.auth import ANON, AuthPrincipal
from hal0.bridge.caller import Caller
from hal0.bridge.errors import BridgeError
from hal0.bridge.output import (
    OUTPUT_WINDOW_BYTES,
    mask_run,
    masked_output,
    output_window,
    run_list_item,
)
from hal0.mcp._binding import BoundFastMCP, ToolContext, bound_server
from hal0.redaction import redact_log_line

if TYPE_CHECKING:
    from hal0.bridge.service import BridgeService

__all__ = ["MAX_TEXT_BYTES", "build_server"]

log = structlog.get_logger(__name__)

#: The most bytes of one run text (``output``, ``error``, an event's text) a tool returns.
MAX_TEXT_BYTES = OUTPUT_WINDOW_BYTES


def _more(next_offset: int) -> str:
    """The call a ``more`` pointer names: the next window of the run's output."""
    return f"agent_run_status(run_id, offset={next_offset})"


def build_server(
    *,
    state: Any,
    principal_resolver: Callable[[], AuthPrincipal] | None = None,
    name: str = "hal0-agents",
) -> FastMCP:
    """Construct the ``hal0-agents`` server around the shared tool definitions.

    ``state`` is ``app.state`` for a mounted server (the tools read its
    ``bridge_service`` at call time) or any namespace with that attribute.
    ``principal_resolver`` returns the caller of the in-flight request; without
    one every caller is anonymous and every tool refuses with ``auth.forbidden``.
    """
    return BoundFastMCP(
        name,
        tools=_agents_tool_definitions().values(),
        state=state,
        hooks={"principal_resolver": principal_resolver or (lambda: ANON)},
    )


# -- per-call plumbing --------------------------------------------------------------------


def _context(ctx: ToolContext | None) -> tuple[Caller, BridgeService]:
    """The caller of this call and the bridge service it runs against.

    Identity is checked first, so an anonymous caller learns nothing about
    whether the bridge is up. ``bridge_service`` is None until the boot phase
    publishes it, and stays None when it could not be built: 503
    ``bridge.unavailable``, as the ``/api/bridge/runs`` routes answer. A
    disabled bridge never gets here: ``/mcp/agents`` is then not mounted.
    """
    server = bound_server(ctx)
    caller = Caller.from_principal(server.hal0_hooks["principal_resolver"]())
    service: BridgeService | None = getattr(server.hal0_state, "bridge_service", None)
    if service is None:
        raise BridgeError(
            "bridge.unavailable",
            "the agent bridge's run service is not running; see the hal0-api log",
        )
    return caller, service


def _clean(text: str) -> tuple[str, bool]:
    """``text`` with secrets masked, cut to :data:`MAX_TEXT_BYTES`; True when it was cut.

    Masking runs first so the cut cannot leave a half-token the redactor no
    longer recognises. The cut falls on a character boundary.
    """
    text = redact_log_line(text)
    raw = text.encode("utf-8")
    if len(raw) <= MAX_TEXT_BYTES:
        return text, False
    return raw[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore"), True


def _clean_tree(value: Any) -> tuple[Any, bool]:
    """``value`` with every string in it passed through :func:`_clean`; True when any was cut."""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        cut = False
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[key], was_cut = _clean_tree(item)
            cut = cut or was_cut
        return out, cut
    if isinstance(value, list):
        cut = False
        items: list[Any] = []
        for item in value:
            cleaned, was_cut = _clean_tree(item)
            items.append(cleaned)
            cut = cut or was_cut
        return items, cut
    return value, False


# -- streamed text masked across event boundaries (Codex on #3138) -------------------------

_TEXT_EVENT = "message.text"


def _text_of(event: Any) -> str | None:
    """The streamed text a stored ``message.text`` event carries, else None."""
    if not isinstance(event, dict) or event.get("event") != _TEXT_EVENT:
        return None
    payload = event.get("payload")
    text = payload.get("text") if isinstance(payload, dict) else None
    return text if isinstance(text, str) else None


def _clean_events(
    events: list[dict[str, Any]],
    *,
    before: list[dict[str, Any]] | None = None,
    after: list[dict[str, Any]] | None = None,
    at_edge: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    """Events made safe to return; streamed text is masked across event boundaries.

    Each run of consecutive ``message.text`` events is masked as one text by
    :func:`~hal0.bridge.output.mask_run`, the first run together with ``before`` and the last with
    ``after`` (the stored ``message.text`` runs just outside the window, oldest
    first, from :meth:`BridgeService.run_events_window`); then every event goes
    through :func:`_clean_tree` as before. True when any text was cut.

    ``at_edge`` says the end of the events and ``after`` together is the newest
    stored event (the live edge; a wait's tail always is): a last run that reaches
    the end of the window is then masked ``at_edge`` with ``after`` joined to it,
    since the rest of a token running to the end of that text may not be stored
    yet. False when ``after`` was cut by the context cap or another event follows.
    """
    left_context = [t for t in map(_text_of, before or []) if t is not None]
    right_context = [t for t in map(_text_of, after or []) if t is not None]
    texts = [_text_of(event) for event in events]
    rewritten: dict[int, str] = {}
    index = 0
    while index < len(events):
        if texts[index] is None:
            index += 1
            continue
        stop = index
        while stop < len(events) and texts[stop] is not None:
            stop += 1
        group = [t for t in texts[index:stop] if t is not None]
        left = left_context if index == 0 else []
        right = right_context if stop == len(events) else []
        masked = mask_run([*left, *group, *right], at_edge=at_edge and stop == len(events))
        masked = masked[len(left) : len(masked) - len(right)]
        for offset, text in enumerate(masked):
            rewritten[index + offset] = text
        index = stop
    out: list[dict[str, Any]] = []
    for position, event in enumerate(events):
        if position in rewritten:
            event = {**event, "payload": {**event["payload"], "text": rewritten[position]}}
        out.append(event)
    return _clean_tree(out)


def _view(
    view: dict[str, Any], *, offset: int = 0, tail_before: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """A RunView (or a wait's view) made safe to return: secrets masked, texts capped.

    ``output`` is masked whole (:func:`~hal0.bridge.output.masked_output`), then the 16 KiB window
    at byte ``offset`` is shown with ``output_offset``, ``output_total`` and
    ``next_offset`` (:func:`hal0.bridge.output.output_window`); masking first
    means a window edge cannot split a secret the redactor would have caught.
    ``error``, ``input_preview``, the ``pending`` request (a clarification's
    question or an approval's command) and ``events_tail`` of a wait go through
    :func:`_clean`, the tail's streamed text masked across events (:func:`_clean_events`, with
    ``tail_before``, the stored text run just before the tail, and as the live
    edge: the tail is the newest events stored).
    ``truncated`` is true when any text was cut or more output follows; ``more``
    names the call that reads the next window of the output.
    """
    out = dict(view)
    truncated = False
    output = out.get("output")
    if isinstance(output, str):
        out["output"], page = output_window(masked_output(out, output), offset)
        out.update(page)
        if page["next_offset"] is not None:
            truncated = True
            out["more"] = _more(page["next_offset"])
    error = out.get("error")
    if isinstance(error, str):
        out["error"], cut = _clean(error)
        truncated = truncated or cut
    tail = out.get("events_tail")
    if isinstance(tail, list):
        out["events_tail"], cut = _clean_events(tail, before=tail_before)
        truncated = truncated or cut
    preview = out.get("input_preview")
    if isinstance(preview, str):
        out["input_preview"], cut = _clean(preview)
        truncated = truncated or cut
    pending = out.get("pending")
    if pending is not None:
        out["pending"], cut = _clean_tree(pending)
        truncated = truncated or cut
    if truncated:
        out["truncated"] = True
    return out


def _list_item(view: dict[str, Any]) -> dict[str, Any]:
    """A RunView as an ``agent_runs_list`` item: ``output_preview`` in place of ``output``.

    The projection is :func:`hal0.bridge.output.run_list_item`, the one
    ``GET /api/bridge/runs`` uses, so a live run's ``output_preview`` is masked
    to the live edge here and there alike; this adds the tool's masking and caps
    on ``error``, ``input_preview`` and ``pending``. ``output_preview`` is already
    masked and 200 characters, so :func:`_view` leaves it as it is.
    """
    return _view(run_list_item(view))


async def _wait_view(
    service: BridgeService, caller: Caller, view: dict[str, Any]
) -> dict[str, Any]:
    """A wait's view made safe, its tail's text masked with the stored text run before it."""
    tail = view.get("events_tail")
    before: list[dict[str, Any]] = []
    if isinstance(tail, list) and tail and _text_of(tail[0]) is not None:
        before = await service.events_before(caller, view["run_id"], int(tail[0]["seq"]))
    return _view(view, tail_before=before)


def _cleaned(value: dict[str, Any]) -> dict[str, Any]:
    """``value`` with every string masked and capped like a run text; ``truncated`` when any was cut."""
    cleaned, cut = _clean_tree(value)
    out: dict[str, Any] = dict(cleaned)
    if cut:
        out["truncated"] = True
    return out


def _who(ctx: Any) -> str:
    """The calling client's name for a log line; never raises."""
    try:
        return Caller.from_principal(bound_server(ctx).hal0_hooks["principal_resolver"]()).name
    except Exception:  # an anonymous caller, an unbound call: log "unknown", not a second failure
        return "unknown"


def _guarded[**P](
    fn: Callable[P, Awaitable[dict[str, Any]]],
) -> Callable[P, Awaitable[dict[str, Any]]]:
    """Run a tool body so only a :class:`BridgeError` (or FastMCP's own ``ToolError``) escapes.

    Anything else is logged with its traceback on hal0-api and replaced by
    ``bridge.internal``: the exception text may carry paths, SQL or addresses
    that a remote client has no business seeing (ADR-0043). The signature is
    preserved (``functools.wraps``), so FastMCP builds the same input schema and
    still finds the ``ctx`` parameter.
    """

    @functools.wraps(fn)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except (BridgeError, ToolError):
            raise
        except Exception as exc:
            log.error(
                "hal0.mcp.agents.internal_error",
                tool=fn.__name__,
                client=_who(kwargs.get("ctx")),
                error=redact_log_line(f"{type(exc).__name__}: {exc}"),
                exc_info=True,
            )
            raise BridgeError("bridge.internal", "internal error; see hal0-api logs") from None

    return guarded


# -- tool definitions ----------------------------------------------------------------------

_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "agents_list": _READ,
    "agent_run": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
    # A wait changes nothing, but it can block for minutes on an external agent.
    "agent_run_wait": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "agent_run_status": _READ,
    "agent_run_events": _READ,
    "agent_run_answer": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
    "agent_run_steer": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
    "agent_run_stop": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
    ),
    "agent_runs_list": _READ,
    "agent_memory_add": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "agent_memory_search": _READ,
    "agent_skills_list": _READ,
    "agent_skill_get": _READ,
    # A push is a proposal for the operator's inbox, and pushing the same skill again is the
    # same row: idempotent, and nothing is destroyed until a human approves.
    "agent_skill_put": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "agent_skill_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "agent_capabilities": _READ,
}


@functools.cache
def _agents_tool_definitions() -> dict[str, Tool]:
    """The shared ``hal0-agents`` tool definitions, built on first use per process.

    Registered on a scratch FastMCP that is never mounted: it only collects the
    :class:`Tool` objects every :func:`build_server` then shares (#2396). Nothing
    per-app goes into a definition; each tool reaches its server's state through
    the injected ``ctx``.
    """
    server = FastMCP("hal0-agents")

    @server.tool(
        name="agents_list",
        description=(
            "List the agents hal0 can run for you: Hermes (agent id 'hermes' is its "
            "default profile, 'hermes/<profile>' a named one) with each profile's "
            "health and whether hal0 holds its key, and Pi (not runnable here). "
            "Pass an agent id to agent_run."
        ),
        annotations=_ANNOTATIONS["agents_list"],
    )
    @_guarded
    async def agents_list(ctx: ToolContext | None = None) -> dict[str, Any]:
        _, service = _context(ctx)
        return await service.list_agents()

    @server.tool(
        name="agent_run",
        description=(
            "Start a run on a Hermes profile and return its view (run_id, status, ...). "
            "agent is 'hermes' or 'hermes/<profile>'; input is the task. session_key "
            "continues one conversation across runs and idempotency_key makes a retried "
            "start safe; both are private to your client. A start that repeats an "
            "idempotency_key (per client and profile) is answered from hal0's run ledger "
            "for as long as it keeps the run ([bridge].run_retention_days, longer than "
            "Hermes's own idempotency window) and returns that run, finished or not; the "
            "same key with a different request is bridge.idempotency_conflict. wait_s > 0 "
            "also waits that many "
            "seconds (at most 600) for the run to end or need an answer, like agent_run_wait; "
            "0 returns at once. A run that waits on an approval is the operator's to approve "
            "in the hal0 inbox."
        ),
        annotations=_ANNOTATIONS["agent_run"],
    )
    @_guarded
    async def agent_run(
        agent: str,
        input: str,
        session_key: str | None = None,
        instructions: str | None = None,
        reasoning_effort: str | None = None,
        idempotency_key: str | None = None,
        wait_s: int = 0,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        started = await service.start_run(
            caller,
            agent=agent,
            input=input,
            session_key=session_key,
            instructions=instructions,
            reasoning_effort=reasoning_effort,
            idempotency_key=idempotency_key,
        )
        if wait_s > 0:
            waited = await service.wait_run(caller, started["run_id"], timeout_s=wait_s)
            return await _wait_view(service, caller, waited)
        return _view(started)

    @server.tool(
        name="agent_run_wait",
        description=(
            "Block until the run ends, asks you a question (a pending clarification: answer "
            "it with agent_run_answer), or timeout_s seconds pass (default 120, at most 600). "
            "Returns the run view plus events_tail (the last 20 events) and timed_out."
        ),
        annotations=_ANNOTATIONS["agent_run_wait"],
    )
    @_guarded
    async def agent_run_wait(
        run_id: str, timeout_s: int = 120, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        return await _wait_view(
            service, caller, await service.wait_run(caller, run_id, timeout_s=timeout_s)
        )

    @server.tool(
        name="agent_run_status",
        description=(
            "A run's current view: status, output (the text so far), error, usage, and "
            "pending (the approval or clarification it waits on, if any). Output comes in "
            "16 KiB windows: offset is the byte offset to read from (default 0), and the "
            "view carries output_offset, output_total and next_offset (null after the last "
            "window); a negative offset is refused. While more follows, truncated is true and more names the next call, "
            "agent_run_status(run_id, offset=<next_offset>)."
        ),
        annotations=_ANNOTATIONS["agent_run_status"],
    )
    @_guarded
    async def agent_run_status(
        run_id: str, offset: int = 0, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        if offset < 0:  # as GET /api/bridge/runs/{run_id}?offset= refuses one
            raise BridgeError("bridge.invalid_offset", f"offset must be 0 or more, not {offset}")
        caller, service = _context(ctx)
        return _view(await service.get_run(caller, run_id), offset=offset)

    @server.tool(
        name="agent_run_events",
        description=(
            "The run's stored events with seq above since_seq, oldest first (limit at most "
            "500). Returns {run_id, events, last_seq}; call again with since_seq=last_seq "
            "to follow a run, or since_seq=0 to replay it from the start. Streamed text "
            "arrives as message.text events. Only a run's newest 500 events are kept, so "
            "read a long output with agent_run_status(run_id, offset=...); an event text "
            "over 16 KiB is cut and the page then carries truncated: true."
        ),
        annotations=_ANNOTATIONS["agent_run_events"],
    )
    @_guarded
    async def agent_run_events(
        run_id: str, since_seq: int = 0, limit: int = 100, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        before, events, after, at_edge = await service.run_events_window(
            caller, run_id, since_seq=since_seq, limit=limit
        )
        cleaned, cut = _clean_events(events, before=before, after=after, at_edge=at_edge)
        last_seq = max((int(e.get("seq", 0)) for e in cleaned), default=max(since_seq, 0))
        page: dict[str, Any] = {"run_id": run_id, "events": cleaned, "last_seq": last_seq}
        if cut:
            page["truncated"] = True  # an event's text was over 16 KiB; nothing pages the rest
        return page

    @server.tool(
        name="agent_run_answer",
        description=(
            "Answer the clarification a run is waiting on. request_id comes from the run's "
            "pending field (event 'clarify.request'); the run resumes. Refused with "
            "bridge.no_pending_request when that clarification is not pending."
        ),
        annotations=_ANNOTATIONS["agent_run_answer"],
    )
    @_guarded
    async def agent_run_answer(
        run_id: str, request_id: str, answer: str, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _view(await service.answer_run(caller, run_id, request_id=request_id, answer=answer))

    @server.tool(
        name="agent_run_steer",
        description=(
            "Add guidance to a run that is still running. A run in any other state is "
            "refused with bridge.run_not_running."
        ),
        annotations=_ANNOTATIONS["agent_run_steer"],
    )
    @_guarded
    async def agent_run_steer(
        run_id: str, input: str, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _view(await service.steer_run(caller, run_id, input=input))

    @server.tool(
        name="agent_run_stop",
        description=(
            "Ask Hermes to stop a run. The view shows 'stopping' until the run ends. A run "
            "that has already ended is refused with bridge.run_not_running."
        ),
        annotations=_ANNOTATIONS["agent_run_stop"],
    )
    @_guarded
    async def agent_run_stop(run_id: str, ctx: ToolContext | None = None) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _view(await service.stop_run(caller, run_id))

    @server.tool(
        name="agent_runs_list",
        description=(
            "Your runs, newest first. status filters to one status (for example 'running'); "
            "limit is at most 200. Returns {runs}: each a run view with output_preview (the "
            "first 200 characters of the output) in place of output; read a run's output "
            "with agent_run_status. The operator's admin key sees every client's runs."
        ),
        annotations=_ANNOTATIONS["agent_runs_list"],
    )
    @_guarded
    async def agent_runs_list(
        status: str | None = None, limit: int = 20, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        runs = await service.list_runs(caller, status=status, limit=limit)
        return {"runs": [_list_item(run) for run in runs]}

    # -- teach tools (ADR-0043 decision 10) ---------------------------------------------

    @server.tool(
        name="agent_memory_add",
        description=(
            "Remember a note for a Hermes agent. agent is 'hermes' (the default profile), "
            "'hermes/<profile>', or 'shared' (the box's shared memory). The note is stored in "
            "that agent's own private memory, or in the shared memory for 'shared'; hal0 picks "
            "the namespace, not you. It is tagged source:bridge:<your client name> along with "
            "any tags you pass, except tags that start with agent:, visibility:, project: or "
            "source:bridge: (hal0 owns those, so they are dropped). Returns {status, id, "
            "timestamp}. Refused with bridge.unknown_agent for any other agent and "
            "bridge.memory_disabled when the box runs no memory."
        ),
        annotations=_ANNOTATIONS["agent_memory_add"],
    )
    @_guarded
    async def agent_memory_add(
        agent: str, text: str, tags: list[str] | None = None, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _cleaned(await service.memory_add(caller, agent=agent, text=text, tags=tags))

    @server.tool(
        name="agent_memory_search",
        description=(
            "Search what a Hermes agent remembers. agent is 'hermes', 'hermes/<profile>' (its "
            "own private memory and the shared memory) or 'shared' (the shared memory and any "
            "private memory filed under your own client name). limit is 1 to 50 (default 10). "
            "Returns {status, results}. A text over 16 KiB is cut and the result then carries "
            "truncated: true."
        ),
        annotations=_ANNOTATIONS["agent_memory_search"],
    )
    @_guarded
    async def agent_memory_search(
        agent: str, query: str, limit: int = 10, ctx: ToolContext | None = None
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        found = await service.memory_search(caller, agent=agent, query=query, limit=limit)
        return _cleaned(found)

    @server.tool(
        name="agent_skills_list",
        description=(
            "List the skills Hermes loads: hal0's shipped skills (source 'shipped', read-only) "
            "and the operator's drop-in skills (source 'dropin'; the ones you add with "
            "agent_skill_put). Returns {skills: [{name, source, category, description, mtime, "
            "files, broken}]}; broken is true for a skill whose frontmatter cannot be read."
        ),
        annotations=_ANNOTATIONS["agent_skills_list"],
    )
    @_guarded
    async def agent_skills_list(ctx: ToolContext | None = None) -> dict[str, Any]:
        _, service = _context(ctx)
        return _cleaned({"skills": await asyncio.to_thread(service.skills_list)})

    @server.tool(
        name="agent_skill_get",
        description=(
            "One skill: {name, source, category, skill_md, files}. skill_md is the SKILL.md text "
            "(cut at 16 KiB with truncated: true); files names the supporting files, whose "
            "content is not returned. Refused with bridge.skill_not_found."
        ),
        annotations=_ANNOTATIONS["agent_skill_get"],
    )
    @_guarded
    async def agent_skill_get(name: str, ctx: ToolContext | None = None) -> dict[str, Any]:
        _, service = _context(ctx)
        return _cleaned(await asyncio.to_thread(service.skill_get, name))

    @server.tool(
        name="agent_skill_put",
        description=(
            "Propose a skill for Hermes: create or replace the drop-in skill name (lowercase "
            "letters, digits and hyphens). skill_md is the whole SKILL.md and must start with a "
            "frontmatter block holding name (the same name) and description; files maps "
            "supporting file names (no paths) to text. The operator reads the whole push on "
            "the inbox card, so it must be reviewable UTF-8 text: SKILL.md at most 16 KiB, "
            "everything at most 48 KiB, at most 16 files. It is checked at once "
            "(bridge.skill_invalid), but nothing is written until the operator approves it "
            "in the hal0 inbox. Returns {status: 'pending_approval', approval_id, "
            "name}; pushing the same skill again is the same inbox row, and an edited push "
            "replaces your own pending one. Another client's pending change to that skill is "
            "bridge.skill_pending_elsewhere, and more than 8 changes of yours waiting in the "
            "inbox is bridge.too_many_pending. A shipped skill cannot be changed "
            "(bridge.skill_readonly)."
        ),
        annotations=_ANNOTATIONS["agent_skill_put"],
    )
    @_guarded
    async def agent_skill_put(
        name: str,
        skill_md: str,
        files: dict[str, str] | None = None,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _cleaned(await service.skill_put(caller, name=name, skill_md=skill_md, files=files))

    @server.tool(
        name="agent_skill_delete",
        description=(
            "Propose removing the drop-in skill name. Nothing is deleted until the operator "
            "approves it in the hal0 inbox. Returns {status: 'pending_approval', approval_id, "
            "name}. A shipped skill cannot be deleted (bridge.skill_readonly); an unknown one "
            "is bridge.skill_not_found."
        ),
        annotations=_ANNOTATIONS["agent_skill_delete"],
    )
    @_guarded
    async def agent_skill_delete(name: str, ctx: ToolContext | None = None) -> dict[str, Any]:
        caller, service = _context(ctx)
        return _cleaned(await service.skill_delete(caller, name=name))

    @server.tool(
        name="agent_capabilities",
        description=(
            "What a Hermes agent can do right now, read-only and with every secret left out: "
            "{agent, profile, api_server_version, model_aliases: {alias: {model, provider}}, "
            "toolsets_enabled, toolsets_disabled, mcp_servers (names only), skills_dirs, "
            "platforms_enabled, approvals_mode}. agent is 'hermes' or 'hermes/<profile>'. "
            "Each field is what that profile's own config.yaml sets; null means the file does "
            "not set it, so Hermes uses its built-in default. api_server_version is null when "
            "Hermes is not answering."
        ),
        annotations=_ANNOTATIONS["agent_capabilities"],
    )
    @_guarded
    async def agent_capabilities(agent: str, ctx: ToolContext | None = None) -> dict[str, Any]:
        _, service = _context(ctx)
        return _cleaned(await service.capabilities(agent))

    return {tool.name: tool for tool in server._tool_manager.list_tools()}
