"""hal0 memory MCP server — Cognee-backed long-term memory tools.

Per ADR-0005 §2, memory is a first-class MCP surface so bundled and
external agents share one persistence layer for "what the user
remembers about themselves and their hal0". The wrapper around Cognee
itself lives in :mod:`hal0.memory.cognee_wrapper` (Memory-engine team
owns that module); this module exposes the four MCP tools and the
schema validation that bridges agent calls to the wrapper.

Tool catalog (ADR-0005 §2)
--------------------------

::

    memory_add     — write one item; returns {id, timestamp}
    memory_search  — vector + tag + time-window query; returns {results}
    memory_list    — paginated walk; returns {items, next_cursor}
    memory_delete  — remove by id(s); returns {deleted}

Per ADR-0005 §2 the v0.2 schema is rich from day 1 so we don't pay a
schema-versioning tax in Phase 9::

    memory_add(text, dataset="shared", tags=[], metadata={})
        → {id: str, timestamp: iso8601}
        # `source` is auto-extracted server-side from the caller's
        # client_id (Bearer-derived). Clients CANNOT pass `source`
        # themselves — that's how ADR-0005 §5 keeps the audit trail
        # forensically grounded.

    memory_search(query, limit=10, dataset="shared"|list, tags=[],
                  before=null, after=null)
        → {results: [{id, text, score, timestamp, dataset, tags,
                      source, metadata}, ...]}

    memory_list(dataset="shared", cursor=null, limit=50)
        → {items: [...], next_cursor: str | null}

    memory_delete(ids: list[str])
        → {deleted: int}

Namespace rule (ADR-0005 §3): writes default to dataset ``shared``;
clients opting into private mode at the transport layer promote to
``private:<client_id>``. We resolve the effective dataset in
:func:`_resolve_dataset` so callers don't have to know the rule.

Fail-fast import
----------------

When the ``cognee`` package is not installed (Memory-engine wave does
that), importing :mod:`hal0.memory.cognee_wrapper` raises ImportError.
We do the import lazily inside the dispatch helper so the module
itself stays importable for unit tests that mock the wrapper.

Transport
---------

The Streamable-HTTP MCP server pattern matches :mod:`hal0.mcp.admin` —
``build_server()`` returns a FastMCP instance that the orchestrator
mounts at ``/mcp/memory`` via ``app.mount()``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from hal0.memory.namespace import (
    DEFAULT_DATASET as _DEFAULT_DATASET,
)
from hal0.memory.namespace import (
    MemoryNamespaceError,
    resolve_write_dataset,
)

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
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
    """Raised when a memory tool call's args don't match the ADR-0005 §2 schema."""


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


def _normalise_tags(value: Any) -> list[str]:
    """Tags may arrive as None, list, or stringified CSV (some MCP clients
    don't speak JSON-array literals well). Normalise to list[str] (possibly
    empty) per ADR-0005 §2's default of ``[]``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        # CSV / comma-separated input.
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t) for t in value]
    raise MemorySchemaError(f"tags must be list[str] or comma-string, got {type(value).__name__}")


# ── Namespace resolution (ADR-0005 §3) ───────────────────────────────────────
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


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


# ── Tool implementations ─────────────────────────────────────────────────────
#
# Each helper returns the JSON payload an MCP client should see. They
# share one CogneeWrapper instance held by the caller — we pass it in
# rather than importing globally so tests can substitute a mock.


async def _memory_add(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_add(text, dataset?, tags?, metadata?) → {id, timestamp}.

    ADR-0005 §2 schema:
      - ``text``: required, non-empty str.
      - ``dataset``: defaults to ``"shared"``; ``--private`` promotes
        to ``private:<client_id>``.
      - ``tags``: defaults to ``[]``.
      - ``metadata``: defaults to ``{}``.
      - ``source``: NOT accepted from the caller. Server-injected from
        ``client_id`` so callers cannot lie about their identity
        (ADR-0005 §5 audit grounding).

    CogneeWrapper contract::

        await wrapper.add(text, dataset, tags, source, metadata)
            -> {"id": str, "timestamp": iso8601_str}
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
    source = client_id or "anonymous"
    result = await wrapper.add(
        text=text,
        dataset=dataset,
        tags=tags,
        source=source,
        metadata=metadata_raw,
    )
    return {
        "id": result["id"],
        "timestamp": result.get("timestamp") or _iso_now(),
    }


async def _memory_search(
    wrapper: Any,
    args: dict[str, Any],
    *,
    client_id: str | None,
    private: bool,
) -> dict[str, Any]:
    """memory_search(query, limit=10, dataset="shared"|list, tags=[],
                     before=null, after=null) → {results}.

    CogneeWrapper contract::

        await wrapper.search(query, limit, dataset, tags, before, after)
            -> list[ItemDict]

    ``dataset`` MAY be a list — a private-mode client sees both
    ``shared`` + their own ``private:<client_id>`` per ADR-0005 §3.
    """
    query = _require(args, "query", str)
    if not query.strip():
        raise MemorySchemaError("query must be non-empty")
    limit_raw = args.get("limit", 10)
    if not isinstance(limit_raw, int) or limit_raw < 1 or limit_raw > 200:
        raise MemorySchemaError("limit must be 1..200")
    requested = args.get("dataset")
    dataset: str | list[str]
    if isinstance(requested, list):
        dataset = [str(d) for d in requested]
    elif requested is None or (isinstance(requested, str) and not requested):
        # Private-mode read sees both shared + own-private namespace.
        dataset = ["shared", f"private:{client_id}"] if private and client_id else _DEFAULT_DATASET
    elif isinstance(requested, str):
        dataset = _resolve_dataset(requested, private=private, client_id=client_id)
    else:
        raise MemorySchemaError("dataset must be str | list[str] | null")
    tags = _normalise_tags(args.get("tags"))
    before = _optional(args, "before", str)
    after = _optional(args, "after", str)
    results = await wrapper.search(
        query=query,
        limit=limit_raw,
        dataset=dataset,
        tags=tags,
        before=before,
        after=after,
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
    page = await wrapper.list_items(dataset=dataset, cursor=cursor, limit=limit_raw)
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
    """memory_delete(ids) → {deleted: int}.

    Returns the count of deleted rows per ADR-0005 §2. ``ids`` must be
    non-empty. Approval-gating for bulk deletes (>1 id) lives in
    :mod:`hal0.mcp.admin`; by the time we get here it has already been
    approved (or is a single-id autonomous call).
    """
    ids_raw = args.get("ids")
    if not isinstance(ids_raw, list) or not ids_raw:
        raise MemorySchemaError("ids must be a non-empty list[str]")
    ids = [str(i) for i in ids_raw]
    result = await wrapper.delete(ids=ids)
    deleted_raw = result.get("deleted", len(ids))
    # Accept either a count or the list of deleted ids from the wrapper
    # so we're forgiving of either contract while still returning the
    # ADR-0005 count shape.
    if isinstance(deleted_raw, list):
        deleted_count = len(deleted_raw)
    elif isinstance(deleted_raw, int):
        deleted_count = deleted_raw
    else:
        deleted_count = len(ids)
    return {"deleted": deleted_count}


_MEMORY_HANDLERS = {
    "memory_add": _memory_add,
    "memory_search": _memory_search,
    "memory_list": _memory_list,
    "memory_delete": _memory_delete,
}


def make_dispatcher(
    wrapper: Any,
    *,
    client_id_resolver: Any = None,
    private_resolver: Any = None,
):
    """Return an async dispatcher closure bound to ``wrapper``.

    The admin server passes this into :func:`hal0.mcp.admin.dispatch`
    via ``memory_dispatcher=`` so memory tool calls bypass the HTTP
    round-trip and hit Cognee directly in-process. Validation errors
    surface as the same error envelope shape the REST routes use.

    ``client_id_resolver`` is a 0-arg callable that returns the
    Bearer-derived caller id (used to stamp ``source`` on add + power
    the ``private:<client_id>`` namespace promotion). ``None`` is
    treated as "anonymous" — for tests that don't care about audit.

    ``private_resolver`` returns the per-call ``--private`` toggle
    state (the transport layer reads this off the agent's session).
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
        try:
            payload = await handler(wrapper, args, client_id=client_id, private=private)
            return {"status": "ok", **payload}
        except MemorySchemaError as exc:
            return {
                "status": "error",
                "error": {"code": "mcp.memory_schema", "detail": str(exc)},
            }
        except Exception as exc:  # pragma: no cover — surfaced to client
            log.warning("mcp.memory.failed", tool=tool, error=str(exc))
            return {
                "status": "error",
                "error": {"code": "mcp.memory_failed", "detail": str(exc)},
            }

    return _dispatch


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
}


# ── Standalone server (used when the memory MCP is mounted on its own) ───────


def build_server(
    *,
    wrapper: Any,
    name: str = "hal0-memory",
    client_id_resolver: Any = None,
    private_resolver: Any = None,
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
    """
    server = FastMCP(name)
    dispatcher = make_dispatcher(
        wrapper,
        client_id_resolver=client_id_resolver,
        private_resolver=private_resolver,
    )

    def _register(tool: str, description: str) -> None:
        async def _tool(args: dict[str, Any] | None = None) -> dict[str, Any]:
            return await dispatcher(tool, args or {})

        _tool.__name__ = tool
        _tool.__doc__ = description
        annotations = _ANNOTATIONS.get(tool)
        server.tool(name=tool, description=description, annotations=annotations)(_tool)

    _register("memory_add", "Add an item to long-term memory.")
    _register("memory_search", "Search long-term memory.")
    _register("memory_list", "Page through long-term memory items.")
    _register(
        "memory_delete",
        "Delete one or more memory items by id (bulk deletes gate at admin layer).",
    )

    return server


__all__ = [
    "_ANNOTATIONS",
    "MemorySchemaError",
    "build_server",
    "make_dispatcher",
]
