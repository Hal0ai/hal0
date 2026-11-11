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

Two more follow from "every bank is one bank". A document id a client sends
is stored inside that client's namespace of its project
(:mod:`hal0.memory.document_namespace`) and shown back without it, so no
client can replace, extend or delete a document it did not write. And an
asynchronous retain's operation id is derived from the client and the id it
sent (:func:`_engine_operation_id`), so a client can poll its own operations
and no one else's. A write here is never global: caller ``scope:`` tags are
dropped.

Sharing a project shares content, not identity: of a memory or a shared
document the caller did not write, a read returns an allow-list
(:func:`_recall_row`, :func:`_listing_row`), which leaves out the writer's
name, document id, session id, context and metadata.

What one request and one client may cost is bounded: see the constants below
and ``[memory.compat]``. The counters live in this process
(:class:`_CompatState`) and start empty when it restarts.

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
import hashlib
import importlib
import json
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
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
from hal0.config.schema import MemoryCompatConfig, MemoryLanesConfig
from hal0.errors import (
    BadRequest,
    Forbidden,
    Hal0Error,
    MemoryEngineUnavailable,
    NotFound,
    Unauthorized,
)
from hal0.memory.document_namespace import (
    CLIENT_DOCUMENT_ID_KEY,
    chunk_id_part,
    is_digested,
    is_namespaced,
    readable_prefixes,
    stored_document_id,
    strip_stored_prefixes,
    visible_document_id,
)
from hal0.memory.envelope import (
    AGENT_TAG_PREFIX,
    PROJECT_TAG_PREFIX,
    PROJECT_UNRESOLVED_TAG,
    SCOPE_GLOBAL_TAG,
    SCOPE_TAG_PREFIX,
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

#: Most items one retain may carry, and the largest retain body read. The
#: body cap leaves room for one item of the largest configurable size
#: (``[memory.compat].max_item_bytes``, at most 32 MiB) with its JSON escapes.
MAX_RETAIN_ITEMS = 32
MAX_RETAIN_BODY_BYTES = 48 * 1024 * 1024
#: The largest body read on the query routes (reflect, recall).
MAX_QUERY_BODY_BYTES = 1024 * 1024

#: The engine's ``Budget`` enum, for reflect and recall.
BUDGETS = ("low", "mid", "high")
#: The most answer tokens a reflect, and the most result tokens a recall, may
#: ask for. A larger ``max_tokens`` is lowered to this, not refused.
MAX_REFLECT_TOKENS = 8192
MAX_RECALL_TOKENS = 16384
#: The largest documents page: the plugin's own page size.
MAX_DOCUMENTS_LIMIT = 500
_DEFAULT_DOCUMENTS_LIMIT = 100
#: The longest caller-chosen ``operation_id`` and the longest
#: ``error_message`` returned for an operation.
MAX_OPERATION_ID_LENGTH = 200
MAX_ERROR_MESSAGE_LENGTH = 500

#: How many requests of one kind one client may have in flight. A reflect
#: holds the engine's language model for minutes; a retain holds its body in
#: memory. Beyond the limit the answer is 429 with ``Retry-After``, which the
#: plugin honours. The limit across all clients' reflects is
#: ``[memory.compat].max_concurrent_reflects``.
REFLECTS_PER_CLIENT = 1
RECALLS_PER_CLIENT = 2
RETAINS_PER_CLIENT = 4
_REFLECT_RETRY_AFTER_S = 15
_RECALL_RETRY_AFTER_S = 5
_RETAIN_RETRY_AFTER_S = 2

#: A retain body up to this size is read without waiting on anyone; a larger
#: one takes one of :data:`LARGE_RETAIN_BODIES` slots, across all clients,
#: and one client holds at most :data:`LARGE_RETAIN_BODIES_PER_CLIENT` of
#: them. That bounds what retain bodies hold in hal0-api's memory: a large
#: body is at most 48 MiB as received, about as much again parsed, and once
#: more while the engine request is written, so two at once stay near
#: 300 MiB; small ones add at most 1 MiB each, :data:`RETAINS_PER_CLIENT`
#: per client.
SMALL_RETAIN_BODY_BYTES = 1024 * 1024
LARGE_RETAIN_BODIES = 2
LARGE_RETAIN_BODIES_PER_CLIENT = 1

#: When a client is at its quota of unfinished asynchronous retains: how many
#: of its oldest are read from the engine before it is refused, how many
#: status reads run at once, how long before the engine is asked again for
#: the same client, and what the refusal says to wait.
_QUOTA_STATUS_READS = 16
_OPERATION_READS_AT_ONCE = 4
_QUOTA_RECHECK_S = 5.0
_QUOTA_RETRY_AFTER_S = 30

#: How many operations one client's listing reads fresh from the engine, the
#: largest listing page, and how many operations are remembered per client.
_LISTING_STATUS_READS = 64
MAX_OPERATIONS_LIMIT = 100
OPERATIONS_REMEMBERED_PER_CLIENT = 2000
_EVERY_CLIENT = "*"

#: Namespace of the engine operation ids this endpoint derives
#: (:func:`_engine_operation_id`). Fixed: changing it orphans every operation
#: a client is still polling.
_OPERATION_ID_NAMESPACE = uuid.UUID("6b0f5d1e-3c8a-5e47-9d52-a1c4e7f20b93")

_DEFAULT_SETTINGS = MemoryCompatConfig()
_DEFAULT_LANES = MemoryLanesConfig()

PROJECT_HEADER = "X-hal0-Project"
IGNORED_FIELDS_HEADER = "X-hal0-Ignored-Fields"
FILTERED_ITEMS_HEADER = "X-hal0-Filtered-Items"
INCOMPLETE_LISTING_HEADER = "X-hal0-Listing-Incomplete"

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
#: Metadata keys hal0 writes on a retain here; a caller's value is dropped.
_OWNED_METADATA = frozenset(
    {"bridge_client", "project", "project_source", "client_source", CLIENT_DOCUMENT_ID_KEY}
)
#: A tag namespace the bridge's own memory tool stamps as provenance
#: (``hal0.bridge.service``); a caller of this endpoint may not forge it.
_BRIDGE_SOURCE_TAG_PREFIX = "source:bridge:"

#: How many more engine pages a documents listing reads to fill a page that
#: hal0's own filter shortened.
_LISTING_EXTRA_FETCHES = 3
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
_CHILD_FIELDS = ("status", "sub_batch_index", "items_count")

# What is returned of a memory, and of a document, the caller did not write:
# an allow-list. Not on it, on purpose: ``metadata`` / ``document_metadata``
# (the writer's client name, its own document id, its session id),
# ``context`` and ``retain_params`` (the writer's name by default),
# ``entities``, the stored ``document_id``, and everything the engine may add
# later. What is on it: the engine's own id for the row, the content, what
# type of memory it is, and when.
_FOREIGN_RESULT_FIELDS = ("id", "text", "type", "occurred_start", "occurred_end", "mentioned_at")
_FOREIGN_DOCUMENT_FIELDS = ("created_at", "updated_at")
#: Tag namespaces that describe a memory without naming who wrote it.
_DESCRIPTIVE_TAG_PREFIXES = (PROJECT_TAG_PREFIX, "kind:", "knowledge:", "source:")

# A reflect answer as returned: ``based_on`` and ``trace`` name facts and
# documents by their stored ids, are never requested, and are not passed on.
_REFLECT_RESPONSE_FIELDS = ("text", "structured_output", "structured_output_error", "usage")

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


class CompatItemTooLarge(Hal0Error):
    code = "memory.compat_item_too_large"
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
      its client name. A key under a name hal0 reserves (``hal0.bridge.keys``),
      which one minted before the reservation could carry, never gets here:
      the middleware refuses it on every non-OPEN path with 403
      ``bridge.client_name_reserved`` (:func:`hal0.api.auth.reserved_bridge_principal`);
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


#: An operation status after which the engine does no more work on it.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "error", "not_found"})


@dataclass(slots=True)
class _Operation:
    """One asynchronous retain a client started, as this process knows it."""

    visible_id: str
    size: int
    #: The engine has answered the retain that started it. Until then it is
    #: counted, and the engine is not asked about it: it would not know it.
    accepted: bool = False
    #: The engine has finished with it (or no longer has it).
    done: bool = False
    #: Its last status, as the client is shown it; ``None`` until first read.
    view: dict[str, Any] | None = None


@dataclass(slots=True)
class _CompatState:
    """What this router keeps per app, in memory.

    The strategy cache and the import log are conveniences. ``operations``
    and ``in_flight`` are per process: hal0-api serves this endpoint from one
    process, and both are rebuilt empty when it restarts. After a restart the
    operations a client started before it are not known here, so they are
    not listed and do not count toward its quota (:func:`_admit_operation`);
    a poll by id still answers (:func:`get_operation`).
    """

    strategies: dict[str, tuple[float, frozenset[str]]] = field(default_factory=dict)
    import_noted: set[str] = field(default_factory=set)
    #: Client -> engine operation id -> the operation, oldest first. Kept per
    #: client so that one client's volume cannot push out another's entries.
    operations: dict[str, OrderedDict[str, _Operation]] = field(default_factory=dict)
    #: Clients whose oldest remembered operations have been dropped.
    forgotten: set[str] = field(default_factory=set)
    #: ``(kind, client)`` -> requests in flight.
    in_flight: dict[tuple[str, str], int] = field(default_factory=dict)
    #: Client -> when its outstanding operations were last read from the
    #: engine for the quota (``time.monotonic``).
    quota_read_at: dict[str, float] = field(default_factory=dict)

    def operations_of(self, client: str) -> OrderedDict[str, _Operation]:
        return self.operations.setdefault(client, OrderedDict())

    def remember_operation(self, client: str, engine_id: str, visible_id: str, size: int) -> None:
        mine = self.operations_of(client)
        if engine_id in mine:
            return  # a resend of the same operation: the engine starts nothing new
        mine[engine_id] = _Operation(visible_id=visible_id, size=size)
        while len(mine) > OPERATIONS_REMEMBERED_PER_CLIENT:
            # Finished ones go first, so what is dropped is history, not quota.
            oldest = next((key for key, op in mine.items() if op.done), next(iter(mine)))
            del mine[oldest]
            self.forgotten.add(client)

    def outstanding(self, client: str) -> list[tuple[str, _Operation]]:
        return [(key, op) for key, op in self.operations_of(client).items() if not op.done]


class _Busy(Exception):
    """A client, or the endpoint as a whole, is at a limit; try again later."""

    def __init__(self, what: str, retry_after_s: int) -> None:
        super().__init__(what)
        self.what = what
        self.retry_after_s = retry_after_s

    def response(self, headers: dict[str, str] | None = None) -> Response:
        """429 in the standard error envelope, with ``Retry-After``."""
        body = {
            "error": {
                "code": "memory.compat_busy",
                "message": f"{self.what}; retry shortly",
                "details": {"retry_after_s": self.retry_after_s},
            }
        }
        return JSONResponse(
            content=body,
            status_code=429,
            headers={**(headers or {}), "Retry-After": str(self.retry_after_s)},
        )


@contextlib.contextmanager
def _in_flight(
    state: _CompatState, kind: str, client: str, limit: int, retry_after_s: int
) -> Iterator[None]:
    """Hold one of ``client``'s ``limit`` slots of ``kind``, or raise :class:`_Busy`."""
    key = (kind, client)
    if state.in_flight.get(key, 0) >= limit:
        whose = "in flight" if client == _EVERY_CLIENT else "in flight from this client"
        raise _Busy(f"too many {kind} requests {whose}", retry_after_s)
    state.in_flight[key] = state.in_flight.get(key, 0) + 1
    try:
        yield
    finally:
        left = state.in_flight.get(key, 1) - 1
        if left > 0:
            state.in_flight[key] = left
        else:
            state.in_flight.pop(key, None)


def _settings(request: Request) -> MemoryCompatConfig:
    """``[memory.compat]`` as hal0-api holds it, or the shipped defaults."""
    config = getattr(request.app.state, "hal0_config", None)
    compat = getattr(getattr(config, "memory", None), "compat", None)
    return compat if isinstance(compat, MemoryCompatConfig) else _DEFAULT_SETTINGS


def _reflect_limit(request: Request) -> int:
    """How many reflects this endpoint runs at once, across all clients.

    ``[memory.compat].max_concurrent_reflects``, lowered so that it always
    leaves one of the engine's language-model slots to callers that do not
    come through this endpoint (hal0's own agents, the dashboard). The engine
    runs ``[memory.lanes].llm_max_concurrent`` model calls at once, and a
    reflect's at most ``reflect.max_concurrent`` of those when that is set;
    nothing in the engine reserves one. Where it has a single slot (the
    shipped default) there is none to leave: one reflect runs here at a
    time, and since the engine's limit is per model call, other callers'
    calls take their turn between a reflect's steps. Lanes hal0-api cannot
    read are taken to be the shipped ones.
    """
    configured = _settings(request).max_concurrent_reflects
    lanes = getattr(
        getattr(getattr(request.app.state, "hal0_config", None), "memory", None), "lanes", None
    )
    caps = [
        cap
        for cap in (
            getattr(lanes, "llm_max_concurrent", None),
            getattr(getattr(lanes, "reflect", None), "max_concurrent", None),
        )
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1
    ]
    engine_slots = min(caps) if caps else _DEFAULT_LANES.llm_max_concurrent
    return min(configured, max(1, engine_slots - 1))


def _engine_operation_id(client: str, caller_id: str) -> str:
    """The engine's id for the operation ``client`` calls ``caller_id``.

    A UUID (the engine requires one) derived from both, so the same client
    resending the same id still names the same operation, which is what makes
    the plugin's resend idempotent, while no id one client chooses can name,
    claim in advance or probe another client's operation, or one hal0's own
    agents started.
    """
    return str(uuid.uuid5(_OPERATION_ID_NAMESPACE, f"{client}\n{caller_id}"))


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
    shared_prefixes: tuple[str, ...] = ()

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

    def id_prefixes(self) -> tuple[str, ...]:
        """The stored-id prefixes this caller may list and address: its own
        namespace and, inside a project, the shared one
        (:mod:`hal0.memory.document_namespace`)."""
        return readable_prefixes(self.slug, self.agent)

    def stored_id(self, client_id: str) -> str:
        """The id the engine knows a document of this caller by."""
        return stored_document_id(self.slug, self.agent, client_id, self.shared_prefixes)

    def visible_id(self, stored_id: Any, metadata: Any = None) -> str | None:
        """The caller's own id for a stored document; ``None`` if the document
        is in neither of its namespaces."""
        return visible_document_id(self.slug, self.agent, stored_id, metadata, self.shared_prefixes)

    def project_groups(self) -> list[dict[str, Any]]:
        if self.slug:
            return project_scope_groups(self.slug)
        return unresolved_scope_groups(self.agent)

    def read_groups(self) -> list[dict[str, Any]]:
        """Everything this caller may read: visibility AND the project scope."""
        return [*reflect_visibility_scope(self.agent), *self.project_groups()]


def _scope(
    request: Request, provider: Any, bank: str, agent: str, tags: Iterable[Any] = ()
) -> _Scope:
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
    return _Scope(
        agent=agent,
        resolution=resolution,
        shared_prefixes=tuple(_settings(request).shared_document_prefixes),
    )


# ── reading the request ──────────────────────────────────────────────────────


async def _read_json_sized(request: Request, *, cap: int) -> tuple[dict[str, Any], int]:
    """The JSON object in the body and its size in bytes, read with a hard cap.

    The body is streamed and counted, so a request without (or with a false)
    ``Content-Length`` is cut off at ``cap`` bytes too. It is collected in one
    buffer and parsed from it once; the buffer is gone when this returns, so
    what the handler holds is the parsed object alone.
    """
    declared = request.headers.get("content-length", "")
    too_large = CompatBodyTooLarge(
        f"request body is larger than {cap} bytes", details={"max_bytes": cap}
    )
    if declared.isdigit() and int(declared) > cap:
        raise too_large
    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > cap:
            raise too_large
        buffer += chunk
    size = len(buffer)
    try:
        body = json.loads(buffer or b"null")
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON", code="request.invalid_json") from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.invalid_json")
    return body, size


async def _read_json(request: Request, *, cap: int) -> dict[str, Any]:
    body, _size = await _read_json_sized(request, cap=cap)
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
    hidden: Iterable[str] = (),
) -> Response:
    """Turn a failed engine call into this endpoint's answer.

    Returns the response to pass on for an engine 4xx, raises a typed error
    for everything else, and re-raises an exception that is not the engine's.

    * An engine 4xx goes to the caller with the engine's body (and its
      ``Retry-After``): a 409 on a reused operation id, a 422 on a malformed
      field and a 429 are the caller's to act on. The engine names documents
      by their stored ids in such a body, so the id prefixes in ``hidden``
      are removed from it first.
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
        content = exc.response.content
        prefixes = [prefix for prefix in dict.fromkeys(hidden) if prefix]
        if prefixes:
            text = content.decode("utf-8", "replace")
            for prefix in prefixes:
                text = text.replace(prefix, "")
            content = text.encode("utf-8")
        return Response(
            content=content,
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
    hidden: Iterable[str] = (),
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
        return _engine_failure(exc, what=what, headers=headers, hidden=hidden)


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


def _dropped_tag(tag: str) -> bool:
    """A caller tag this endpoint never writes.

    ``scope:`` would make the write global (``scope:global`` is read by every
    project and every unresolved caller), and ``source:bridge:`` is the
    provenance stamp of the bridge's own memory tool. The provider and the
    envelope drop or replace ``agent:``, ``visibility:`` and ``project:``.
    ``kind:`` stays the caller's choice: it widens nothing.
    """
    folded = tag.strip().lower()
    return folded.startswith((SCOPE_TAG_PREFIX, _BRIDGE_SOURCE_TAG_PREFIX))


def _retain_item(
    index: int, raw: Any, agent: str, strategies: frozenset[str], max_item_bytes: int
) -> dict[str, Any]:
    """One caller item, reduced to what ``HindsightProvider.retain_items`` takes.

    The document id is left as the caller sent it; :func:`_namespace_item`
    gives it its stored form once the item's project is known.
    """
    if not isinstance(raw, dict):
        raise BadRequest(f"retain item {index} must be an object", details={"index": index})
    item = {key: raw[key] for key in _ITEM_PASSTHROUGH if raw.get(key) is not None}
    content = item.get("content")
    if isinstance(content, str) and (
        len(content) > max_item_bytes
        or (
            len(content) * 4 > max_item_bytes
            and len(content.encode("utf-8", "surrogatepass")) > max_item_bytes
        )
    ):
        raise CompatItemTooLarge(
            f"retain item {index} 'content' is larger than {max_item_bytes} bytes",
            details={"index": index, "max_bytes": max_item_bytes},
        )
    if "entities" in item and not isinstance(item["entities"], list):
        raise BadRequest(f"retain item {index} 'entities' must be a list", details={"index": index})
    if "tags" in item:
        item["tags"] = [tag for tag in coerce_tags(item["tags"]) if not _dropped_tag(tag)]
    strategy = raw.get("strategy")
    if isinstance(strategy, str) and strategy in strategies:
        item["strategy"] = strategy
    caller_metadata = raw.get("metadata")
    metadata = (
        {key: value for key, value in caller_metadata.items() if key not in _OWNED_METADATA}
        if isinstance(caller_metadata, dict)
        else {}
    )
    # The provider writes the client's name as ``source``; what the caller
    # called its source (the plugin sends ``chat``) is kept beside it.
    # ``project`` and ``project_source`` are written by the envelope from the
    # resolution, which is why a caller's own values were removed above.
    if metadata.get("source") not in (None, ""):
        metadata["client_source"] = metadata["source"]
    metadata["bridge_client"] = agent
    item["metadata"] = metadata
    return item


def _namespace_item(item: dict[str, Any], scope: _Scope) -> None:
    """Give one prepared item its stored document id, in place.

    Every item gets an id in one of the caller's namespaces
    (:mod:`hal0.memory.document_namespace`): the caller's own id behind the
    prefix, or a fresh one when it named none, so that no write here can land
    on a document outside them. An item whose own tags named several projects
    has those tags removed and is written as unresolved: a document has one
    namespace, so a write here has one project or none.
    """
    if scope.resolution.slugs:
        item["tags"] = [
            tag
            for tag in item.get("tags", ())
            if not tag.strip().lower().startswith(PROJECT_TAG_PREFIX)
        ]
    named = item.get("document_id")
    client_id = str(named) if named not in (None, "") else uuid.uuid4().hex
    item["document_id"] = scope.stored_id(client_id)
    if is_digested(scope.slug, scope.agent, client_id, scope.shared_prefixes):
        item["metadata"][CLIENT_DOCUMENT_ID_KEY] = client_id


@router.post(_BANK + "/memories")
async def retain(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Retain: the engine's ``POST .../memories``, through the write envelope.

    Every item goes through ``HindsightProvider.retain_items`` as a coding
    client's write to the platform bank: exactly one ``agent:<caller>`` tag,
    the resolved ``project:`` tag in place of any the caller sent
    (``project:unresolved`` when there is none, never ``scope:global``),
    session ids and lifecycle words moved out of the tags into metadata, and
    an observation scope hal0 chooses. All items go to the engine in one call
    and the answer is the engine's own JSON, which names no document.

    The engine upserts on document id, and every bank name here is one bank,
    so each document id is stored inside one of the caller's namespaces
    (:func:`_namespace_item`): two repositories, or two clients, that both
    retain ``readme`` write two documents, and no id a client sends can
    address a document another client, or one of hal0's own agents, wrote.

    An asynchronous retain is given an engine operation id derived from the
    client and the ``operation_id`` it sent (:func:`_engine_operation_id`),
    and the client's own id is what the answer carries. The plugin's resend
    with the same id is therefore still one operation.

    What it may cost is bounded three ways, each answered 429 with
    ``Retry-After``: one client has :data:`RETAINS_PER_CLIENT` retains in
    flight; :data:`LARGE_RETAIN_BODIES` bodies over
    :data:`SMALL_RETAIN_BODY_BYTES` are held in memory at once, across all
    clients, and one of them per client; and one client's unfinished
    asynchronous retains are held to a count and a size
    (:func:`_admit_operation`).
    """
    state = _state(request)
    declared = request.headers.get("content-length", "")
    # A body that says it is small is read with the small cap, so it cannot
    # turn out large; one that does not say takes a large-body slot.
    small = declared.isdigit() and int(declared) <= SMALL_RETAIN_BODY_BYTES
    try:
        with contextlib.ExitStack() as held:
            held.enter_context(
                _in_flight(state, "retain", agent, RETAINS_PER_CLIENT, _RETAIN_RETRY_AFTER_S)
            )
            if not small:
                # One per client, so that one key cannot hold every slot.
                for whose, limit in (
                    (agent, LARGE_RETAIN_BODIES_PER_CLIENT),
                    (_EVERY_CLIENT, LARGE_RETAIN_BODIES),
                ):
                    held.enter_context(
                        _in_flight(state, "large retain", whose, limit, _RETAIN_RETRY_AFTER_S)
                    )
            cap = SMALL_RETAIN_BODY_BYTES if small else MAX_RETAIN_BODY_BYTES
            return await _retain(bank, request, agent, body_cap=cap)
    except _Busy as busy:
        return busy.response()


async def _retain(bank: str, request: Request, agent: str, *, body_cap: int) -> Response:
    _require_writes_allowed()
    provider = _provider(request)
    settings = _settings(request)
    state = _state(request)
    body, size = await _read_json_sized(request, cap=body_cap)
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
    caller_operation = body.get("operation_id")
    if caller_operation is not None and not (
        isinstance(caller_operation, str) and 0 < len(caller_operation) <= MAX_OPERATION_ID_LENGTH
    ):
        raise BadRequest(
            f"retain 'operation_id' must be a string of 1 to {MAX_OPERATION_ID_LENGTH} characters"
        )
    asynchronous = request_fields.get("async_") is True
    visible_operation = engine_operation = ""
    if asynchronous:
        # The engine ignores an operation id on a synchronous retain, so one
        # is sent only here; a caller that named none still gets one it can
        # poll.
        visible_operation = caller_operation or str(uuid.uuid4())
        engine_operation = _engine_operation_id(agent, visible_operation)
        request_fields["operation_id"] = engine_operation

    named_strategy = any(isinstance(i, dict) and i.get("strategy") is not None for i in items)
    strategies = await _bank_strategies(request, provider) if named_strategy else frozenset()
    wire_items = [
        _retain_item(i, raw, agent, strategies, settings.max_item_bytes)
        for i, raw in enumerate(items)
    ]

    ignored = {name for name in body if name not in _RETAIN_FIELDS}
    for raw, item in zip(items, wire_items, strict=True):
        ignored.update(f"items.{name}" for name in raw if name not in _ITEM_FIELDS)
        if raw.get("strategy") is not None and "strategy" not in item:
            ignored.add("items.strategy")

    # The bank in the URL decides the project for every item. When it names
    # none the provider resolves each item from its own ``project:`` tags,
    # exactly as the per-item scope below does; the header reports what all
    # of them together resolve to.
    bank_scope = _scope(request, provider, bank, agent)
    every_tag = [tag for item in wire_items for tag in item.get("tags", ())]
    headers = {**_scope(request, provider, bank, agent, every_tag).headers(), **_ignored(ignored)}
    item_scopes = [
        _scope(request, provider, bank, agent, item.get("tags", ())) for item in wire_items
    ]
    for item, scope in zip(wire_items, item_scopes, strict=True):
        _namespace_item(item, scope)

    # An admitted operation is counted from here on, so that retains of one
    # client running side by side cannot each find the quota free. It is
    # given back if the engine never accepts it.
    counted = asynchronous and await _admit_operation(
        request, provider, agent, engine_operation, visible_operation, size
    )
    accepted = False
    try:
        # The same rule every other hal0 writer runs first (#1903): an
        # extraction slot too small for the extraction prompt can never
        # process a retain.
        await extraction_ctx_preflight(request.app, provider)
        try:
            out = await provider.retain_items(
                wire_items,
                agent=agent,
                project_resolution=bank_scope.resolution,
                coding=True,
                timeout_s=_RETAIN_ASYNC_TIMEOUT_S if asynchronous else _RETAIN_SYNC_TIMEOUT_S,
                **request_fields,
            )
        except ValueError as exc:
            # The unified bank keeps ``private:``-prefixed document ids for
            # private documents. A stored id here starts with its project, so
            # only a project literally named ``private`` can reach this.
            raise BadRequest(
                "this project name cannot hold documents through this endpoint",
                code="memory.compat_document_id_reserved",
            ) from exc
        except Exception as exc:
            return _engine_failure(
                exc,
                what="the retain",
                headers=headers,
                not_found_is_gateway=True,
                hidden=[prefix for scope in item_scopes for prefix in scope.id_prefixes()],
            )
        accepted = True
    finally:
        started = state.operations_of(agent).get(engine_operation)
        if started is not None and accepted:
            started.accepted = True
        elif counted:
            state.operations_of(agent).pop(engine_operation, None)
    # The engine's answer names no document today; any string in it is still
    # cleared of this client's stored-id prefixes before it is passed on.
    answer = _without_stored_ids(dict(out.get("engine_response") or {}), agent)
    if asynchronous:
        _own_operations(answer, state, agent, engine_operation, visible_operation, size)
    return _json(answer, headers)


def _without_stored_ids(value: Any, client: str) -> Any:
    """``value`` with ``client``'s stored-id prefixes cut out of every string."""
    if isinstance(value, str):
        return strip_stored_prefixes(value, client)
    if isinstance(value, list):
        return [_without_stored_ids(item, client) for item in value]
    if isinstance(value, dict):
        return {key: _without_stored_ids(item, client) for key, item in value.items()}
    return value


def _own_operations(
    answer: dict[str, Any],
    state: _CompatState,
    agent: str,
    engine_id: str,
    visible_id: str,
    size: int,
) -> None:
    """Record the operations a retain started and show the caller its own id.

    The engine answers with the id it was given, and with further ids of its
    own when it split the items by strategy. Those stay as they are and are
    remembered, so the client that was handed them can poll them. The
    request's size is counted once, on the operation the client named.
    """

    def shown(operation_id: Any) -> Any:
        if not isinstance(operation_id, str) or not operation_id:
            return operation_id
        named = operation_id == engine_id
        state.remember_operation(
            agent, operation_id, visible_id if named else operation_id, size if named else 0
        )
        remembered = state.operations_of(agent).get(operation_id)
        if remembered is not None:
            remembered.accepted = True
        return visible_id if named else operation_id

    if "operation_id" in answer:
        answer["operation_id"] = shown(answer["operation_id"])
    if isinstance(answer.get("operation_ids"), list):
        answer["operation_ids"] = [shown(operation_id) for operation_id in answer["operation_ids"]]


async def _refresh_operations(
    provider: Any, agent: str, operations: list[tuple[str, _Operation]]
) -> None:
    """Read the current status of ``operations`` from the engine, in place.

    One status read each, a few at a time. A finished operation is marked
    done and keeps its last view; one the engine no longer has is done too.
    A read that fails leaves the operation as it was, and one whose retain
    the engine has not answered yet is not read.
    """
    client = provider.hindsight_client
    gate = asyncio.Semaphore(_OPERATION_READS_AT_ONCE)

    async def read(engine_id: str, operation: _Operation) -> None:
        if not operation.accepted:
            return
        path = _bank_path(provider, f"/operations/{quote(engine_id, safe='')}")
        async with gate:
            try:
                record = await client.request_json(
                    "GET",
                    path,
                    params={"include_payload": "false"},
                    timeout_s=_OPERATION_TIMEOUT_S,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    operation.done = True
                return
            except httpx.HTTPError:
                return
        _note_status(operation, record, agent)

    await asyncio.gather(*(read(engine_id, operation) for engine_id, operation in operations))


def _note_status(operation: _Operation, record: Any, agent: str) -> None:
    """Keep what a status read said about a remembered operation."""
    if not isinstance(record, dict) or not operation.accepted:
        return
    if record.get("status") == "not_found":
        operation.done = True
        return
    operation.view = _operation_view(record, operation.visible_id, agent)
    if str(record.get("status") or "").lower() in _TERMINAL_STATUSES:
        operation.done = True


async def _admit_operation(
    request: Request, provider: Any, agent: str, engine_id: str, visible_id: str, size: int
) -> bool:
    """Hold one client's unfinished asynchronous retains to a count and a size.

    An asynchronous retain is accepted at once and extracted later, so the
    in-flight limit bounds how fast a client submits, not how much it has
    queued in the engine. This does: beyond
    ``[memory.compat].max_outstanding_operations`` unfinished operations, or
    ``max_outstanding_bytes`` of them, the retain is refused with 429 until
    earlier ones finish. A resend of an operation already counted is let
    through (the engine starts nothing new for it), and so is any single
    retain when the client has nothing outstanding.

    An admitted operation is counted at once, before the engine is called,
    and the answer is ``True`` when this call added it (the caller gives it
    back if the engine then refuses the retain).

    What is outstanding is what this process saw the client start and has
    not seen finish. The client's own polling keeps that current
    (:func:`get_operation`); when the limit is reached the oldest are read
    from the engine here first, at most once every
    :data:`_QUOTA_RECHECK_S` seconds per client. After a hal0-api restart
    the operations submitted before it are unknown and are not counted: the
    quota fails open for those.
    """
    state = _state(request)
    settings = _settings(request)
    if engine_id in state.operations_of(agent):
        return False

    def over() -> bool:
        waiting = state.outstanding(agent)
        queued = sum(operation.size for _key, operation in waiting)
        return len(waiting) >= settings.max_outstanding_operations or (
            bool(waiting) and queued + size > settings.max_outstanding_bytes
        )

    if over():
        now = time.monotonic()
        if now - state.quota_read_at.get(agent, float("-inf")) >= _QUOTA_RECHECK_S:
            state.quota_read_at[agent] = now
            await _refresh_operations(
                provider, agent, state.outstanding(agent)[:_QUOTA_STATUS_READS]
            )
        # Judged again after the reads, with nothing awaited before the
        # operation is counted below.
        if engine_id in state.operations_of(agent):
            return False
        if over():
            raise _Busy(
                "this client's earlier retains are still being processed", _QUOTA_RETRY_AFTER_S
            )
    state.remember_operation(agent, engine_id, visible_id, size)
    return True


# ── 3. reflect ───────────────────────────────────────────────────────────────


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _budget(body: dict[str, Any], what: str) -> str | None:
    budget = body.get("budget")
    if budget is None:
        return None
    if not isinstance(budget, str) or budget not in BUDGETS:
        raise BadRequest(f"{what} 'budget' must be one of " + ", ".join(BUDGETS))
    return budget


def _max_tokens(body: dict[str, Any], what: str, cap: int) -> int | None:
    """The caller's ``max_tokens``, lowered to ``cap`` when it asks for more."""
    max_tokens = body.get("max_tokens")
    if max_tokens is None:
        return None
    if not _positive_int(max_tokens):
        raise BadRequest(f"{what} 'max_tokens' must be a positive integer")
    return min(int(max_tokens), cap)


@router.post(_BANK + "/reflect")
async def reflect(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Reflect: the engine's ``POST .../reflect``, held to the caller's scope.

    ``HindsightProvider.reflect`` gives the engine the visibility rule for
    this agent, and leaves mental models out while the bank holds other
    agents' private memory. The project scope is added to that as one more
    group, and so are the caller's own ``tags``. The engine call is cancelled
    when the client disconnects.

    A reflect holds the engine's language model for minutes, so one client
    runs one at a time and all clients together run
    ``[memory.compat].max_concurrent_reflects``, less what
    :func:`_reflect_limit` keeps back for callers outside this endpoint;
    beyond either the answer is 429 with ``Retry-After``. The answer is the engine's ``text``,
    ``structured_output`` and ``usage``.
    """
    provider = _provider(request)
    body = await _read_json(request, cap=MAX_QUERY_BODY_BYTES)
    query = _query(body, "reflect")
    tags, tags_match = _caller_filter(body, "reflect")
    extra: dict[str, Any] = {}
    budget = _budget(body, "reflect")
    if budget is not None:
        extra["budget"] = budget
    max_tokens = _max_tokens(body, "reflect", MAX_REFLECT_TOKENS)
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if body.get("response_schema") is not None:
        if not isinstance(body["response_schema"], dict):
            raise BadRequest("reflect 'response_schema' must be an object")
        extra["response_schema"] = body["response_schema"]

    scope = _scope(request, provider, bank, agent, tags or ())
    headers = {**scope.headers(), **_ignored(n for n in body if n not in _REFLECT_FIELDS)}
    groups = [*scope.project_groups(), *_caller_groups(body, "reflect")]
    state = _state(request)
    everyone = _reflect_limit(request)
    try:
        with (
            _in_flight(state, "reflect", agent, REFLECTS_PER_CLIENT, _REFLECT_RETRY_AFTER_S),
            _in_flight(state, "reflect", _EVERY_CLIENT, everyone, _REFLECT_RETRY_AFTER_S),
        ):
            call = provider.reflect(
                query,
                client_id=agent,
                tags=tags,
                tags_match=tags_match,
                tag_groups=groups,
                **extra,
            )
            result = await _until_disconnect(request, call)
    except _Busy as busy:
        return busy.response(headers)
    except _ClientGone:
        log.info("hal0.memory.compat.reflect_abandoned", client=agent)
        return Response(status_code=499)
    except Exception as exc:
        return _engine_failure(exc, what="the reflect", headers=headers, hidden=scope.id_prefixes())
    return _json(_pick(result, _REFLECT_RESPONSE_FIELDS), headers)


# ── 4. recall ────────────────────────────────────────────────────────────────

# The RecallRequest fields passed to the engine as sent: ``(name, shape check,
# what the 400 says)``. The engine validates their values. ``budget`` and
# ``max_tokens`` are checked apart.
_RECALL_KNOBS: tuple[tuple[str, Callable[[Any], bool], str], ...] = (
    (
        "types",
        lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v),
        "a list of strings",
    ),
    ("query_timestamp", lambda v: isinstance(v, str), "a string"),
    ("min_scores", lambda v: isinstance(v, dict), "an object"),
    ("prefer_observations", lambda v: isinstance(v, bool), "a boolean"),
)


def _opaque_chunk_id(chunk_id: str) -> str:
    return "chunk-" + hashlib.sha256(chunk_id.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _descriptive_tags(tags: Any) -> list[str]:
    """The tags of someone else's memory that say what it is, not whose.

    The project (or ``scope:global``), and the ``kind:``, ``knowledge:`` and
    ``source:`` tags. Every ``agent:`` tag, the bridge's ``source:bridge:``
    stamp, ``harness:`` and any free tag are left out.
    """
    return [
        tag
        for tag in (tags if isinstance(tags, list) else [])
        if isinstance(tag, str)
        and (tag == SCOPE_GLOBAL_TAG or tag.startswith(_DESCRIPTIVE_TAG_PREFIXES))
        and not tag.startswith(_BRIDGE_SOURCE_TAG_PREFIX)
    ]


def _is_own(record: dict[str, Any], scope: _Scope) -> bool:
    """True when the caller itself wrote the memory ``record`` came from.

    Every write through hal0 carries exactly one ``agent:`` tag, its
    writer's, and a caller cannot set it.
    """
    return _carries(record.get("tags"), (scope.agent_tag,))


def _recall_row(record: dict[str, Any], scope: _Scope, chunk_ids: dict[str, str]) -> dict[str, Any]:
    """A recall result or source fact as the caller may see it.

    **The caller's own** is returned whole, with its document id as the
    caller knows it. The engine's chunk id has the document id inside it, so
    it is rewritten the same way, or replaced by an opaque one; ``chunk_ids``
    collects old id to new id for the response's ``chunks``.

    **Anyone else's** (another client's memory in the same project, a shared
    document another client wrote, anything hal0's own agents wrote) is
    reduced to an allow-list: :data:`_FOREIGN_RESULT_FIELDS` and its
    descriptive tags. Its ``document_id`` is ``null`` and its chunk id opaque;
    its ``metadata`` (which holds the other client's name, its document id
    and its session id), its ``context`` (the other client's name by default)
    and its entities are not returned, and neither is its chunk: a raw chunk
    of another client's session names that session.
    """
    chunk_id = record.get("chunk_id")
    if not _is_own(record, scope):
        out = _pick(record, _FOREIGN_RESULT_FIELDS)
        if "tags" in record:
            out["tags"] = _descriptive_tags(record["tags"])
        if "document_id" in record:
            out["document_id"] = None
        if isinstance(chunk_id, str) and chunk_id:
            out["chunk_id"] = _opaque_chunk_id(chunk_id)
        return out
    out = dict(record)
    stored = record.get("document_id")
    visible = scope.visible_id(stored, record.get("metadata")) if stored is not None else None
    if visible is not None:
        out["document_id"] = visible
    elif is_namespaced(stored):
        out["document_id"] = None
    if isinstance(chunk_id, str) and chunk_id:
        shown = chunk_id
        if visible is not None and isinstance(stored, str):
            shown = chunk_id.replace(chunk_id_part(stored), chunk_id_part(visible), 1)
        if any(chunk_id_part(prefix) in shown for prefix in scope.id_prefixes()):
            shown = _opaque_chunk_id(chunk_id)
        chunk_ids[chunk_id] = shown
        out["chunk_id"] = shown
    return out


def _scrub_recall(
    response: dict[str, Any], groups: list[dict[str, Any]], scope: _Scope
) -> dict[str, Any]:
    """The engine's recall answer, with what the filter cannot vouch for removed.

    The engine was given ``groups`` and returned only matching results; each
    one is checked against the same groups again here, which costs nothing
    when the engine did its part. The parts of the answer that sit beside the
    results are joined to them and otherwise dropped:

    * ``entities`` is bank-wide state, built from every project's and every
      agent's facts, and says nothing about where it came from: removed;
    * ``source_facts`` are kept when one of the caller's OWN results names
      them and their own tags are inside the groups (a result that is not
      the caller's is returned without its ``source_fact_ids``);
    * ``chunks`` are kept when one of the caller's OWN results or source
      facts names them;
    * ``trace`` is never requested and is removed if present.

    Every kept row then goes through :func:`_recall_row`.
    """
    out = dict(response)
    raw_results = response.get("results")
    results = [
        r
        for r in (raw_results if isinstance(raw_results, list) else [])
        if isinstance(r, dict) and tags_readable(groups, r.get("tags") or [])
    ]
    out.pop("trace", None)
    if "entities" in out:
        out["entities"] = None
    named = {
        str(i)
        for r in results
        if _is_own(r, scope) and isinstance(r.get("source_fact_ids"), list)
        for i in r["source_fact_ids"]
    }
    chunk_ids: dict[str, str] = {}
    out["results"] = [_recall_row(r, scope, chunk_ids) for r in results]
    source_facts = response.get("source_facts")
    if isinstance(source_facts, dict):
        out["source_facts"] = {
            key: _recall_row(fact, scope, chunk_ids)
            for key, fact in source_facts.items()
            if str(key) in named
            and isinstance(fact, dict)
            and tags_readable(groups, fact.get("tags") or [])
        }
    chunks = response.get("chunks")
    if isinstance(chunks, dict):
        shown_chunks: dict[str, Any] = {}
        for key, chunk in chunks.items():
            if str(key) not in chunk_ids:
                continue
            shown = chunk_ids[str(key)]
            if isinstance(chunk, dict):
                chunk = dict(chunk)
                if "document_id" in chunk:
                    chunk["document_id"] = scope.visible_id(chunk["document_id"])
                for id_field in ("id", "chunk_id"):
                    if chunk.get(id_field) == key:
                        chunk[id_field] = shown
            shown_chunks[shown] = chunk
        out["chunks"] = shown_chunks
    return out


@router.post(_BANK + "/memories/recall")
async def recall(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """Recall: the engine's ``POST .../memories/recall``, in the engine's shape.

    Unlike ``POST /api/memory/recall`` the answer is not reshaped into hal0's
    items: ``HindsightProvider.recall_raw`` returns what the engine returned
    for the platform bank, filtered by the engine to the same groups a
    reflect gets. :func:`_scrub_recall` then removes what a tag filter cannot
    vouch for, and what is not the caller's own is reduced to an allow-list.

    One client runs :data:`RECALLS_PER_CLIENT` recalls at a time and all
    clients together ``[memory.compat].max_concurrent_recalls``; beyond
    either the answer is 429 with ``Retry-After``.
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
    budget = _budget(body, "recall")
    if budget is not None:
        extra["budget"] = budget
    max_tokens = _max_tokens(body, "recall", MAX_RECALL_TOKENS)
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    include = body.get("include")
    wanted = include if isinstance(include, dict) else {}
    # Entities are on by default in the engine and are bank-wide; ``null``
    # turns them off. Chunks and source facts are the caller's choice.
    extra["include"] = {
        **{key: wanted[key] for key in ("chunks", "source_facts") if key in wanted},
        "entities": None,
    }

    scope = _scope(request, provider, bank, agent, tags or ())
    headers = {**scope.headers(), **_ignored(n for n in body if n not in _RECALL_FIELDS)}
    groups = [*scope.project_groups(), *_caller_groups(body, "recall")]
    state = _state(request)
    everyone = _settings(request).max_concurrent_recalls
    try:
        with (
            _in_flight(state, "recall", agent, RECALLS_PER_CLIENT, _RECALL_RETRY_AFTER_S),
            _in_flight(state, "recall", _EVERY_CLIENT, everyone, _RECALL_RETRY_AFTER_S),
        ):
            result = await provider.recall_raw(
                query,
                client_id=agent,
                tags=tags,
                tags_match=tags_match,
                tag_groups=groups,
                timeout_s=_RECALL_TIMEOUT_S,
                **extra,
            )
    except _Busy as busy:
        return busy.response(headers)
    except Exception as exc:
        return _engine_failure(exc, what="the recall", headers=headers, hidden=scope.id_prefixes())
    return _json(_scrub_recall(result, scope.read_groups(), scope), headers)


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


def _page_number(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdigit():
        raise BadRequest(f"'{name}' must be a non-negative integer")
    return int(raw)


def _listing_row(
    row: Any, scope: _Scope, required: list[str], visible: list[dict[str, Any]]
) -> Any:
    """One engine row as the caller may see it, or ``None`` to leave it out.

    Left out: a row outside the caller's namespaces, one without the required
    tags, one private to another agent. The caller's own document is returned
    whole under the caller's id. A shared document another client wrote last
    is returned as an allow-list (:data:`_FOREIGN_DOCUMENT_FIELDS` and its
    descriptive tags): its id is the shared id every client of the project
    derives, and nothing of its writer (metadata, retain parameters, the
    writer's tags) comes with it.
    """
    if not isinstance(row, dict) or not _carries(row.get("tags"), required):
        return None
    if not tags_readable(visible, row["tags"]):
        return None
    own = _is_own(row, scope)
    shown = scope.visible_id(row.get("id"), row.get("document_metadata") if own else None)
    if shown is None:
        return None
    if own:
        return {**row, "id": shown}
    return {
        **_pick(row, _FOREIGN_DOCUMENT_FIELDS),
        "id": shown,
        "tags": _descriptive_tags(row["tags"]),
    }


@router.get(_BANK + "/documents")
async def list_documents(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """List documents: the engine's ``GET .../documents``, for this caller.

    The engine filters by tag (:func:`_document_filter`). What it cannot
    express is applied to each returned row in hal0 (:func:`_listing_row`):
    the row's stored id must be in one of the caller's namespaces (its own,
    or the project's shared one), which leaves out every document hal0's own
    agents or another client wrote under an id of their own, and the row must
    not be private to someone else. A kept row is returned with the caller's
    own id.

    The plugin stops paging at the first page shorter than ``limit``. So when
    rows were left out, the listing reads on from the engine, up to
    :data:`_LISTING_EXTRA_FETCHES` more pages, until the page is full or the
    engine has no more rows. Rows read that way can come back again on the
    caller's next page; ids are unique, so a caller collecting them loses
    nothing. ``total`` is the engine's count for the tag filter, before hal0
    left anything out: an upper bound, never an undercount.
    ``X-hal0-Filtered-Items`` says how many rows were left out, and
    ``X-hal0-Listing-Incomplete`` is set when the page is short although the
    engine had more.
    """
    provider = _provider(request)
    scope = _scope(request, provider, bank, agent, request.query_params.getlist("tags"))
    tags, required = _document_filter(request, scope)
    limit = min(_page_number(request, "limit", _DEFAULT_DOCUMENTS_LIMIT), MAX_DOCUMENTS_LIMIT)
    offset = _page_number(request, "offset", 0)
    params: dict[str, Any] = {"tags": tags, "tags_match": "all_strict", "limit": limit}
    if request.query_params.get("q") is not None:
        params["q"] = request.query_params["q"]
    headers = scope.headers()
    visible = reflect_visibility_scope(agent)

    first: dict[str, Any] = {}
    kept: list[dict[str, Any]] = []
    left_out = 0
    exhausted = False
    for fetch in range(1 + _LISTING_EXTRA_FETCHES):
        out = await _forward(
            request,
            "GET",
            _bank_path(provider, "/documents"),
            what="the documents listing",
            timeout_s=_DOCUMENT_TIMEOUT_S,
            params={**params, "offset": offset + fetch * limit},
            headers=headers,
            hidden=scope.id_prefixes(),
        )
        if isinstance(out, Response):
            return out
        page = out if isinstance(out, dict) else {}
        if fetch == 0:
            first = dict(page)
        items = page.get("items")
        rows: list[Any] = items if isinstance(items, list) else []
        for row in rows:
            if len(kept) >= limit:
                break
            shown = _listing_row(row, scope, required, visible)
            if shown is None:
                left_out += 1
            else:
                kept.append(shown)
        exhausted = len(rows) < limit or limit == 0
        if exhausted or len(kept) >= limit:
            break
    first["items"] = kept
    first["limit"] = limit
    first["offset"] = offset
    if left_out:
        headers[FILTERED_ITEMS_HEADER] = str(left_out)
    if len(kept) < limit and not exhausted:
        headers[INCOMPLETE_LISTING_HEADER] = "1"
    return _json(first, headers)


def _document_not_found(document_id: str) -> NotFound:
    """The one answer for a document the caller cannot address, whether the
    engine has no such document or it is simply not in the caller's
    namespaces: nothing a caller sends can tell the two apart."""
    return NotFound(
        "no such document",
        code="memory.compat_document_not_found",
        details={"document_id": document_id[:MAX_OPERATION_ID_LENGTH]},
    )


@router.delete(_BANK + "/documents/{document_id:path}")
async def delete_document(
    bank: str, document_id: str, request: Request, agent: str = _CALLER
) -> Response:
    """Delete one of the caller's own documents.

    The id is the caller's, and is looked up inside its namespaces only
    (its own, or the project's shared one for a shared id), so a document
    anywhere else does not exist as far as this route can tell: 404
    ``memory.compat_document_not_found``. The document is read first and
    must carry ``agent:<caller>``; a shared document another client wrote
    last is 403 ``memory.compat_document_not_owned``.
    """
    _require_writes_allowed()
    provider = _provider(request)
    client = provider.hindsight_client
    scope = _scope(request, provider, bank, agent)
    headers = scope.headers()
    path = _bank_path(provider, f"/documents/{quote(scope.stored_id(document_id), safe='')}")

    async def call(method: str) -> Any:
        """One engine call on the document; its 404 is this route's 404."""
        try:
            return await client.request_json(method, path, timeout_s=_DOCUMENT_TIMEOUT_S)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise _document_not_found(document_id) from exc
            raise

    try:
        document = await call("GET")
        tags = document.get("tags") if isinstance(document, dict) else None
        if not _carries(tags, (scope.agent_tag,)):
            raise Forbidden(
                "this document was last written by another client",
                code="memory.compat_document_not_owned",
            )
        # The engine's own failure is raised inside the audit block, so the
        # row records it; it becomes this endpoint's answer outside.
        async with _audited_delete(request, agent, scope.stored_id(document_id)):
            out = await call("DELETE")
    except Exception as exc:
        return _engine_failure(
            exc, what="the document delete", headers=headers, hidden=scope.id_prefixes()
        )
    deleted = out if isinstance(out, dict) else {}
    answer = {
        "success": deleted.get("success", True),
        "message": "document deleted",
        "document_id": document_id,
        "memory_units_deleted": deleted.get("memory_units_deleted", 0),
    }
    return _json(answer, headers)


@contextlib.asynccontextmanager
async def _audited_delete(request: Request, agent: str, stored_id: str) -> AsyncIterator[None]:
    """Record a document delete in the audit store, with the real caller.

    ``hal0.api._audit.record_action`` takes the actor from ``X-hal0-Agent``,
    which this endpoint does not trust, so the row is written with the
    credential's identity instead, and with the stored id an operator can
    find the document by.
    """
    store = getattr(request.app.state, "audit", None)
    if store is None:
        yield
        return
    async with audit_action(
        store,
        category="memory",
        action="memory.document.delete",
        target=stored_id,
        actor=f"bridge:{agent}",
        message="engine-compatible memory endpoint",
        request_id=request.headers.get("X-Request-ID"),
    ):
        yield


# ── 7. operations ────────────────────────────────────────────────────────────


def _pick(record: Any, fields: Iterable[str]) -> dict[str, Any]:
    source = record if isinstance(record, dict) else {}
    return {key: source[key] for key in fields if key in source}


def _operation_view(record: Any, visible_id: str, agent: str) -> dict[str, Any]:
    """One of the caller's operations as a status poll needs it.

    Nothing a client submitted is returned: no payload, no result metadata,
    no document id, no file name, and no engine id (the operation's id is the
    one the caller knows it by; child operations are counted, not named).
    ``error_message`` is the caller's own operation's; the engine may name a
    document in it by its stored id, so the caller's stored-id prefixes are
    cut out of it, and it is cut to :data:`MAX_ERROR_MESSAGE_LENGTH`.
    """
    view = _pick(record, _OPERATION_FIELDS)
    source = record if isinstance(record, dict) else {}
    for id_field in ("id", "operation_id"):
        if id_field in view:
            view[id_field] = visible_id
    if "error_message" in source:
        error = source["error_message"]
        view["error_message"] = (
            strip_stored_prefixes(error, agent)[:MAX_ERROR_MESSAGE_LENGTH]
            if isinstance(error, str)
            else None
        )
    if isinstance(source.get("progress"), dict):
        view["progress"] = _pick(source["progress"], _PROGRESS_FIELDS)
    children = source.get("child_operations")
    if isinstance(children, list):
        view["child_operations"] = [_pick(child, _CHILD_FIELDS) for child in children]
    return view


@router.get(_BANK + "/operations")
async def list_operations(bank: str, request: Request, agent: str = _CALLER) -> Response:
    """The caller's own operations on the platform bank, as statuses.

    The engine's list covers every client's operations and does not say who
    started which, so it is not used. The list is built from the operations
    this hal0-api process saw the caller start (:class:`_CompatState`),
    newest first; each unfinished one is read from the engine by id, so the
    statuses are current however many operations other clients have. The
    caller's ``limit`` and ``offset`` index into the caller's own operations
    only, ``status`` and ``type`` filter them, and ``total`` counts them.

    Two things can make the list incomplete, and both set
    ``X-hal0-Listing-Incomplete``: the client has more than
    :data:`OPERATIONS_REMEMBERED_PER_CLIENT` remembered operations (the
    oldest finished ones are dropped), or more than
    :data:`_LISTING_STATUS_READS` of them are unfinished. After a restart the
    list is empty until the caller retains again; a poll by id
    (:func:`get_operation`) does not depend on this memory.
    """
    provider = _provider(request)
    state = _state(request)
    limit = min(_page_number(request, "limit", 20), MAX_OPERATIONS_LIMIT)
    offset = _page_number(request, "offset", 0)
    headers = _scope(request, provider, bank, agent).headers()

    waiting = state.outstanding(agent)
    await _refresh_operations(provider, agent, waiting[-_LISTING_STATUS_READS:])
    if agent in state.forgotten or len(waiting) > _LISTING_STATUS_READS:
        headers[INCOMPLETE_LISTING_HEADER] = "1"

    wanted_status = request.query_params.get("status")
    wanted_type = request.query_params.get("type")
    mine = [
        operation.view
        for operation in reversed(state.operations_of(agent).values())
        if operation.view is not None
        and (wanted_status is None or operation.view.get("status") == wanted_status)
        and (
            wanted_type is None
            or wanted_type
            in (operation.view.get("task_type"), operation.view.get("operation_type"))
        )
    ]
    page = {
        "bank_id": str(provider.platform_bank),
        "total": len(mine),
        "limit": limit,
        "offset": offset,
        "operations": mine[offset : offset + limit],
    }
    return _json(page, headers)


@router.get(_BANK + "/operations/{operation_id}")
async def get_operation(
    bank: str, operation_id: str, request: Request, agent: str = _CALLER
) -> Response:
    """One of the caller's own operations.

    The id is the caller's: the engine is asked for the operation derived
    from this client and that id (:func:`_engine_operation_id`), which no
    other client's id can name. So an id the caller did not start, another
    client's included, is 404 ``memory.compat_operation_not_found``, and this
    needs no memory of who started what: it answers the same after a restart.
    The exception is an id the engine chose itself and this client was handed
    in a retain answer, which is remembered per process. The submitted
    payload is never requested (``include_payload=false``) or returned.

    What the poll learns is kept: a finished operation stops counting toward
    the client's quota (:func:`_admit_operation`).
    """
    provider = _provider(request)
    client = provider.hindsight_client
    headers = _scope(request, provider, bank, agent).headers()
    remembered = _state(request).operations_of(agent)
    candidates = [_engine_operation_id(agent, operation_id)]
    handed = remembered.get(operation_id)
    if handed is not None and handed.visible_id == operation_id:
        candidates.append(operation_id)
    for engine_id in candidates:
        try:
            record = await client.request_json(
                "GET",
                _bank_path(provider, f"/operations/{quote(engine_id, safe='')}"),
                params={"include_payload": "false"},
                timeout_s=_OPERATION_TIMEOUT_S,
            )
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500 and exc.response.status_code not in (401, 403):
                continue
            return _engine_failure(exc, what="the operation read", headers=headers)
        except Exception as exc:
            return _engine_failure(exc, what="the operation read", headers=headers)
        known = remembered.get(engine_id)
        if known is not None:
            _note_status(known, record, agent)
        if isinstance(record, dict) and record.get("status") != "not_found":
            return _json(_operation_view(record, operation_id, agent), headers)
    raise NotFound(
        "no such operation",
        code="memory.compat_operation_not_found",
        details={"operation_id": operation_id[:MAX_OPERATION_ID_LENGTH]},
    )


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
    headers = _scope(request, provider, bank, agent).headers()
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
