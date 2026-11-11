"""An engine-compatible memory endpoint for coding agents (ADR-0058 decision 4b).

The Hindsight coding-agents plugin (Claude Code, Codex, Pi) speaks the memory
engine's own REST protocol and, pointed at the engine's port, bypasses
everything hal0 does to a memory write. This router is what the plugin is
pointed at instead. It is mounted at
:data:`hal0.security.exposure.COMPAT_MEMORY_PREFIX` and answers the subset of
engine routes the plugin uses, under the engine's own paths
(``v1/default/banks/{bank}/...``), so a plugin whose ``apiUrl`` ends in the
mount prefix needs no other change.

It is not a reverse proxy. Each route here is parsed and then goes through
``HindsightProvider`` (retain, reflect, recall) or one scoped forwarder
(:func:`_forward`), so the write envelope and the read rules exist once, in
the provider and :mod:`hal0.memory.read_scope`. A request field this module
does not name never reaches the engine; the response says which were left out
(``X-hal0-Ignored-Fields``).

Four rules hold on every route:

* **Who.** :func:`require_memory_bridge` admits the admin credential, or a
  bridge key (ADR-0043) that holds the ``memory`` scope. The identity is the
  bridge client's name, stamped as ``agent:<name>`` (``agent:admin`` for the
  admin credential). ``X-hal0-Agent`` is not read.
* **Which bank.** Whatever bank the URL names, the engine call goes to the
  platform bank (``HindsightProvider.platform_bank``).
* **Which project.** The bank in the URL carries it: the plugin's default bank
  id is ``<harness>::<repository directory>``, and
  :func:`hal0.memory.projects.resolve_project` turns that into a slug through
  the operator's aliases and deny list. A bank id that names no project falls
  back to the request's own ``project:`` tags, and a caller left with none is
  ``unresolved``. Responses carry the outcome as ``X-hal0-Project``.
* **What it may read.** The visibility rule for this agent AND one project
  scope: the project plus global memory, or, for an unresolved caller, global
  memory plus its own unresolved writes. There is no cross-project read here.

What the plugin does with a status shapes the answers. It returns a 404 to
its caller without raising on every route, so a 404 on a retain would be
silent data loss: an engine 404 on a retain is answered 502. Every other
non-2xx raises in the plugin and is logged, so hal0's own refusal of a write
is 403, 405 or 409, never 404. The knowledge-base routes are the exception by
design: the plugin reads 404 there as "pages unsupported" and carries on
without them.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from hal0.activity import audit_action
from hal0.api.auth import judged_principal
from hal0.api.routes.memory import MemoryUnavailable, extraction_ctx_preflight
from hal0.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    Hal0Error,
    MemoryEngineUnavailable,
    NotFound,
    Unauthorized,
)
from hal0.memory.envelope import (
    AGENT_TAG_PREFIX,
    PROJECT_TAG_PREFIX,
    PROJECT_UNRESOLVED_TAG,
    coerce_tags,
)
from hal0.memory.projects import (
    UNRESOLVED_SLUG,
    ProjectResolution,
    resolve_project,
    strip_legacy_bank_prefix,
)
from hal0.memory.read_scope import (
    project_scope_groups,
    reflect_visibility_scope,
    tags_readable,
    unresolved_scope_groups,
)

router = APIRouter()

log = structlog.get_logger(__name__)

#: The bridge scope a key must hold to use this endpoint (ADR-0043).
MEMORY_SCOPE = "memory"

#: The identity an admin credential writes and reads as. No bridge client can
#: take the name (``hal0.bridge.keys.RESERVED_CLIENT_NAMES``).
ADMIN_AGENT = "admin"

#: Most items one retain may carry, and the largest retain body read.
MAX_RETAIN_ITEMS = 32
MAX_RETAIN_BODY_BYTES = 48 * 1024 * 1024
#: The largest body read on the query routes (reflect, recall).
MAX_QUERY_BODY_BYTES = 1024 * 1024

PROJECT_HEADER = "X-hal0-Project"
IGNORED_FIELDS_HEADER = "X-hal0-Ignored-Fields"
FILTERED_ITEMS_HEADER = "X-hal0-Filtered-Items"

# Engine read budgets, in seconds. A reflect uses the client's own
# ``REFLECT_TIMEOUT_S`` (``HindsightRestClient.reflect``).
_PROBE_TIMEOUT_S = 3.0
_RETAIN_ASYNC_TIMEOUT_S = 30.0
_RETAIN_SYNC_TIMEOUT_S = 300.0
_RECALL_TIMEOUT_S = 60.0
_DOCUMENT_TIMEOUT_S = 30.0
_OPERATION_TIMEOUT_S = 15.0
_CONFIG_TIMEOUT_S = 15.0

#: How long the platform bank's strategy names are trusted, and how long a
#: failed read of them is.
_STRATEGY_TTL_S = 300.0
_STRATEGY_FAILURE_TTL_S = 30.0

#: The write gate, consulted when it exists (:func:`_require_writes_allowed`).
_WRITE_GATE_MODULE = "hal0.memory.write_gate"

#: How often a long engine call checks that its client is still there.
_DISCONNECT_POLL_S = 1.0

_BANK = "/v1/default/banks/{bank}"

#: The routes this endpoint serves, as the plugin spells them. Returned in the
#: details of the unknown-route answer.
SUPPORTED_ROUTES: tuple[str, ...] = (
    "GET /version",
    "GET /health",
    "POST /v1/default/banks/{bank}/memories",
    "POST /v1/default/banks/{bank}/memories/recall",
    "POST /v1/default/banks/{bank}/reflect",
    "GET /v1/default/banks/{bank}/documents",
    "DELETE /v1/default/banks/{bank}/documents/{document_id}",
    "GET /v1/default/banks/{bank}/operations",
    "GET /v1/default/banks/{bank}/operations/{operation_id}",
    "GET /v1/default/banks/{bank}/config",
    "POST /v1/default/banks/{bank}/import",
)

# ── request fields ───────────────────────────────────────────────────────────

# RetainRequest. ``observation_scopes`` is named so that it is not reported as
# unknown: hal0 chooses the scope and the caller's value is dropped.
_RETAIN_FIELDS = frozenset({"items", "async", "operation_id"})
_ITEM_PASSTHROUGH = (
    "content",
    "context",
    "document_id",
    "tags",
    "timestamp",
    "entities",
    "update_mode",
)
_ITEM_FIELDS = frozenset({*_ITEM_PASSTHROUGH, "metadata", "strategy", "observation_scopes"})
#: Metadata keys hal0 writes on every retain here; a caller's value is dropped.
_OWNED_METADATA = frozenset({"bridge_client", "project", "project_source"})
#: A tag namespace the bridge's own memory tool stamps as provenance
#: (``hal0.bridge.service``); a caller of this endpoint may not forge it.
_BRIDGE_SOURCE_TAG_PREFIX = "source:bridge:"
#: The document-id prefix the unified bank keeps for private documents
#: (``HindsightProvider._retain_item``).
_PRIVATE_DOCUMENT_PREFIX = "private:"

_REFLECT_FIELDS = frozenset(
    {"query", "budget", "max_tokens", "response_schema", "tags", "tags_match", "tag_groups"}
)
_RECALL_FIELDS = frozenset(
    {
        "query",
        "types",
        "budget",
        "max_tokens",
        "include",
        "query_timestamp",
        "prefer_observations",
        "min_scores",
        "tags",
        "tags_match",
        "tag_groups",
    }
)
_TAGS_MATCH_MODES = frozenset({"any", "all", "any_strict", "all_strict", "exact"})

# What ``X-hal0-Ignored-Fields`` may echo of a caller's field names.
_PLAIN_FIELD_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_IGNORED_FIELDS_SHOWN = 20

# ── response fields ──────────────────────────────────────────────────────────

# An operation record as this endpoint returns it. The platform bank's
# operations belong to every client, so only what a status poll needs is kept:
# no payload, no result metadata, no document id, no file name.
_OPERATION_FIELDS = (
    "id",
    "operation_id",
    "task_type",
    "operation_type",
    "status",
    "items_count",
    "created_at",
    "updated_at",
    "completed_at",
    "retry_count",
    "next_retry_at",
)
_PROGRESS_FIELDS = ("stage", "at", "processed", "total")
_CHILD_FIELDS = ("operation_id", "status", "sub_batch_index", "items_count")
_OPERATION_LIST_FIELDS = ("bank_id", "total", "limit", "offset")

# The bank configuration keys this endpoint returns: the three the plugin
# reads to decide whether a bank already has missions.
_CONFIG_FIELDS = ("reflect_mission", "retain_mission", "observations_mission")


# ── typed errors ─────────────────────────────────────────────────────────────


class CompatScopeRequired(Hal0Error):
    """The credential is valid but may not use the memory endpoint."""

    code = "memory.compat_scope_required"
    status = 403


class CompatBodyTooLarge(Hal0Error):
    code = "memory.compat_body_too_large"
    status = 413


class CompatTooManyItems(Hal0Error):
    code = "memory.compat_too_many_items"
    status = 413


class CompatEngineError(Hal0Error):
    """The engine answered in a way this endpoint does not pass on."""

    code = "memory.compat_engine_error"
    status = 502


class CompatEngineTimeout(Hal0Error):
    code = "memory.compat_engine_timeout"
    status = 504


class CompatMutationUnsupported(Hal0Error):
    code = "memory.compat_mutation_unsupported"
    status = 405


# ── who is calling ───────────────────────────────────────────────────────────


def require_memory_bridge() -> Callable[[Request], str]:
    """A dependency that admits the caller and returns its agent identity.

    The exposure class of this prefix (BRIDGE) admits the admin credential
    and ANY bridge key: the auth middleware does not look at a bridge key's
    scopes on a BRIDGE-class path (``hal0.api.auth._verdict``). So the scope
    is checked here, on every handler:

    * the admin credential is admitted and acts as :data:`ADMIN_AGENT`;
    * a bridge key that holds :data:`MEMORY_SCOPE` is admitted and acts as
      its client name;
    * any other bridge key, and the box client key, is refused with 403
      ``memory.compat_scope_required``;
    * no credential is 401. The middleware answers that case first, at every
      auth posture; this is the same answer for an app assembled without it.

    The principal is the one the middleware judged
    (:func:`hal0.api.auth.judged_principal`), never a second key lookup, and
    never the ``X-hal0-Agent`` header.
    """

    def _caller(request: Request) -> str:
        principal = judged_principal(request)
        if principal.tier == "admin":
            return ADMIN_AGENT
        if principal.tier == "bridge" and principal.name and MEMORY_SCOPE in principal.scopes:
            return principal.name
        if principal.tier == "anon":
            raise Unauthorized("authentication required")
        raise CompatScopeRequired(
            "this endpoint needs a bridge key with the 'memory' scope, or the admin credential",
            details={"required_scope": MEMORY_SCOPE},
        )

    return _caller


_CALLER = Depends(require_memory_bridge())


# ── the engine, the project, the read scope ──────────────────────────────────


@dataclass(slots=True)
class _CompatState:
    """What this router keeps per app: the strategy cache and the import log."""

    strategies: dict[str, tuple[float, frozenset[str]]] = field(default_factory=dict)
    import_noted: set[str] = field(default_factory=set)


def _state(request: Request) -> _CompatState:
    state = getattr(request.app.state, "memory_compat", None)
    if not isinstance(state, _CompatState):
        state = _CompatState()
        request.app.state.memory_compat = state
    return state


def _provider(request: Request) -> Any:
    """The live Hindsight provider, or 503.

    The volatile fallback provider has no engine behind it and no envelope, so
    this endpoint does not answer from it: a plugin that gets 503 keeps its
    session cursor dirty and sends the content again.
    """
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None or getattr(provider, "hindsight_client", None) is None:
        raise MemoryUnavailable("the memory engine is not available on this hal0 instance")
    return provider


def _bank_path(provider: Any, suffix: str = "") -> str:
    return f"/v1/default/banks/{quote(str(provider.platform_bank), safe='')}{suffix}"


@dataclass(frozen=True, slots=True)
class _Scope:
    """One request's caller and project, and what follows from them."""

    agent: str
    resolution: ProjectResolution

    @property
    def slug(self) -> str | None:
        return self.resolution.slug

    @property
    def project_tag(self) -> str:
        """The one project tag this caller's own writes carry."""
        return self.resolution.tag or PROJECT_UNRESOLVED_TAG

    @property
    def agent_tag(self) -> str:
        return f"{AGENT_TAG_PREFIX}{self.agent}"

    def headers(self) -> dict[str, str]:
        return {PROJECT_HEADER: self.slug or UNRESOLVED_SLUG}

    def write_tags(self) -> list[str]:
        """The tags that place a document inside this caller's project.

        Every write of this request carries them: its project tag (each one,
        when its own tags named several), or, for a caller with no project,
        ``project:unresolved`` together with its own ``agent:`` tag, because
        the marker alone is shared by every repository's unresolved writes.
        """
        slugs = self.resolution.project_slugs
        if slugs:
            return [f"{PROJECT_TAG_PREFIX}{slug}" for slug in slugs]
        return [PROJECT_UNRESOLVED_TAG, self.agent_tag]

    def project_groups(self) -> list[dict[str, Any]]:
        if self.slug:
            return project_scope_groups(self.slug)
        return unresolved_scope_groups(self.agent)

    def read_groups(self) -> list[dict[str, Any]]:
        """Everything this caller may read: visibility AND the project scope."""
        return [*reflect_visibility_scope(self.agent), *self.project_groups()]


def _scope(provider: Any, bank: str, agent: str, tags: Iterable[Any] = ()) -> _Scope:
    """Resolve the request's project from the bank in the URL, then its tags.

    Two or more distinct projects among the tags are not one project, and a
    read is never widened to several: such a request is unresolved.
    """
    resolution = resolve_project(
        bank_segment=strip_legacy_bank_prefix(bank),
        tags=tags,
        coding=True,
        registry=provider.projects,
    )
    return _Scope(agent=agent, resolution=resolution)


# ── reading the request ──────────────────────────────────────────────────────


async def _read_json(request: Request, *, cap: int) -> dict[str, Any]:
    """The JSON object in the body, read with a hard size cap.

    The body is streamed and counted, so a request without (or with a false)
    ``Content-Length`` is cut off at ``cap`` bytes too.
    """
    declared = request.headers.get("content-length", "")
    too_large = CompatBodyTooLarge(
        f"request body is larger than {cap} bytes", details={"max_bytes": cap}
    )
    if declared.isdigit() and int(declared) > cap:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise too_large
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks) or b"null")
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON", code="request.invalid_json") from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.invalid_json")
    return body


def _ignored(found: Iterable[str]) -> dict[str, str]:
    """The ``X-hal0-Ignored-Fields`` header for request fields left out.

    The names are the caller's own JSON keys, so only plain ones are echoed
    (a header value cannot carry a newline or a character outside Latin-1),
    and only the first :data:`_IGNORED_FIELDS_SHOWN`.
    """
    names = sorted({name for name in found if _PLAIN_FIELD_RE.fullmatch(name)})
    if not names:
        return {}
    return {IGNORED_FIELDS_HEADER: ",".join(names[:_IGNORED_FIELDS_SHOWN])}


def _caller_filter(body: dict[str, Any], what: str) -> tuple[list[str] | None, str | None]:
    """The caller's own ``tags`` / ``tags_match``, validated."""
    tags = body.get("tags")
    if tags is not None and not (isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
        raise BadRequest(f"{what} 'tags' must be a list of strings")
    tags_match = body.get("tags_match")
    if tags_match is not None and not (
        isinstance(tags_match, str) and tags_match in _TAGS_MATCH_MODES
    ):
        raise BadRequest(
            f"{what} 'tags_match' must be one of " + ", ".join(sorted(_TAGS_MATCH_MODES))
        )
    return (list(tags) if tags else None), tags_match


def _caller_groups(body: dict[str, Any], what: str) -> list[dict[str, Any]]:
    """``tag_groups`` the caller sent. The engine ANDs every group, so these
    can narrow a read and never widen it; the engine validates their shape."""
    groups = body.get("tag_groups")
    if groups is None:
        return []
    if not (isinstance(groups, list) and all(isinstance(g, dict) for g in groups)):
        raise BadRequest(f"{what} 'tag_groups' must be a list of objects")
    return list(groups)


def _query(body: dict[str, Any], what: str) -> str:
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise BadRequest(f"{what} requires 'query' (non-empty string)")
    return query


# ── talking to the engine ────────────────────────────────────────────────────


def _engine_failure(
    exc: BaseException,
    *,
    what: str,
    headers: dict[str, str] | None = None,
    not_found_is_gateway: bool = False,
) -> Response:
    """Turn a failed engine call into this endpoint's answer.

    Returns the response to pass on for an engine 4xx, raises a typed error
    for everything else, and re-raises an exception that is not the engine's.

    * An engine 4xx goes to the caller with the engine's body (and its
      ``Retry-After``): a 409 on a reused operation id, a 422 on a malformed
      field and a 429 are the caller's to act on.
    * An engine 401 or 403 is about hal0's own engine credential, not the
      caller's key, and a 5xx is the engine's failure: both are 502.
    * With ``not_found_is_gateway`` an engine 404 is 502 as well. The plugin
      does not raise on a 404, so a write that answered 404 would be lost
      without a trace.
    * A connect failure is 503, a read that ran out of time 504.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status >= 500 or status in (401, 403) or (status == 404 and not_found_is_gateway):
            raise CompatEngineError(
                f"the memory engine answered {status} to {what}",
                details={"upstream_status": status},
            ) from exc
        passed = dict(headers or {})
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            passed["Retry-After"] = retry_after
        return Response(
            content=exc.response.content,
            status_code=status,
            media_type=exc.response.headers.get("content-type", "application/json"),
            headers=passed,
        )
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        raise MemoryEngineUnavailable() from exc
    if isinstance(exc, httpx.TimeoutException):
        raise CompatEngineTimeout(f"the memory engine did not finish {what} in time") from exc
    if isinstance(exc, httpx.HTTPError):
        raise MemoryEngineUnavailable() from exc
    raise exc


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    what: str,
    timeout_s: float,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any | Response:
    """The scoped forwarder: one engine call with no body, chosen by a handler.

    Never given a caller-controlled path prefix, bank or body. Returns the
    engine's JSON, or the :class:`Response` to pass on when the engine
    answered 4xx (see :func:`_engine_failure`).
    """
    client = _provider(request).hindsight_client
    try:
        return await client.request_json(method, path, params=params, timeout_s=timeout_s)
    except Exception as exc:
        return _engine_failure(exc, what=what, headers=headers)


class _ClientGone(Exception):
    """The client disconnected while the engine was still working."""


async def _until_disconnect(request: Request, call: Awaitable[Any]) -> Any:
    """Await ``call``, cancelling it when the client goes away.

    A reflect runs for minutes and the plugin gives up on its own timer.
    Cancelling the task closes the upstream connection, and the engine stops a
    reflect whose client has left, so an abandoned one does not keep the
    extraction model busy.
    """
    task = asyncio.ensure_future(call)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_DISCONNECT_POLL_S)
            if done:
                return task.result()
            if await request.is_disconnected():
                raise _ClientGone
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def _require_writes_allowed() -> None:
    """The one place this router consults the memory write gate.

    ``hal0.memory.write_gate`` pauses memory writes while the store is being
    rebuilt (ADR-0058 decision 2). It is not in the tree this module was
    written against, so the import is optional: with the module present the
    retain and delete routes refuse with its typed 503 exactly as
    ``POST /api/memory/add`` does, and until then this is a no-op. Only the
    module being absent is tolerated; one that is there and fails to import
    is an error, not an open gate. Once the module is on main this becomes a
    plain import.
    """
    try:
        write_gate = importlib.import_module(_WRITE_GATE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _WRITE_GATE_MODULE:
            raise
        return
    write_gate.require_writes_allowed()


def _carries(tags: Any, required: Iterable[str]) -> bool:
    """True when ``tags`` is a tag list holding every one of ``required``."""
    if not isinstance(tags, list):
        return False
    return set(required) <= {tag for tag in tags if isinstance(tag, str)}


async def _bank_strategies(request: Request, provider: Any) -> frozenset[str]:
    """The retain strategies the platform bank defines, cached per app.

    The plugin names a strategy on every retain (``conversation``, ``git``,
    ...) because it used to configure its own bank with them. hal0 owns the
    platform bank's configuration, so a name is kept only when that bank
    really defines it; an unknown one is dropped and the bank's default
    strategy applies. A config read that fails counts as "no strategies" for a
    short while, which also drops the name.
    """
    bank = str(provider.platform_bank)
    cache = _state(request).strategies
    now = time.monotonic()
    hit = cache.get(bank)
    if hit is not None and hit[0] > now:
        return hit[1]
    names: set[str] = set()
    ttl = _STRATEGY_TTL_S
    try:
        payload = await provider.hindsight_client.request_json(
            "GET", _bank_path(provider, "/config"), timeout_s=_CONFIG_TIMEOUT_S
        )
    except Exception as exc:
        ttl = _STRATEGY_FAILURE_TTL_S
        log.warning("hal0.memory.compat.strategies_unreadable", error=type(exc).__name__)
    else:
        for section in ("config", "overrides"):
            strategies = payload.get(section, {}) if isinstance(payload, dict) else {}
            strategies = (
                strategies.get("retain_strategies") if isinstance(strategies, dict) else None
            )
            if isinstance(strategies, dict):
                names.update(name for name in strategies if isinstance(name, str))
    found = frozenset(names)
    cache[bank] = (now + ttl, found)
    return found


def _json(data: Any, headers: dict[str, str] | None = None, status_code: int = 200) -> Response:
    return JSONResponse(content=data, status_code=status_code, headers=headers or None)


# ── 1. version and health ────────────────────────────────────────────────────


@router.get("/version")
async def version(request: Request, agent: str = _CALLER) -> Response:
    """The engine's ``/version``. The plugin reads ``api_version`` from it
    before it trusts an idempotent retain."""
    out = await _forward(request, "GET", "/version", what="version", timeout_s=_PROBE_TIMEOUT_S)
    return out if isinstance(out, Response) else _json(out)


@router.get("/health")
async def health(request: Request, agent: str = _CALLER) -> Response:
    """The engine's ``/health``."""
    out = await _forward(request, "GET", "/health", what="health", timeout_s=_PROBE_TIMEOUT_S)
    return out if isinstance(out, Response) else _json(out)


# ── 2. retain ────────────────────────────────────────────────────────────────


def _retain_item(index: int, raw: Any, agent: str, strategies: frozenset[str]) -> dict[str, Any]:
    """One caller item, reduced to what ``HindsightProvider.retain_items`` takes."""
    if not isinstance(raw, dict):
        raise BadRequest(f"retain item {index} must be an object", details={"index": index})
    item = {key: raw[key] for key in _ITEM_PASSTHROUGH if raw.get(key) is not None}
    if "entities" in item and not isinstance(item["entities"], list):
        raise BadRequest(f"retain item {index} 'entities' must be a list", details={"index": index})
    if str(item.get("document_id", "")).startswith(_PRIVATE_DOCUMENT_PREFIX):
        # The unified bank keeps this prefix for private documents, and this
        # endpoint never writes one.
        raise BadRequest(
            f"retain item {index} 'document_id' may not start with {_PRIVATE_DOCUMENT_PREFIX!r}",
            code="memory.compat_document_id_reserved",
            details={"index": index},
        )
    if "tags" in item:
        item["tags"] = [
            tag
            for tag in coerce_tags(item["tags"])
            if not tag.strip().lower().startswith(_BRIDGE_SOURCE_TAG_PREFIX)
        ]
    strategy = raw.get("strategy")
    if isinstance(strategy, str) and strategy in strategies:
        item["strategy"] = strategy
    caller_metadata = raw.get("metadata")
    metadata = (
        {key: value for key, value in caller_metadata.items() if key not in _OWNED_METADATA}
        if isinstance(caller_metadata, dict)
        else {}
    )
    # ``project`` and ``project_source`` are written by the envelope from the
    # resolution, which is why a caller's own values were removed above.
    metadata["bridge_client"] = agent
    item["metadata"] = metadata
    return item


async def _refuse_foreign_documents(
    provider: Any, items: list[dict[str, Any]], scopes: list[_Scope]
) -> None:
    """Refuse a retain that would replace or extend a document outside the caller's project.

    The engine upserts on document id, per bank. The plugin picks its ids
    expecting a bank of its own (``conversation:<session>``, a slug of a
    document's title), and here every bank name is the one platform bank. So
    without this check two repositories that both retain ``readme`` would
    silently replace each other's document, and any client could overwrite a
    document hal0's own agents wrote by naming its id.

    Each item that names a document id is looked up first. An id the engine
    does not have is free. An existing document must already carry the tags
    this write would give it (:meth:`_Scope.write_tags`); otherwise the retain
    is refused whole with 409 ``memory.compat_document_id_taken``, which the
    plugin raises on. Engine failures propagate to the caller's handling.
    """
    wanted: dict[str, list[str]] = {}
    for item, scope in zip(items, scopes, strict=True):
        document_id = item.get("document_id")
        if document_id not in (None, ""):
            wanted.setdefault(str(document_id), scope.write_tags())

    async def _taken(document_id: str, required: list[str]) -> bool:
        path = _bank_path(provider, f"/documents/{quote(document_id, safe='')}")
        try:
            document = await provider.hindsight_client.request_json(
                "GET", path, timeout_s=_DOCUMENT_TIMEOUT_S
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise
        tags = document.get("tags") if isinstance(document, dict) else None
        return not _carries(tags, required)

    verdicts = await asyncio.gather(*(_taken(doc, tags) for doc, tags in wanted.items()))
    taken = [doc for doc, refused in zip(wanted, verdicts, strict=True) if refused]
    if taken:
        raise Conflict(
            "a document with this id already exists outside this client's project; "
            "choose another document_id",
            code="memory.compat_document_id_taken",
            details={"document_ids": taken[:10]},
        )


@router.post(_BANK + "/memories")
async def retain(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Retain: the engine's ``POST .../memories``, through the write envelope.

    Every item goes through ``HindsightProvider.retain_items`` as a coding
    client's write to the platform bank: exactly one ``agent:<caller>`` tag,
    the resolved ``project:`` tag in place of any the caller sent
    (``project:unresolved`` when there is none), session ids and lifecycle
    words moved out of the tags into metadata, and an observation scope hal0
    chooses. ``async`` and ``operation_id`` are sent to the engine as given,
    in one call, so the plugin's idempotent resend keeps working. The answer
    is the engine's own JSON.

    Before the retain, a document id an item names is looked up, and the
    retain is refused if that document exists outside the caller's project
    (:func:`_refuse_foreign_documents`).
    """
    _require_writes_allowed()
    provider = _provider(request)
    body = await _read_json(request, cap=MAX_RETAIN_BODY_BYTES)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise BadRequest("retain requires 'items' (a non-empty list)")
    if len(items) > MAX_RETAIN_ITEMS:
        raise CompatTooManyItems(
            f"a retain may carry at most {MAX_RETAIN_ITEMS} items",
            details={"max_items": MAX_RETAIN_ITEMS, "items": len(items)},
        )
    request_fields: dict[str, Any] = {}
    if "async" in body:
        if not isinstance(body["async"], bool):
            raise BadRequest("retain 'async' must be a boolean")
        request_fields["async_"] = body["async"]
    if body.get("operation_id") is not None:
        if not isinstance(body["operation_id"], str):
            raise BadRequest("retain 'operation_id' must be a string")
        request_fields["operation_id"] = body["operation_id"]

    named_strategy = any(isinstance(i, dict) and i.get("strategy") is not None for i in items)
    strategies = await _bank_strategies(request, provider) if named_strategy else frozenset()
    wire_items = [_retain_item(i, raw, agent, strategies) for i, raw in enumerate(items)]

    ignored = {name for name in body if name not in _RETAIN_FIELDS}
    for raw, item in zip(items, wire_items, strict=True):
        ignored.update(f"items.{name}" for name in raw if name not in _ITEM_FIELDS)
        if raw.get("strategy") is not None and "strategy" not in item:
            ignored.add("items.strategy")

    # The bank in the URL decides the project for every item. When it names
    # none the provider resolves each item from its own ``project:`` tags;
    # the header then reports what all of them together resolve to.
    bank_scope = _scope(provider, bank, agent)
    every_tag = [tag for item in wire_items for tag in item.get("tags", ())]
    headers = {**_scope(provider, bank, agent, every_tag).headers(), **_ignored(ignored)}

    # The same rule every other hal0 writer runs first (#1903): an extraction
    # slot too small for the extraction prompt can never process a retain.
    await extraction_ctx_preflight(request.app, provider)

    synchronous = request_fields.get("async_") is not True
    item_scopes = [_scope(provider, bank, agent, item.get("tags", ())) for item in wire_items]
    try:
        await _refuse_foreign_documents(provider, wire_items, item_scopes)
        out = await provider.retain_items(
            wire_items,
            agent=agent,
            project_resolution=bank_scope.resolution,
            coding=True,
            timeout_s=_RETAIN_SYNC_TIMEOUT_S if synchronous else _RETAIN_ASYNC_TIMEOUT_S,
            **request_fields,
        )
    except ValueError as exc:
        # The provider's own refusal of a reserved document id; a backstop to
        # the check in :func:`_retain_item`.
        raise BadRequest(str(exc), code="memory.compat_document_id_reserved") from exc
    except Exception as exc:
        return _engine_failure(exc, what="the retain", headers=headers, not_found_is_gateway=True)
    return _json(out.get("engine_response") or {}, headers)


# ── 3. reflect ───────────────────────────────────────────────────────────────


@router.post(_BANK + "/reflect")
async def reflect(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Reflect: the engine's ``POST .../reflect``, held to the caller's scope.

    ``HindsightProvider.reflect`` gives the engine the visibility rule for
    this agent, and leaves mental models out while the bank holds other
    agents' private memory. The project scope is added to that as one more
    group, and so are the caller's own ``tags``. The engine call is cancelled
    when the client disconnects.
    """
    provider = _provider(request)
    body = await _read_json(request, cap=MAX_QUERY_BODY_BYTES)
    query = _query(body, "reflect")
    tags, tags_match = _caller_filter(body, "reflect")
    extra: dict[str, Any] = {}
    if body.get("budget") is not None:
        if not isinstance(body["budget"], str):
            raise BadRequest("reflect 'budget' must be a string")
        extra["budget"] = body["budget"]
    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        if not _positive_int(max_tokens):
            raise BadRequest("reflect 'max_tokens' must be a positive integer")
        extra["max_tokens"] = max_tokens
    if body.get("response_schema") is not None:
        if not isinstance(body["response_schema"], dict):
            raise BadRequest("reflect 'response_schema' must be an object")
        extra["response_schema"] = body["response_schema"]

    scope = _scope(provider, bank, agent, tags or ())
    headers = {**scope.headers(), **_ignored(n for n in body if n not in _REFLECT_FIELDS)}
    call = provider.reflect(
        query,
        client_id=agent,
        tags=tags,
        tags_match=tags_match,
        tag_groups=[*scope.project_groups(), *_caller_groups(body, "reflect")],
        **extra,
    )
    try:
        result = await _until_disconnect(request, call)
    except _ClientGone:
        log.info("hal0.memory.compat.reflect_abandoned", client=agent)
        return Response(status_code=499)
    except Exception as exc:
        return _engine_failure(exc, what="the reflect", headers=headers)
    return _json(result if isinstance(result, dict) else {"text": str(result)}, headers)


# ── 4. recall ────────────────────────────────────────────────────────────────


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


# The RecallRequest fields passed to the engine as sent: ``(name, shape check,
# what the 400 says)``. The engine validates their values.
_RECALL_KNOBS: tuple[tuple[str, Callable[[Any], bool], str], ...] = (
    (
        "types",
        lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v),
        "a list of strings",
    ),
    ("budget", lambda v: isinstance(v, str), "a string"),
    ("max_tokens", _positive_int, "a positive integer"),
    ("query_timestamp", lambda v: isinstance(v, str), "a string"),
    ("min_scores", lambda v: isinstance(v, dict), "an object"),
    ("prefer_observations", lambda v: isinstance(v, bool), "a boolean"),
)


def _scrub_recall(response: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """The engine's recall answer, with what the filter cannot vouch for removed.

    The engine was given ``groups`` and returned only matching results; each
    one is checked against the same groups again here, which costs nothing
    when the engine did its part. The parts of the answer that sit beside the
    results are joined to them and otherwise dropped:

    * ``entities`` is bank-wide state, built from every project's and every
      agent's facts, and says nothing about where it came from: removed;
    * ``source_facts`` are kept when a surviving result names them and their
      own tags are inside the groups;
    * ``chunks`` are kept when a surviving result or source fact names them;
    * ``trace`` is never requested and is removed if present.
    """
    out = dict(response)
    raw_results = response.get("results")
    results = [
        r
        for r in (raw_results if isinstance(raw_results, list) else [])
        if isinstance(r, dict) and tags_readable(groups, r.get("tags") or [])
    ]
    out["results"] = results
    out.pop("trace", None)
    if "entities" in out:
        out["entities"] = None
    named = {
        str(i)
        for r in results
        if isinstance(r.get("source_fact_ids"), list)
        for i in r["source_fact_ids"]
    }
    chunk_ids = {str(r["chunk_id"]) for r in results if r.get("chunk_id")}
    source_facts = response.get("source_facts")
    if isinstance(source_facts, dict):
        kept = {
            key: fact
            for key, fact in source_facts.items()
            if str(key) in named
            and isinstance(fact, dict)
            and tags_readable(groups, fact.get("tags") or [])
        }
        out["source_facts"] = kept
        chunk_ids.update(str(f["chunk_id"]) for f in kept.values() if f.get("chunk_id"))
    chunks = response.get("chunks")
    if isinstance(chunks, dict):
        out["chunks"] = {key: chunk for key, chunk in chunks.items() if str(key) in chunk_ids}
    return out


@router.post(_BANK + "/memories/recall")
async def recall(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Recall: the engine's ``POST .../memories/recall``, in the engine's shape.

    Unlike ``POST /api/memory/recall`` the answer is not reshaped into hal0's
    items: ``HindsightProvider.recall_raw`` returns what the engine returned
    for the platform bank, filtered by the engine to the same groups a
    reflect gets. :func:`_scrub_recall` then removes what a tag filter cannot
    vouch for.
    """
    provider = _provider(request)
    body = await _read_json(request, cap=MAX_QUERY_BODY_BYTES)
    query = _query(body, "recall")
    tags, tags_match = _caller_filter(body, "recall")
    extra: dict[str, Any] = {}
    for name, shape, expected in _RECALL_KNOBS:
        value = body.get(name)
        if value is None:
            continue
        if not shape(value):
            raise BadRequest(f"recall '{name}' must be {expected}")
        extra[name] = value
    include = body.get("include")
    wanted = include if isinstance(include, dict) else {}
    # Entities are on by default in the engine and are bank-wide; ``null``
    # turns them off. Chunks and source facts are the caller's choice.
    extra["include"] = {
        **{key: wanted[key] for key in ("chunks", "source_facts") if key in wanted},
        "entities": None,
    }

    scope = _scope(provider, bank, agent, tags or ())
    headers = {**scope.headers(), **_ignored(n for n in body if n not in _RECALL_FIELDS)}
    try:
        result = await provider.recall_raw(
            query,
            client_id=agent,
            tags=tags,
            tags_match=tags_match,
            tag_groups=[*scope.project_groups(), *_caller_groups(body, "recall")],
            timeout_s=_RECALL_TIMEOUT_S,
            **extra,
        )
    except Exception as exc:
        return _engine_failure(exc, what="the recall", headers=headers)
    return _json(_scrub_recall(result, scope.read_groups()), headers)


# ── 5 and 6. documents ───────────────────────────────────────────────────────


def _document_filter(request: Request, scope: _Scope) -> tuple[list[str], list[str]]:
    """``(tags to send, tags every returned document must carry)``.

    The engine's documents listing takes ONE flat tag set and ONE match mode
    (``tags`` and ``tags_match``; it has no ``tag_groups``). The strictest
    filter that can say is "carries all of these", so the listing is held to:

    * the caller's project tag, always: the listing shows this project's
      documents, not global ones, because "this project OR global" cannot be
      AND-ed with the caller's own tags in one flat set;
    * for an unresolved caller, its own ``agent:`` tag as well, since
      ``project:unresolved`` is shared by every repository's unresolved
      writes;
    * the caller's own tags, which only narrow it. A ``project:`` tag among
      them is replaced by the resolved one.

    The caller's ``tags_match`` must therefore mean "all of them": ``all`` /
    ``all_strict``, or any mode over at most one tag. ``exact`` and a
    several-tag ``any`` are refused, because sending them on would turn the
    required tags into alternatives.
    """
    caller = [
        tag
        for tag in request.query_params.getlist("tags")
        if tag.strip() and not tag.strip().lower().startswith(PROJECT_TAG_PREFIX)
    ]
    match = request.query_params.get("tags_match") or "any_strict"
    conjunctive = match in ("all", "all_strict") or (
        match in ("any", "any_strict") and len(set(caller)) <= 1
    )
    if not conjunctive:
        raise BadRequest(
            "the documents listing here supports tags_match 'all' or 'all_strict' "
            "(or a single tag): it is always narrowed to the caller's project",
            code="memory.compat_filter_unsupported",
            details={"tags_match": match},
        )
    required = [scope.project_tag]
    if scope.slug is None:
        required.append(scope.agent_tag)
    return list(dict.fromkeys([*required, *caller])), required


@router.get(_BANK + "/documents")
async def list_documents(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """List documents: the engine's ``GET .../documents``, for this project.

    See :func:`_document_filter` for the filter the engine is given. What it
    cannot express is "and not another agent's private document", so the
    returned page is filtered in hal0 as well: an item that is
    ``visibility:private`` to someone else, or that lacks a required tag, is
    removed. A page can therefore hold fewer than ``limit`` items while more
    follow, and ``total`` is the engine's count before that step;
    ``X-hal0-Filtered-Items`` says how many were removed.
    """
    provider = _provider(request)
    scope = _scope(provider, bank, agent, request.query_params.getlist("tags"))
    tags, required = _document_filter(request, scope)
    params: dict[str, Any] = {"tags": tags, "tags_match": "all_strict"}
    for name in ("limit", "offset", "q"):
        value = request.query_params.get(name)
        if value is not None:
            params[name] = value
    headers = scope.headers()
    out = await _forward(
        request,
        "GET",
        _bank_path(provider, "/documents"),
        what="the documents listing",
        timeout_s=_DOCUMENT_TIMEOUT_S,
        params=params,
        headers=headers,
    )
    if isinstance(out, Response):
        return out
    page = dict(out) if isinstance(out, dict) else {}
    visible = reflect_visibility_scope(agent)
    items = page.get("items")
    listed: list[Any] = items if isinstance(items, list) else []
    kept = [
        doc
        for doc in listed
        if isinstance(doc, dict)
        and _carries(doc.get("tags"), required)
        and tags_readable(visible, doc["tags"])
    ]
    page["items"] = kept
    if len(kept) != len(listed):
        headers[FILTERED_ITEMS_HEADER] = str(len(listed) - len(kept))
    return _json(page, headers)


@router.delete(_BANK + "/documents/{document_id:path}")
async def delete_document(
    bank: str, document_id: str, request: Request, agent: str = _CALLER
) -> Response:
    """Delete one document, when it is the caller's own in the caller's project.

    The document is read first. It is deleted only when it carries both
    ``agent:<caller>`` and the caller's project tag (``project:unresolved``
    for a caller with no project); anything else is 403
    ``memory.compat_document_not_owned``. A document the engine does not have
    is the engine's 404.
    """
    _require_writes_allowed()
    provider = _provider(request)
    scope = _scope(provider, bank, agent)
    headers = scope.headers()
    path = _bank_path(provider, f"/documents/{quote(document_id, safe='')}")
    document = await _forward(
        request,
        "GET",
        path,
        what="the document read",
        timeout_s=_DOCUMENT_TIMEOUT_S,
        headers=headers,
    )
    if isinstance(document, Response):
        return document
    tags = document.get("tags") if isinstance(document, dict) else None
    if not _carries(tags, (scope.agent_tag, scope.project_tag)):
        raise Forbidden(
            "this document was not written by this client in this project",
            code="memory.compat_document_not_owned",
            details={"project": scope.slug or UNRESOLVED_SLUG},
        )
    try:
        # The engine's own failure is raised inside the audit block, so the
        # row records it; it becomes this endpoint's answer outside.
        async with _audited_delete(request, agent, document_id):
            out = await provider.hindsight_client.request_json(
                "DELETE", path, timeout_s=_DOCUMENT_TIMEOUT_S
            )
    except Exception as exc:
        return _engine_failure(exc, what="the document delete", headers=headers)
    return _json(out, headers)


@contextlib.asynccontextmanager
async def _audited_delete(request: Request, agent: str, document_id: str) -> AsyncIterator[None]:
    """Record a document delete in the audit store, with the real caller.

    ``hal0.api._audit.record_action`` takes the actor from ``X-hal0-Agent``,
    which this endpoint does not trust, so the row is written with the
    credential's identity instead.
    """
    store = getattr(request.app.state, "audit", None)
    if store is None:
        yield
        return
    async with audit_action(
        store,
        category="memory",
        action="memory.document.delete",
        target=document_id,
        actor=f"bridge:{agent}",
        message="engine-compatible memory endpoint",
        request_id=request.headers.get("X-Request-ID"),
    ):
        yield


# ── 7. operations ────────────────────────────────────────────────────────────


def _pick(record: Any, fields: Iterable[str]) -> dict[str, Any]:
    source = record if isinstance(record, dict) else {}
    return {key: source[key] for key in fields if key in source}


def _operation_view(record: Any, *, with_error: bool) -> dict[str, Any]:
    """One operation as a status poll needs it, and nothing a client submitted.

    ``error_message`` is kept only on a read by id: the caller of that read
    chose the operation, while the list covers every client's.
    """
    view = _pick(record, _OPERATION_FIELDS)
    source = record if isinstance(record, dict) else {}
    if with_error and "error_message" in source:
        view["error_message"] = source["error_message"]
    if isinstance(source.get("progress"), dict):
        view["progress"] = _pick(source["progress"], _PROGRESS_FIELDS)
    children = source.get("child_operations")
    if isinstance(children, list):
        view["child_operations"] = [_pick(child, _CHILD_FIELDS) for child in children]
    return view


@router.get(_BANK + "/operations")
async def list_operations(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """The platform bank's operations, as statuses only."""
    provider = _provider(request)
    params = {
        name: request.query_params[name]
        for name in ("status", "type", "limit", "offset", "exclude_parents")
        if name in request.query_params
    }
    headers = _scope(provider, bank, agent).headers()
    out = await _forward(
        request,
        "GET",
        _bank_path(provider, "/operations"),
        what="the operations listing",
        timeout_s=_OPERATION_TIMEOUT_S,
        params=params or None,
        headers=headers,
    )
    if isinstance(out, Response):
        return out
    page = _pick(out, _OPERATION_LIST_FIELDS)
    listed = out.get("operations") if isinstance(out, dict) else None
    page["operations"] = [
        _operation_view(op, with_error=False) for op in (listed if isinstance(listed, list) else [])
    ]
    return _json(page, headers)


@router.get(_BANK + "/operations/{operation_id}")
async def get_operation(
    bank: str, operation_id: str, request: Request, agent: str = _CALLER
) -> Response:
    """One operation's status. The submitted payload is never requested
    (``include_payload=false``) and never returned."""
    provider = _provider(request)
    headers = _scope(provider, bank, agent).headers()
    out = await _forward(
        request,
        "GET",
        _bank_path(provider, f"/operations/{quote(operation_id, safe='')}"),
        what="the operation read",
        timeout_s=_OPERATION_TIMEOUT_S,
        params={"include_payload": "false"},
        headers=headers,
    )
    if isinstance(out, Response):
        return out
    return _json(_operation_view(out, with_error=True), headers)


# ── 8 to 10. bank configuration and the bank itself ──────────────────────────


@router.get(_BANK + "/config")
async def bank_config(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """The platform bank's missions, in the engine's config shape.

    The engine's answer is the fully resolved configuration of the bank. This
    endpoint returns the three mission keys the plugin reads from it, under
    ``config`` and ``overrides``, and nothing else: a bridge key with the
    ``memory`` scope has no use for the rest of the engine's configuration.
    """
    provider = _provider(request)
    headers = _scope(provider, bank, agent).headers()
    out = await _forward(
        request,
        "GET",
        _bank_path(provider, "/config"),
        what="the bank configuration read",
        timeout_s=_CONFIG_TIMEOUT_S,
        headers=headers,
    )
    if isinstance(out, Response):
        return out
    source = out if isinstance(out, dict) else {}
    view = {
        "bank_id": source.get("bank_id", provider.platform_bank),
        "config": _pick(source.get("config"), _CONFIG_FIELDS),
        "overrides": _pick(source.get("overrides"), _CONFIG_FIELDS),
    }
    return _json(view, headers)


@router.post(_BANK + "/import")
async def import_template(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Accept a bank template and do nothing with it.

    The plugin posts its template on every configure pass and raises on a
    refusal. hal0 owns the platform bank's missions, strategies and
    directives, so the template is not applied; the answer says so.
    """
    noted = _state(request).import_noted
    if agent not in noted:
        noted.add(agent)
        log.info("hal0.memory.compat.import_ignored", client=agent, bank=bank)
    return _json({"status": "ignored", "reason": "bank configuration is managed by hal0"})


@router.delete(_BANK)
async def delete_bank(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Refuse: every bank name here is the platform bank, shared by all agents."""
    raise Forbidden(
        "a bank cannot be deleted through this endpoint: every bank name maps to "
        "the shared platform bank",
        code="memory.compat_bank_delete_refused",
    )


# ── 11. knowledge base ───────────────────────────────────────────────────────


def knowledge_base_unsupported(method: str, path: str) -> NotFound:
    """The answer to every knowledge-base route, reads and writes: 404.

    The plugin reads 404 on its first knowledge-base call as "this server has
    no pages" and carries on without them, which is what is wanted until
    pages are mapped to per-project folders. That mapping replaces this one
    function.
    """
    return NotFound(
        "knowledge pages are not available through this endpoint",
        code="memory.compat_knowledge_base_unsupported",
        details={"method": method, "path": path},
    )


@router.api_route(
    _BANK + "/knowledge-base", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
)
@router.api_route(
    _BANK + "/knowledge-base/{rest:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
)
async def knowledge_base(bank: str, request: Request, agent: str = _CALLER) -> Response:
    raise knowledge_base_unsupported(request.method, request.url.path)


# ── 12. everything else ──────────────────────────────────────────────────────


@router.api_route("/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def unsupported(rest: str, request: Request, agent: str = _CALLER) -> Response:
    """Any route this endpoint does not serve.

    A read is 404 with the supported routes in the details. A mutation is
    405: the plugin raises on that, so an unsupported write is visible in its
    log instead of being counted as done.
    """
    details = {"method": request.method, "path": f"/{rest}", "supported": list(SUPPORTED_ROUTES)}
    if request.method in ("GET", "HEAD"):
        raise NotFound(
            "the engine-compatible memory endpoint does not serve this route",
            code="memory.compat_route_unsupported",
            details=details,
        )
    raise CompatMutationUnsupported(
        "the engine-compatible memory endpoint does not accept this request", details=details
    )


__all__ = [
    "ADMIN_AGENT",
    "MAX_RETAIN_BODY_BYTES",
    "MAX_RETAIN_ITEMS",
    "MEMORY_SCOPE",
    "PROJECT_HEADER",
    "SUPPORTED_ROUTES",
    "knowledge_base_unsupported",
    "require_memory_bridge",
    "router",
]
