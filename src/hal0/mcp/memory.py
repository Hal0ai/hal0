"""hal0 memory MCP server — Hindsight-backed long-term memory tools.

By design, memory is a first-class MCP surface so bundled and
external agents share one persistence layer for "what the user
remembers about themselves and their hal0". The active engine is
resolved through :class:`hal0.memory.MemoryProvider` (Hindsight, with a
PgVector boot-degrade fallback); this module exposes the four MCP tools
and the schema validation that bridges agent calls to the provider.

Tool catalog
------------

::

    memory_add     — write one item; returns {id, timestamp}
    memory_search  — vector + tag + time-window query; returns {results}
    memory_list    — paginated walk; returns {items, next_cursor}
    memory_delete  — remove by id(s); returns {deleted}

By design the v0.2 schema is rich from day 1 so we don't pay a
schema-versioning tax in Phase 9::

    memory_add(text, dataset="shared", tags=[], metadata={},
               document_id=null)
        → {id: str, timestamp: iso8601, operation_id?: str}
        # `source` is auto-extracted server-side from the caller's
        # client_id (Bearer-derived). Clients CANNOT pass `source`
        # themselves — that keeps the audit trail forensically
        # grounded. `document_id` reuse upserts one
        # logical document; `operation_id` surfaces async ingestion.

    memory_search(query, limit=10, dataset="shared"|list, tags=[],
                  before=null, after=null)
        → {results: [{id, text, score, timestamp, dataset, tags,
                      source, metadata}, ...]}

    memory_list(dataset="shared", cursor=null, limit=50)
        → {items: [...], next_cursor: str | null}

    memory_delete(ids: list[str], dataset=null)
        → {deleted: int}
        # Deleting >1 id is a BULK delete: it enqueues for operator
        # approval and returns {status: "pending_approval", approval_id}
        # instead of running. Same rule on /mcp/admin and /mcp/memory —
        # classification is owned by hal0.mcp.admin.is_gated (#1302).

Namespace rule: writes default to dataset ``shared``;
clients opting into private mode at the transport layer promote to
``private:<client_id>``. We resolve the effective dataset in
:func:`_resolve_dataset` so callers don't have to know the rule.

Lazy engine import
------------------

The active memory engine (Hindsight, with a PgVector boot-degrade fallback —
ADR-0023) is resolved through :class:`hal0.memory.MemoryProvider`. We import /
construct it lazily inside the dispatch helper so this module stays importable
for unit tests that mock the provider.

Transport
---------

The Streamable-HTTP MCP server pattern matches :mod:`hal0.mcp.admin` —
``build_server()`` returns a FastMCP instance that the orchestrator
mounts at ``/mcp/memory`` via ``app.mount()``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from hal0.memory.namespace import (
    MemoryNamespaceError,
    resolve_read_datasets,
    resolve_write_dataset,
)

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[import-not-found]
    from mcp.types import ToolAnnotations  # type: ignore[import-not-found]
except ImportError as _import_exc:  # pragma: no cover — exercised at install time
    raise ImportError(
        "hal0.mcp.memory requires the 'mcp' Python SDK. "
        "Install via 'pip install mcp' or the Memory-engine wave's pyproject extras."
    ) from _import_exc

audit_log = structlog.get_logger("hal0.mcp.audit")
log = structlog.get_logger(__name__)


# ── Schema validation helpers ────────────────────────────────────────────────
#
# We validate by hand (no pydantic dependency here) so the MCP server's
# error envelope stays consistent with admin.py and tests can read the
# rules off these helpers without spinning up a model.


class MemorySchemaError(ValueError):
    """Raised when a memory tool call's args don't match the tool schema."""


# document_id becomes a URL path segment on the engine's documents API —
# same bounded grammar as agent ids keeps it traversal-free.
_AGENT_ID_LIKE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _require(args: dict[str, Any], key: str, type_: type) -> Any:
    if key not in args:
        raise MemorySchemaError(f"missing required arg {key!r}")
    value = args[key]
    if not isinstance(value, type_):
        raise MemorySchemaError(f"arg {key!r} must be {type_.__name__}, got {type(value).__name__}")
    return value


def _optional(args: dict[str, Any], key: str, type_: type) -> Any:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, type_):
        raise MemorySchemaError(
            f"arg {key!r} must be {type_.__name__} or null, got {type(value).__name__}"
        )
    return value


def _optional_dict(args: dict[str, Any], key: str) -> dict[str, Any] | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, dict):
        raise MemorySchemaError(f"arg {key!r} must be dict or null, got {type(value).__name__}")
    return value


def _optional_list(args: dict[str, Any], key: str) -> list[Any] | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, list):
        raise MemorySchemaError(f"arg {key!r} must be list or null, got {type(value).__name__}")
    return value


def _optional_bool(args: dict[str, Any], key: str, *, default: bool | None = None) -> bool | None:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, bool):
        raise MemorySchemaError(f"arg {key!r} must be bool or null, got {type(value).__name__}")
    return value


def _optional_int(args: dict[str, Any], key: str) -> int | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise MemorySchemaError(f"arg {key!r} must be int or null, got {type(value).__name__}")
    return value


def _optional_entities(args: dict[str, Any], key: str = "entities") -> list[dict[str, Any]] | None:
    """``entities``: ``[{"text": ..., "type": ...}, ...]`` — light shape check
    (every item is a dict carrying a non-empty ``text``); the engine owns the
    rest of EntityInput validation."""
    value = _optional_list(args, key)
    if value is None:
        return None
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"]:
            raise MemorySchemaError(f"arg {key!r} items must be {{'text': str, ...}}")
    return value


def _optional_id(args: dict[str, Any], key: str, *, required: bool = True) -> str | None:
    """A per-fact/directive/mental-model/operation id — same bounded
    identity grammar as ``document_id`` since it lands in an engine URL
    path segment."""
    value = args.get(key)
    if value is None:
        if required:
            raise MemorySchemaError(f"missing required arg {key!r}")
        return None
    if not isinstance(value, str) or not _AGENT_ID_LIKE.match(value):
        raise MemorySchemaError(
            f"arg {key!r} must match the identity grammar (alnum/-/_ ≤64 chars)"
        )
    return value


def _normalise_tags(value: Any) -> list[str]:
    """Tags may arrive as None, list, or stringified CSV (some MCP clients
    don't speak JSON-array literals well). Normalise to list[str] (possibly
    empty) matching the schema's default of ``[]``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        # CSV / comma-separated input.
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t) for t in value]
    raise MemorySchemaError(f"tags must be list[str] or comma-string, got {type(value).__name__}")


# ── Namespace resolution ─────────────────────────────────────────────────────
#
# The actual rule lives in :mod:`hal0.memory.namespace` so the REST shims
# in ``hal0.api.routes.memory`` apply the same logic (issue #317). This
# wrapper preserves the MCP-side error type so dispatcher-level catches
# don't have to learn a second exception class.


def _resolve_dataset(
    requested: str | None,
    *,
    private: bool,
    client_id: str | None,
) -> str:
    """Thin shim around :func:`hal0.memory.namespace.resolve_write_dataset`
    that re-raises ``MemoryNamespaceError`` as ``MemorySchemaError`` for
    compatibility with existing MCP dispatcher error envelopes."""
    try:
        return resolve_write_dataset(requested, private=private, client_id=client_id)
    except MemoryNamespaceError as exc:
        raise MemorySchemaError(str(exc)) from exc


def _resolve_read_dataset(
    requested: str | list[str] | None,
    *,
    private: bool,
    client_id: str | None,
) -> str | list[str]:
    """Thin shim around :func:`hal0.memory.namespace.resolve_read_datasets`
    that re-raises ``MemoryNamespaceError`` as ``MemorySchemaError``.

    This is the read-side counterpart to :func:`_resolve_dataset` above.
    Diagnosis #8: the three list-accepting handlers below (``search``,
    ``delete``, ``recall``) used to build the effective ``dataset`` filter
    by hand (``[str(d) for d in requested]``) with zero membership check
    against the spec §3 closed namespace table — a foreign/unknown
    namespace string in a list arg passed straight through to the engine.
    ``resolve_read_datasets`` filters the list against the closed table
    instead of trusting the caller — degrading on a partial drop, and
    (#1451) raising when *every* entry is unaddressable rather than
    returning ``[]`` for the providers to re-expand into the default
    shared sweep. ``api/routes/memory.py`` calls the same resolver on
    every read AND on delete, so the two surfaces cannot drift.
    """
    try:
        return resolve_read_datasets(requested, private=private, client_id=client_id)
    except MemoryNamespaceError as exc:
        raise MemorySchemaError(str(exc)) from exc


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _envelope_safe(result: Any, *, list_key: str = "result") -> dict[str, Any]:
    """Coerce a provider/engine payload into the tool's return dict, renaming
    a top-level ``status`` key to ``operation_status`` first.

    ``make_dispatcher``'s outer envelope is ``{"status": "ok", **payload}`` —
    a handler payload that itself carries a ``status`` key (Hindsight's
    ``OperationStatusResponse``/``AsyncOperationSubmitResponse`` both do:
    pending/processing/completed/failed/...) would silently overwrite "ok"
    with the engine's own status, which every existing caller reads as
    "the MCP call failed". Renaming avoids the collision without losing the
    information — it is still returned, just not under the contended key.
    """
    if not isinstance(result, dict):
        return {list_key: result}
    out = dict(result)
    if "status" in out:
        out["operation_status"] = out.pop("status")
    return out


# ── Tool implementations ─────────────────────────────────────────────────────
#
# Each helper returns the JSON payload an MCP client should see. They
# share one MemoryProvider instance held by the caller — we pass it in
# rather than importing globally so tests can substitute a mock.


async def _memory_add(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_add(text, dataset?, tags?, metadata?, document_id?, entities?,
    observation_scopes?, strategy?, update_mode?, sync?)
    → {id, timestamp, operation_id?, operation_ids?, items_count?}.

    Schema:
      - ``text``: required, non-empty str.
      - ``dataset``: defaults to ``"shared"``; ``--private`` promotes
        to ``private:<client_id>``.
      - ``tags``: defaults to ``[]``.
      - ``metadata``: defaults to ``{}``.
      - ``document_id``: optional engine grouping key — reuse one id
        across adds to upsert the same logical document (conversation
        evolution). Same identity grammar as agent ids.
      - ``entities``: optional ``[{"text": ..., "type": ...}, ...]`` to
        combine with auto-extracted entities.
      - ``observation_scopes``: how to scope observations during
        consolidation (``"per_tag"|"combined"|"all_combinations"|"shared"``
        or a list of tag-lists) — engine-native, forwarded as-is.
      - ``strategy``: named retain strategy overriding the bank default.
      - ``update_mode``: ``"replace"`` (default) or ``"append"`` when
        ``document_id`` names an existing document.
      - ``sync``: ``true`` waits for extraction before returning (no
        ``operation_id``, but ``items_count`` reflects the finished write);
        ``false`` (default) is the existing fire-and-forget path.
      - ``source``: NOT accepted from the caller. Server-injected from
        ``client_id`` so callers cannot lie about their identity,
        keeping the audit trail grounded.

    ``operation_id``/``operation_ids``/``items_count`` appear whenever the
    engine reports them (Hindsight retain is async by default) — poll
    ``memory_operation_get``/``memory_operation_list`` to track completion.
    """
    text = _require(args, "text", str)
    if not text.strip():
        raise MemorySchemaError("text must be non-empty")
    requested_ds = args.get("dataset")
    if requested_ds is not None and not isinstance(requested_ds, str):
        raise MemorySchemaError("dataset must be str when provided")
    dataset = _resolve_dataset(requested_ds, private=private, client_id=client_id)
    tags = _normalise_tags(args.get("tags"))
    metadata_raw = args.get("metadata", {})
    if not isinstance(metadata_raw, dict):
        raise MemorySchemaError("metadata must be dict when provided")
    if "source" in args:
        raise MemorySchemaError(
            "source is server-injected from client_id and cannot be supplied by callers"
        )
    document_id = _optional(args, "document_id", str)
    if document_id is not None and not _AGENT_ID_LIKE.match(document_id):
        raise MemorySchemaError("document_id must match the identity grammar (alnum/-/_ ≤64 chars)")
    entities = _optional_entities(args)
    observation_scopes = args.get("observation_scopes")
    strategy = _optional(args, "strategy", str)
    update_mode = _optional(args, "update_mode", str)
    if update_mode is not None and update_mode not in ("replace", "append"):
        raise MemorySchemaError("update_mode must be 'replace' or 'append'")
    sync = _optional_bool(args, "sync", default=False)
    source = client_id or "anonymous"
    result = await wrapper.add(
        text=text,
        dataset=dataset,
        tags=tags,
        source=source,
        metadata=metadata_raw,
        client_id=client_id,
        document_id=document_id,
        entities=entities,
        observation_scopes=observation_scopes,
        strategy=strategy,
        update_mode=update_mode,
        sync=bool(sync),
    )
    out = {
        "id": result["id"],
        "timestamp": result.get("timestamp") or _iso_now(),
    }
    if result.get("operation_id"):
        out["operation_id"] = result["operation_id"]
    if result.get("operation_ids"):
        out["operation_ids"] = result["operation_ids"]
    if isinstance(result.get("items_count"), int):
        out["items_count"] = result["items_count"]
    return out


async def _memory_search(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_search(query, limit=10, dataset="shared"|list, tags=[],
                     before=null, after=null, tag_groups=null, min_scores=null)
    → {results}.

    MemoryProvider contract::

        await provider.search(query, limit, dataset, tags, before, after,
                               tag_groups=..., min_scores=...) -> list[ItemDict]

    ``dataset`` MAY be a list — a private-mode client sees both
    ``shared`` + their own ``private:<client_id>``. ``tag_groups``/
    ``min_scores`` are Hindsight-native recall filters (boolean tag
    expressions / per-stage score floors) forwarded verbatim; engines
    without them ignore both.
    """
    query = _require(args, "query", str)
    if not query.strip():
        raise MemorySchemaError("query must be non-empty")
    limit_raw = args.get("limit", 10)
    if not isinstance(limit_raw, int) or limit_raw < 1 or limit_raw > 200:
        raise MemorySchemaError("limit must be 1..200")
    requested = args.get("dataset")
    if requested is not None and not isinstance(requested, (str, list)):
        raise MemorySchemaError("dataset must be str | list[str] | null")
    # Namespace-validated via resolve_read_datasets (spec §3 closed set) —
    # private-mode empty/None reads expand to [shared, private:<client_id>].
    dataset = _resolve_read_dataset(requested, private=private, client_id=client_id)
    tags = _normalise_tags(args.get("tags"))
    before = _optional(args, "before", str)
    after = _optional(args, "after", str)
    tag_groups = _optional_list(args, "tag_groups")
    min_scores = _optional_dict(args, "min_scores")
    results = await wrapper.search(
        query=query,
        limit=limit_raw,
        dataset=dataset,
        tags=tags,
        before=before,
        after=after,
        tag_groups=tag_groups,
        min_scores=min_scores,
        client_id=client_id,
    )
    return {"results": list(results)}


async def _memory_list(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_list(dataset="shared", cursor=null, limit=50) → {items, next_cursor}."""
    requested = args.get("dataset")
    if requested is not None and not isinstance(requested, str):
        raise MemorySchemaError("dataset must be str when provided")
    dataset = _resolve_dataset(requested, private=private, client_id=client_id)
    cursor = _optional(args, "cursor", str)
    limit_raw = args.get("limit", 50)
    if not isinstance(limit_raw, int) or limit_raw < 1 or limit_raw > 200:
        raise MemorySchemaError("limit must be 1..200")
    page = await wrapper.list_items(
        dataset=dataset, cursor=cursor, limit=limit_raw, client_id=client_id
    )
    return {
        "items": list(page.get("items", [])),
        "next_cursor": page.get("next_cursor"),
    }


async def _memory_delete(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_delete(ids, dataset?) → {deleted: int}.

    Returns the count of deleted rows. ``ids`` must be
    non-empty. ``dataset`` optionally directs the engine's bank sweep
    (e.g. ``project:<id>`` items live outside the default
    shared + own-private sweep).

    Approval-gating for bulk deletes (>1 id) is classified by
    :func:`hal0.mcp.admin.is_gated` and enforced one layer up — by
    ``admin.dispatch`` when the call arrives on ``/mcp/admin``, and by
    this module's dispatcher (when built with ``approval_queue=``) when
    it arrives on the standalone ``/mcp/memory`` mount (#1302). By the
    time execution reaches here the call is either approved or a
    single-id autonomous delete.
    """
    ids_raw = args.get("ids")
    if not isinstance(ids_raw, list) or not ids_raw:
        raise MemorySchemaError("ids must be a non-empty list[str]")
    ids = [str(i) for i in ids_raw]
    requested = args.get("dataset")
    if requested is not None and not isinstance(requested, (str, list)):
        raise MemorySchemaError("dataset must be str | list[str] | null")
    # Namespace-validated via resolve_read_datasets (spec §3 closed set) —
    # an unset dataset resolves to "shared", which the providers already
    # expand to include the caller's own private bank during the sweep.
    dataset = _resolve_read_dataset(requested, private=private, client_id=client_id)
    result = await wrapper.delete(ids=ids, client_id=client_id, dataset=dataset)
    deleted_raw = result.get("deleted", len(ids))
    # Accept either a count or the list of deleted ids from the wrapper
    # so we're forgiving of either contract while still returning the
    # expected count shape.
    if isinstance(deleted_raw, list):
        deleted_count = len(deleted_raw)
    elif isinstance(deleted_raw, int):
        deleted_count = deleted_raw
    else:
        deleted_count = len(ids)
    return {"deleted": deleted_count}


async def _memory_recall(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_recall(query, max_tokens=4096, types?, dataset?, tags?,
    tags_match?, tag_groups?, budget?, prefer_observations?, include?,
    query_timestamp?, min_scores?) → {results, entities?, chunks?,
    source_facts?}.

    The Hindsight-preferred retrieval path: token-budgeted, observation
    hierarchy, native ranking score when the engine supplies one.
    Provider.recall falls back to search on engines without a richer
    recall (ABC default), so this tool is safe regardless of active engine.

    ``entities``/``chunks``/``source_facts`` — response-level enrichment
    (entity mental-model snapshots, raw chunk text, observation provenance)
    — appear only when the engine returned them (``include`` controls what
    Hindsight computes; entities are included by default).
    """
    query = _require(args, "query", str)
    if not query.strip():
        raise MemorySchemaError("query must be non-empty")
    max_tokens = args.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32768:
        raise MemorySchemaError("max_tokens must be 1..32768")
    requested = args.get("dataset")
    if requested is not None and not isinstance(requested, (str, list)):
        raise MemorySchemaError("dataset must be str | list[str] | null")
    # Namespace-validated via resolve_read_datasets (spec §3 closed set) —
    # private-mode empty/None reads expand to [shared, private:<client_id>].
    dataset = _resolve_read_dataset(requested, private=private, client_id=client_id)
    tags = _normalise_tags(args.get("tags"))
    types = args.get("types")
    tags_match = _optional(args, "tags_match", str)
    tag_groups = _optional_list(args, "tag_groups")
    budget = _optional(args, "budget", str)
    if budget is not None and budget not in ("low", "mid", "high"):
        raise MemorySchemaError("budget must be 'low' | 'mid' | 'high'")
    prefer_observations = _optional_bool(args, "prefer_observations", default=False)
    include = _optional_dict(args, "include")
    query_timestamp = _optional(args, "query_timestamp", str)
    min_scores = _optional_dict(args, "min_scores")
    results = await wrapper.recall(
        query=query,
        types=types,
        max_tokens=max_tokens,
        dataset=dataset,
        tags=tags,
        tags_match=tags_match,
        tag_groups=tag_groups,
        budget=budget or "mid",
        prefer_observations=bool(prefer_observations),
        include=include,
        query_timestamp=query_timestamp,
        min_scores=min_scores,
        client_id=client_id,
    )
    out: dict[str, Any] = {"results": list(results)}
    entities = getattr(results, "entities", None)
    if entities:
        out["entities"] = entities
    chunks = getattr(results, "chunks", None)
    if chunks:
        out["chunks"] = chunks
    source_facts = getattr(results, "source_facts", None)
    if source_facts:
        out["source_facts"] = source_facts
    return out


# ── Reflect (LLM-backed synthesis) ───────────────────────────────────────────


async def _memory_reflect(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_reflect(query, dataset?, budget?, max_tokens?, fact_types?,
    tags?, tags_match?, tag_groups?, exclude_mental_models?,
    exclude_mental_model_ids?, include?, response_schema?)
    → {text, based_on?, structured_output?, usage?, trace?}.

    LLM-backed synthesis over the bank's memory. Reflect operates on exactly
    one bank (no cross-bank merge — there is no server-side cross-bank
    reflect and merging LLM narratives wouldn't mean anything), resolved via
    the same single-namespace ACL ``memory_add`` uses for its write target.

    Engines without a reflect capability (the PgVector degrade fallback)
    answer ``{status: "unsupported"}`` rather than erroring — the ABC default
    is a safe no-op, so this tool is always callable regardless of engine.
    """
    query = _require(args, "query", str)
    if not query.strip():
        raise MemorySchemaError("query must be non-empty")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    budget = _optional(args, "budget", str)
    if budget is not None and budget not in ("low", "mid", "high"):
        raise MemorySchemaError("budget must be 'low' | 'mid' | 'high'")
    max_tokens = args.get("max_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 32768:
        raise MemorySchemaError("max_tokens must be 1..32768")
    fact_types = _optional_list(args, "fact_types")
    tags = _normalise_tags(args.get("tags"))
    tags_match = _optional(args, "tags_match", str)
    tag_groups = _optional_list(args, "tag_groups")
    exclude_mental_models = _optional_bool(args, "exclude_mental_models", default=False)
    exclude_mental_model_ids = _optional_list(args, "exclude_mental_model_ids")
    include = _optional_dict(args, "include")
    response_schema = _optional_dict(args, "response_schema")
    result = await wrapper.reflect(
        query=query,
        dataset=dataset,
        client_id=client_id,
        budget=budget or "low",
        max_tokens=max_tokens,
        fact_types=fact_types,
        tags=tags or None,
        tags_match=tags_match,
        tag_groups=tag_groups,
        exclude_mental_models=bool(exclude_mental_models),
        exclude_mental_model_ids=exclude_mental_model_ids,
        include=include,
        response_schema=response_schema,
    )
    return dict(result) if isinstance(result, dict) else {"text": str(result)}


# ── Single-memory curation (non-destructive "this is wrong" path) ───────────


async def _memory_curate(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_curate(id, dataset?, text?, context?, occurred_start?,
    occurred_end?, fact_type?, entities?, state?, reason?) → the updated
    memory unit.

    Edits a single memory unit's text/context/occurred-range/fact_type/
    entities, or soft-invalidates/reverts it via ``state``
    (``"invalidated"``|``"valid"`` — reversible either way). This is the
    non-destructive correction path, distinct from ``memory_delete``.

    ``id`` is the PER-FACT id (``RecallResult.id`` / ``metadata.fact_id`` on
    a search/recall/list item) — NOT the ``document_id`` ``memory_add``/
    ``memory_delete`` address. ``entities`` here replaces the fact's entity
    list by NAME (``list[str]``) — different shape from ``memory_add``'s
    ``entities`` (which combines ``{"text":...}`` objects with extraction).
    """
    memory_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    text = _optional(args, "text", str)
    context = _optional(args, "context", str)
    occurred_start = _optional(args, "occurred_start", str)
    occurred_end = _optional(args, "occurred_end", str)
    fact_type = _optional(args, "fact_type", str)
    if fact_type is not None and fact_type not in ("world", "experience"):
        raise MemorySchemaError("fact_type must be 'world' | 'experience'")
    entities = _optional_list(args, "entities")
    if entities is not None and not all(isinstance(e, str) for e in entities):
        raise MemorySchemaError("arg 'entities' items must be str (entity names) for memory_curate")
    state = _optional(args, "state", str)
    if state is not None and state not in ("invalidated", "valid"):
        raise MemorySchemaError("state must be 'invalidated' | 'valid'")
    reason = _optional(args, "reason", str)
    if all(
        v is None for v in (text, context, occurred_start, occurred_end, fact_type, entities, state)
    ):
        raise MemorySchemaError("memory_curate requires at least one field to change")
    result = await wrapper.curate(
        memory_id,
        dataset=dataset,
        client_id=client_id,
        text=text,
        context=context,
        occurred_start=occurred_start,
        occurred_end=occurred_end,
        fact_type=fact_type,
        entities=entities,
        state=state,
        reason=reason,
    )
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_history(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_history(id, dataset?) → the memory unit's curation history."""
    memory_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.memory_history(memory_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"history": result}


# ── Mental models ─────────────────────────────────────────────────────────


async def _memory_mental_model_list(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_list(dataset?, tags?, tags_match?, detail?,
    limit?, offset?) → {items: [...]}."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    tags = _normalise_tags(args.get("tags"))
    tags_match = _optional(args, "tags_match", str)
    detail = _optional(args, "detail", str)
    limit = _optional_int(args, "limit")
    offset = _optional_int(args, "offset")
    result = await wrapper.list_mental_models(
        dataset=dataset,
        client_id=client_id,
        tags=tags or None,
        tags_match=tags_match,
        detail=detail,
        limit=limit,
        offset=offset,
    )
    return dict(result) if isinstance(result, dict) else {"items": result}


async def _memory_mental_model_get(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_get(id, dataset?) → one mental model."""
    mm_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.get_mental_model(mm_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_mental_model_create(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_create(name, source_query, dataset?, id?, tags?,
    max_tokens?, trigger?) → {mental_model_id, operation_id} (the initial
    content generation runs as an async refresh)."""
    name = _require(args, "name", str)
    source_query = _require(args, "source_query", str)
    if not name.strip() or not source_query.strip():
        raise MemorySchemaError("name and source_query must be non-empty")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    custom_id = _optional_id(args, "id", required=False)
    tags = _normalise_tags(args.get("tags"))
    max_tokens = args.get("max_tokens", 2048)
    if not isinstance(max_tokens, int) or max_tokens < 256 or max_tokens > 8192:
        raise MemorySchemaError("max_tokens must be 256..8192")
    trigger = _optional_dict(args, "trigger")
    result = await wrapper.create_mental_model(
        name=name,
        source_query=source_query,
        dataset=dataset,
        client_id=client_id,
        id=custom_id,
        tags=tags,
        max_tokens=max_tokens,
        trigger=trigger,
    )
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_mental_model_update(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_update(id, dataset?, name?, source_query?,
    max_tokens?, tags?, trigger?) → the updated mental model."""
    mm_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    name = _optional(args, "name", str)
    source_query = _optional(args, "source_query", str)
    max_tokens = _optional_int(args, "max_tokens")
    if max_tokens is not None and not (256 <= max_tokens <= 8192):
        raise MemorySchemaError("max_tokens must be 256..8192")
    tags_raw = args.get("tags")
    tags = _normalise_tags(tags_raw) if tags_raw is not None else None
    trigger = _optional_dict(args, "trigger")
    result = await wrapper.update_mental_model(
        mm_id,
        dataset=dataset,
        client_id=client_id,
        name=name,
        source_query=source_query,
        max_tokens=max_tokens,
        tags=tags,
        trigger=trigger,
    )
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_mental_model_delete(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_delete(id, dataset?) → destructive; gated
    (see ``_LOCAL_GATED_TOOLS``)."""
    mm_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.delete_mental_model(mm_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_mental_model_refresh(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_mental_model_refresh(id, dataset?) → {operation_id,
    operation_status} (async — poll with memory_operation_get). The engine's
    own ``status`` field is renamed to ``operation_status`` — see
    :func:`_envelope_safe`."""
    mm_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.refresh_mental_model(mm_id, dataset=dataset, client_id=client_id)
    return _envelope_safe(result)


# ── Directives ───────────────────────────────────────────────────────────


async def _memory_directive_list(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_directive_list(dataset?, tags?, tags_match?, active_only?,
    limit?, offset?) → {items: [...]}."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    tags = _normalise_tags(args.get("tags"))
    tags_match = _optional(args, "tags_match", str)
    active_only = _optional_bool(args, "active_only")
    limit = _optional_int(args, "limit")
    offset = _optional_int(args, "offset")
    result = await wrapper.list_directives(
        dataset=dataset,
        client_id=client_id,
        tags=tags or None,
        tags_match=tags_match,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return dict(result) if isinstance(result, dict) else {"items": result}


async def _memory_directive_get(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_directive_get(id, dataset?) → one directive."""
    d_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.get_directive(d_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_directive_create(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_directive_create(name, content, dataset?, priority?,
    is_active?, tags?) → the created directive."""
    name = _require(args, "name", str)
    content = _require(args, "content", str)
    if not name.strip() or not content.strip():
        raise MemorySchemaError("name and content must be non-empty")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    priority = args.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise MemorySchemaError("priority must be int")
    is_active = _optional_bool(args, "is_active", default=True)
    tags = _normalise_tags(args.get("tags"))
    result = await wrapper.create_directive(
        name=name,
        content=content,
        dataset=dataset,
        client_id=client_id,
        priority=priority,
        is_active=bool(is_active),
        tags=tags,
    )
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_directive_update(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_directive_update(id, dataset?, name?, content?, priority?,
    is_active?, tags?) → the updated directive."""
    d_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    name = _optional(args, "name", str)
    content = _optional(args, "content", str)
    priority = _optional_int(args, "priority")
    is_active = _optional_bool(args, "is_active")
    tags_raw = args.get("tags")
    tags = _normalise_tags(tags_raw) if tags_raw is not None else None
    result = await wrapper.update_directive(
        d_id,
        dataset=dataset,
        client_id=client_id,
        name=name,
        content=content,
        priority=priority,
        is_active=is_active,
        tags=tags,
    )
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_directive_delete(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_directive_delete(id, dataset?) → destructive; gated
    (see ``_LOCAL_GATED_TOOLS``)."""
    d_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.delete_directive(d_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


# ── Async operations (so memory_add's async retain is pollable) ────────────


async def _memory_operation_list(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_operation_list(dataset?, status?, type?, limit?, offset?,
    exclude_parents?) → {operations: [...]}."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    status = _optional(args, "status", str)
    if status is not None and status not in (
        "pending",
        "processing",
        "completed",
        "failed",
        "cancelled",
    ):
        raise MemorySchemaError(
            "status must be one of pending|processing|completed|failed|cancelled"
        )
    op_type = _optional(args, "type", str)
    limit = _optional_int(args, "limit")
    offset = _optional_int(args, "offset")
    exclude_parents = _optional_bool(args, "exclude_parents")
    result = await wrapper.list_operations(
        dataset=dataset,
        client_id=client_id,
        status=status,
        type=op_type,
        limit=limit,
        offset=offset,
        exclude_parents=exclude_parents,
    )
    return dict(result) if isinstance(result, dict) else {"operations": result}


async def _memory_operation_get(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_operation_get(id, dataset?, include_payload?) → one operation's
    status as ``operation_status`` (poll target for memory_add/
    mental_model_refresh/consolidate). The engine's own ``status`` field is
    renamed to ``operation_status`` — see :func:`_envelope_safe`; it would
    otherwise collide with the MCP envelope's own ``status: "ok"``."""
    op_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    include_payload = _optional_bool(args, "include_payload")
    result = await wrapper.get_operation(
        op_id, dataset=dataset, client_id=client_id, include_payload=include_payload
    )
    return _envelope_safe(result)


async def _memory_operation_cancel(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_operation_cancel(id, dataset?) → aborts in-flight work; does
    NOT delete any memory already retained, so it is not gated like
    memory_delete."""
    op_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.cancel_operation(op_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_operation_retry(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_operation_retry(id, dataset?) → re-queues a failed operation."""
    op_id = _optional_id(args, "id")
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.retry_operation(op_id, dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


# ── Tags / bank stats ────────────────────────────────────────────────────


async def _memory_tags_list(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_tags_list(dataset?, q?, source?, limit?, offset?) →
    {items: [{tag, count}, ...]}."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    q = _optional(args, "q", str)
    source = _optional(args, "source", str)
    limit = _optional_int(args, "limit")
    offset = _optional_int(args, "offset")
    result = await wrapper.list_tags(
        dataset=dataset, client_id=client_id, q=q, source=source, limit=limit, offset=offset
    )
    return dict(result) if isinstance(result, dict) else {"items": result}


async def _memory_bank_stats(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_bank_stats(dataset?, refresh?) → node/link/observation/
    operation counts for the resolved bank. Read-only — no destructive bank
    ops (delete/clear) are exposed via MCP."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    refresh = _optional_bool(args, "refresh")
    result = await wrapper.bank_stats(dataset=dataset, client_id=client_id, refresh=refresh)
    return dict(result) if isinstance(result, dict) else {"result": result}


async def _memory_bank_consolidate(
    wrapper: Any, args: dict[str, Any], *, client_id: str | None, private: bool
) -> dict[str, Any]:
    """memory_bank_consolidate(dataset?) → {operation_id, deduplicated}
    (async — poll with memory_operation_get). Triggers the engine's
    world/experience → observation consolidation pass early instead of
    waiting for its normal trigger; not destructive (source facts are kept,
    only new/merged observations are derived)."""
    dataset = _resolve_dataset(
        _optional(args, "dataset", str), private=private, client_id=client_id
    )
    result = await wrapper.consolidate(dataset=dataset, client_id=client_id)
    return dict(result) if isinstance(result, dict) else {"result": result}


_MEMORY_HANDLERS = {
    "memory_add": _memory_add,
    "memory_search": _memory_search,
    "memory_list": _memory_list,
    "memory_delete": _memory_delete,
    "memory_recall": _memory_recall,
    "memory_reflect": _memory_reflect,
    "memory_curate": _memory_curate,
    "memory_history": _memory_history,
    "memory_mental_model_list": _memory_mental_model_list,
    "memory_mental_model_get": _memory_mental_model_get,
    "memory_mental_model_create": _memory_mental_model_create,
    "memory_mental_model_update": _memory_mental_model_update,
    "memory_mental_model_delete": _memory_mental_model_delete,
    "memory_mental_model_refresh": _memory_mental_model_refresh,
    "memory_directive_list": _memory_directive_list,
    "memory_directive_get": _memory_directive_get,
    "memory_directive_create": _memory_directive_create,
    "memory_directive_update": _memory_directive_update,
    "memory_directive_delete": _memory_directive_delete,
    "memory_operation_list": _memory_operation_list,
    "memory_operation_get": _memory_operation_get,
    "memory_operation_cancel": _memory_operation_cancel,
    "memory_operation_retry": _memory_operation_retry,
    "memory_tags_list": _memory_tags_list,
    "memory_bank_stats": _memory_bank_stats,
    "memory_bank_consolidate": _memory_bank_consolidate,
}


# ── Approval gating (#1302) ──────────────────────────────────────────────────
#
# The classification itself is owned by :func:`hal0.mcp.admin.is_gated` —
# one owner per fact. We only decide *whether this layer enforces it*
# (see ``make_dispatcher``'s ``approval_queue`` argument). The import is
# function-local because ``hal0.mcp.admin`` is a heavy module and the
# standalone memory mount must stay importable on its own.

_PENDING_APPROVAL_DETAIL = (
    "bulk memory delete queued for operator approval — the call runs only "
    "after the operator approves it (top-bar bell / approvals inbox). "
    "Nothing waits on this transport, so the outcome is NOT returned here; "
    "re-check with memory_search or memory_list once approved."
)


#: Destructive new tools this module owns that ``hal0.mcp.admin``'s
#: GATED_TOOLS/is_gated doesn't yet classify (admin.py's catalog is a
#: hardcoded frozenset maintained on the admin side; wiring these tool
#: names into it — so they also gate when reached via the /mcp/admin
#: mount — is a follow-up there). Gating them here keeps the standalone
#: /mcp/memory mount safe regardless of when that catalog update lands:
#: a bank wipe of a mental model or directive is exactly the kind of call
#: the approval-gate pattern (memory_delete) exists for. Non-destructive
#: writes (create/update/refresh, memory_curate's reversible invalidate,
#: memory_operation_cancel) stay autonomous, same posture as memory_add.
_LOCAL_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "memory_mental_model_delete",
        "memory_directive_delete",
    }
)


def _needs_approval(tool: str, args: dict[str, Any], *, client_id: str | None = None) -> bool:
    """True when this memory call must gate through the approval queue.

    Delegates to ``admin.is_gated`` so ``/mcp/memory`` and ``/mcp/admin``
    can never drift on what counts as a bulk delete. ``client_id`` lets
    the namespace check (#8) recognise the caller's own ``private:<id>``
    dataset as autonomous instead of over-gating it. ``_LOCAL_GATED_TOOLS``
    (above) is checked first for tools admin.py's classification doesn't
    cover yet.
    """
    if tool in _LOCAL_GATED_TOOLS:
        return True
    from hal0.mcp.admin import is_gated

    return is_gated(tool, args, client_id=client_id)


def make_dispatcher(
    wrapper: Any,
    *,
    client_id_resolver: Any = None,
    private_resolver: Any = None,
    approval_queue: Any = None,
):
    """Return an async dispatcher closure bound to ``wrapper``.

    The admin server passes this into :func:`hal0.mcp.admin.dispatch`
    via ``memory_dispatcher=`` so memory tool calls bypass the HTTP
    round-trip and hit the memory engine directly in-process. Validation errors
    surface as the same error envelope shape the REST routes use.

    ``client_id_resolver`` is a 0-arg callable that returns the
    Bearer-derived caller id (used to stamp ``source`` on add + power
    the ``private:<client_id>`` namespace promotion). ``None`` is
    treated as "anonymous" — for tests that don't care about audit.

    ``private_resolver`` returns the per-call ``--private`` toggle
    state (the transport layer reads this off the agent's session).

    ``approval_queue`` arms this dispatcher's own destructive-call gate
    (#1302). Pass it when the dispatcher is the OUTERMOST layer — i.e.
    the standalone ``/mcp/memory`` mount, where no admin dispatcher sits
    in front to classify the call. Leave it ``None`` when the dispatcher
    is handed to ``admin.dispatch``: admin gates first and then invokes
    the approved executor through this same callable, so a second gate
    here would re-enqueue the approved call forever.
    """

    async def _dispatch(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = _MEMORY_HANDLERS.get(tool)
        if handler is None:
            return {
                "status": "error",
                "error": {"code": "mcp.unknown_memory_tool", "tool": tool},
            }
        client_id = None
        if client_id_resolver is not None:
            client_id = client_id_resolver()
        private = False
        if private_resolver is not None:
            private = bool(private_resolver())

        async def _run(call_args: dict[str, Any]) -> dict[str, Any]:
            payload = await handler(wrapper, call_args, client_id=client_id, private=private)
            return {"status": "ok", **payload}

        try:
            if approval_queue is not None and _needs_approval(tool, args, client_id=client_id):
                approval_id = await approval_queue.enqueue(
                    tool=tool,
                    args=args,
                    client_id=client_id or "anonymous",
                    executor=_run,
                )
                audit_log.info(
                    "mcp.memory.gated",
                    tool=tool,
                    client_id=client_id,
                    approval_id=approval_id,
                )
                return {
                    "status": "pending_approval",
                    "approval_id": approval_id,
                    "detail": _PENDING_APPROVAL_DETAIL,
                }
            return await _run(args)
        except MemorySchemaError as exc:
            return {
                "status": "error",
                "error": {"code": "mcp.memory_schema", "detail": str(exc)},
            }
        except Exception as exc:
            log.warning("mcp.memory.failed", tool=tool, error=str(exc))
            return {
                "status": "error",
                "error": {"code": "mcp.memory_failed", "detail": _scrub_detail(exc)},
            }

    return _dispatch


_URL_RE = re.compile(r"https?://\S+")


def _scrub_detail(exc: Exception) -> str:
    """Client-safe error detail: engine URLs (httpx repeats the full
    internal request URL in HTTPStatusError messages) are not the
    caller's business — redact them, keep the rest of the message."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return f"memory engine returned HTTP {status}"
    return _URL_RE.sub("<engine>", str(exc))


# ── Tool annotations (mcp-builder Phase 2.3) ─────────────────────────────────
#
# Matches the standalone-server view of memory tools. Hints stay
# consistent with :mod:`hal0.mcp.admin`'s table — the destructive bit
# on memory_delete is intrinsic to the operation; admin-layer approval
# gating for bulk deletes is a separate enforcement layer that doesn't
# change the annotation.

_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "memory_add": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "memory_search": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "memory_recall": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_reflect": ToolAnnotations(
        # Not idempotent: an LLM synthesis call, re-run may answer differently.
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
    "memory_curate": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_history": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_mental_model_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_mental_model_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_mental_model_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "memory_mental_model_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_mental_model_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "memory_mental_model_refresh": ToolAnnotations(
        # Not idempotent: regenerates content, which may change each run.
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
    "memory_directive_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_directive_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_directive_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "memory_directive_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_directive_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "memory_operation_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_operation_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_operation_cancel": ToolAnnotations(
        # Aborts in-flight work — mutates operation state, but deletes no
        # durable memory, so it does not carry the delete-style destructive bit.
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "memory_operation_retry": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "memory_tags_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_bank_stats": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_bank_consolidate": ToolAnnotations(
        # Mutates derived state (observations) but keeps every source fact —
        # not the delete-style destructive bit, and re-triggering is harmless
        # (the engine dedupes a pending consolidation task).
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
}


# ── Standalone server (used when the memory MCP is mounted on its own) ───────


def build_server(
    *,
    wrapper: Any,
    name: str = "hal0-memory",
    client_id_resolver: Any = None,
    private_resolver: Any = None,
    approval_queue: Any = None,
) -> FastMCP:
    """Construct a focused memory-only FastMCP server.

    Mounted at ``/mcp/memory`` by the orchestrator. An agent that only
    needs memory access can speak to this server without seeing the
    full admin tool surface — a smaller attack surface for narrow
    integrations.

    ``client_id_resolver`` / ``private_resolver`` are wired the same
    way as :func:`hal0.mcp.admin.build_server`'s ``bearer_resolver`` —
    transport-layer hooks the orchestrator stitches into the active
    MCP session's HTTP headers.

    ``approval_queue`` is the process-wide :class:`ApprovalQueue`. This
    server is an outermost mount (nothing gates in front of it), so pass
    it — otherwise the narrow surface becomes a bypass around the admin
    surface's bulk-delete gate (#1302).
    """
    server = FastMCP(name)
    # See hal0.mcp.admin's build_admin_mcp_server for why this is stamped
    # post-construction (#1796): FastMCP has no ``version`` kwarg, so
    # ``serverInfo.version`` otherwise defaults to the ``mcp`` SDK version.
    from hal0 import __version__ as _hal0_version

    server._mcp_server.version = _hal0_version
    _raw_dispatcher = make_dispatcher(
        wrapper,
        client_id_resolver=client_id_resolver,
        private_resolver=private_resolver,
        approval_queue=approval_queue,
    )

    async def dispatcher(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Every ``@server.tool`` function below calls this, unchanged.

        ``_raw_dispatcher`` signals failure (bad args, memory-schema error,
        engine exception, ...) as a normal ``{"status": "error", ...}``
        return, same convention as ``hal0.mcp.admin.dispatch`` — so every
        one of this mount's tool calls came back ``isError: false`` even on
        a hard validation failure (#1796; ``POST /api/memory/add`` with a
        wrong field name is the concrete repro). Raise here, once, instead
        of touching all ~20 ``@server.tool`` bodies below — FastMCP's
        lowlevel handler catches the exception and flips ``isError: true``.
        """
        result = await _raw_dispatcher(tool, args)
        if isinstance(result, dict) and result.get("status") == "error":
            raise ToolError(json.dumps(result.get("error", result)))
        return result

    # Typed signatures so FastMCP publishes real parameter schemas —
    # the old single ``args: dict`` param advertised an opaque object
    # and every client had to guess the call shape from docs. The
    # trailing ``args`` param keeps the legacy envelope working:
    # explicit params win over same-named ``args`` keys.

    def _merged(args: dict[str, Any] | None, **explicit: Any) -> dict[str, Any]:
        merged = dict(args or {})
        merged.update({k: v for k, v in explicit.items() if v is not None})
        return merged

    @server.tool(
        name="memory_add",
        description=(
            "Add an item to long-term memory. Returns {id, timestamp} plus "
            "operation_id/operation_ids/items_count when the engine reports them. "
            "Reuse document_id across calls to upsert one logical document (e.g. "
            "a conversation). sync=true waits for extraction instead of the "
            "default fire-and-forget background ingest."
        ),
        annotations=_ANNOTATIONS["memory_add"],
    )
    async def memory_add(
        text: str | None = None,
        dataset: str | None = None,
        tags: list[str] | str | None = None,
        metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        observation_scopes: Any = None,
        strategy: str | None = None,
        update_mode: str | None = None,
        sync: bool | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_add",
            _merged(
                args,
                text=text,
                dataset=dataset,
                tags=tags,
                metadata=metadata,
                document_id=document_id,
                entities=entities,
                observation_scopes=observation_scopes,
                strategy=strategy,
                update_mode=update_mode,
                sync=sync,
            ),
        )

    @server.tool(
        name="memory_search",
        description=(
            "Search long-term memory. Returns {results: [...]}. tag_groups/"
            "min_scores are Hindsight-native filters (boolean tag expressions / "
            "per-stage score floors) — ignored by engines without them."
        ),
        annotations=_ANNOTATIONS["memory_search"],
    )
    async def memory_search(
        query: str | None = None,
        limit: int | None = None,
        dataset: str | list[str] | None = None,
        tags: list[str] | str | None = None,
        before: str | None = None,
        after: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        min_scores: dict[str, float] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_search",
            _merged(
                args,
                query=query,
                limit=limit,
                dataset=dataset,
                tags=tags,
                before=before,
                after=after,
                tag_groups=tag_groups,
                min_scores=min_scores,
            ),
        )

    @server.tool(
        name="memory_list",
        description="Page through long-term memory items. Returns {items, next_cursor}.",
        annotations=_ANNOTATIONS["memory_list"],
    )
    async def memory_list(
        dataset: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_list",
            _merged(args, dataset=dataset, cursor=cursor, limit=limit),
        )

    @server.tool(
        name="memory_delete",
        description=(
            "Delete one or more memory items by id. Deleting >1 id is a bulk "
            "delete and returns {status: pending_approval} — it runs only "
            "after the operator approves it. dataset optionally directs the "
            "sweep (e.g. project:<id>)."
        ),
        annotations=_ANNOTATIONS["memory_delete"],
    )
    async def memory_delete(
        ids: list[str] | None = None,
        dataset: str | list[str] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_delete",
            _merged(args, ids=ids, dataset=dataset),
        )

    @server.tool(
        name="memory_recall",
        description=(
            "Recall token-budgeted, consolidated memory (preferred over search). "
            "types defaults to world+experience+observation."
        ),
        annotations=_ANNOTATIONS["memory_recall"],
    )
    async def memory_recall(
        query: str | None = None,
        max_tokens: int | None = None,
        types: list[str] | None = None,
        dataset: str | list[str] | None = None,
        tags: list[str] | str | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        budget: str | None = None,
        prefer_observations: bool | None = None,
        include: dict[str, Any] | None = None,
        query_timestamp: str | None = None,
        min_scores: dict[str, float] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_recall",
            _merged(
                args,
                query=query,
                max_tokens=max_tokens,
                types=types,
                dataset=dataset,
                tags=tags,
                tags_match=tags_match,
                tag_groups=tag_groups,
                budget=budget,
                prefer_observations=prefer_observations,
                include=include,
                query_timestamp=query_timestamp,
                min_scores=min_scores,
            ),
        )

    @server.tool(
        name="memory_reflect",
        description=(
            "LLM-backed synthesis over memory — ask a question and get a "
            "written-out answer grounded in recalled facts, not a raw fact "
            "list. Single-bank (dataset resolves to one namespace, default "
            "shared). Returns {status: unsupported} on engines without this "
            "capability instead of erroring."
        ),
        annotations=_ANNOTATIONS["memory_reflect"],
    )
    async def memory_reflect(
        query: str | None = None,
        dataset: str | None = None,
        budget: str | None = None,
        max_tokens: int | None = None,
        fact_types: list[str] | None = None,
        tags: list[str] | str | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        exclude_mental_models: bool | None = None,
        exclude_mental_model_ids: list[str] | None = None,
        include: dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_reflect",
            _merged(
                args,
                query=query,
                dataset=dataset,
                budget=budget,
                max_tokens=max_tokens,
                fact_types=fact_types,
                tags=tags,
                tags_match=tags_match,
                tag_groups=tag_groups,
                exclude_mental_models=exclude_mental_models,
                exclude_mental_model_ids=exclude_mental_model_ids,
                include=include,
                response_schema=response_schema,
            ),
        )

    @server.tool(
        name="memory_curate",
        description=(
            "Edit a memory unit, or soft-invalidate/revert it via state "
            "('invalidated'|'valid' — reversible). The non-destructive 'this "
            "is wrong' correction path. id is the PER-FACT id (from a search/"
            "recall/list result), not memory_add's document_id."
        ),
        annotations=_ANNOTATIONS["memory_curate"],
    )
    async def memory_curate(
        id: str | None = None,
        dataset: str | None = None,
        text: str | None = None,
        context: str | None = None,
        occurred_start: str | None = None,
        occurred_end: str | None = None,
        fact_type: str | None = None,
        entities: list[str] | None = None,
        state: str | None = None,
        reason: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_curate",
            _merged(
                args,
                id=id,
                dataset=dataset,
                text=text,
                context=context,
                occurred_start=occurred_start,
                occurred_end=occurred_end,
                fact_type=fact_type,
                entities=entities,
                state=state,
                reason=reason,
            ),
        )

    @server.tool(
        name="memory_history",
        description="Revision history for one memory unit (curation audit trail).",
        annotations=_ANNOTATIONS["memory_history"],
    )
    async def memory_history(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_history", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_mental_model_list",
        description="List a bank's mental models (stored reflect responses).",
        annotations=_ANNOTATIONS["memory_mental_model_list"],
    )
    async def memory_mental_model_list(
        dataset: str | None = None,
        tags: list[str] | str | None = None,
        tags_match: str | None = None,
        detail: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_mental_model_list",
            _merged(
                args,
                dataset=dataset,
                tags=tags,
                tags_match=tags_match,
                detail=detail,
                limit=limit,
                offset=offset,
            ),
        )

    @server.tool(
        name="memory_mental_model_get",
        description="Get one mental model by id.",
        annotations=_ANNOTATIONS["memory_mental_model_get"],
    )
    async def memory_mental_model_get(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_mental_model_get", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_mental_model_create",
        description=(
            "Create a mental model — a standing question whose answer is kept "
            "refreshed from memory (e.g. 'what does the user prefer for X'). "
            "Returns {mental_model_id, operation_id}; the initial content "
            "generation runs as an async refresh — poll with memory_operation_get."
        ),
        annotations=_ANNOTATIONS["memory_mental_model_create"],
    )
    async def memory_mental_model_create(
        name: str | None = None,
        source_query: str | None = None,
        dataset: str | None = None,
        id: str | None = None,
        tags: list[str] | str | None = None,
        max_tokens: int | None = None,
        trigger: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_mental_model_create",
            _merged(
                args,
                name=name,
                source_query=source_query,
                dataset=dataset,
                id=id,
                tags=tags,
                max_tokens=max_tokens,
                trigger=trigger,
            ),
        )

    @server.tool(
        name="memory_mental_model_update",
        description="Update a mental model's name/source_query/max_tokens/tags/trigger.",
        annotations=_ANNOTATIONS["memory_mental_model_update"],
    )
    async def memory_mental_model_update(
        id: str | None = None,
        dataset: str | None = None,
        name: str | None = None,
        source_query: str | None = None,
        max_tokens: int | None = None,
        tags: list[str] | str | None = None,
        trigger: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_mental_model_update",
            _merged(
                args,
                id=id,
                dataset=dataset,
                name=name,
                source_query=source_query,
                max_tokens=max_tokens,
                tags=tags,
                trigger=trigger,
            ),
        )

    @server.tool(
        name="memory_mental_model_delete",
        description="Delete a mental model. Destructive — gated for operator approval.",
        annotations=_ANNOTATIONS["memory_mental_model_delete"],
    )
    async def memory_mental_model_delete(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_mental_model_delete", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_mental_model_refresh",
        description=(
            "Trigger a mental model refresh (async). Returns {operation_id, "
            "status} — poll with memory_operation_get."
        ),
        annotations=_ANNOTATIONS["memory_mental_model_refresh"],
    )
    async def memory_mental_model_refresh(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_mental_model_refresh", _merged(args, id=id, dataset=dataset)
        )

    @server.tool(
        name="memory_directive_list",
        description="List a bank's directives (standing instructions injected into prompts).",
        annotations=_ANNOTATIONS["memory_directive_list"],
    )
    async def memory_directive_list(
        dataset: str | None = None,
        tags: list[str] | str | None = None,
        tags_match: str | None = None,
        active_only: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_directive_list",
            _merged(
                args,
                dataset=dataset,
                tags=tags,
                tags_match=tags_match,
                active_only=active_only,
                limit=limit,
                offset=offset,
            ),
        )

    @server.tool(
        name="memory_directive_get",
        description="Get one directive by id.",
        annotations=_ANNOTATIONS["memory_directive_get"],
    )
    async def memory_directive_get(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_directive_get", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_directive_create",
        description="Create a directive (standing instruction injected into prompts).",
        annotations=_ANNOTATIONS["memory_directive_create"],
    )
    async def memory_directive_create(
        name: str | None = None,
        content: str | None = None,
        dataset: str | None = None,
        priority: int | None = None,
        is_active: bool | None = None,
        tags: list[str] | str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_directive_create",
            _merged(
                args,
                name=name,
                content=content,
                dataset=dataset,
                priority=priority,
                is_active=is_active,
                tags=tags,
            ),
        )

    @server.tool(
        name="memory_directive_update",
        description="Update a directive's name/content/priority/is_active/tags.",
        annotations=_ANNOTATIONS["memory_directive_update"],
    )
    async def memory_directive_update(
        id: str | None = None,
        dataset: str | None = None,
        name: str | None = None,
        content: str | None = None,
        priority: int | None = None,
        is_active: bool | None = None,
        tags: list[str] | str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_directive_update",
            _merged(
                args,
                id=id,
                dataset=dataset,
                name=name,
                content=content,
                priority=priority,
                is_active=is_active,
                tags=tags,
            ),
        )

    @server.tool(
        name="memory_directive_delete",
        description="Delete a directive. Destructive — gated for operator approval.",
        annotations=_ANNOTATIONS["memory_directive_delete"],
    )
    async def memory_directive_delete(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_directive_delete", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_operation_list",
        description="List async operations (retain, consolidation, refresh, ...).",
        annotations=_ANNOTATIONS["memory_operation_list"],
    )
    async def memory_operation_list(
        dataset: str | None = None,
        status: str | None = None,
        type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        exclude_parents: bool | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_operation_list",
            _merged(
                args,
                dataset=dataset,
                status=status,
                type=type,
                limit=limit,
                offset=offset,
                exclude_parents=exclude_parents,
            ),
        )

    @server.tool(
        name="memory_operation_get",
        description=(
            "Get one async operation's status — the poll target for memory_add's "
            "operation_id, memory_mental_model_refresh, and memory_bank_consolidate."
        ),
        annotations=_ANNOTATIONS["memory_operation_get"],
    )
    async def memory_operation_get(
        id: str | None = None,
        dataset: str | None = None,
        include_payload: bool | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_operation_get",
            _merged(args, id=id, dataset=dataset, include_payload=include_payload),
        )

    @server.tool(
        name="memory_operation_cancel",
        description="Cancel a pending/processing operation. Aborts in-flight work only.",
        annotations=_ANNOTATIONS["memory_operation_cancel"],
    )
    async def memory_operation_cancel(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_operation_cancel", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_operation_retry",
        description="Re-queue a failed operation.",
        annotations=_ANNOTATIONS["memory_operation_retry"],
    )
    async def memory_operation_retry(
        id: str | None = None,
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_operation_retry", _merged(args, id=id, dataset=dataset))

    @server.tool(
        name="memory_tags_list",
        description="List tags in use in a bank, with usage counts.",
        annotations=_ANNOTATIONS["memory_tags_list"],
    )
    async def memory_tags_list(
        dataset: str | None = None,
        q: str | None = None,
        source: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_tags_list",
            _merged(args, dataset=dataset, q=q, source=source, limit=limit, offset=offset),
        )

    @server.tool(
        name="memory_bank_stats",
        description=(
            "Node/link/observation/operation counts for a bank. Read-only — no "
            "destructive bank operations (delete/clear) are exposed via MCP."
        ),
        annotations=_ANNOTATIONS["memory_bank_stats"],
    )
    async def memory_bank_stats(
        dataset: str | None = None,
        refresh: bool | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher(
            "memory_bank_stats", _merged(args, dataset=dataset, refresh=refresh)
        )

    @server.tool(
        name="memory_bank_consolidate",
        description=(
            "Trigger the engine's world/experience → observation consolidation "
            "pass early. Returns {operation_id, deduplicated} — poll with "
            "memory_operation_get. Not destructive: source facts are kept."
        ),
        annotations=_ANNOTATIONS["memory_bank_consolidate"],
    )
    async def memory_bank_consolidate(
        dataset: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await dispatcher("memory_bank_consolidate", _merged(args, dataset=dataset))

    return server


__all__ = [
    "_ANNOTATIONS",
    "MemorySchemaError",
    "build_server",
    "make_dispatcher",
]
