"""hal0 agents MCP server: the run tools of the agent bridge (ADR-0043 decision 9).

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
from hal0.bridge.output import OUTPUT_WINDOW_BYTES, output_window
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


def _view(view: dict[str, Any], *, offset: int = 0) -> dict[str, Any]:
    """A RunView (or a wait's view) made safe to return: secrets masked, texts capped.

    ``output`` is masked whole, then the 16 KiB window at byte ``offset`` is
    shown with ``output_offset``, ``output_total`` and ``next_offset``
    (:func:`hal0.bridge.output.output_window`); masking first means a window
    edge cannot split a secret the redactor would have caught. ``error``,
    ``input_preview``, the ``pending`` request (a clarification's question or an
    approval's command) and ``events_tail`` of a wait go through :func:`_clean`.
    ``truncated`` is true when any text was cut or more output follows; ``more``
    names the call that reads the next window of the output.
    """
    out = dict(view)
    truncated = False
    output = out.get("output")
    if isinstance(output, str):
        out["output"], page = output_window(redact_log_line(output), offset)
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
        out["events_tail"], cut = _clean_tree(tail)
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


#: Characters of a run's output an ``agent_runs_list`` item previews.
OUTPUT_PREVIEW_CHARS = 200


def _list_item(view: dict[str, Any]) -> dict[str, Any]:
    """A RunView as an ``agent_runs_list`` item: ``output_preview`` in place of ``output``.

    A list is for finding a run, not reading it: the first
    :data:`OUTPUT_PREVIEW_CHARS` characters of the masked output, and ``error``
    (cleaned like a view's). ``agent_run_status`` reads the whole output.
    """
    item = dict(view)
    output = item.pop("output", None)
    preview = redact_log_line(output)[:OUTPUT_PREVIEW_CHARS] if isinstance(output, str) else None
    out = _view(item)
    out["output_preview"] = preview
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
            "start safe; both are private to your client. wait_s > 0 also waits that many "
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
            return _view(await service.wait_run(caller, started["run_id"], timeout_s=wait_s))
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
        return _view(await service.wait_run(caller, run_id, timeout_s=timeout_s))

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
        events = await service.run_events(caller, run_id, since_seq=since_seq, limit=limit)
        cleaned, cut = _clean_tree(events)
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

    return {tool.name: tool for tool in server._tool_manager.list_tools()}
