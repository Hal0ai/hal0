"""Memory endpoints — ADR-0014 graph-extraction gate + status.

Mounted under ``/api/memory/*``. The dashboard's Memory tab + the
``hal0 memory graph {enable,disable,status}`` CLI both read + write
through this surface; there is no other writer for ``[memory.graph]``
so a swap-flip from either client lands atomically through the same
``save_hal0_config`` pipeline.

The actual graph-extraction dispatch lives in the active memory provider
(:class:`hal0.memory.MemoryProvider`); this module is the thin HTTP
veneer that:

  - Returns ``graph_status()`` (enabled / route / counters / last-built).
  - Validates the toggle payload against :class:`MemoryGraphConfig`.
  - Persists to ``hal0.toml`` via the existing atomic writer.
  - Flips the live wrapper so callers don't need a restart.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import MemoryGraphConfig
from hal0.memory.hindsight_provider import bank_to_namespace
from hal0.memory.namespace import (
    DEFAULT_DATASET,
    MemoryNamespaceError,
    resolve_read_datasets,
    resolve_write_dataset,
)

router = APIRouter()

log = logging.getLogger(__name__)


# ── ADR-0012 identity + ADR-0005 §3 namespace helpers ─────────────────────
#
# Post-ADR-0012 hal0-api is open on 0.0.0.0:8080; agent identity flows on
# the ``X-hal0-Agent`` header (NOT Bearer — auth surface was removed).
# Private-mode opt-in flows on ``X-hal0-Private`` to match the MCP mount
# (:mod:`hal0.api.mcp_mount`); the same toggle gates the same namespace
# promotion rule across both surfaces (issue #317).


_AGENT_HEADER = "x-hal0-agent"
_PRIVATE_HEADER = "x-hal0-private"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# ADR-0005 §5 security hardening: agent identity feeds the
# ``private:<agent>`` dataset name AND the audit log's ``source``
# field. We allow alnum + ``-`` + ``_`` only, up to 64 chars — keeps
# the resolved namespace path-traversal-free, sql-quotable, and
# bounded. Matches the convention used by other hal0 identity headers
# (slot names, capability ids).
_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class MemoryNamespaceInvalid(Hal0Error):
    """The caller's headers + body produced an unresolvable namespace.

    Distinct from a body-shape error so the dashboard can paint a
    different toast ("you asked for private without an agent identity")
    vs a generic 400.
    """

    code = "memory.namespace_invalid"
    status = 400


class MemoryAgentIdInvalid(Hal0Error):
    """The ``X-hal0-Agent`` header value failed the ADR-0005 §5
    identity-shape check.

    Distinct from :class:`MemoryNamespaceInvalid` so the dashboard
    can render a focused message ("agent id must be alnum/-/_, ≤64
    chars, no ``private:`` prefix") rather than a generic namespace
    error.
    """

    code = "memory.agent_id_invalid"
    status = 400


def _agent_id(request: Request) -> str:
    """Return the validated ``X-hal0-Agent`` value or ``"anonymous"``.

    Mirrors :func:`hal0.api.mcp_mount.client_id_resolver` for the REST
    surface — both translate the absence of an identity header into the
    same sentinel so audit + dataset resolution stay consistent.

    Validation (ADR-0005 §5 hardening, surfaced by PR #366 review):

      - Empty / whitespace → ``"anonymous"`` (back-compat with
        unauthenticated callers).
      - Values starting with ``private:`` are REJECTED so a caller
        cannot manufacture ``private:private:bob`` by smuggling the
        prefix through the header. The ``private`` toggle is the
        only path to the namespace.
      - Values must match ``^[a-zA-Z0-9_\\-]{1,64}$`` — agent ids
        flow into the Cognee dataset name + the audit log's
        ``source`` field. Path-traversal candidates (``../etc``),
        control chars, and over-long values are all rejected here.
    """
    raw = request.headers.get(_AGENT_HEADER)
    if raw is None:
        return "anonymous"
    candidate = raw.strip()
    if not candidate:
        return "anonymous"
    if candidate.startswith("private:"):
        raise MemoryAgentIdInvalid(
            "X-hal0-Agent must not be prefixed with 'private:' — the "
            "private namespace is reached via X-hal0-Private: 1, not by "
            "embedding the prefix in the identity header",
            details={"header": "X-hal0-Agent"},
        )
    if not _AGENT_ID_PATTERN.match(candidate):
        raise MemoryAgentIdInvalid(
            "X-hal0-Agent must match [a-zA-Z0-9_-]{1,64}",
            details={"header": "X-hal0-Agent"},
        )
    return candidate


def _is_private(request: Request) -> bool:
    """Return whether the caller opted into ``--private`` mode."""
    raw = request.headers.get(_PRIVATE_HEADER, "")
    return raw.strip().lower() in _TRUTHY


class MemoryGraphConfigInvalid(Hal0Error):
    """Schema validation failure for ``[memory.graph]``."""

    code = "config.memory_graph_invalid"
    status = 400


class MemoryGraphSlotInvalid(Hal0Error):
    """Enable rejected: ``extraction_slot`` is not an enabled llm slot.

    ADR-0023 — graph extraction is dispatched to a local llm slot. A slot that
    doesn't exist (or isn't type=llm/enabled) is rejected with the list of valid
    slots so the dashboard + CLI can fail fast without flipping the gate on.
    """

    code = "config.memory_graph_slot_invalid"
    status = 422


class MemoryUnavailable(Hal0Error):
    """The memory engine failed to initialise at boot.

    Returned when the API got far enough to mount the router but the
    underlying memory engine isn't usable — e.g. the Hindsight daemon is
    unreachable on a stripped-down install. Letting this surface as a 503
    instead of a generic 500 means the dashboard can paint a clear
    "Memory engine unavailable" state rather than a red toast.
    """

    code = "memory.unavailable"
    status = 503


async def _enabled_llm_slots(request: Request) -> list[str]:
    """Return the names of enabled ``type=llm`` slots (valid extraction targets)."""
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return []
    from hal0.api import hal0_chat_slot_alias_map

    try:
        alias_map = await hal0_chat_slot_alias_map(slot_manager)
    except Exception:
        return []
    return sorted(alias_map.keys())


def _wrapper(request: Request) -> Any:
    """Return the live memory provider or raise 503."""
    wrapper = getattr(request.app.state, "memory_provider", None)
    if wrapper is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")
    return wrapper


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# ── GET /api/memory/graph/status ───────────────────────────────────────────


@router.get("/graph/status")
async def graph_status(request: Request) -> dict[str, Any]:
    """Return live graph-extraction state (ADR-0023).

    Response shape (stable contract — the dashboard depends on every
    key being present)::

        {
          "enabled":         bool,
          "extraction_slot": str,            # the local llm slot used for extraction
          "route":           str,            # deprecated mirror of extraction_slot
          "slot_resolves":   bool,           # does extraction_slot match an enabled llm slot?
          "available_slots": [str, ...],     # enabled llm slots the operator can pick
          "in_flight":       int,
          "builds_ok":       int,
          "errors":          int,
          "last_built_at":   iso8601 | None,
          "last_error":      str | None,
        }
    """
    wrapper = _wrapper(request)
    status = wrapper.graph_status()
    available = await _enabled_llm_slots(request)
    status["available_slots"] = available
    status["slot_resolves"] = status.get("extraction_slot") in available
    # llm_timeout_s lives in hal0.toml (not on the provider) — echo it so the
    # dashboard's graph panel can edit it without a second config fetch.
    status["llm_timeout_s"] = load_hal0_config().memory.graph.llm_timeout_s
    await _augment_build_counters(request, status)
    return status


async def _augment_build_counters(request: Request, status: dict[str, Any]) -> None:
    """Replace the provider's placeholder ``0``/``None`` build counters with
    real extraction/consolidation activity aggregated from Hindsight's
    per-bank ``/stats`` (``operations_by_status``, ``pending_operations``,
    ``failed_operations``, ``last_consolidated_at``).

    Extraction runs inside the Hindsight daemon, so hal0 keeps no in-process
    counter — we read it back. Best-effort: on any error the provider's
    placeholder values are left intact, so the endpoint never fails.
    """
    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is None:
        return

    async def _get(path: str) -> Any | None:
        try:
            return await client.request_json("GET", path)
        except Exception:
            return None

    banks_resp = await _get("/v1/default/banks")
    banks = banks_resp.get("banks") if isinstance(banks_resp, dict) else banks_resp
    if not isinstance(banks, list):
        return

    in_flight = builds_ok = errors = 0
    last_built: str | None = None
    saw_stats = False
    for entry in banks:
        bank_id = entry.get("bank_id") if isinstance(entry, dict) else entry
        if not bank_id:
            continue
        st = await _get(f"/v1/default/banks/{bank_id}/stats")
        if not isinstance(st, dict):
            continue
        saw_stats = True
        by_status = st.get("operations_by_status") or {}
        in_flight += int(st.get("pending_operations") or 0)
        in_flight += int(by_status.get("processing") or 0) + int(by_status.get("claimed") or 0)
        builds_ok += int(by_status.get("completed") or 0)
        errors += int(st.get("failed_operations") or by_status.get("failed") or 0)
        built = st.get("last_consolidated_at")
        if built and (last_built is None or built > last_built):
            last_built = built

    if saw_stats:
        status["in_flight"] = in_flight
        status["builds_ok"] = builds_ok
        status["errors"] = errors
        status["last_built_at"] = last_built


# ── PUT /api/memory/graph ──────────────────────────────────────────────────


@router.put("/graph")
async def update_graph_config(request: Request) -> dict[str, Any]:
    """Replace the ``[memory.graph]`` section (ADR-0023).

    Body shape: any subset of :class:`MemoryGraphConfig` fields
    (``enabled``, ``extraction_slot``). The merge preserves un-set fields
    (PATCH-style "flip enabled but keep the slot") because dashboards
    typically send the delta, not the whole block.

    When ``extraction_slot`` changes, it is validated against the live
    enabled-llm-slot set and propagated to the hindsight-api service (via a
    systemd drop-in + restart) so the engine's native extraction LLM follows
    the operator's choice. On success persists ``hal0.toml`` atomically and
    flips the live wrapper's reported state.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    wrapper = _wrapper(request)
    cfg = load_hal0_config()
    current_raw = cfg.memory.graph.model_dump(mode="python")
    merged_raw = {**current_raw, **body}

    try:
        new_cfg = MemoryGraphConfig.model_validate(merged_raw)
    except ValidationError as exc:
        raise MemoryGraphConfigInvalid(
            "memory.graph config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    # Validate extraction_slot against the live slot set when it is being
    # changed — reject an unknown / non-llm slot with the valid options so the
    # gate never flips onto a target that can't serve extraction.
    slot_changed = new_cfg.extraction_slot != cfg.memory.graph.extraction_slot
    if slot_changed:
        available = await _enabled_llm_slots(request)
        if available and new_cfg.extraction_slot not in available:
            raise MemoryGraphSlotInvalid(
                f"extraction_slot {new_cfg.extraction_slot!r} is not an enabled llm slot",
                details={"available_slots": ", ".join(available)},
            )

    # Flip the live wrapper's reported state BEFORE persisting.
    try:
        wrapper.set_graph_enabled(new_cfg.enabled, extraction_slot=new_cfg.extraction_slot)
    except ValueError as exc:
        raise MemoryGraphConfigInvalid(str(exc)) from exc

    # Propagate the extraction slot + LLM timeout to hindsight-api (drop-in +
    # restart) so the engine's native extraction LLM follows the choice.
    # Best-effort: a restart failure is surfaced in the response but does not
    # roll back the config.
    timeout_changed = new_cfg.llm_timeout_s != cfg.memory.graph.llm_timeout_s
    propagation: dict[str, Any] | None = None
    if slot_changed or timeout_changed:
        from hal0.memory.extraction_env import apply_extraction_slot

        propagation = apply_extraction_slot(
            new_cfg.extraction_slot, timeout_s=new_cfg.llm_timeout_s
        )

    cfg.memory.graph = new_cfg
    try:
        save_hal0_config(cfg)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    out = new_cfg.model_dump(mode="json")
    # Echo the live status so the dashboard's optimistic-update path
    # gets the counters in the same round trip without a second fetch.
    out["status"] = wrapper.graph_status()
    if propagation is not None:
        out["propagation"] = propagation
    return out


# ── REST shims for /api/memory/{add,search,list,delete} (#302) ─────────────
#
# Plain-HTTP veneer over CogneeWrapper for callers that don't speak the
# MCP protocol (Hermes bootstrap CLI, dashboard Agents > Peers tab,
# in-process scripts). The MCP transport at /mcp/memory/mcp stays
# available for proper MCP clients; these routes are a parallel path
# for the much-larger HTTP-only audience.
#
# Why: #302 surfaced that the bootstrap + CLI + dashboard were all
# POSTing to /mcp/memory as if it were one-shot JSON-RPC. Real FastMCP
# transport needs initialize + session-tagged subsequent calls — that's
# work for a future MCP-SDK-client refactor. Until then, REST shims are
# the cheapest unblock so identity cards actually get written.


@router.post("/add")
async def memory_add(request: Request) -> dict[str, Any]:
    """Add a memory item. Body: ``{text, dataset?, tags?, metadata?, document_id?}``.

    Returns ``{id, timestamp}`` plus ``operation_id`` when the engine
    ingests asynchronously (Hindsight retain). Reuse ``document_id``
    across calls to upsert one logical document.

    Identity headers (issue #317):

      - ``X-hal0-Agent``: post-ADR-0012 agent identity. Stamped onto
        the wrapper's ``source`` field — server-injected so callers
        cannot lie (ADR-0005 §5). Absent header → ``"anonymous"``.
      - ``X-hal0-Private: 1``: opt into the private namespace.
        Promotes ``dataset`` to ``private:<agent>`` regardless of the
        body value (ADR-0005 §3).

    The body's ``source`` field is REJECTED — clients supplying it is
    treated as an attempt to impersonate, matching the MCP rule. Use
    the ``X-hal0-Agent`` header to claim identity.

    Returns ``{id, timestamp}`` from :meth:`CogneeWrapper.add`.
    """
    body = await _read_json_body(request)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        raise Hal0Error(
            "memory_add requires 'text' (non-empty string)",
            details={"path": "/api/memory/add"},
        )
    if "source" in body:
        # ADR-0005 §5 — source is server-injected from the X-hal0-Agent
        # header so callers cannot impersonate another agent in the
        # audit log.
        raise Hal0Error(
            "memory_add 'source' is server-injected from X-hal0-Agent and cannot be supplied",
            details={"path": "/api/memory/add"},
        )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_write_dataset(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    document_id = body.get("document_id")
    if document_id is not None and (
        not isinstance(document_id, str) or not _AGENT_ID_PATTERN.match(document_id)
    ):
        raise BadRequest(
            "memory_add 'document_id' must match the identity grammar (alnum/-/_ ≤64 chars)",
            details={"path": "/api/memory/add"},
        )

    wrapper = _wrapper(request)
    return await wrapper.add(
        text=text,
        dataset=dataset,
        tags=body.get("tags") or [],
        source=agent_id,
        metadata=body.get("metadata") or {},
        client_id=agent_id if agent_id != "anonymous" else None,
        document_id=document_id,
    )


@router.post("/search")
async def memory_search(request: Request) -> dict[str, Any]:
    """Search memory. Body: ``{query, limit?, dataset?, tags?, before?, after?}``.

    Identity headers behave like ``/add`` — ``X-hal0-Private: 1``
    expands a default-empty ``dataset`` to ``[shared, private:<agent>]``
    per ADR-0005 §3 so a private-mode caller sees both their own scoped
    items + the shared bucket without per-call opt-in.

    Returns ``{items: [MemoryRecord, ...]}`` — wrapped in an envelope so
    we can add ``next_cursor`` / counters later without breaking clients.
    """
    body = await _read_json_body(request)
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise Hal0Error(
            "memory_search requires 'query' (non-empty string)",
            details={"path": "/api/memory/search"},
        )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_read_datasets(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    items = await wrapper.search(
        query=query,
        limit=int(body.get("limit", 10)),
        dataset=dataset,
        tags=body.get("tags") or [],
        before=body.get("before"),
        after=body.get("after"),
        client_id=agent_id if agent_id != "anonymous" else None,
    )
    return {"items": items}


@router.post("/recall")
async def memory_recall(request: Request) -> dict[str, Any]:
    """Token-budgeted recall (Hindsight's preferred path).

    Body: ``{query, max_tokens?, types?, dataset?, tags?}``. Identity +
    namespace resolution behave like ``/search`` (X-hal0-Agent +
    X-hal0-Private). Returns ``{items: [MemoryItem, ...]}`` ordered by
    relevance (no numeric score — Hindsight recall returns none).

    Falls back to ``search`` semantics on engines without a richer recall
    (the ABC default), so this route is safe regardless of active engine.

    Contract note (#1026): this is the NAMESPACE recall — ACL-scoped,
    cross-bank fan-out, envelope ``{items}``. It is distinct from the bank
    console recall ``POST /api/memory/banks/{bank}/recall`` (single-bank
    Hindsight passthrough, envelope ``{results}``). Same verb, different scope.
    """
    body = await _read_json_body(request)
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise BadRequest(
            "memory_recall requires 'query' (non-empty string)",
            details={"path": "/api/memory/recall"},
        )
    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_read_datasets(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    items = await wrapper.recall(
        query=query,
        types=body.get("types"),
        max_tokens=int(body.get("max_tokens", 4096)),
        dataset=dataset,
        tags=body.get("tags") or [],
        client_id=agent_id if agent_id != "anonymous" else None,
    )
    return {"items": items}


@router.get("/list")
async def memory_list(
    request: Request,
    dataset: str | None = None,
    bank: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Paginated list. Returns ``{items: [...], next_cursor: str | null}``.

    Identity rules mirror ``/search``: ``X-hal0-Private: 1`` with no
    explicit ``?dataset=`` resolves to the caller's own private bucket
    so the ``hal0 agent memory list`` CLI subcommand can enumerate
    per-agent items without the operator passing the namespace by hand.

    ``?bank=`` is a convenience alias for the dashboard's Hindsight bank
    browser: a bank id (``private__hermes``) is translated to the matching
    dataset namespace (``private:hermes``) when no explicit ``?dataset=`` is
    given. If both are supplied and conflict, the explicit ``dataset`` wins.
    """
    if bank is not None:
        bank_dataset = bank_to_namespace(bank)
        if dataset is None:
            dataset = bank_dataset
        elif dataset != bank_dataset:
            log.info(
                "memory_list: ?dataset=%r overrides conflicting ?bank=%r (->%r)",
                dataset,
                bank,
                bank_dataset,
            )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        resolved = resolve_write_dataset(
            dataset,
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    return await wrapper.list_items(
        dataset=resolved,
        cursor=cursor,
        limit=limit,
        client_id=agent_id if agent_id != "anonymous" else None,
    )


@router.post("/delete")
async def memory_delete(request: Request) -> dict[str, int]:
    """Delete by id. Body: ``{ids: [...], dataset?}``. Returns ``{deleted: int}``.

    ``dataset`` optionally directs the engine's bank sweep (e.g.
    ``project:<id>`` items live outside the default shared + own-private
    sweep). Identity headers otherwise are not consulted: id-scoped
    delete bypasses the namespace surface entirely (the wrapper's audit
    log still stamps the call with the agent identity for forensics —
    see :meth:`CogneeWrapper._audit`).
    """
    body = await _read_json_body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise Hal0Error(
            "memory_delete requires 'ids' (non-empty list)",
            details={"path": "/api/memory/delete"},
        )
    agent_id = _agent_id(request)
    private = _is_private(request)
    requested = body.get("dataset")
    dataset: str | list[str] | None
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        dataset = None
    elif isinstance(requested, list):
        dataset = [str(d) for d in requested]
    else:
        try:
            dataset = resolve_write_dataset(
                str(requested),
                private=private,
                client_id=agent_id if agent_id != "anonymous" else None,
            )
        except MemoryNamespaceError as exc:
            raise MemoryNamespaceInvalid(str(exc)) from exc
    wrapper = _wrapper(request)
    # #1024 hardening: id-scoped delete is destructive — record it (actor +
    # ids + outcome) so bulk removals are attributable after the fact.
    async with record_action(
        request,
        category="memory",
        action="memory.items.delete",
        target=",".join(str(i) for i in ids)[:200],
    ):
        return await wrapper.delete(
            ids=ids,
            client_id=agent_id if agent_id != "anonymous" else None,
            dataset=dataset,
        )


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Tolerant JSON body parser (mirrors v1.py:_read_json_body)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")
    return body


# ── Helper exports for tests ────────────────────────────────────────────────


__all__ = [
    "DEFAULT_DATASET",
    "MemoryAgentIdInvalid",
    "MemoryGraphConfig",
    "MemoryGraphConfigInvalid",
    "MemoryGraphSlotInvalid",
    "MemoryNamespaceInvalid",
    "MemoryUnavailable",
    "router",
]
