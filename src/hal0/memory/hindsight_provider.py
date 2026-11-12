"""HindsightProvider — the platform memory engine (brain-redesign P1).

Maps hal0's engine-neutral MemoryProvider contract onto the shared
``hindsight-api`` over REST. Key design points (spec §3, §4b, P1):

* **Bank mapping** lives HERE (not namespace.py, which is unchanged). In the
  two-bank layout (ADR-0058 decision 7) ``shared`` is the platform bank
  (``hal0:shared``) and every ``private:<agent>`` is the one private bank
  (``hal0:private``), where the ``agent:<id>`` tag scopes each agent to its
  own. A store on its old layout keeps what it had: one platform bank for
  everything (unified) or ``private:<agent>`` → ``private__<agent>`` (split).
  ``agents`` passes through in every layout.
* ``MemoryItem.id`` is the Hindsight **document_id** — idempotent on retain,
  recall-visible, delete-addressable. NOT a per-fact id (those are async +
  many-per-add).
* ``add`` routes to Hindsight **retain** so background consolidation fires.
* **Multi-bank recall fan-out** (Task P1-3): Hindsight recall is per-bank,
  client-orchestrated; we fan out to the caller's allowed banks and merge
  under one reranked token budget (recall returns NO numeric score, so the
  union is re-ranked via the :8086 reranker; §4b precedence is the tiebreak).
* **Live ``degraded`` flag** (#1301): reports whether the daemon is answering
  *right now*, driven by observed transport failures on every engine call —
  not a boot-time constant. The boot probe in ``hal0.memory`` decides which
  provider gets built; this decides whether the one that got built is still
  working.
* **Separate ``write_degraded`` flag** (#1420): reports whether *retains are
  landing*. A reachable daemon accepts a retain, returns ``200`` with an
  ``operation_id``, and extracts the facts asynchronously — so the whole
  write path can be dead while ``degraded`` is correctly ``False`` and
  recalls keep working. See :meth:`HindsightProvider.write_health`.
"""

from __future__ import annotations

import functools
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from hal0.errors import BadRequest, Forbidden, MemoryEngineUnavailable, NotFound
from hal0.memory.bank_layout import (
    HEALTH_LEGACY_SPLIT,
    HEALTH_LEGACY_UNIFIED,
    HEALTH_TWO_BANK,
    TWO_BANK_PRIVATE_BANK,
)
from hal0.memory.envelope import (
    ANONYMOUS_AGENT,
    COMBINED_OBSERVATION_SCOPES,
    PROJECT_UNRESOLVED_TAG,
    UNKNOWN_AGENT,
    apply_envelope,
    coerce_tags,
    is_identified,
    resolve_observation_scopes,
)
from hal0.memory.projects import (
    SOURCE_NAMESPACE,
    SOURCE_NONE,
    ProjectRegistry,
    ProjectResolution,
    resolve_project,
)
from hal0.memory.provider import MemoryProvider
from hal0.memory.read_scope import (
    VISIBILITY_PRIVATE_TAG,
    model_scope_confined_to,
    project_scope_groups,
    reflect_visibility_scope,
    tags_readable,
)
from hal0.memory.write_gate import read_gate, require_writes_allowed

log = structlog.get_logger(__name__)

_SHARED = "shared"
_PRIVATE = "private:"
# The ``agents`` namespace is a federated agent-registry /
# identity-card store (written by the ``hal0 agent`` CLI), NOT chat memory.
# Unified mode collapses shared/private/project onto ``shared`` but leaves
# ``agents`` routing to its own bank untouched, both read and write.
_AGENTS = "agents"

# Unified-bank visibility tagging (PR #1244). A write made under the
# X-hal0-Private toggle lands in the single ``shared`` bank stamped
# ``visibility:private`` + ``agent:<id>`` (see ``add``). Read-side enforcement
# (below) keeps such a doc addressable only by its owning agent. The tag is
# owned by ``hal0.memory.read_scope``, which builds the reflect filter from it.
_VISIBILITY_PRIVATE = VISIBILITY_PRIVATE_TAG
_AGENT_TAG_PREFIX = "agent:"
#: Tag namespaces the provider owns. A caller's tag is judged stripped and
#: lower-cased (as the bridge's ``_client_tags`` does), so ``AGENT:x``,
#: `` agent:x`` and ``Visibility:Shared`` are reserved too.
_RESERVED_TAG_PREFIXES = (_AGENT_TAG_PREFIX, "visibility:")


def _gated_write(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Refuse a mutating provider call while memory writes are paused (#3518).

    Every hal0 writer reaches the engine through one of the decorated
    methods, so this is where ``hal0 memory embedder switch`` holds them off:
    :func:`hal0.memory.write_gate.require_writes_allowed` raises the typed
    ``memory.writes_paused`` 503 before anything is sent, and before the
    write-health bookkeeping can count the refusal as a failed write.
    """

    @functools.wraps(method)
    async def gated(self: Any, *args: Any, **kwargs: Any) -> Any:
        require_writes_allowed()
        return await method(self, *args, **kwargs)

    return gated


def _is_reserved_tag(tag: Any) -> bool:
    return str(tag).strip().lower().startswith(_RESERVED_TAG_PREFIXES)


def _strip_reserved_scopes(scopes: Any) -> Any:
    """``observation_scopes`` with reserved tags removed from list-form scopes.

    A string mode (``per_tag`` ...) passes through. A list of tag-lists keeps
    each scope's other tags; a scope left empty is dropped, and a list left
    with no scope at all becomes ``None`` (the engine default), so a caller
    cannot aim consolidation at another agent's or the private partition.
    """
    if not isinstance(scopes, list):
        return scopes
    kept: list[Any] = []
    for scope in scopes:
        if isinstance(scope, list):
            inner = [t for t in scope if not _is_reserved_tag(t)]
            if inner:
                kept.append(inner)
        elif not _is_reserved_tag(scope):
            kept.append(scope)
    return kept or None


def _private_observation_scopes(tags: list[str]) -> Any:
    """The observation scope of a private write in the two-bank private bank.

    ``[["agent:<id>", "visibility:private", "project:<slug>"]]``, one scope per
    project the write carries, or ``[["agent:<id>", "visibility:private"]]``
    when it carries none (``project:unresolved`` is not a project). Built from
    the write's final tags, which the provider stamped, never from a scope the
    caller sent.

    The engine consolidates within an explicit scope only against
    observations that carry all of the scope's tags (``all_strict``) and
    writes each observation with exactly those tags (Hindsight 1.2.0,
    ``engine/consolidation/consolidator.py``), so another agent's facts never
    join this one's observation and the observation keeps ``visibility:private``.
    The engine reads a ``*`` in a scope as a glob; a tag holding one cannot be
    named literally, and such a write falls back to ``combined``, which keeps
    every one of the write's tags on the observation.
    """
    agent = next((t for t in tags if isinstance(t, str) and t.startswith(_AGENT_TAG_PREFIX)), None)
    projects = list(
        dict.fromkeys(
            t
            for t in tags
            if isinstance(t, str) and t.startswith(_PROJECT) and t != PROJECT_UNRESOLVED_TAG
        )
    )
    if agent is None or any("*" in t for t in (agent, *projects)):
        return COMBINED_OBSERVATION_SCOPES
    base = [agent, _VISIBILITY_PRIVATE]
    if not projects:
        return [base]
    return [[*base, project] for project in projects]


#: A ``tag_groups`` entry no memory passes: private and not private at once.
_NOTHING_GROUP: dict[str, Any] = {
    "and": [
        {"tags": [VISIBILITY_PRIVATE_TAG], "match": "any_strict"},
        {"not": {"tags": [VISIBILITY_PRIVATE_TAG], "match": "any_strict"}},
    ]
}


def _own_private_group(agent_id: str | None) -> dict[str, Any]:
    """The engine group for a read of the two-bank private bank: the caller's
    own ``agent:`` tag. A caller with no identity (none, ``anonymous`` or
    ``unknown``) owns nothing there, so its group admits nothing, which keeps
    ``agent:unknown`` notes unreadable (#3560)."""
    if not is_identified(agent_id):
        return _NOTHING_GROUP
    return {"tags": [f"{_AGENT_TAG_PREFIX}{agent_id}"], "match": "any_strict"}


# The front-door sentinel for a missing X-hal0-Agent; collapses to ``unknown``
# on both write (agent tag) and read (caller identity) so an absent identity
# can never match a real agent's private docs. Both names are owned by the
# write envelope (``hal0.memory.envelope``), which stamps the ``agent:`` tag.
_ANONYMOUS = ANONYMOUS_AGENT
_UNKNOWN_AGENT = UNKNOWN_AGENT

# The retired ``project:<id>`` namespace (#1300, ADR-0005) still reaches the
# provider from direct callers. Where it lands in the platform bank (unified,
# two-bank) the project survives as a tag, spelled as the namespace name
# (``project:apollo``), so there is one spelling of a project across banks,
# tags and the wire. ``project:`` is a scope a read may narrow by, never a
# visibility wall (ADR-0058 decision 7b).
_PROJECT = "project:"

#: A read's ``project`` field that asks for every project: the whole bank.
ALL_PROJECTS = "*"

# Page size for the pre-delete visibility scan (unified mode). delete() lists
# the caller's banks to resolve each target doc's owner before removing it; a
# page big enough that a normal delete resolves in one round-trip.
_DELETE_SCAN_PAGE = 500


def _agent_of(tags: list[str] | tuple[str, ...] | None) -> str | None:
    """Return the ``<id>`` of the first ``agent:<id>`` tag, else None."""
    for t in tags or ():
        if isinstance(t, str) and t.startswith(_AGENT_TAG_PREFIX):
            return t[len(_AGENT_TAG_PREFIX) :]
    return None


def namespace_to_bank(namespace: str) -> str:
    """Map a hal0 namespace to a Hindsight bank id (spec §3 table)."""
    return namespace.replace(":", "__")


def bank_to_namespace(bank: str) -> str:
    """Inverse of :func:`namespace_to_bank` — map a Hindsight bank id back to
    its hal0 namespace (``private__hermes`` -> ``private:hermes``; ``shared``
    stays ``shared``). Only the prefix delimiter is rewritten, so agent ids
    that contain single underscores survive untouched."""
    return bank.replace("__", ":", 1)


def _bank_namespace(bank: str, platform_bank: str = _SHARED) -> str:
    """Namespace a Hindsight bank id answers for on the wire.

    The platform bank ([memory] bank, ADR-0052) is the ``shared`` namespace
    whatever it is called; every other bank id goes through
    :func:`bank_to_namespace`'s inverse spelling.
    """
    if bank == platform_bank:
        return _SHARED
    return bank.replace("__", ":")


class Hal0Reranker:
    """Async reranker over hal0-api's OpenAI surface (Cohere-style ``/v1/rerankings``).

    POSTs {model, query, documents} to ``{base_url}/v1/rerankings`` (served by
    the rerank container slot via the dispatcher) and returns the raw
    ``results`` list (``[{"index", "relevance_score"}, ...]``, NOT pre-sorted)
    that HindsightProvider._rerank_union maps onto the cross-bank union.
    Fail-soft: returns [] on any error (gateway down, model load fail, bad
    shape, timeout) so recall falls back to fused order.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        model: str = "builtin.jina-reranker-v1-tiny-en-q8",
        connect_timeout_s: float = 1.0,
        read_timeout_s: float = 8.0,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._model = model
        self._connect_timeout_s = float(connect_timeout_s)
        self._read_timeout_s = float(read_timeout_s)

    async def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        if not self._base_url or not documents:
            return []
        import httpx

        from hal0.http_client import async_client

        payload = {"model": self._model, "query": query, "documents": list(documents)}
        timeout = httpx.Timeout(
            connect=self._connect_timeout_s, read=self._read_timeout_s, write=2.0, pool=None
        )
        try:
            async with async_client(timeout=timeout) as client:
                resp = await client.post(f"{self._base_url}/v1/rerankings", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception:
            return []
        results = body.get("results") if isinstance(body, dict) else None
        return results if isinstance(results, list) else []


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_metadata(md: dict[str, Any]) -> dict[str, Any]:
    """Re-inflate nested metadata values the write path flattened (#1905).

    ``retain`` stores nested dict/list values as JSON strings (Hindsight's
    metadata is flat string→string). Callers of the engine-neutral surface
    handed ``add`` a real dict, so read adapters must hand the same shape
    back — otherwise every REST/MCP consumer gets ``metadata.x`` as a
    string and nesting doesn't round-trip (only decoded case-by-case in
    CLI commands). Only values that LOOK like JSON containers are decoded;
    scalars were always stringified on this engine and stay strings, so
    existing consumers see no change.
    """
    out: dict[str, Any] = {}
    for k, v in md.items():
        if isinstance(v, str) and v[:1] in "{[":
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError:
                out[k] = v
                continue
            out[k] = parsed if isinstance(parsed, (dict, list)) else v
            continue
        out[k] = v
    return out


# ── time-window filtering + list cursors (#1471) ─────────────────────────────


def _parse_stamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 → aware datetime; ``None`` when unparseable.

    Accepts the bare dates callers actually type (``2026-05-01``) as well as
    full timestamps, and normalises a naive value to UTC so comparisons against
    Hindsight's aware ``mentioned_at`` never raise.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _within_window(item: dict[str, Any], *, before: Any = None, after: Any = None) -> bool:
    """Is ``item``'s timestamp inside ``[after, before]``?

    Bounds are inclusive-of-unknown: an item whose timestamp will not parse is
    KEPT rather than dropped, because silently discarding a memory over a
    formatting quirk is worse than returning one the caller can see and judge.
    An unparseable BOUND is ignored for the same reason (a typo'd filter must
    not look like an empty bank).
    """
    stamp = _parse_stamp(item.get("timestamp"))
    if stamp is None:
        return True
    lower = _parse_stamp(after)
    if lower is not None and stamp < lower:
        return False
    upper = _parse_stamp(before)
    return not (upper is not None and stamp > upper)


def _encode_cursor(bank: str, offset: int) -> str:
    """``bank@offset`` — opaque to callers, cheap to read in a log line.

    Hindsight's ``/memories/list`` already supports ``offset``, so a cursor
    needs to carry nothing else. The bank is part of it because a read can fan
    out across several banks and a page may stop partway through one.
    """
    return f"{bank}@{offset}"


def _decode_cursor(cursor: str | None, banks: list[str]) -> tuple[str | None, int]:
    """Inverse of :func:`_encode_cursor`, clamped to ``banks``.

    Returns ``(bank, offset)``, or ``(None, 0)`` when the cursor names a bank
    this caller may no longer read — a revoked namespace must not be walkable
    with a cursor minted while access was still granted. A malformed cursor
    restarts from the first bank rather than erroring: a paging token is not
    worth a 500, and restarting is visible to the caller.
    """
    if not banks:
        return None, 0
    if not cursor:
        return banks[0], 0
    bank, _, raw_offset = str(cursor).rpartition("@")
    if not bank or bank not in banks:
        return (None, 0) if bank else (banks[0], 0)
    try:
        offset = max(0, int(raw_offset))
    except ValueError:
        offset = 0
    return bank, offset


class ObservationMultiSourceDelete(BadRequest):
    """A delete named an observation derived from more than one document (#2747).

    An observation has no document of its own; the engine only removes it by
    deleting the documents of its source facts. One source document is the
    same blast radius as an ordinary single-id delete, so that resolves. More
    than one would let a single id, which :func:`hal0.mcp.admin.is_gated` runs
    without approval, wipe several documents and everything derived from
    them. The refusal lists those document ids instead, so the caller deletes
    them explicitly and the multi-id approval gate applies. ``details`` is
    ``{"document_ids": [...], "observations": {observation_id: [...]}}``.
    """

    code = "memory.delete.observation_multi_source"


def _refuse_multi_source(ids: list[str], resolved: dict[str, list[str]]) -> None:
    """Raise :class:`ObservationMultiSourceDelete` if any of ``ids`` resolved
    to more than one document. Only an observation can: a document id or a
    per-fact id always names exactly one. ``resolved`` holds only ids that
    passed the ACL, so the listed document ids are ones the caller may read.
    """
    multi = {i: resolved[i] for i in dict.fromkeys(ids) if len(resolved.get(i, [])) > 1}
    if not multi:
        return
    document_ids = list(dict.fromkeys(d for docs in multi.values() for d in docs))
    raise ObservationMultiSourceDelete(
        f"observation {', '.join(multi)} is derived from {len(document_ids)} documents; "
        "deleting it would delete all of them. Delete those document ids explicitly "
        "instead (a multi-id delete needs approval).",
        details={"document_ids": document_ids, "observations": multi},
    )


def _http_status(exc: Exception) -> int | None:
    """Status code of an httpx.HTTPStatusError-shaped exception, else None.

    Duck-typed (``exc.response.status_code``) so the fake clients in tests
    don't need httpx to exercise the 404-sweep behavior."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return int(code) if isinstance(code, int) else None


# Hindsight recall defaults to world+experience when ``types`` is omitted,
# which silently hides the consolidated observation layer — the highest-value
# tier (deduplicated, evidence-grounded beliefs). hal0's default includes it;
# callers can still narrow with an explicit ``types``.
_DEFAULT_RECALL_TYPES = ("world", "experience", "observation")

# ── raw-document recall fallback (#1794) ──────────────────────────────────
#
# Fact extraction rewrites facts as meta-observations and can drop the
# literal value entirely (worst on small extraction models) — a marker,
# ID, or key stored in good faith becomes unrecallable even though the raw
# document text still holds it. Hindsight has no engine-side full-text
# search over raw document bodies (``GET .../documents?q=`` matches the
# document ID, not its content — confirmed against the live daemon's
# OpenAPI schema), so this is the best hal0-side approximation: detect
# literal-looking tokens in the query, and if none of the extracted facts
# already contain them, scan a bounded page of the bank's most recent raw
# documents and substring-match. The real fix is an engine-side raw-content
# search endpoint (documented in issue #1794); this is a client-side
# stopgap that AUGMENTS fact results with provenance, never replaces them.

#: A "literal-looking" token: an alnum run (with ``-``/``_``) at least 4
#: chars long that mixes letters and digits — catches markers (MARKER-7734),
#: host/box ids (CT151), version-ish codes, etc. Plain words never match
#: (no digit), so ordinary queries never trigger the fallback scan.
_LITERAL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")

#: Bounded per-bank document scan — the engine has no content search, so
#: this is a page-and-fetch-each walk; keep it cheap enough to run inline
#: on every recall that has an uncovered literal token.
_DOC_FALLBACK_MAX_DOCS_PER_BANK = 25
#: Cap on how many raw-document results the fallback ever appends.
_DOC_FALLBACK_MAX_RESULTS = 5

# ── retain-pipeline health (#1420) ────────────────────────────────────────
#
#: How long an observed write failure keeps ``write_degraded`` true. A single
#: later retain that the daemon ACCEPTS is not evidence of recovery — accepting
#: a retain into a queue whose extraction step is failing is exactly the
#: behaviour that made the old surface green while nothing landed for 8 days.
#: The window is what clears the flag, so recovery is timed rather than
#: claimed, and no restart is needed once the pipeline is genuinely healthy.
_WRITE_FAILURE_HOLD_S = 600.0

#: Minimum age of the cached :meth:`HindsightProvider.write_health` verdict
#: before a fresh probe is issued. ``/api/status`` is polled every few seconds
#: by the dashboard; the engine's operations counters are not.
_WRITE_HEALTH_TTL_S = 30.0

#: Operation statuses sampled from the engine's operations endpoint.
_WRITE_OP_STATUSES = ("failed", "pending", "processing")

#: Age past which an in-flight (``pending``/``processing``) retain op counts
#: as stalled (#1833). Mirrors the threshold the Hindsight worker itself logs
#: ``[STUCK_STACK] … threshold=600s`` at. Ops wedged in flight never touch
#: the ``failed`` counter, so without this the verdict read "landing" on a
#: store that had never held a fact.
_WRITE_STALL_AGE_S = 600.0

#: Completed retain rows scanned for the newest completion (#1833). The
#: engine's list is ordered by ``created_at`` only and caps ``limit`` at 100
#: (hindsight-api 0.9.2 ``GET …/operations``: ``limit`` is ``le=100``, no sort
#: parameter), so the latest completion is the greatest ``updated_at`` over
#: this bounded window of most-recently-created completed rows.
_WRITE_STALL_DONE_PAGE = 100


# ── auto-retry of no-chat-model dead letters (#1792) ──────────────────────
#
# A fresh install ships every llm slot model-less (WS-E #1107); until the
# operator loads one, every retain's extraction call 404s against hal0's own
# dispatcher (dispatch.no_route). Hindsight's poller exhausts its own retry
# ladder (~28 minutes) and dead-letters the op — nothing then re-queues it
# once a model finally loads. ``maybe_auto_retry_dead_letters`` closes that
# gap; these bounds keep it from ever becoming an unattended retry storm for
# a *different*, genuinely broken pipeline.

#: Minimum gap between two auto-retry sweeps.
_AUTO_RETRY_COOLDOWN_S = 60.0

#: Hard cap on sweeps for this provider's lifetime. A model-availability
#: window closes once, so a handful of sweeps is plenty; capping means a
#: pipeline that is failing for some OTHER reason surfaces as genuinely
#: FAILING again after this budget instead of retrying forever.
_AUTO_RETRY_MAX_SWEEPS = 5

#: Cap on ops requeued per sweep — a backstop against a runaway ledger.
_AUTO_RETRY_MAX_OPS = 200


# ── private visibility on the bank-wide read tools (#3573) ──────────────────

#: Page size and page cap of the scan for private documents another agent
#: owns. Past the cap the scan answers "present" (fail closed).
_PRIVATE_SCAN_PAGE = 100
_PRIVATE_SCAN_PAGES = 10
#: Documents read per tag when deciding whether a tag that other agents'
#: private documents carry is also on a document the caller can see.
_TAG_CHECK_LIMIT = 50
#: Engine reads at a time when a model listing's rows need their trigger.
_TRIGGER_READS = 8
#: Operation types that carry or produce memory content: a retain's submitted
#: text, a document or bank import's archive, an export of one.
_CONTENT_OP_TYPES = frozenset(
    {
        "retain",
        "batch_retain",
        "file_convert_retain",
        "import_documents",
        "export_documents",
        "import_bank",
        "export_bank",
    }
)
#: Fields of a mental model that carry its generated content.
_MODEL_CONTENT_FIELDS = frozenset({"content", "reflect_response", "structured_content"})
#: Extra engine pages a documents listing reads to fill a page that rows the
#: caller may not see left short.
_LISTING_EXTRA_FETCHES = 3
#: Bank stats fields kept when the bank holds other agents' private memory:
#: operational health, no volume. The admin API has the full record.
_STATS_HEALTH_FIELDS = (
    "bank_id",
    "pending_operations",
    "failed_operations",
    "operations_by_status",
    "last_consolidated_at",
    "pending_consolidation",
    "failed_consolidation",
)


def _visible_total(offset: int | None, shown: int, limit: int | None) -> int:
    """A listing's ``total`` that counts only rows the caller can see (#3573).

    ``offset`` plus the rows shown, plus one when they fill the page (there
    may be more). It is derived from nothing the caller cannot see: not the
    engine's count, and not whether the engine's page was full, both of which
    include rows left out. A page that rows left out made short can therefore
    read as the last one.
    """
    full = limit is not None and limit > 0 and shown >= limit
    return (offset or 0) + shown + (1 if full else 0)


# ── zero-fact retain visibility (#2030) ─────────────────────────────────────

#: Operation types whose completion means "fact extraction ran over a
#: document" — the ones a yield annotation is meaningful for.
_RETAIN_OP_TYPES = {"retain", "batch_retain"}


async def annotate_retain_yield(op: Any, fetch_document: Any) -> Any:
    """Stamp extraction *yield* onto a terminal retain operation record.

    A retain whose fact extraction produced zero units used to finish as a
    bare ``status=completed, retry_count=0, error_message=None`` — the raw
    document was stored, nothing was ever retrievable, and no surface said so
    (#2030, rc-validate ``memory-retain-zero-yield-reports-completed``). This
    annotates the engine's record (verified live against hindsight-api 0.9.2:
    the single-op shape carries ``operation_type`` +
    ``result_metadata.unit_ids_count``; list rows carry ``task_type`` +
    ``document_id`` and no unit count) with:

    * ``facts_extracted`` — memory units this operation created
      (``result_metadata.unit_ids_count``), falling back to the document's
      total ``memory_unit_count`` on engines whose op record has no
      ``result_metadata``;
    * ``document_fact_count`` — the document's total units, fetched only when
      the operation's own yield is zero or unknown. This is what tells a
      genuinely-unretrievable retain apart from a duplicate-``content_hash``
      retain of an already-extracted document (correct dedup, per the rc.7
      adversarial verification — not a defect);
    * ``nothing_learned: true`` plus a human-readable ``notice`` — only when
      the document itself holds zero units, i.e. the retain stored nothing
      that search/recall can ever return. Empty yield stays ``completed`` —
      it is a legitimate outcome that must be *visible*, not reclassified as
      an error.

    Fail-soft by construction: a document fetch that raises leaves
    ``document_fact_count`` unset rather than failing the poll, and non-dict
    payloads / non-retain / non-completed ops pass through untouched.
    ``fetch_document`` is ``async (document_id) -> dict | None``.
    """
    if not isinstance(op, dict):
        return op
    op_type = op.get("task_type") or op.get("operation_type")
    if op_type not in _RETAIN_OP_TYPES or op.get("status") != "completed":
        return op
    meta = op.get("result_metadata")
    meta = meta if isinstance(meta, dict) else {}
    created = meta.get("unit_ids_count")
    created = created if isinstance(created, int) else None

    doc_units: int | None = None
    if created is None or created == 0:
        doc_id = op.get("document_id") or meta.get("document_id")
        if doc_id and fetch_document is not None:
            try:
                doc = await fetch_document(str(doc_id))
            except Exception:
                doc = None
            if isinstance(doc, dict):
                mu = doc.get("memory_unit_count")
                doc_units = mu if isinstance(mu, int) else None

    facts = created if created is not None else doc_units
    if facts is None:
        return op
    op["facts_extracted"] = facts
    if doc_units is not None:
        op["document_fact_count"] = doc_units
    if facts == 0:
        if doc_units:
            # This op added nothing NEW, but the document's facts exist and
            # are retrievable — the duplicate-retain / re-ingest case.
            op["notice"] = (
                "completed with no new facts: this document's content was "
                f"already known ({doc_units} fact(s) previously extracted from it)"
            )
        else:
            op["nothing_learned"] = True
            op["notice"] = (
                "completed, but nothing was learned: fact extraction produced "
                "0 memory units from this document — the raw text was stored, "
                "but nothing from it is retrievable via search or recall"
            )
    return op


class RecallResults(list):
    """``recall()``'s return value: a ``list[dict]`` of MemoryItem-shaped
    results, PLUS the response-level enrichment Hindsight's RecallResponse
    carries alongside ``results`` that has no per-item home — entity mental
    models, raw chunks, and observation source facts.

    Every existing caller (``search()``, the MCP ``memory_recall`` handler,
    every test in the suite) treats a recall as a plain list — iterating it,
    slicing it, comparing it to ``[]`` — and none of that changes: this IS a
    list, byte-for-byte, via ``list.__eq__``/``__iter__``/``__getitem__``.
    Only a caller that explicitly reads ``.entities``/``.chunks``/
    ``.source_facts`` sees the data recall() used to silently discard.
    """

    def __init__(
        self,
        items: list[dict[str, Any]],
        *,
        entities: dict[str, Any] | None = None,
        chunks: dict[str, Any] | None = None,
        source_facts: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(items)
        self.entities = entities
        self.chunks = chunks
        self.source_facts = source_facts


class HindsightProvider(MemoryProvider):
    def __init__(
        self,
        *,
        client: Any,
        client_id: str = "anonymous",
        reranker: Any = None,
        graph_enabled: bool = False,
        extraction_slot: str = "memory",
        unified_bank: bool = False,
        bank: str = _SHARED,
        two_bank: bool = False,
        private_bank: str = TWO_BANK_PRIVATE_BANK,
        projects: ProjectRegistry | None = None,
    ) -> None:
        self._client = client
        # The project registry ([memory.projects], ADR-0058 decision 4a):
        # aliases, the deny list and the confirmed names every write's project
        # is resolved against. Absent means the shipped defaults.
        self._projects = projects if projects is not None else ProjectRegistry()
        # Engine bank id behind the logical ``shared`` namespace ([memory] bank,
        # ADR-0052). The namespace grammar is untouched; only this mapping
        # moves. ``private:<agent>`` / ``agents`` banks keep their own names.
        self._platform_bank = bank or _SHARED
        self._client_id = client_id
        self._reranker = reranker
        # The bank layout (ADR-0058 decision 7), decided per store by
        # ``hal0.memory.bank_layout`` and handed in by ``provider_from_config``.
        #
        # Two-bank: ``shared`` is the platform bank and every ``private:<id>``
        # is ``private_bank``, one bank for all agents, where each private
        # write is stamped ``agent:<id>`` + ``visibility:private`` and a read
        # of that bank is held to the caller's own ``agent:`` tag.
        #
        # Legacy unified ([memory] unified_bank on a store from before):
        # every namespace collapses onto the platform bank; a private write is
        # stamped the same way and recall stops fanning out. Legacy split: a
        # ``private__<agent>`` bank per agent. The constructor defaults to the
        # split layout so directly-constructed providers keep the oldest
        # behaviour; the live layout always comes from the record.
        self._two_bank = bool(two_bank)
        self._unified_bank = bool(unified_bank) and not self._two_bank
        self._private_bank = private_bank or TWO_BANK_PRIVATE_BANK
        if self._two_bank and self._private_bank == self._platform_bank:
            raise ValueError("the private bank must differ from the platform bank")
        # ADR-0023: reporting-only on this engine (Hindsight builds its graph
        # natively via its own extraction LLM, which hal0 points at the
        # `extraction_slot` via the hindsight-api systemd drop-in). Seeded from
        # [memory.graph] so graph_status() agrees with hal0.toml.
        self._graph_enabled = bool(graph_enabled)
        self._extraction_slot = extraction_slot
        self._rerank_enabled = reranker is not None
        # Live engine reachability (#1301). The factory only builds this
        # provider after a successful boot probe, so False is the correct
        # starting point; every engine call updates it (see ``_call``).
        self._degraded = False
        # Retain-pipeline health (#1420) — deliberately NOT the same field.
        # ``_last_write_failure_at`` is a monotonic stamp set by either
        # observation (a retain that raised, or the engine's failed-operation
        # counter increasing); ``write_degraded`` is that stamp inside the
        # hold window. ``_ops_sample`` is the previous operations snapshot the
        # delta is taken against — an absolute failed count is useless because
        # the counter is cumulative and never returns to zero.
        self._last_write_failure_at: float | None = None
        self._last_write_error: str | None = None
        self._last_write_reason: str | None = None
        self._ops_sample: dict[str, int] | None = None
        # Edge-trigger for the #1833 stall log line; the verdict itself is
        # recomputed from engine timestamps on every sample, not held here.
        self._write_stalled = False
        self._write_health: dict[str, Any] | None = None
        self._write_health_at: float | None = None
        # Auto-retry bookkeeping (#1792) — see the constants above.
        self._auto_retry_last_at: float | None = None
        self._auto_retry_sweeps_done = 0

    @property
    def platform_bank(self) -> str:
        """Engine bank id that backs the ``shared`` namespace ([memory] bank)."""
        return self._platform_bank

    @property
    def unified_bank(self) -> bool:
        """Whether every namespace shares the platform bank (the old unified
        layout), so that bank also holds each agent's ``visibility:private``
        memories. ``False`` in the two-bank layout."""
        return self._unified_bank

    @property
    def two_bank(self) -> bool:
        """Whether the store is on the two-bank layout (ADR-0058 decision 7)."""
        return self._two_bank

    @property
    def private_bank(self) -> str | None:
        """Engine bank behind every ``private:<id>`` namespace in the two-bank
        layout; ``None`` on the old layout."""
        return self._private_bank if self._two_bank else None

    @property
    def layout(self) -> str:
        """``two_bank``, ``legacy_unified`` or ``legacy_split``."""
        if self._two_bank:
            return HEALTH_TWO_BANK
        return HEALTH_LEGACY_UNIFIED if self._unified_bank else HEALTH_LEGACY_SPLIT

    def holds_other_agents_private(self, namespace: str) -> bool:
        """Whether the bank ``namespace`` maps to can hold other agents' private
        memories, which a mental model built from the whole bank would carry."""
        return self._holds_others_private(self._bank_of(self._write_namespace(namespace, None)))

    @property
    def _acl_enforced(self) -> bool:
        """Whether one bank holds more than one agent's private memory, so
        every read and delete is held to the caller's own (unified, two-bank)."""
        return self._unified_bank or self._two_bank

    @property
    def projects(self) -> ProjectRegistry:
        """The project registry writes are resolved against ([memory.projects])."""
        return self._projects

    def bank_for(self, namespace: str) -> str:
        """Engine bank id a namespace maps to (public face of :meth:`_bank_of`)."""
        return self._bank_of(namespace)

    def _bank_of(self, namespace: str) -> str:
        """Hindsight bank id for a resolved namespace.

        ``shared`` is the platform bank. In the two-bank layout every
        ``private:<id>`` is the private bank; otherwise a namespace keeps the
        :func:`namespace_to_bank` spelling (``private:x`` -> ``private__x``).
        """
        if namespace == _SHARED:
            return self._platform_bank
        if self._two_bank and namespace.startswith(_PRIVATE):
            return self._private_bank
        return namespace_to_bank(namespace)

    def _dataset_of(self, bank: str, tags: Any) -> str:
        """The namespace an item read from ``bank`` is reported under.

        Items of the two-bank private bank answer for their owner's
        ``private:<agent>``, from the item's own ``agent:`` tag."""
        if self._two_bank and bank == self._private_bank:
            return f"{_PRIVATE}{_agent_of(tags) or _UNKNOWN_AGENT}"
        return _bank_namespace(bank, self._platform_bank)

    def _label(self, item: dict[str, Any], bank: str) -> dict[str, Any]:
        """``item`` with the namespace it was read from (:meth:`_dataset_of`)."""
        item["dataset"] = self._dataset_of(bank, item.get("tags"))
        return item

    @property
    def hindsight_client(self) -> Any:
        """REST client handle for the engine admin surface (memory_admin routes)."""
        return self._client

    @property
    def extraction_slot(self) -> str:
        """The local llm slot Hindsight's native extraction is pointed at.

        Read by ``/api/status`` (#1792) to tell "writes are failing because
        this slot has no model bound yet" apart from a genuine pipeline
        failure — the route layer owns the slot-manager lookup (this
        provider has no slot-manager handle), so it only needs the name.
        """
        return self._extraction_slot

    # ── Live engine health (#1301) ─────────────────────────────────────

    @property
    def degraded(self) -> bool:
        """True when the Hindsight daemon is NOT answering.

        The BOOT-time degrade ladder (``hal0.memory.provider_from_config`` +
        ``hindsight_client.probe_health``) swaps in :class:`PgVectorProvider`
        — whose ``degraded`` is a constant ``True`` — when the daemon is down
        at boot. But a daemon that dies *after* boot leaves this provider in
        place, and the boot probe has no opinion about that: before this,
        ``/api/status.memory_degraded`` and ``hal0 memory status`` kept
        reporting healthy while every recall came back empty and every retain
        raised. A boot-only probe starts lying the moment the daemon dies.

        This flag tracks what the engine actually did on the last call, so the
        reported state matches reality — and it clears itself when the daemon
        comes back, which a boot probe cannot do either.
        """
        return self._degraded

    @staticmethod
    def _engine_answered(exc: Exception) -> bool:
        """True when ``exc`` proves the daemon is up (it sent an HTTP response).

        Duck-typed on ``exc.response.status_code`` (via the module's existing
        :func:`_http_status`) so the fake clients in tests need no httpx. A 5xx
        counts as NOT answering — it matches the boot probe's rule in
        ``hindsight_client.probe_health``, and a daemon returning 500s is not
        usable memory. 4xx passes: a 404 on one bank during a delete sweep, or
        a 401 from a stale key, does not mean the engine is down.
        """
        code = _http_status(exc)
        return code is not None and code < 500

    def _mark_engine(self, *, reachable: bool, error: str | None = None) -> None:
        """Record the outcome of one engine call; log only on a state change.

        Edge-triggered on purpose: a down daemon is hit on every recall, and an
        unconditional log would emit a line per call for as long as the outage
        lasts.
        """
        if reachable == (not self._degraded):
            return
        self._degraded = not reachable
        if reachable:
            log.info("hal0.memory.hindsight_recovered")
        else:
            log.warning("hal0.memory.hindsight_unreachable_runtime", error=error)

    # ── Live retain-pipeline health (#1420) ─────────────────────────────

    @property
    def write_degraded(self) -> bool:
        """True when a memory WRITE was recently observed to fail.

        Distinct from :attr:`degraded` on purpose. That flag answers "is the
        daemon answering?" and on the box that produced #1420 the honest
        answer was *yes*: it accepted every retain with a ``200`` and an
        ``operation_id``, served recalls in 11s, and passed ``/health`` — while
        fact extraction 503'd against an offline slot, 170 operations sat
        failed, and the newest durable fact was 8 days old. Widening
        ``degraded`` to cover that would both break #1301's contract and
        misreport the read path, which genuinely worked.

        Two observations feed this, both of the write path itself: a retain
        that raises, and the engine's own failed-operation counter increasing
        between two samples (see :meth:`write_health`). The second is the one
        that catches the reported failure — in that mode no retain ever raises.
        """
        if self._last_write_failure_at is None:
            return False
        return (time.monotonic() - self._last_write_failure_at) < _WRITE_FAILURE_HOLD_S

    def _mark_write_failure(self, error: str, *, reason: str) -> None:
        """Record an observed write-path failure; log only on a state change."""
        was = self.write_degraded
        self._last_write_failure_at = time.monotonic()
        self._last_write_error = error
        self._last_write_reason = reason
        if not was:
            log.warning("hal0.memory.retain_pipeline_failing", reason=reason, error=error)

    async def _sample_operations(self, bank: str) -> dict[str, int] | None:
        """Current ``{status: total}`` counts for ``bank``, or None if the
        engine can't answer (older build, outage, permission).

        Fail-soft by contract: this runs behind ``/api/status``, which must
        never 500 because a health probe did.
        """
        counts: dict[str, int] = {}
        for status in _WRITE_OP_STATUSES:
            try:
                resp = await self._client.request_json(
                    "GET",
                    f"/v1/default/banks/{bank}/operations",
                    params={"status": status, "limit": 1},
                )
            except Exception as exc:
                log.debug("hal0.memory.operations_probe_failed", error=str(exc))
                return None
            total = (resp or {}).get("total") if isinstance(resp, dict) else None
            counts[status] = int(total) if isinstance(total, int) else 0
        return counts

    async def _retain_ops(
        self, bank: str, status: str, op_type: str, *, limit: int, offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]] | None:
        """``(total, rows)`` of ``bank``'s ``status`` ops of type ``op_type``.

        Uses the engine's server-side ``type`` filter (hindsight-api 0.9.2
        ``GET /v1/default/banks/{bank}/operations?status=&type=``, matched
        against ``operation_type``), so ``total`` and offsets count only that
        type. The engine orders the list ``created_at DESC`` (newest first)
        and offers no other sort. Rows are re-checked client-side, so an
        engine that ignored the filter yields no rows rather than another
        task type's. None when the probe fails or the reply has no integer
        ``total`` — fail-soft like :meth:`_sample_operations`.
        """
        try:
            resp = await self._client.request_json(
                "GET",
                f"/v1/default/banks/{bank}/operations",
                params={"status": status, "type": op_type, "limit": limit, "offset": offset},
            )
        except Exception as exc:
            log.debug("hal0.memory.operations_probe_failed", error=str(exc))
            return None
        if not isinstance(resp, dict):
            return None
        total = resp.get("total")
        if not isinstance(total, int) or isinstance(total, bool):
            return None
        rows = [
            r
            for r in resp.get("operations") or []
            if isinstance(r, dict) and r.get("task_type") == op_type and r.get("status") == status
        ]
        return total, rows

    @staticmethod
    def _row_age_s(row: dict[str, Any] | None, field: str, now: datetime) -> float | None:
        """Seconds since ``row[field]`` (an ISO-8601 stamp), or None if absent/unparseable."""
        raw = row.get(field) if row else None
        if not isinstance(raw, str):
            return None
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return (now - stamp).total_seconds()

    async def _oldest_in_flight_age(
        self, bank: str, status: str, op_type: str, now: datetime
    ) -> float | None:
        """Age in seconds of the oldest ``status`` op of ``op_type``, or None.

        The oldest row sits at ``offset = total - 1`` of the newest-first
        list, but ``total`` comes from an earlier request: retains queued in
        between shift every offset and would land the lookup on a fresh row.
        Each reply carries its own ``total``, so the lookup retries once at
        the new offset when it moved. If it moved again, the row in hand is
        still used: on a newest-first list every row is no older than the
        head, so its age is a LOWER bound — it can miss a stall, never invent
        one.
        """
        head = await self._retain_ops(bank, status, op_type, limit=1)
        if head is None:
            return None
        total = head[0]
        rows: list[dict[str, Any]] = []
        for _attempt in range(2):
            if total <= 0:
                return None
            page = await self._retain_ops(bank, status, op_type, limit=1, offset=total - 1)
            if page is None:
                return None
            seen, rows = page
            if seen == total:
                break
            total = seen
        return self._row_age_s(rows[0] if rows else None, "created_at", now)

    async def _latest_completion_age(self, bank: str, now: datetime) -> float | None:
        """Seconds since the newest retain completion, ``inf`` if none is listed,
        or None when there is no usable evidence (probe failed, stamps unparseable).

        Completion time is the row's ``updated_at`` (set with ``completed_at``
        when it completes), but the engine lists by ``created_at``: an older
        retain that finishes now sits below newer ones that finished long
        ago. So this takes the greatest ``updated_at`` over a bounded page of
        each retain type rather than the first row's.
        """
        best: float | None = None
        listed = False
        for op_type in sorted(_RETAIN_OP_TYPES):
            page = await self._retain_ops(bank, "completed", op_type, limit=_WRITE_STALL_DONE_PAGE)
            if page is None:
                return None
            for row in page[1]:
                listed = True
                age = self._row_age_s(row, "updated_at", now)
                if age is not None and (best is None or age < best):
                    best = age
        if best is not None:
            return best
        return None if listed else float("inf")

    async def _stall_detail(self, bank: str, counts: dict[str, int]) -> str | None:
        """Describe a stalled retain pipeline on ``bank``, or None if it isn't (#1833).

        Stalled means the OLDEST in-flight (``pending``/``processing``) RETAIN
        op has been in flight past :data:`_WRITE_STALL_AGE_S` AND no retain
        has completed within that window. Only retain task types count
        (:data:`_RETAIN_OP_TYPES`): the operations list also carries
        consolidation, mental-model refreshes and the like, and an old
        consolidation is not the write path stalling, nor does its completion
        prove retains are landing. Both halves read engine timestamps, so the
        verdict needs no hal0-side state and is right on the first sample
        after a restart:

        * the oldest in-flight op is found per status and type by
          :meth:`_oldest_in_flight_age`, so a wedged queue that keeps
          accepting fresh retains cannot hide its stale head;
        * the newest completion comes from :meth:`_latest_completion_age`. A
          completed COUNT would not do — terminal ops are pruned by retention,
          so it can stay flat or drop while ops are completing.

        A completion inside the window keeps a long-but-draining backlog
        green and is what clears the verdict — recovery takes one completed
        op, not a timer. Missing or unparseable timestamps, and probes that
        fail, are no evidence either way and never produce a stall.
        """
        now = datetime.now(UTC)
        oldest: float | None = None
        for status in ("pending", "processing"):
            # The unfiltered count gates the typed lookups: nothing in flight
            # of any type means no retain is either, and no extra requests.
            if (counts.get(status) or 0) <= 0:
                continue
            for op_type in sorted(_RETAIN_OP_TYPES):
                age = await self._oldest_in_flight_age(bank, status, op_type, now)
                if age is not None and (oldest is None or age > oldest):
                    oldest = age
        if oldest is None or oldest < _WRITE_STALL_AGE_S:
            return None

        since_done = await self._latest_completion_age(bank, now)
        if since_done is None or since_done < _WRITE_STALL_AGE_S:
            return None
        return (
            f"retain operation(s) on bank {bank} in flight for over "
            f"{int(_WRITE_STALL_AGE_S)}s (oldest {int(oldest)}s) with none completing"
        )

    async def _fact_count(self, bank: str) -> int | None:
        """Facts stored in ``bank`` per the engine's stats, or None if unreadable.

        The stats payload reports ``total_nodes`` (memory units); a
        ``fact_count`` key, as the banks listing uses, is honoured too.
        Fail-soft like :meth:`_sample_operations`: no answer is no evidence.
        """
        try:
            stats = await self._client.bank_stats(bank_id=bank)
        except Exception as exc:
            log.debug("hal0.memory.bank_stats_probe_failed", error=str(exc))
            return None
        if not isinstance(stats, dict):
            return None
        for key in ("total_nodes", "fact_count"):
            val = stats.get(key)
            if isinstance(val, int) and not isinstance(val, bool):
                return val
        return None

    async def write_health(self, *, max_age_s: float | None = None) -> dict[str, Any]:
        """Retain-pipeline health for ``/api/status`` and ``hal0 memory status``.

        Returns ``{degraded, reason, last_error, operations, bank}``.
        ``reason`` is one of:

        ``retain_failed``
            A retain call itself raised inside the hold window.
        ``retain_operations_failing``
            The engine's ``failed`` operation counter grew between two samples
            — retains are being accepted and then dying in extraction. This is
            the #1420 shape.
        ``retain_operations_stalled``
            An op has sat ``pending``/``processing`` past
            :data:`_WRITE_STALL_AGE_S` and none has completed in that window
            (#1833). Live, not held: it clears on the first completed op.
        ``no_write_ever_landed``
            The bank holds failed ops, zero facts, and no retain has ever
            completed (#2404). Catches a static backlog after a restart,
            when the ``failed`` delta above has no history. A fresh install
            (``failed == 0``) is not this.
        ``ok``
            Two clean samples, nothing failing.
        ``unknown``
            The engine could not be sampled (no operations endpoint, or an
            outage) and no retain has raised. Reported as NOT degraded but
            explicitly unknown, so a caller can tell "healthy" from "no data".

        The verdict is TTL-cached (``_WRITE_HEALTH_TTL_S``) because the
        dashboard polls ``/api/status`` every few seconds. Pass ``max_age_s=0``
        to force a fresh sample.
        """
        ttl = _WRITE_HEALTH_TTL_S if max_age_s is None else max_age_s
        now = time.monotonic()
        if (
            self._write_health is not None
            and self._write_health_at is not None
            and (now - self._write_health_at) < ttl
        ):
            return dict(self._write_health)

        bank = self._bank_of(self._write_namespace(_SHARED, None))
        counts = await self._sample_operations(bank)
        stall: str | None = None
        if counts is not None:
            previous = self._ops_sample
            self._ops_sample = counts
            if previous is not None and counts["failed"] > previous["failed"]:
                delta = counts["failed"] - previous["failed"]
                self._mark_write_failure(
                    f"{delta} retain operation(s) failed on bank {bank} since the last check",
                    reason="retain_operations_failing",
                )
            stall = await self._stall_detail(bank, counts)
        if (stall is not None) != self._write_stalled:
            self._write_stalled = stall is not None
            if stall is not None:
                log.warning("hal0.memory.retain_pipeline_stalled", bank=bank, error=stall)
            else:
                log.info("hal0.memory.retain_pipeline_unstalled", bank=bank)

        held = self.write_degraded
        never_landed: str | None = None
        fact_count: int | None = None
        if counts is not None and not held and stall is None and counts["failed"] > 0:
            fact_count = await self._fact_count(bank)
            if fact_count == 0 and await self._latest_completion_age(
                bank, datetime.now(UTC)
            ) == float("inf"):
                never_landed = (
                    f"No write has landed: {counts['failed']} failed, 0 facts stored on bank {bank}"
                )
        degraded = held or stall is not None or never_landed is not None
        if held:
            reason = self._last_write_reason or "retain_failed"
        elif stall is not None:
            reason = "retain_operations_stalled"
        elif never_landed is not None:
            reason = "no_write_ever_landed"
        elif counts is None:
            reason = "unknown"
        else:
            reason = "ok"

        out: dict[str, Any] = {
            "degraded": degraded,
            "reason": reason,
            "last_error": self._last_write_error if held else (stall or never_landed),
            "operations": dict(counts) if counts is not None else None,
            "bank": bank,
            "fact_count": fact_count,
        }
        self._write_health = out
        self._write_health_at = now
        return dict(out)

    async def maybe_auto_retry_dead_letters(self) -> dict[str, Any] | None:
        """Best-effort auto-retry of failed retain ops once a model is bound (#1792).

        The caller (``hal0.api.routes.health._memory_write_health``) only
        invokes this after confirming the extraction slot now resolves to a
        model-bound llm slot — that is the signal a retry is worth
        attempting, since a stalled no-route window is the one failure mode
        this whole issue is about. Returns ``None`` when there is nothing to
        do (no failed ops, cooldown still running, or the sweep budget is
        spent) or a ``{bank, queued, skipped, sweep}`` tally when a sweep
        actually ran.

        Bounded by :data:`_AUTO_RETRY_COOLDOWN_S` and
        :data:`_AUTO_RETRY_MAX_SWEEPS` so a pipeline that keeps failing for
        some OTHER reason after a model loads stops being auto-retried and
        goes back to reading as genuinely FAILING, rather than retrying
        forever on every ``/api/status`` poll.
        """
        # #3518: a retry is a write. While writes are paused it would queue
        # work on a bank that is about to be replaced.
        if read_gate() is not None:
            return None
        health = await self.write_health()
        ops = health.get("operations") if isinstance(health, dict) else None
        failed = int((ops or {}).get("failed") or 0)
        if failed <= 0:
            return None
        if self._auto_retry_sweeps_done >= _AUTO_RETRY_MAX_SWEEPS:
            return None
        now = time.monotonic()
        if (
            self._auto_retry_last_at is not None
            and (now - self._auto_retry_last_at) < _AUTO_RETRY_COOLDOWN_S
        ):
            return None
        self._auto_retry_last_at = now
        self._auto_retry_sweeps_done += 1

        bank = health.get("bank")
        if not bank:
            return None
        try:
            resp = await self._client.request_json(
                "GET",
                f"/v1/default/banks/{bank}/operations",
                params={"status": "failed", "limit": _AUTO_RETRY_MAX_OPS},
            )
        except Exception as exc:
            log.debug("hal0.memory.auto_retry_list_failed", bank=bank, error=str(exc))
            return None
        ops_list = resp.get("operations") if isinstance(resp, dict) else None
        op_ids = [
            entry.get("id")
            for entry in (ops_list or [])
            if isinstance(entry, dict) and entry.get("id")
        ]

        queued = skipped = 0
        for op_id in op_ids:
            try:
                res = await self._client.request_json(
                    "POST", f"/v1/default/banks/{bank}/operations/{op_id}/retry"
                )
            except Exception:
                skipped += 1
                continue
            if not isinstance(res, dict) or bool(res.get("success", True)):
                queued += 1
            else:
                skipped += 1

        log.info(
            "hal0.memory.auto_retry_dead_letters",
            bank=bank,
            queued=queued,
            skipped=skipped,
            sweep=self._auto_retry_sweeps_done,
        )
        # Force a fresh sample on the next read so status reflects the retry
        # immediately instead of the stale cached verdict.
        self._write_health = None
        self._write_health_at = None
        return {
            "bank": bank,
            "queued": queued,
            "skipped": skipped,
            "sweep": self._auto_retry_sweeps_done,
        }

    async def _call(self, method: str, /, **kwargs: Any) -> Any:
        """Invoke a client method, updating the live ``degraded`` flag.

        Every engine round-trip funnels through here so reachability is
        OBSERVED rather than assumed — that is the whole point, and it is why
        this is a wrapper and not a periodic background poll: the signal is
        already there in the calls the process is making anyway.

        Exceptions propagate untouched, so callers keep their existing
        fail-soft / 404-sweep handling exactly as before.
        """
        try:
            out = await getattr(self._client, method)(**kwargs)
        except Exception as exc:
            self._mark_engine(reachable=self._engine_answered(exc), error=str(exc))
            raise
        self._mark_engine(reachable=True)
        return out

    # ── ACL: the caller's allowed namespaces → banks ───────────────────

    @staticmethod
    def _requested_list(requested: str | list[str] | None) -> list[str]:
        """Normalise a namespace request to a list WITHOUT collapsing an
        empty one onto the default (#1451).

        ``None`` means "the caller named nothing" → the default sweep.
        ``[]`` means "the caller named namespaces and none of them resolved"
        → sweep nothing. The old ``requested or [_SHARED]`` read both as the
        default, which turned an unaddressable scope into a shared-bank
        wipe. The front door (``namespace.resolve_read_datasets``) now
        rejects that shape outright; this is the executor-side backstop for
        the paths that reach a provider without it.
        """
        if requested is None:
            return [_SHARED]
        if isinstance(requested, str):
            return [requested]
        return list(requested)

    def _allowed_namespaces(
        self, requested: str | list[str] | None, client_id: str | None
    ) -> list[str]:
        reqs = self._requested_list(requested)
        # Unified mode: one bank. No cross-bank fan-out, no own-private
        # expansion — every read resolves to ``shared`` alone, EXCEPT the
        # ``agents`` registry namespace, which keeps its own bank.
        if self._unified_bank:
            out: list[str] = []
            for ds in reqs:
                target = _AGENTS if ds == _AGENTS else _SHARED
                if target not in out:
                    out.append(target)
            return out
        cid = client_id or self._client_id
        own = f"{_PRIVATE}{cid}"
        # Two-bank: a ``shared`` read is the platform bank plus the caller's
        # own memory in the private bank, and an unidentified caller has no
        # private memory to read. The retired ``project:<id>`` namespace is a
        # tag in the platform bank, so it reads as ``shared``.
        reads_private = not self._two_bank or is_identified(cid)
        out = []
        for ds in reqs:
            if ds == _SHARED:
                wanted = (_SHARED, own) if reads_private else (_SHARED,)
                out += [d for d in wanted if d not in out]
            elif ds == own:
                if reads_private and own not in out:
                    out.append(own)
            elif ds.startswith(_PRIVATE):
                continue  # foreign private — dropped (fail-open-empty)
            elif self._two_bank and ds.startswith(_PROJECT):
                if _SHARED not in out:
                    out.append(_SHARED)
            elif ds not in out:
                out.append(ds)
        return out

    def _write_namespace(self, requested: str, client_id: str | None) -> str:
        # Unified mode collapses every resolved namespace onto ``shared`` — the
        # front door still resolves ``private:<agent>`` (so the private intent
        # survives for the ``visibility:private`` tag in ``add``), but the bank
        # is always ``shared`` here. The ``agents`` registry namespace is the
        # one exception: it keeps its own bank.
        if self._unified_bank:
            return _AGENTS if requested == _AGENTS else _SHARED
        # Two-bank: ``private:<id>`` stays (it maps to the private bank and
        # carries the private intent); the retired ``project:<id>`` is a tag in
        # the platform bank.
        if self._two_bank and isinstance(requested, str) and requested.startswith(_PROJECT):
            return _SHARED
        # The REST/MCP front door already resolved the write namespace via
        # namespace.resolve_write_dataset; trust it verbatim here.
        return requested or _SHARED

    # ── ACL: who may read what ──────────────────────────────────────────

    def _caller_agent(self, client_id: str | None) -> str:
        """Resolve the reading caller's agent identity for private-visibility
        matching. Mirrors the write-path collapse in ``add``: a missing id or
        the ``anonymous`` front-door sentinel becomes ``unknown``. ``unknown``
        is the marker of an unidentified caller, and :meth:`_is_visible_to`
        shows it no private doc at all (``agent:unknown`` notes included)."""
        cid = client_id or self._client_id or ""
        if not cid or cid == _ANONYMOUS:
            return _UNKNOWN_AGENT
        return cid

    @staticmethod
    def _is_visible_to(item: dict[str, Any], caller_agent: str) -> bool:
        """Read-side enforcement of the ``visibility:private`` tag (PR #1244).

        Non-private docs are shared-readable exactly as before. A private doc
        is returned ONLY to the identified agent whose id matches the doc's
        ``agent:<id>`` tag. A doc missing that tag, or owned by ``unknown``
        (what an unidentified write is stamped with), is private-to-nobody and
        withheld; so is every private doc for an unidentified caller
        (fail-closed). Mirrored by ``read_scope.reflect_visibility_scope``."""
        tags = item.get("tags") or []
        if _VISIBILITY_PRIVATE not in tags:
            return True
        owner = _agent_of(tags)
        return is_identified(owner) and owner == caller_agent

    def _readable(self, item: dict[str, Any], caller_agent: str, bank: str | None) -> bool:
        """Whether ``caller_agent`` may read ``item``, read from ``bank``.

        A memory of the two-bank private bank is its owner's alone, whatever
        else it is tagged: one with no ``agent:`` tag is nobody's (fail
        closed). Where one bank holds several agents' private memories
        (unified, and the two-bank platform bank as a backstop)
        :meth:`_is_visible_to` decides. ``bank`` is ``None`` when the item's
        bank is not known (response-level enrichment): a two-bank provider
        then applies :meth:`_is_visible_to`, which every private-bank memory
        is subject to as well, since each one carries ``visibility:private``.
        """
        if self._two_bank and bank == self._private_bank:
            # The #3560 rule here too: no owner, or the ``unknown`` marker,
            # is nobody's, and an unidentified caller owns nothing.
            owner = _agent_of(item.get("tags") or [])
            return is_identified(owner) and owner == caller_agent
        if not self._acl_enforced:
            return True
        return self._is_visible_to(item, caller_agent)

    def _private_readable(self, item: dict[str, Any], caller_agent: str, bank: str) -> bool:
        """The rule the bank-wide read tools apply (#3573), in every layout.

        :meth:`_is_visible_to` on the tags the engine returned, and, where one
        bank holds several agents' private memory, that bank's own rule
        (:meth:`_readable`): on the two-bank private bank a row is its
        owner's alone, whatever its tags, and a row with no ``agent:`` tag,
        or owned by ``unknown``, is nobody's.
        """
        return self._is_visible_to(item, caller_agent) and self._readable(item, caller_agent, bank)

    @staticmethod
    def _wants_private_only(requested: str | list[str] | None) -> bool:
        """True when every requested namespace is ``private:<id>`` (#1654).

        ``_is_visible_to`` only enforces OWNERSHIP of private docs — a
        non-private doc always passes it, unconditionally, regardless of
        what was requested. So in the unified bank a read for
        ``private:<agent>`` alone (no ``shared``) would return identically to
        a ``shared`` read: the caller's own private docs AND every ordinary
        public doc in the bank. Callers that explicitly ask for the private
        namespace ONLY (e.g. the per-agent memory-stats chip) want private
        docs alone; this is the signal :meth:`list_items` uses to apply that
        extra filter. A mixed request (``["shared", "private:x"]``) is NOT
        private-only — it wants the ordinary union — so this returns
        ``False`` unless every entry is a private namespace. The two-bank
        layout needs no such filter: a private-only read reads the private
        bank alone.
        """
        reqs = HindsightProvider._requested_list(requested)
        return bool(reqs) and all(isinstance(ds, str) and ds.startswith(_PRIVATE) for ds in reqs)

    def _project_groups(self, project: str | None) -> list[dict[str, Any]] | None:
        """Engine ``tag_groups`` for a read's ``project`` field (ADR-0058 7b).

        No field, or ``"*"``, reads the whole bank: ``None``. A name is
        resolved through the project registry's aliases and deny list and
        reads that project plus global memory
        (:func:`hal0.memory.read_scope.project_scope_groups`); a name that
        resolves to no project (denied, or nothing left of it) reads global
        memory only.
        """
        if project is None:
            return None
        if not isinstance(project, str):
            raise BadRequest("'project' must be a string, or '*' for every project")
        name = project.strip()
        if not name or name == ALL_PROJECTS:
            return None
        resolution = resolve_project(field=name, registry=self._projects)
        return project_scope_groups(resolution.slug)

    def _bank_groups(self, bank: str, caller_agent: str) -> list[dict[str, Any]] | None:
        """The engine ``tag_groups`` a read of ``bank`` is held to before any
        caller filter. Two-bank: the caller's own ``agent:`` tag on the
        private bank, the visibility rule on any other. Legacy: ``None``,
        nothing added, as before."""
        if not self._two_bank:
            return None
        if bank == self._private_bank:
            return [_own_private_group(caller_agent)]
        return reflect_visibility_scope(caller_agent)

    def _engine_filter(
        self,
        bank: str,
        caller_agent: str,
        tags: list[str] | None,
        tags_match: str | None,
        tag_groups: list[dict[str, Any]] | None,
        project_groups: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """``{tags, tags_match, tag_groups}`` for one bank's recall.

        Unchanged from what the caller sent when the bank adds no group and no
        project was named (every read on the old layout that names none). Otherwise one
        list of groups, which the engine ANDs: the bank's own
        (:meth:`_bank_groups`), the caller's ``tags`` folded in as one group
        (the engine refuses ``tags`` beside ``tag_groups``), the caller's own
        ``tag_groups``, and the project scope. A caller's groups narrow the
        read and never widen it.
        """
        base = self._bank_groups(bank, caller_agent)
        if base is None and project_groups is None:
            return {"tags": tags, "tags_match": tags_match, "tag_groups": tag_groups}
        groups = list(base or [])
        if tags:
            groups.append({"tags": list(tags), "match": tags_match or "any"})
        groups.extend(tag_groups or [])
        groups.extend(project_groups or [])
        return {"tags": None, "tags_match": None, "tag_groups": groups}

    def _filter_visible(
        self,
        items: list[dict[str, Any]],
        client_id: str | None,
        *,
        bank: str | None = None,
        project_groups: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply the read ACL to ``items`` read from ``bank``.

        Two checks compose, and a doc must pass both: who may read it
        (:meth:`_readable`), and, when the read named a project, whether it is
        inside that project's scope (``project_groups``, evaluated the way the
        engine evaluates them, :func:`hal0.memory.read_scope.tags_readable`).
        A ``project:`` tag on its own hides nothing (ADR-0058 decision 7b).

        The first check is a no-op in the old split layout, where per-bank
        ACL (``_allowed_namespaces`` never returns a foreign private bank)
        already isolates private memory."""
        caller = self._caller_agent(client_id)
        return [
            it
            for it in items
            if self._readable(it, caller, bank)
            and (project_groups is None or tags_readable(project_groups, it.get("tags")))
        ]

    # ── ACL: the bank-wide read tools (#3573) ───────────────────────────
    #
    # Documents, knowledge pages, mental models, tags, operations and bank
    # stats are read from one bank, and that bank can hold several agents'
    # private memory: ``shared`` under the unified layout, ``hal0:private``
    # under the two-bank one. So these reads apply the same visibility rule as
    # recall (``_is_visible_to``), judged on the tags of what the engine
    # returns and never on the bank's name, in every layout. On a per-agent
    # private bank the rule changes nothing: every private item there is the
    # caller's own. An unidentified caller sees no private item at all.
    #
    # Per tool:
    #
    # * documents: a listing drops the rows the caller may not see and counts
    #   only what it returns, so a filter cannot confirm a hidden document;
    #   a get of one is 404, the same answer as a document that does not
    #   exist.
    # * mental models and knowledge pages: the content is written from every
    #   memory the model's scope admits and cannot be filtered afterwards, so
    #   a model is readable when its own tags pass the rule AND either the
    #   bank holds no private memory of another agent or the model's scope is
    #   confined to the caller (``read_scope.model_scope_confined_to``). The
    #   rest are left out of a list or tree and are 404 on a get, an update, a
    #   delete or a refresh. This is the reflect rule (mental models left out
    #   where others' private memory is in the bank), applied per model.
    # * tags: a tag that other agents' private documents carry is listed only
    #   when a document the caller can see carries it too. Counts stay the
    #   engine's, an upper bound.
    # * operations: a retain's submitted content carries its tags; an
    #   operation over memory the caller may not see is left out of a list,
    #   a get answers what the engine answers for an unknown id, and a cancel
    #   or retry is 404.
    # * bank stats: counts cannot be scoped, so while the bank holds another
    #   agent's private memory only the health fields are returned.
    #
    # The operator's admin API (``/api/memory/banks/{bank}/...``, ADMIN
    # class) forwards to the engine without these rules and keeps full
    # access; it does not go through these methods.

    async def _foreign_private(
        self, bank: str, caller: str, *, collect_tags: bool = False
    ) -> tuple[bool, set[str] | None]:
        """Whether ``bank`` holds a private document ``caller`` may not read.

        Returns ``(present, tags)``. With ``collect_tags`` the scan reads every
        private document and ``tags`` is the union of the tags on the ones the
        caller may not read; without it the scan stops at the first. A scan
        that reaches :data:`_PRIVATE_SCAN_PAGES` answers present with tags
        ``None`` (unknown): fail closed. A row that comes back without a tag
        list counts as another agent's, for the same reason.
        """
        tags: set[str] = set()
        present = False
        # On the two-bank private bank every row is somebody's private memory,
        # tagged so or not: the scan reads them all.
        whole_bank = self._two_bank and bank == self._private_bank
        scan_filter: dict[str, Any] = (
            {} if whole_bank else {"tags": [_VISIBILITY_PRIVATE], "tags_match": "all_strict"}
        )
        for page in range(_PRIVATE_SCAN_PAGES):
            resp = await self._call(
                "list_documents",
                bank_id=bank,
                limit=_PRIVATE_SCAN_PAGE,
                offset=page * _PRIVATE_SCAN_PAGE,
                **scan_filter,
            )
            items = resp.get("items") if isinstance(resp, dict) else None
            rows = [r for r in items or [] if isinstance(r, dict)]
            for row in rows:
                row_tags = row.get("tags")
                if isinstance(row_tags, list) and self._private_readable(row, caller, bank):
                    continue
                present = True
                if not collect_tags:
                    return True, None
                if not isinstance(row_tags, list):
                    return True, None
                tags.update(t for t in row_tags if isinstance(t, str))
            if len(rows) < _PRIVATE_SCAN_PAGE:
                return present, tags
        return True, None

    async def _readable_models(
        self, bank: str, models: list[Any], caller: str, *, listed: bool = False
    ) -> list[dict[str, Any]]:
        """The mental models (or knowledge-page nodes) ``caller`` may read.

        See the section comment above. The bank is scanned only when some
        model's scope is not confined to the caller. ``listed`` marks rows
        from the engine's model listing, which at its default ``metadata``
        detail carries ``trigger: null`` (Hindsight 1.2.0, #3584): once the
        scan finds another agent's private memory, such a row is judged on
        the trigger a GET of the model returns, and dropped if it is gone.
        """
        kept = [m for m in models if isinstance(m, dict) and self._is_visible_to(m, caller)]
        if any(not model_scope_confined_to(m, caller) for m in kept):
            present, _ = await self._foreign_private(bank, caller)
            if present:
                if listed:
                    kept = await self._with_stored_triggers(bank, kept)
                kept = [m for m in kept if model_scope_confined_to(m, caller)]
        return kept

    async def _with_stored_triggers(
        self, bank: str, models: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """``models`` with each missing ``trigger`` read from the engine, at
        most :data:`_TRIGGER_READS` at a time. Only the trigger is taken, so a
        row keeps the detail it was listed at. A model that is 404 by now is
        dropped."""
        import asyncio

        sem = asyncio.Semaphore(_TRIGGER_READS)

        async def _fill(model: dict[str, Any]) -> dict[str, Any] | None:
            if isinstance(model.get("trigger"), dict):
                return model
            async with sem:
                try:
                    one = await self._call(
                        "get_mental_model", bank_id=bank, mental_model_id=model.get("id")
                    )
                except Exception as exc:
                    if _http_status(exc) == 404:
                        return None
                    raise
            trigger = one.get("trigger") if isinstance(one, dict) else None
            return {**model, "trigger": trigger}

        filled = await asyncio.gather(*[_fill(m) for m in models])
        return [m for m in filled if m is not None]

    async def _operation_visible(
        self, bank: str, op: dict[str, Any], caller: str, *, depth: int = 0
    ) -> bool:
        """Whether ``caller`` may see operation ``op``.

        Only an operation that carries memory content is judged; the rest
        (consolidation, a mental-model refresh) carry ids and status only. A
        retain is judged on the tags of the items it submitted
        (``task_payload.contents`` and ``document_tags``), else on its
        document's tags, else on its child operations (a ``batch_retain``
        parent holds no payload). With nothing to judge it on, or a read that
        fails, it is hidden: fail closed.
        """
        op_type = op.get("task_type") or op.get("operation_type")
        if op_type not in _CONTENT_OP_TYPES:
            return True
        op_id = op.get("operation_id") or op.get("id")
        payload = op.get("task_payload")
        if not isinstance(payload, dict) and op_id:
            try:
                full = await self._call(
                    "get_operation", bank_id=bank, operation_id=op_id, include_payload=True
                )
            except Exception:
                return False
            if isinstance(full, dict):
                op = {**op, **full}
                payload = full.get("task_payload")
        if isinstance(payload, dict):
            contents = payload.get("contents")
            document_tags = payload.get("document_tags")
            extra = list(document_tags) if isinstance(document_tags, list) else []
            items = (
                [c for c in contents if isinstance(c, dict)] if isinstance(contents, list) else []
            )
            if items:
                return all(
                    self._private_readable(
                        {"tags": list(c.get("tags") or []) + extra}, caller, bank
                    )
                    for c in items
                )
        meta = op.get("result_metadata") if isinstance(op.get("result_metadata"), dict) else {}
        doc_id = op.get("document_id") or meta.get("document_id")
        if doc_id:
            try:
                doc = await self._call("get_document", bank_id=bank, document_id=str(doc_id))
            except Exception as exc:
                # A document that is gone has no content left to show.
                return _http_status(exc) == 404
            return isinstance(doc, dict) and self._private_readable(doc, caller, bank)
        children = op.get("child_operations")
        if depth == 0 and isinstance(children, list) and children:
            for child in children:
                if not isinstance(child, dict):
                    return False
                child_op = {"operation_type": "retain", **child}
                if not await self._operation_visible(bank, child_op, caller, depth=1):
                    return False
            return True
        return False

    # ── Core five ──────────────────────────────────────────────────────

    def _retain_item(
        self,
        text: str,
        *,
        dataset: str = _SHARED,
        tags: Any = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_id: str | None = None,
        document_id: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        observation_scopes: Any = None,
        strategy: str | None = None,
        update_mode: str | None = None,
        project: str | None = None,
        project_resolution: ProjectResolution | None = None,
        coding: bool = False,
        context: str | None = None,
        timestamp: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Normalise one write into ``(bank id, engine retain item)``.

        The one place a write is shaped, shared by :meth:`add` (one item) and
        :meth:`retain_items` (a batch): the document id, the reserved-tag
        strip, the project resolution, the write envelope, the
        ``visibility:private`` stamp, the context and timestamp defaults, the
        metadata encoding and the observation scope. The item's keys are the
        client's ``retain`` keywords. Raises ``ValueError`` for a shared write
        that claims a private document id.
        """
        # ``private:<agent>`` / ``project:<id>`` still arrive from the front
        # door even in unified mode; capture that intent before the bank
        # collapses to shared, so it can survive as a tag.
        requested_private = isinstance(dataset, str) and dataset.startswith(_PRIVATE)
        requested_project = (
            dataset if isinstance(dataset, str) and dataset.startswith(_PROJECT) else None
        )
        ns = self._write_namespace(dataset, client_id)
        bank = self._bank_of(ns)

        meta = dict(metadata or {})
        if source:
            meta["source"] = source

        # Resolved agent identity — stamps the ``agent:`` tag + backs the
        # session-derived document id. ``anonymous`` (the front-door sentinel
        # for a missing X-hal0-Agent) collapses to ``unknown`` so it can't be
        # confused with a real id.
        agent = client_id or source or ""
        if not agent or agent == _ANONYMOUS:
            agent = _UNKNOWN_AGENT

        # The join key. Precedence: caller-supplied document_id → deterministic
        # ``<agent>:<session_id>`` (stable across a conversation, so Hindsight
        # upserts/groups the same logical document) → fresh uuid4.
        session_id = meta.get("session_id")
        if document_id:
            resolved_id = document_id
        elif session_id:
            resolved_id = f"{agent}:{session_id}"
        else:
            resolved_id = str(uuid.uuid4())
        if self._acl_enforced:
            # One bank holds several agents' private documents (and, unified,
            # the shared ones too), and the engine upserts on document id.
            # Without a namespace one agent's private write could reuse another
            # one's id, or a shared write a private one's, and replace it.
            # Private ids carry ``private:<agent>:``; a shared write may not
            # claim that prefix, which also keeps the two-bank fold-in's split
            # by id prefix exact. A fresh uuid needs neither.
            own_prefix = f"{_PRIVATE}{agent}:"
            if requested_private:
                # Re-sending the id ``add`` returned must upsert the same document.
                if (document_id or session_id) and not resolved_id.startswith(own_prefix):
                    resolved_id = f"{own_prefix}{document_id or session_id}"
            elif resolved_id.startswith(_PRIVATE):
                raise ValueError(
                    f"document_id may not start with {_PRIVATE!r}: that prefix is reserved "
                    "for private documents"
                )

        # Server-side tag enforcement (#3136): the provider owns both the
        # ``agent:`` and ``visibility:`` tags. Any caller-supplied ``agent:*``
        # or ``visibility:*`` (compared stripped and case-insensitively) is
        # dropped; ``agent:`` is replaced with the resolved caller and
        # ``visibility:private`` is added only when the write came in under the
        # private toggle — otherwise a caller could plant a note in another
        # agent's private namespace. Other caller tags are preserved.
        caller_tags = [t for t in coerce_tags(tags) if not _is_reserved_tag(t)]
        # The write envelope (ADR-0058 decision 4) then normalises what is
        # left: it stamps ``agent:`` and exactly one ``kind:`` tag, and moves
        # session ids and lifecycle words out of the tag space into metadata.
        #
        # The project (decision 4a) is resolved first. The retired
        # ``project:<id>`` namespace (#1300, ADR-0005) wins where it lands in
        # the platform bank (unified, two-bank) and is stamped as written, the
        # one spelling of that project. In the old split layout the
        # project is isolated by its bank, so a tag there would be a
        # second owner of the same fact: such a write gets neither a project
        # tag nor ``scope:global``. Every other write goes through the
        # resolver: the project its caller named, else the caller's own
        # ``project:`` tags, each through the registry's aliases and deny
        # list. The envelope then removes every ``project:`` tag the caller
        # sent and stamps only what the resolver made.
        namespace_slug = requested_project[len(_PROJECT) :].strip() if requested_project else ""
        resolution: ProjectResolution
        if namespace_slug and self._acl_enforced:
            resolution = ProjectResolution(
                slug=namespace_slug,
                source=SOURCE_NAMESPACE,
                registered=self._projects.is_registered(namespace_slug),
            )
        elif project_resolution is not None and project_resolution.source != SOURCE_NONE:
            resolution = project_resolution
        else:
            resolution = resolve_project(
                field=project, tags=caller_tags, registry=self._projects, coding=coding
            )
        envelope = apply_envelope(
            caller_tags,
            agent=agent,
            project_resolution=resolution,
            coding=coding,
            stamp_global=not (namespace_slug and not self._acl_enforced),
            metadata=meta,
        )
        out_tags = list(envelope.tags)
        if requested_private:
            out_tags.append(_VISIBILITY_PRIVATE)
        # Merged only now, after the document id was resolved above: a session
        # id lifted out of the tags is provenance and must not become the
        # ``<agent>:<session_id>`` join key, or every write of one session
        # would replace the last.
        meta.update(envelope.metadata)

        # Extraction quality suffers without a ``context`` line and a
        # timestamp; always supply both unless the caller already did (as an
        # item field, or under the metadata key of the same name).
        context = (
            context
            or meta.pop("context", None)
            or meta.get("source")
            or f"{agent} conversation turn"
        )
        timestamp = timestamp or meta.pop("timestamp", None) or _now()

        # #1905: nested structures must round-trip. ``str(v)`` on a dict/list
        # produced a Python repr (single quotes) that no JSON reader on the
        # way back out could decode — the peer registry's
        # ``hal0_state``/``endpoint``/``roles`` cards on upgraded boxes were
        # written exactly that way. ``json.dumps`` keeps the value
        # machine-readable; scalars stay ``str()`` to preserve the wire shape
        # existing readers expect.
        item: dict[str, Any] = {
            "content": text,
            "document_id": resolved_id,
            "context": context,
            "metadata": {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                for k, v in meta.items()
            },
            "tags": out_tags,
            "timestamp": timestamp,
        }

        # Full RetainRequest item shape (entities/strategy/update_mode) —
        # forwarded only when the caller actually set them.
        # ``observation_scopes`` is the exception: every write that is not
        # private-in-the-unified-bank carries one (see below), so a client
        # fake must accept that keyword.
        if entities is not None:
            item["entities"] = entities
        # A private note must not feed an observation another agent can read,
        # so on a private write the caller's scopes (any form) are ignored.
        # Two-bank: the write is sent with its writer's own scope,
        # ``[agent:<id>, visibility:private(, project:<slug>)]``; the engine
        # consolidates an explicit scope only with observations that carry
        # every one of its tags and writes the observation with exactly those
        # tags, so it stays this agent's and stays private. Legacy unified:
        # the engine default applies, as before (#3547 is held off there by
        # ``spec apply``). Every other write is sent with the envelope's scope
        # unless the caller chose one: one scope per ``project:`` tag when it
        # carries any, else ``shared``, the one untagged scope
        # (``project:unresolved`` is not a project for this). Either way
        # per-call tags stop splitting observations into silos.
        if self._two_bank and requested_private:
            observation_scopes = _private_observation_scopes(out_tags)
        elif self._unified_bank and requested_private:
            observation_scopes = None
        else:
            observation_scopes = resolve_observation_scopes(
                _strip_reserved_scopes(observation_scopes), out_tags
            )
        if observation_scopes is not None:
            item["observation_scopes"] = observation_scopes
        if strategy is not None:
            item["strategy"] = strategy
        if update_mode is not None:
            item["update_mode"] = update_mode
        return bank, item

    async def _retain(self, method: str, /, **kwargs: Any) -> Any:
        """Send a retain through :meth:`_call`, recording a failure as
        write-path evidence."""
        try:
            return await self._call(method, **kwargs)
        except Exception as exc:
            # #1420: a raised retain is write-path evidence, distinct from the
            # daemon-reachability signal ``_call`` already updated (a 4xx means
            # the daemon is fine AND the write still didn't land).
            self._mark_write_failure(str(exc), reason="retain_failed")
            if isinstance(exc, (ConnectionError, httpx.NetworkError, httpx.ConnectTimeout)):
                raise MemoryEngineUnavailable() from exc
            raise

    @staticmethod
    def _retain_receipt(resp: Any) -> dict[str, Any]:
        """The engine's accounting of a retain, as hal0 reports it.

        retain is async by default on this engine — surface the operation
        id(s) so callers (dashboard ingestion indicator, CLI,
        memory_operation_* MCP tools) can poll instead of wondering why list
        doesn't show the item yet. ``items_count``/``operation_ids`` used to
        be dropped on the floor entirely — surface them whenever the engine
        reports them so a caller doing per-item accounting doesn't have to
        guess.
        """
        resp_dict = resp if isinstance(resp, dict) else {}
        out: dict[str, Any] = {}
        operation_id = resp_dict.get("operation_id")
        if operation_id:
            out["operation_id"] = str(operation_id)
        operation_ids = resp_dict.get("operation_ids")
        if isinstance(operation_ids, list) and operation_ids:
            out["operation_ids"] = [str(i) for i in operation_ids]
        items_count = resp_dict.get("items_count")
        if isinstance(items_count, int):
            out["items_count"] = items_count
        return out

    @_gated_write
    async def add(
        self,
        text: str,
        dataset: str = _SHARED,
        tags: list[str] | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_id: str | None = None,
        document_id: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        observation_scopes: Any = None,
        strategy: str | None = None,
        update_mode: str | None = None,
        sync: bool = False,
        project: str | None = None,
        coding: bool = False,
    ) -> dict[str, Any]:
        bank, item = self._retain_item(
            text,
            dataset=dataset,
            tags=tags,
            source=source,
            metadata=metadata,
            client_id=client_id,
            document_id=document_id,
            entities=entities,
            observation_scopes=observation_scopes,
            strategy=strategy,
            update_mode=update_mode,
            project=project,
            coding=coding,
        )
        # The sync/async toggle is forwarded only when the caller set it.
        toggle: dict[str, Any] = {"sync": True} if sync else {}
        resp = await self._retain("retain", bank_id=bank, **item, **toggle)
        out: dict[str, Any] = {"id": item["document_id"], "timestamp": _now()}
        if update_mode is not None:
            # The engine was sent this mode with the retain. A caller that
            # appends to one document id over many writes waits for this
            # before it trusts the id (an engine that ignored the mode would
            # replace); the volatile fallback never reports it.
            out["update_mode"] = update_mode
        out.update(self._retain_receipt(resp))
        return out

    @_gated_write
    async def retain_items(
        self,
        items: list[dict[str, Any]],
        *,
        agent: str | None,
        dataset: str = _SHARED,
        project_resolution: ProjectResolution | None = None,
        coding: bool = False,
        async_: bool | None = None,
        operation_id: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Retain a batch of items with ONE engine call.

        ``items`` are in the engine's ``RetainRequest`` item shape:
        ``content`` (required), and optionally ``context``, ``document_id``,
        ``tags``, ``metadata``, ``observation_scopes``, ``strategy``,
        ``update_mode``, ``timestamp`` and ``entities``. Each one is
        normalised exactly as :meth:`add` normalises its single write
        (:meth:`_retain_item`): ``agent`` is the caller's identity for all of
        them, ``dataset`` the namespace they are written to, and
        ``project_resolution`` the project the request named. An item whose
        request named no project is resolved from its own ``project:`` tags.

        ``async_`` and ``operation_id`` are the engine's request-level fields
        and are forwarded exactly as given; ``None`` leaves the field out of
        the request, so the engine's own default applies. The engine treats
        ``operation_id`` as the identity of an async retain: a resend with the
        same id returns the original operation and drops changed content, and
        an id that belongs to another operation is refused with 409.
        ``timeout_s`` is the read budget for the engine call; ``None`` keeps
        the client's own.

        Returns ``{document_ids, timestamp}`` plus ``operation_id`` /
        ``operation_ids`` / ``items_count`` when the engine reports them, and
        the engine's answer verbatim as ``engine_response``.
        """
        if not isinstance(items, list) or not items:
            raise BadRequest("retain_items needs at least one item")
        bank: str | None = None
        wire_items: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            content = raw.get("content") if isinstance(raw, dict) else None
            if not isinstance(content, str) or not content:
                raise BadRequest(
                    f"retain_items item {index} needs 'content' (non-empty string)",
                    details={"index": index},
                )
            metadata = raw.get("metadata")
            document_id = raw.get("document_id")
            bank, item = self._retain_item(
                content,
                dataset=dataset,
                tags=raw.get("tags"),
                source=agent,
                metadata=dict(metadata) if isinstance(metadata, dict) else None,
                client_id=agent,
                document_id=str(document_id) if document_id not in (None, "") else None,
                entities=raw.get("entities"),
                observation_scopes=raw.get("observation_scopes"),
                strategy=raw.get("strategy"),
                update_mode=raw.get("update_mode"),
                project_resolution=project_resolution,
                coding=coding,
                context=raw.get("context") or None,
                timestamp=raw.get("timestamp") or None,
            )
            wire_items.append(item)
        # Forwarded only when set, so a client fake built against the older
        # signature stays compatible.
        budget: dict[str, Any] = {"timeout_s": timeout_s} if timeout_s is not None else {}
        resp = await self._retain(
            "retain_batch",
            bank_id=bank,
            items=wire_items,
            async_=async_,
            operation_id=operation_id,
            **budget,
        )
        out: dict[str, Any] = {
            "document_ids": [item["document_id"] for item in wire_items],
            "timestamp": _now(),
        }
        out.update(self._retain_receipt(resp))
        out["engine_response"] = resp if isinstance(resp, dict) else {}
        return out

    async def search(
        self,
        query: str,
        limit: int = 10,
        dataset: str | list[str] = _SHARED,
        tags: list[str] | None = None,
        before: str | None = None,
        after: str | None = None,
        mode: str = "vector",
        client_id: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        min_scores: dict[str, float] | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search, honouring the time window the whole surface advertises (#1471).

        ``before``/``after`` used to be accepted and then dropped on the floor:
        the REST route documents and forwards them, the MCP schema exposes
        them, and ``PgVectorProvider`` — the degrade fallback — applies them,
        so a caller filtering by time window silently got UNFILTERED results
        from the durable default engine and filtered ones from the fallback.

        Hindsight's ``recall`` has no time-range parameter (its
        ``query_timestamp`` is an "as of when" for temporal reasoning, NOT a
        range filter — mapping a window onto it would change what the query
        means), so the window is applied here, on the merged result. To keep
        ``limit`` honest under filtering we over-fetch the token budget rather
        than the item count, then cut after filtering.

        ``mode`` is rejected rather than ignored — see :meth:`_reject_mode`.

        ``tag_groups``/``min_scores`` are Hindsight-native recall filters
        (boolean tag expressions / per-stage score floors) — passed straight
        through to :meth:`recall` when set, so ``memory_search`` can be honest
        about scope without every caller having to switch to ``memory_recall``.

        ``project`` narrows the read to one project plus global memory, as
        :meth:`recall` does; with none (or ``"*"``) the whole bank is read.
        """
        self._reject_mode(mode)
        windowed = before is not None or after is not None
        out = await self.recall(
            query=query,
            # Over-fetch when a window will thin the result, so a filtered page
            # can still fill `limit` instead of returning a short page.
            max_tokens=max(256, limit * 256 * (4 if windowed else 1)),
            dataset=dataset,
            tags=tags,
            tag_groups=tag_groups,
            min_scores=min_scores,
            client_id=client_id,
            project=project,
        )
        if windowed:
            out = [item for item in out if _within_window(item, before=before, after=after)]
        return list(out[:limit])

    @staticmethod
    def _reject_mode(mode: str | None) -> None:
        """Refuse a retrieval ``mode`` this engine cannot actually perform.

        ``mode`` ("vector"|"graph"|"hybrid") is declared on the provider
        protocol and exposed in the MCP tool schema, but nothing on this engine
        ever consumed it — a caller explicitly asking for graph traversal got
        plain vector recall and no indication of the substitution (#1471).
        Hindsight builds its graph natively and exposes no traversal knob on
        recall, so the honest answer is an error naming the engine, not a
        silent downgrade. ``None``/``"vector"`` is the default path and stays
        free of charge.
        """
        if mode in (None, "", "vector"):
            return
        raise ValueError(
            f"retrieval mode {mode!r} is not supported by the Hindsight engine — "
            "it serves vector recall and builds its graph natively, with no "
            "per-query traversal control. Use mode='vector' (the default)."
        )

    async def list_items(
        self,
        dataset: str | list[str] = _SHARED,
        cursor: str | None = None,
        limit: int = 50,
        client_id: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Page through a bank, honouring ``cursor`` (#1471).

        ``project`` keeps the rows of one project plus global memory (the
        engine's listing takes no tag filter, so hal0 applies the same groups
        a recall sends); with none (or ``"*"``) every row the caller may read.

        ``cursor`` used to be accepted and ignored: every call issued
        ``offset=0`` and returned ``next_cursor=None``, so only the first
        ``limit`` rows of a bank were reachable — on the live shared bank
        that is the first page of 1629 facts, silently. The cursor now encodes
        ``bank@offset`` (see :func:`_encode_cursor`), which is all Hindsight's
        ``/memories/list`` needs since it already supports ``offset``.

        Two ordering rules the old loop got wrong:

        * The visibility filter runs BEFORE the page is cut, not after. It used
          to break out of the bank loop on the RAW count, so a unified-mode
          reader whose page was mostly other agents' private docs received
          fewer than ``limit`` items even when more visible ones existed.
        * Over-fetch per bank so a heavily-filtered page can still fill, and
          only advertise a ``next_cursor`` when the bank actually reported more
          rows behind the offset we consumed.

        Cursor math (#1667): the resume offset for a bank cut mid-page is
        derived from the RAW row that produced the last item actually
        returned, not from ``raw_offset - overflow_count``. The two only
        agree when the ACL filter drops nothing from the tail of the last
        fetched chunk — in unified-bank mode the shared bank interleaves
        other agents' private docs and out-of-scope project docs, so a page
        routinely filters unevenly, and the visible-count correction
        under-rewinds and strands unreturned visible rows behind the next
        cursor forever. Tracking each visible item's raw offset directly
        keeps the walk exact regardless of what the ACL withheld.
        """
        banks = [self._bank_of(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        start_bank, offset = _decode_cursor(cursor, banks)
        if start_bank is None:
            return {"items": [], "next_cursor": None}

        # #1654: in unified mode, ``_filter_visible`` only enforces private
        # OWNERSHIP — a non-private doc passes it unconditionally regardless
        # of what was requested, so a ``private:<agent>``-only request would
        # otherwise return identically to a ``shared`` request (own private
        # docs AND every public doc). A caller that asked for the private
        # namespace(s) alone gets exactly that: private docs only.
        private_only = self._unified_bank and self._wants_private_only(dataset)
        project_groups = self._project_groups(project)

        items: list[dict[str, Any]] = []
        next_cursor: str | None = None
        # Walk banks from the cursor's bank onward, carrying its offset into
        # the first one only — later banks start from the top.
        for bank in banks[banks.index(start_bank) :]:
            bank_offset = offset if bank == start_bank else 0
            # Every item this bank contributes, paired with the raw offset
            # immediately after the row that produced it — the exact point a
            # resumed walk must start from to neither skip nor repeat a row.
            bank_items: list[dict[str, Any]] = []
            bank_offsets: list[int] = []
            while len(items) + len(bank_items) < limit:
                try:
                    # Over-fetch: the ACL filter below may discard most of a page.
                    fetch = max(limit * 2, 50)
                    resp = await self._call(
                        "list_memories", bank_id=bank, limit=fetch, offset=bank_offset
                    )
                except Exception:
                    break  # fail-soft per bank, same as before
                raw = resp.get("items", []) or []
                if not raw:
                    break
                row_start = bank_offset
                bank_offset += len(raw)
                # Same read ACL as recall: where one bank holds several agents'
                # private docs (unified, the two-bank private bank) list must
                # not surface another agent's private items, and a read that
                # named a project keeps that project plus global memory.
                # Applied BEFORE the page cut so the page fills with visible
                # rows.
                candidates = [
                    self._label(self._list_fact_to_item(fact, bank), bank) for fact in raw
                ]
                visible = self._filter_visible(
                    candidates, client_id, bank=bank, project_groups=project_groups
                )
                if private_only:
                    visible = [
                        it for it in visible if _VISIBILITY_PRIVATE in (it.get("tags") or [])
                    ]
                visible_ids = {id(it) for it in visible}
                for i, cand in enumerate(candidates):
                    if id(cand) in visible_ids:
                        bank_items.append(cand)
                        bank_offsets.append(row_start + i + 1)
                total = resp.get("total")
                exhausted = len(raw) < fetch if not isinstance(total, int) else bank_offset >= total
                if exhausted:
                    break
            items.extend(bank_items)
            if len(items) >= limit:
                # This bank overflowed the page. Resume at the raw offset
                # right after the last item we're keeping FROM THIS BANK —
                # not the bank's total raw offset — so any visible row that
                # sat behind an ACL-withheld one in the same fetched chunk is
                # still reachable on the next call.
                overflow = len(items) - limit
                keep_from_bank = len(bank_items) - overflow
                consumed = bank_offsets[keep_from_bank - 1] if keep_from_bank > 0 else bank_offset
                next_cursor = _encode_cursor(bank, consumed)
                break
        return {"items": items[:limit], "next_cursor": next_cursor}

    @staticmethod
    def _list_fact_to_item(
        fact: dict[str, Any], bank: str, platform_bank: str = _SHARED
    ) -> dict[str, Any]:
        """Map a Hindsight /memories/list item to the MemoryItem wire shape.

        ``id`` is the **document_id**, matching :meth:`_fact_to_item` (recall)
        and the ``MemoryProvider`` ABC contract — "idempotent, recall-visible,
        delete-addressable". This used to prefer ``fact["id"]``, the per-fact
        UUID, which on a real 0.8.x engine is a *different* value: the
        list→delete round trip the API advertises 404-swept to
        ``{"deleted": 0}`` (#1456). The per-fact id is still the only handle
        on an individual extracted fact, so it moves to ``metadata.fact_id``
        rather than disappearing.
        """
        document_id = fact.get("document_id")
        fact_id = fact.get("id")
        return {
            "id": document_id or fact_id,
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or fact.get("date") or _now(),
            "dataset": _bank_namespace(bank, platform_bank),
            "tags": list(fact.get("tags") or []),
            "source": None,
            "metadata": {"fact_id": fact_id} if fact_id and fact_id != document_id else {},
            "score": None,
            "type": fact.get("fact_type"),
        }

    async def _deletable_ids(
        self,
        ids: set[str],
        banks: list[str],
        client_id: str | None,
        *,
        enforce_acl: bool = True,
        where: dict[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """Map each of ``ids`` the caller is allowed to delete to the
        **document_id(s)** the engine deletes by, under the read ACL.

        ``where``, when given, is filled with the bank each resolved document
        id was found in, so the delete addresses that bank alone.

        The return type is a mapping, not a set, because the caller-supplied
        id and the engine-addressable id are not always the same string
        (#1456). ``id`` on every read surface is the document_id when the unit
        has one, but a caller may still hold a per-fact id — from a pre-fix
        ``list`` response or from ``metadata.fact_id`` — and the resolution
        scan below already has both fields in hand, so accepting either costs
        nothing. The engine is addressed by document_id in every case;
        matching on the fact-id field is what made a *correct* document_id
        fail-closed withheld and left ``POST /api/memory/delete`` deleting
        nothing.

        **Observations** (#2747) have no document: consolidation derives them
        from one or more source facts, so ``document_id`` is null and every
        read surface hands out the observation's own unit id. The engine has
        no per-observation delete — ``DELETE /documents/{id}`` 404s on it —
        but deleting a source fact's document cascades every observation
        built on it. So an observation id resolves to the documents of *all*
        its ``source_memory_ids`` (deleting only one would let consolidation
        re-derive it from the rest). The ACL runs against the observation
        **and every source fact**: an observation that merges a document the
        caller could not read is withheld whole, so deleting a derived unit
        can never reach into another agent's private memory. A source the scan
        cannot find is withheld too.

        Where one bank holds several agents' ``visibility:private`` docs (the
        unified bank, the two-bank private bank) a blind delete-by-id would let
        any caller remove another agent's private memory by guessing an id.
        Read/search/list all enforce visibility; delete must too. A doc is
        deletable when the caller could have read it in this same call: the
        predicate the read paths use (:meth:`_readable`). An id we cannot
        resolve to a doc in the caller's banks is **withheld** (fail-closed),
        matching the read filter's posture.

        ``enforce_acl=False`` is the old split path, where bank isolation
        already does the ACL's job and this scan only translates fact and
        observation ids into document ids.
        """
        if not ids:
            return {}
        caller = self._caller_agent(client_id)

        def allowed(fact: dict[str, Any], bank: str) -> bool:
            if not enforce_acl:
                return True
            return self._readable(fact, caller, bank)

        wanted = set(ids)
        out: dict[str, list[str]] = {}
        # Observation ids already refused (an invisible, missing or
        # document-less source): they stop the scan paging the bank for them.
        refused: set[str] = set()
        for bank in banks:
            if not wanted - out.keys() - refused:
                break
            # Per-bank index of every unit scanned: id -> (document_id,
            # allowed). An observation's sources can sit on an earlier page
            # than the observation itself, so it is kept for the whole bank.
            seen: dict[str, tuple[str | None, bool]] = {}
            # Observation ids waiting for their sources to turn up.
            pending: dict[str, list[str]] = {}
            offset = 0
            while wanted - out.keys() - refused:
                try:
                    resp = await self._call(
                        "list_memories", bank_id=bank, limit=_DELETE_SCAN_PAGE, offset=offset
                    )
                except Exception:
                    break  # fail-soft per bank; unresolved ids stay withheld
                items = resp.get("items", [])
                for fact in items:
                    fact_id = fact.get("id")
                    document_id = fact.get("document_id")
                    ok = allowed(fact, bank)
                    if fact_id:
                        seen[fact_id] = (document_id, ok)
                    if where is not None and ok and (document_id or fact_id):
                        where.setdefault(str(document_id or fact_id), bank)
                    # Either handle resolves to the same document. Ordered so
                    # the canonical id wins when a caller passes both.
                    matches = (
                        wanted.intersection({i for i in (document_id, fact_id) if i})
                        - out.keys()
                        - refused
                        - pending.keys()
                    )
                    if not matches or not ok:
                        continue
                    if document_id:
                        for given in matches:
                            out[given] = [document_id]
                        continue
                    sources = [s for s in fact.get("source_memory_ids") or [] if s]
                    for given in matches:
                        if sources:
                            pending[given] = sources
                        elif fact.get("fact_type") == "observation":
                            refused.add(given)  # derived from nothing we can address
                        elif fact_id:
                            # A document-less, source-less unit: the fact id is
                            # the only handle there is (older engine / fixture).
                            out[given] = [fact_id]
                for given, sources in list(pending.items()):
                    if not all(s in seen for s in sources):
                        continue
                    del pending[given]
                    resolved = [seen[s] for s in sources]
                    if all(ok and doc for doc, ok in resolved):
                        out[given] = list(dict.fromkeys(doc for doc, _ in resolved if doc))
                    else:
                        refused.add(given)
                if len(items) < _DELETE_SCAN_PAGE:
                    break  # last page
                offset += _DELETE_SCAN_PAGE
            # A source that never turned up in this bank fails closed.
            refused.update(pending)
        return out

    async def _delete_document_anywhere(self, document_id: str, banks: list[str]) -> bool:
        """Delete ``document_id`` from the first of ``banks`` that holds it.

        Hindsight 404s a missing document (NOT idempotent-200), so a per-bank
        probe that misses must continue the sweep — previously the first 404
        aborted the whole call, which made every private-bank item
        undeletable (the shared bank is probed first and always 404'd).
        """
        for bank in banks:
            try:
                res = await self._call("delete_document", bank_id=bank, document_id=document_id)
            except Exception as exc:
                if _http_status(exc) == 404:
                    continue  # not in this bank — keep sweeping
                raise
            if int(res.get("memory_units_deleted", 0)) > 0:
                return True
        return False

    @_gated_write
    async def delete(
        self,
        ids: list[str],
        *,
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, int]:
        """Delete by id; ``deleted`` counts the given ids that removed something.

        An id may be a document id, a per-fact id, or an observation's unit
        id. An observation resolves to its source documents, whose deletion
        cascades it (:meth:`_deletable_ids`, #2747). One that maps to exactly
        one source document deletes that document; one that maps to more is
        refused whole with :class:`ObservationMultiSourceDelete`, so a single
        id never reaches more than one document. The refusal comes before
        anything in the call is deleted, in every layout (#2780).
        """
        deleted = 0
        # We don't know which bank each document_id lives in without a
        # lookup; try the caller's allowed banks.
        # ``dataset`` is passed through verbatim: an empty list is an
        # unaddressable scope, NOT an unset one, and must sweep no banks
        # (#1451 — ``dataset or _SHARED`` here is what turned an approved
        # foreign-bank delete into a shared-bank delete).
        banks = [self._bank_of(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        if not banks:
            return {"deleted": 0}
        if self._acl_enforced:
            # One bank holds several agents' private docs (unified, the
            # two-bank private bank): gate deletes on the same visibility rule
            # the read paths enforce, so one agent can't delete another's
            # private doc by id. Each document is deleted from the bank it was
            # found in.
            where: dict[str, str] = {}
            deletable = await self._deletable_ids(set(ids), banks, client_id, where=where)
            _refuse_multi_source(ids, deletable)
            for given_id in ids:
                if given_id not in deletable:
                    continue  # withheld: not visible to this caller (fail-closed)
                removed = [
                    await self._delete_document_anywhere(
                        doc, [where[doc]] if doc in where else banks
                    )
                    for doc in deletable[given_id]
                ]
                if any(removed):
                    deleted += 1
            return {"deleted": deleted}
        # The old split layout needs no ACL gate — _allowed_namespaces never
        # yields a foreign private bank, so bank isolation already protects
        # it. The scan only translates fact and observation ids into document
        # ids. Like the other layouts it resolves every id and refuses a
        # multi-source observation before the first delete (#2780). An id the
        # scan cannot resolve is still tried as a document id, so a plain
        # document the scan misses keeps deleting.
        resolved = await self._deletable_ids(set(ids), banks, client_id, enforce_acl=False)
        _refuse_multi_source(ids, resolved)
        for given_id in dict.fromkeys(ids):
            docs = resolved.get(given_id, [given_id])
            removed = [await self._delete_document_anywhere(doc, banks) for doc in docs]
            if any(removed):
                deleted += 1
        return {"deleted": deleted}

    # ── recall (fan-out added in Task P1-3) ────────────────────────────

    async def recall(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        max_tokens: int = 4096,
        dataset: str | list[str] = _SHARED,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        budget: str = "mid",
        prefer_observations: bool = False,
        include: dict[str, Any] | None = None,
        query_timestamp: str | None = None,
        min_scores: dict[str, float] | None = None,
        client_id: str | None = None,
        project: str | None = None,
    ) -> RecallResults:
        """Fan out per-bank recall to the caller's allowed banks, merge under
        one token budget.

        What each bank is asked for (:meth:`_engine_filter`): in the two-bank
        layout a ``shared`` read is the platform bank, held to the visibility
        rule, plus the caller's own memory in the private bank, held to its
        ``agent:`` tag. ``project`` narrows every bank to that project plus
        global memory (:meth:`_project_groups`); with none (or ``"*"``) the
        whole bank is read, and a ``project:`` tag hides nothing
        (ADR-0058 decision 7b).

        Modern (0.8.x) Hindsight returns a native per-result ``scores.final``
        — a combined reranker + recency/temporal ranking score — so that is
        used for cross-bank merge ordering whenever at least one result in
        the union carries one; the :8086 reranker only runs as a FALLBACK
        when the engine gave us nothing to rank by (an older Hindsight, or a
        response shape that omitted ``scores``). The §4b precedence ladder
        is always the tiebreak.

        In unified-bank mode ``_allowed_namespaces`` collapses to a single
        ``shared`` bank, so this is a one-bank recall (the budget still
        applies). ``tags_match``/``tag_groups``/``budget``/
        ``prefer_observations``/``include``/``query_timestamp``/
        ``min_scores`` are Hindsight-native recall knobs — each is only
        forwarded to the engine when the caller actually set it, so a client
        fake built against the older narrow signature stays compatible.

        Returns a :class:`RecallResults` — a ``list[dict]`` (every existing
        caller keeps iterating/indexing/comparing it exactly as before) that
        additionally carries the response-level ``entities``/``chunks``/
        ``source_facts`` Hindsight returns alongside ``results`` (merged
        across the fanned-out banks) as ``.entities``/``.chunks``/
        ``.source_facts`` attributes, instead of silently discarding them.
        """
        import asyncio

        banks = list(
            dict.fromkeys(self._bank_of(ns) for ns in self._allowed_namespaces(dataset, client_id))
        )
        if not banks:
            return RecallResults([])
        effective_types = list(types) if types else list(_DEFAULT_RECALL_TYPES)
        caller = self._caller_agent(client_id)
        project_groups = self._project_groups(project)

        async def _one(bank: str) -> tuple[list[dict[str, Any]], dict, dict, dict, bool]:
            scoped = self._engine_filter(bank, caller, tags, tags_match, tag_groups, project_groups)
            kwargs: dict[str, Any] = {
                "bank_id": bank,
                "query": query,
                "types": effective_types,
                "max_tokens": max_tokens,
                "tags": scoped["tags"],
            }
            # Only forward the extended knobs when the caller set them, so
            # clients/fakes that don't know the param stay compatible.
            if scoped["tags_match"] is not None:
                kwargs["tags_match"] = scoped["tags_match"]
            if scoped["tag_groups"] is not None:
                kwargs["tag_groups"] = scoped["tag_groups"]
            if budget is not None and budget != "mid":
                kwargs["budget"] = budget
            if prefer_observations:
                kwargs["prefer_observations"] = True
            if include is not None:
                kwargs["include"] = include
            if query_timestamp is not None:
                kwargs["query_timestamp"] = query_timestamp
            if min_scores is not None:
                kwargs["min_scores"] = min_scores
            resp = await self._call("recall", **kwargs)
            raw = [self._label(self._fact_to_item(f, bank), bank) for f in resp.get("results", [])]
            # The read ACL (and the project scope, which the engine was given
            # too) BEFORE the rerank + token budget, so that docs the caller
            # may not see never consume the caller's budget (and never leak),
            # and the returned count reflects only visible docs. Per bank,
            # because who may read a memory depends on the bank it is in.
            items = self._filter_visible(raw, client_id, bank=bank, project_groups=project_groups)
            return (
                items,
                resp.get("entities") or {},
                resp.get("chunks") or {},
                resp.get("source_facts") or {},
                len(items) != len(raw),
            )

        per_bank = await asyncio.gather(*[_one(b) for b in banks])
        union: list[dict[str, Any]] = []
        entities_merged: dict[str, Any] = {}
        chunks_merged: dict[str, Any] = {}
        source_facts_merged: dict[str, Any] = {}
        withheld = False
        for items, ents, chks, sfacts, dropped in per_bank:
            union.extend(items)
            entities_merged.update(ents)
            chunks_merged.update(chks)
            source_facts_merged.update(sfacts)
            withheld = withheld or dropped
        if not union:
            return RecallResults([])

        # Native scores (Hindsight 0.8.x RecallScores.final) are the engine's
        # own combined ranking signal, computed the same way per bank — trust
        # them across the fan-out and skip the :8086 fallback entirely when
        # present, rather than clobbering a real signal with a second opinion.
        if not any(isinstance(item.get("score"), (int, float)) for item in union):
            union = await self._rerank_union(query, union)
        union.sort(key=self._precedence_key)  # stable: precedence wins ties
        budgeted = self._apply_token_budget(union, max_tokens)

        # #1794 fallback: if the query names a literal-looking token (marker,
        # ID, key) that no extracted fact's text actually contains, extraction
        # may have dropped it — augment (never replace) with raw-document
        # hits carrying it verbatim.
        literal_tokens = self._literal_tokens(query)
        if literal_tokens:
            covered = " ".join((it.get("text") or "") for it in budgeted).lower()
            uncovered = [t for t in literal_tokens if t.lower() not in covered]
            if uncovered:
                try:
                    doc_items = await self._document_fallback(
                        banks, uncovered, client_id=client_id, project_groups=project_groups
                    )
                except Exception:
                    doc_items = []
                if doc_items:
                    budgeted = list(budgeted) + doc_items

        # ``chunks``/``source_facts``/``entities`` ride beside ``results`` at the
        # response level, so filtering only the result list left another
        # agent's private text reachable through them. Wherever one bank holds
        # several agents' private memory, and wherever the read named a
        # project; in the old split layout an unscoped fan-out never fetched
        # a foreign private bank.
        if self._acl_enforced or project_groups is not None:
            chunks_merged, source_facts_merged, entities_merged = self._filter_recall_enrichment(
                budgeted,
                chunks_merged,
                source_facts_merged,
                entities_merged,
                withheld=withheld,
                client_id=client_id,
            )

        return RecallResults(
            budgeted,
            entities=entities_merged or None,
            chunks=chunks_merged or None,
            source_facts=source_facts_merged or None,
        )

    def _filter_recall_enrichment(
        self,
        items: list[dict[str, Any]],
        chunks: dict[str, Any],
        source_facts: dict[str, Any],
        entities: dict[str, Any],
        *,
        withheld: bool,
        client_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Fail closed when joining response-level recall enrichment.

        Only explicit joins to a result that survived visibility and the token
        budget are kept; an enrichment that cannot be joined is omitted rather
        than guessed visible.

        * ``source_facts``: kept when a surviving result lists the fact in
          ``source_fact_ids`` and the fact itself passes the read ACL.
        * ``chunks``: kept when the chunk id, document id or fact id matches a
          surviving result or kept source fact.
        * ``entities``: keyed by entity name, joined to the ``entities`` names
          the surviving results carry. An entity's state is bank-wide, and
          nothing in the response says which facts it was built from, so when
          any result was withheld for visibility none is returned.
        """

        def _values(value: Any) -> set[str]:
            if value is None:
                return set()
            if isinstance(value, (list, tuple, set)):
                return {str(v) for v in value if v is not None and str(v)}
            return {str(value)} if str(value) else set()

        fact_ids: set[str] = set()
        document_ids: set[str] = set()
        chunk_ids: set[str] = set()
        referenced_source_ids: set[str] = set()
        entity_names: set[str] = set()
        for item in items:
            fact_ids.update(_values(item.get("fact_id")))
            document_ids.update(_values(item.get("document_id") or item.get("id")))
            chunk_ids.update(_values(item.get("chunk_id")))
            referenced_source_ids.update(_values(item.get("source_fact_ids")))
            for ent in item.get("entities") or []:
                name = ent.get("text") or ent.get("name") if isinstance(ent, dict) else ent
                entity_names.update(_values(name))

        visible_source_facts: dict[str, Any] = {}
        for key, raw in source_facts.items():
            if not isinstance(raw, dict):
                continue
            if str(raw.get("id") or key) not in referenced_source_ids:
                continue
            # The bank a source fact came from is not in the response, so the
            # rule that holds in every bank applies (:meth:`_readable`).
            if not self._filter_visible([self._fact_to_item(raw, self._platform_bank)], client_id):
                continue
            visible_source_facts[str(key)] = raw
            fact_ids.update(_values(raw.get("id") or key))
            document_ids.update(_values(raw.get("document_id")))
            chunk_ids.update(_values(raw.get("chunk_id")))

        visible_chunks: dict[str, Any] = {}
        for key, raw in chunks.items():
            if not isinstance(raw, dict):
                continue
            if (
                _values(raw.get("id") or key) & chunk_ids
                or _values(raw.get("document_id")) & document_ids
                or _values(raw.get("fact_id") or raw.get("fact_ids")) & fact_ids
            ):
                visible_chunks[str(key)] = raw

        visible_entities: dict[str, Any] = {}
        if not withheld:
            for key, raw in entities.items():
                names = {str(key)}
                if isinstance(raw, dict):
                    names |= _values(raw.get("canonical_name"))
                if names & entity_names:
                    visible_entities[str(key)] = raw

        return visible_chunks, visible_source_facts, visible_entities

    @staticmethod
    def _precedence_key(item: dict[str, Any]) -> tuple[int, float]:
        """§4b ladder: shared/curated observations rank above raw private
        facts. Lower tuple sorts first. Second element is negative score
        (native ``scores.final`` when the engine supplied one, else the
        :8086 fallback reranker's score) so higher score sorts earlier
        within the same tier.
        """
        is_observation = item.get("type") == "observation"
        is_shared = item.get("dataset") == _SHARED
        tier = 0 if (is_observation or is_shared) else 1
        return (tier, -float(item.get("score") or 0.0))

    async def _rerank_union(self, query: str, union: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._reranker is None or not self._rerank_enabled or len(union) < 2:
            return union
        try:
            ranked = await self._reranker.rerank(query, [u["text"] for u in union])
        except Exception:
            return union  # reranker down → keep fused order (fail-soft)
        for entry in ranked:
            idx = entry.get("index")
            if isinstance(idx, int) and 0 <= idx < len(union):
                union[idx]["score"] = float(entry.get("relevance_score", 0.0))
        return union

    @staticmethod
    def _apply_token_budget(items: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
        """Greedy fill by ~4 chars/token on the text field (Hindsight counts
        only fact text toward the budget)."""
        out: list[dict[str, Any]] = []
        spent = 0
        for item in items:
            cost = max(1, len(item.get("text", "")) // 4)
            if spent + cost > max_tokens and out:
                break
            out.append(item)
            spent += cost
        return out

    def _one_bank_groups(
        self,
        bank: str,
        client_id: str | None,
        tags: list[str] | None,
        tags_match: str | None,
        tag_groups: list[dict[str, Any]] | None,
        project_groups: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """The engine ``tag_groups`` of a one-bank read (reflect, raw recall).

        The two-bank private bank: the caller's own ``agent:`` tag. Any other
        bank: the visibility rule
        (:func:`hal0.memory.read_scope.reflect_visibility_scope`). The caller's
        ``tags`` / ``tags_match`` ride along as one more group, then the
        caller's ``tag_groups`` and the project scope; the engine ANDs them.
        """
        caller = client_id or self._client_id
        identity = caller if caller and caller != _ANONYMOUS else None
        if self._two_bank and bank == self._private_bank:
            groups: list[dict[str, Any]] = [_own_private_group(identity)]
            if tags:
                groups.append({"tags": list(tags), "match": tags_match or "any"})
        else:
            groups = reflect_visibility_scope(identity, tags, tags_match)
        groups.extend(tag_groups or [])
        groups.extend(project_groups or [])
        return groups

    def _holds_others_private(self, bank: str) -> bool:
        """Whether ``bank`` holds more than one agent's private memories."""
        return self._unified_bank or (self._two_bank and bank == self._private_bank)

    # ── reflect (LLM-backed synthesis) ───────────────────────────────────
    #
    # Reflect operates on exactly one bank — there is no server-side
    # cross-bank reflect, and merging LLM narratives across banks the way
    # recall merges facts would not make sense. ``dataset`` therefore
    # resolves to a SINGLE namespace via ``_write_namespace`` (the same
    # ACL-checked single-bank resolution ``add`` uses), not the read-side
    # multi-bank ``_allowed_namespaces``.

    async def reflect(
        self,
        query: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        budget: str = "low",
        max_tokens: int = 4096,
        fact_types: list[str] | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        exclude_mental_models: bool = False,
        exclude_mental_model_ids: list[str] | None = None,
        include: dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
        enforce_visibility: bool = True,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Ask the engine to answer ``query`` from one bank.

        A reflect answer is prose, so what the caller may read has to be
        decided before the engine writes it. With ``enforce_visibility`` (the
        default, and what every identified caller gets: REST, both MCP mounts)
        two rules apply, here and nowhere else:

        * the engine is given ``tag_groups`` equal to the read-side visibility
          rule (:func:`hal0.memory.read_scope.reflect_visibility_scope`): a
          memory is readable unless it is ``visibility:private``, or when it
          is the caller's own. The caller's ``tags`` / ``tags_match`` travel
          inside those groups, because the engine refuses ``tags`` next to
          ``tag_groups``; ``tag_groups`` the caller sent are appended, and
          the engine ANDs them, so they can narrow the read and never widen
          it. On the two-bank private bank the rule is the caller's own
          ``agent:`` tag instead: everything there is private;
        * wherever the bank holds other agents' private memories (the unified
          bank, the two-bank private bank) mental models are left out
          (``exclude_mental_models``): a model is written from the whole bank
          and cannot be filtered by tag.

        ``project`` narrows the reflect to one project plus global memory
        (:meth:`_project_groups`); with none (or ``"*"``) the whole bank.

        ``enforce_visibility=False`` sends the request as given. It is for an
        operator surface that names a bank and carries no caller identity.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        if enforce_visibility:
            groups = self._one_bank_groups(
                bank, client_id, tags, tags_match, tag_groups, self._project_groups(project)
            )
            tags, tags_match, tag_groups = None, None, groups
            exclude_mental_models = bool(exclude_mental_models) or self._holds_others_private(bank)
        elif (project_groups := self._project_groups(project)) is not None:
            groups = [{"tags": list(tags), "match": tags_match or "any"}] if tags else []
            groups += [*(tag_groups or []), *project_groups]
            tags, tags_match, tag_groups = None, None, groups
        return await self._call(
            "reflect",
            bank_id=bank,
            query=query,
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
        )

    async def recall_raw(
        self,
        query: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        types: list[str] | None = None,
        max_tokens: int = 4096,
        budget: str | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        prefer_observations: bool | None = None,
        include: dict[str, Any] | None = None,
        query_timestamp: str | None = None,
        min_scores: dict[str, float] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """The engine's own recall answer for ONE bank, in the engine's shape.

        :meth:`recall` fans out, reranks, budgets and returns hal0's items.
        This is for a caller that speaks the engine's protocol and needs the
        engine's response (``{results, entities, chunks, ...}``) as it came.
        The bank is the one a write with the same ``dataset`` would land in.

        What the caller may read is enforced the way :meth:`reflect` enforces
        it, which is why this method sits beside it: the engine is given
        ``tag_groups`` equal to the read-side visibility rule
        (:func:`hal0.memory.read_scope.reflect_visibility_scope`), with the
        caller's ``tags`` / ``tags_match`` folded in as one more group, and
        the ``tag_groups`` passed here (a project scope) appended. The engine
        ANDs the groups, so they narrow the read and never widen it, and no
        ``tags`` key is sent beside them.

        The response-level ``entities``, ``chunks`` and ``source_facts`` come
        back as the engine built them. An entity's state is bank-wide, so a
        caller that passes this response on decides what to do with those.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        groups = self._one_bank_groups(bank, client_id, tags, tags_match, tag_groups, None)
        kwargs: dict[str, Any] = {
            "bank_id": bank,
            "query": query,
            "max_tokens": max_tokens,
            "tag_groups": groups,
        }
        optional = {
            "types": types,
            "budget": budget,
            "prefer_observations": prefer_observations,
            "include": include,
            "query_timestamp": query_timestamp,
            "min_scores": min_scores,
            "timeout_s": timeout_s,
        }
        kwargs.update({key: value for key, value in optional.items() if value is not None})
        resp = await self._call("recall", **kwargs)
        return resp if isinstance(resp, dict) else {}

    # ── single-memory curation (non-destructive "this is wrong" path) ───

    async def _ensure_memory_visible(
        self, bank: str, memory_id: str, client_id: str | None
    ) -> None:
        """Shared-bank guard: fetch the memory unit and check it against the
        read ACL every read path enforces (:meth:`_readable`), so a caller
        cannot curate or inspect another agent's private memory just by
        guessing its id. No-op in the old split layout — bank isolation
        already does the job there, matching :meth:`delete`'s gate.

        Hindsight's single-memory GET response shape isn't in the published
        OpenAPI schema (an empty ``{}`` response model) — this assumes it
        carries ``tags`` the same way list/recall items do. A memory with no
        (or unrecognised) ``tags`` field reads as shared on a bank that holds
        shared memory, the same fail-open default an untagged doc gets
        everywhere else on this ACL, and as nobody's on the two-bank private
        bank.
        """
        if not self._acl_enforced:
            return
        fact = await self._call("get_memory", bank_id=bank, memory_id=memory_id)
        if not isinstance(fact, dict):
            fact = {}
        if not self._readable(fact, self._caller_agent(client_id), bank):
            raise PermissionError(f"memory {memory_id!r} is not visible to this caller")

    @_gated_write
    async def curate(
        self,
        memory_id: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        text: str | None = None,
        context: str | None = None,
        occurred_start: str | None = None,
        occurred_end: str | None = None,
        fact_type: str | None = None,
        entities: list[str] | None = None,
        state: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Edit a memory unit, or soft-invalidate/revert it via ``state``
        (``"invalidated"``/``"valid"`` — reversible either way, so this is
        the non-destructive "this is wrong" correction path, distinct from
        :meth:`delete`).

        ``memory_id`` is the PER-FACT id (``RecallResult.id`` /
        ``metadata.fact_id`` on a list/recall item) — NOT the ``document_id``
        :meth:`add`/:meth:`delete` address. ``dataset`` names the single bank
        the memory lives in (default ``shared``, ACL-checked the same way
        ``add`` resolves its write namespace).
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._ensure_memory_visible(bank, memory_id, client_id)
        return await self._call(
            "update_memory",
            bank_id=bank,
            memory_id=memory_id,
            text=text,
            context=context,
            occurred_start=occurred_start,
            occurred_end=occurred_end,
            fact_type=fact_type,
            entities=entities,
            state=state,
            reason=reason,
        )

    async def memory_history(
        self, memory_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        """Revision history for one memory unit (curation audit trail).

        The history repeats the memory's text, so the memory is read first and
        held to the visibility rule in every layout (#3573), and to the
        project scope under the unified bank as :meth:`_ensure_memory_visible`
        does. A memory the caller may not read is 404, the same answer as one
        that does not exist.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        missing = NotFound(
            "no such memory", code="memory.memory_not_found", details={"id": memory_id}
        )
        try:
            fact = await self._call("get_memory", bank_id=bank, memory_id=memory_id)
        except Exception as exc:
            if _http_status(exc) == 404:
                raise missing from None
            raise
        fact = fact if isinstance(fact, dict) else {}
        caller = self._caller_agent(client_id)
        # The visibility rule in every layout (#3573), and the bank's own rule
        # where one bank holds several agents' private memory (the two-bank
        # private bank: the caller's own agent: tag). A project tag hides
        # nothing (ADR-0058 decision 7b).
        if not self._private_readable(fact, caller, bank):
            raise missing
        return await self._call("memory_history", bank_id=bank, memory_id=memory_id)

    # ── mental models ─────────────────────────────────────────────────────
    #
    # Bank-scoped objects whose content the engine writes from the memories
    # their scope admits. Reads (list, get) follow the rule in "ACL: the
    # bank-wide read tools" (#3573): a model built from memory the caller may
    # not read is left out of a list and is 404 on a get. Update, delete and
    # refresh of such a model are 404 too, so a write cannot be used to read
    # it or to learn that it exists. A scope (tags, trigger) the engine would
    # read as the whole bank by accident is refused on create and update, and
    # a scope change is refused when it could carry content written under the
    # old scope to a caller the old scope would not have let read it. The REST
    # admin surface (``memory_admin.py``) applies no ACL here: it is the
    # operator's.

    @staticmethod
    def _agent_model_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
        """The trigger an agent's create or re-trigger sends to the engine.

        A ``trigger.tag_groups`` that is set but filters nothing is refused:
        the engine treats any ``tag_groups`` that is not None as replacing
        the model's tags, and an empty list as no filter, so the model would
        be built from the whole bank whatever its tags say.

        ``exclude_mental_models`` is always set: a refresh's reflect otherwise
        also searches the bank's other mental models, and an untagged one may
        have been written from other agents' private memory. A model without
        it is never confined to its caller
        (:func:`hal0.memory.read_scope.model_scope_confined_to`).
        """
        out = dict(trigger) if isinstance(trigger, dict) else {}
        out["exclude_mental_models"] = True
        if "tag_groups" not in out or out["tag_groups"] is None:
            return out
        groups = out["tag_groups"]
        if not isinstance(groups, list) or not groups:
            raise BadRequest(
                "trigger.tag_groups must be a non-empty list of tag groups; "
                "an empty list builds the model from the whole bank",
                code="memory.mental_model_scope_invalid",
                details={"field": "trigger.tag_groups"},
            )
        return out

    @staticmethod
    def _model_missing(mental_model_id: str) -> NotFound:
        return NotFound(
            "no such mental model",
            code="memory.mental_model_not_found",
            details={"id": mental_model_id},
        )

    async def _require_readable_model(
        self, bank: str, mental_model_id: str, caller: str
    ) -> dict[str, Any]:
        """The model, when ``caller`` may read it; else the 404 a missing
        model gets, so the two cannot be told apart."""
        try:
            model = await self._call(
                "get_mental_model", bank_id=bank, mental_model_id=mental_model_id
            )
        except Exception as exc:
            if _http_status(exc) == 404:
                raise self._model_missing(mental_model_id) from None
            raise
        if not isinstance(model, dict) or not await self._readable_models(bank, [model], caller):
            raise self._model_missing(mental_model_id)
        return model

    async def list_mental_models(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        detail: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """The bank's mental models the caller may read. ``total`` counts
        what is returned when anything was left out."""
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        resp = await self._call(
            "list_mental_models",
            bank_id=bank,
            tags=tags,
            tags_match=tags_match,
            detail=detail,
            limit=limit,
            offset=offset,
        )
        if isinstance(resp, dict) and isinstance(resp.get("items"), list):
            caller = self._caller_agent(client_id)
            items = resp["items"]
            kept = await self._readable_models(bank, items, caller, listed=True)
            resp = {**resp, "items": kept}
            if "total" in resp:
                resp["total"] = _visible_total(offset, len(kept), limit)
        return resp

    async def get_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        return await self._require_readable_model(
            bank, mental_model_id, self._caller_agent(client_id)
        )

    @_gated_write
    async def create_mental_model(
        self,
        *,
        name: str,
        source_query: str,
        dataset: str = _SHARED,
        client_id: str | None = None,
        id: str | None = None,
        tags: list[str] | None = None,
        max_tokens: int = 2048,
        trigger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trigger = self._agent_model_trigger(trigger)
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        return await self._call(
            "create_mental_model",
            bank_id=bank,
            name=name,
            source_query=source_query,
            id=id,
            tags=tags,
            max_tokens=max_tokens,
            trigger=trigger,
        )

    @_gated_write
    async def update_mental_model(
        self,
        mental_model_id: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        name: str | None = None,
        source_query: str | None = None,
        max_tokens: int | None = None,
        tags: list[str] | None = None,
        trigger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a model the caller may read (404 otherwise).

        A model's content stays as it was until the next refresh, so a change
        of scope (``tags`` or ``trigger``) could otherwise hand content
        written under the old scope to whoever the new scope lets read it:
        re-tagging a whole-bank model to ``agent:<self>`` would serve that
        model's text, other agents' private notes included. So while the bank
        holds another agent's private memory, a scope change is accepted only
        when both the old and the new scope are confined to the caller
        (``memory.mental_model_rescope_refused`` otherwise; create a new model
        instead). The engine's answer is returned without its content when
        the caller may not read the model under its new scope. A trigger sent
        here is merged over the model's current one and always carries
        ``exclude_mental_models`` (:meth:`_agent_model_trigger`).
        """
        if trigger is not None:
            self._agent_model_trigger(trigger)  # refuses an empty tag_groups
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        caller = self._caller_agent(client_id)
        before = await self._require_readable_model(bank, mental_model_id, caller)
        old_trigger = before.get("trigger") if isinstance(before.get("trigger"), dict) else {}
        if trigger is not None:
            trigger = self._agent_model_trigger({**old_trigger, **trigger})
        if tags is not None or trigger is not None:
            after = {
                **before,
                "tags": list(tags) if tags is not None else before.get("tags"),
                "trigger": {**old_trigger, **trigger} if trigger is not None else old_trigger,
            }
            confined = model_scope_confined_to(before, caller) and model_scope_confined_to(
                after, caller
            )
            if not confined and (await self._foreign_private(bank, caller))[0]:
                raise BadRequest(
                    "this model's scope cannot be changed while the bank holds other "
                    "agents' private memory: its current content was written under the "
                    "old scope. Create a new model with the scope you want instead.",
                    code="memory.mental_model_rescope_refused",
                    details={"id": mental_model_id},
                )
        resp = await self._call(
            "update_mental_model",
            bank_id=bank,
            mental_model_id=mental_model_id,
            name=name,
            source_query=source_query,
            max_tokens=max_tokens,
            tags=tags,
            trigger=trigger,
        )
        if isinstance(resp, dict) and not await self._readable_models(bank, [resp], caller):
            resp = {k: v for k, v in resp.items() if k not in _MODEL_CONTENT_FIELDS}
        return resp

    @_gated_write
    async def delete_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_readable_model(bank, mental_model_id, self._caller_agent(client_id))
        return await self._call(
            "delete_mental_model", bank_id=bank, mental_model_id=mental_model_id
        )

    @_gated_write
    async def refresh_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_readable_model(bank, mental_model_id, self._caller_agent(client_id))
        return await self._call(
            "refresh_mental_model", bank_id=bank, mental_model_id=mental_model_id
        )

    # ── directives ───────────────────────────────────────────────────────
    #
    # A directive is a standing instruction the engine injects into a
    # reflect's prompt. On the two-bank private bank a directive is its
    # writer's, like everything else there (#3573, ADR-0058 decision 7):
    #
    # * create stamps ``agent:<caller>`` and ``visibility:private`` (the
    #   caller's own ``agent:``/``visibility:`` tags are dropped), and an
    #   unidentified caller cannot create one;
    # * update re-stamps them when it changes the tags, so a directive cannot
    #   be made untagged (bank-wide) or another agent's;
    # * list and get return only what :meth:`_private_readable` admits, so a
    #   directive with no owner is nobody's; update and delete of any other
    #   are 404, the same answer as a directive that does not exist.
    #
    # The engine applies a reflect's ``tag_groups`` to the directives it
    # loads (``list_directives(isolation_mode=True)``: untagged directives
    # always apply, tagged ones only inside the groups). A private-bank
    # reflect is held to the caller's ``agent:`` tag, so another agent's
    # directive never steers it; an untagged one, which only the operator's
    # admin API or the bank spec can create, still applies to every agent.
    # On any other bank the read rule is the visibility rule, which an
    # untagged or shared directive passes, as before.

    def _directive_tags(self, bank: str, client_id: str | None, tags: Any) -> list[str] | None:
        """The tags a directive is written with on ``bank``."""
        if not (self._two_bank and bank == self._private_bank):
            return tags
        caller = self._caller_agent(client_id)
        if not is_identified(caller):
            raise Forbidden(
                "a private directive needs an identified agent",
                code="memory.directive_owner_required",
            )
        kept = [t for t in coerce_tags(tags) if not _is_reserved_tag(t)]
        return [*kept, f"{_AGENT_TAG_PREFIX}{caller}", _VISIBILITY_PRIVATE]

    async def _require_readable_directive(
        self, bank: str, directive_id: str, caller: str
    ) -> dict[str, Any]:
        """The directive, or 404 when it does not exist or the caller may not read it."""
        missing = NotFound(
            "no such directive",
            code="memory.directive_not_found",
            details={"id": directive_id},
        )
        try:
            directive = await self._call("get_directive", bank_id=bank, directive_id=directive_id)
        except Exception as exc:
            if _http_status(exc) == 404:
                raise missing from None
            raise
        if not isinstance(directive, dict) or not self._private_readable(directive, caller, bank):
            raise missing
        return directive

    async def list_directives(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        active_only: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        resp = await self._call(
            "list_directives",
            bank_id=bank,
            tags=tags,
            tags_match=tags_match,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        if not isinstance(resp, dict):
            return resp
        key = "items" if isinstance(resp.get("items"), list) else "directives"
        rows = resp.get(key)
        if not isinstance(rows, list):
            return resp
        caller = self._caller_agent(client_id)
        kept = [r for r in rows if isinstance(r, dict) and self._private_readable(r, caller, bank)]
        if len(kept) == len(rows):
            return resp
        out = {**resp, key: kept}
        if "total" in resp:
            out["total"] = _visible_total(offset, len(kept), limit)
        return out

    async def get_directive(
        self, directive_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        return await self._require_readable_directive(
            bank, directive_id, self._caller_agent(client_id)
        )

    @_gated_write
    async def create_directive(
        self,
        *,
        name: str,
        content: str,
        dataset: str = _SHARED,
        client_id: str | None = None,
        priority: int = 0,
        is_active: bool = True,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        return await self._call(
            "create_directive",
            bank_id=bank,
            name=name,
            content=content,
            priority=priority,
            is_active=is_active,
            tags=self._directive_tags(bank, client_id, tags),
        )

    @_gated_write
    async def update_directive(
        self,
        directive_id: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        name: str | None = None,
        content: str | None = None,
        priority: int | None = None,
        is_active: bool | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_readable_directive(bank, directive_id, self._caller_agent(client_id))
        return await self._call(
            "update_directive",
            bank_id=bank,
            directive_id=directive_id,
            name=name,
            content=content,
            priority=priority,
            is_active=is_active,
            tags=self._directive_tags(bank, client_id, tags) if tags is not None else None,
        )

    @_gated_write
    async def delete_directive(
        self, directive_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_readable_directive(bank, directive_id, self._caller_agent(client_id))
        return await self._call("delete_directive", bank_id=bank, directive_id=directive_id)

    # ── async operations (so retain/refresh/consolidate are pollable) ───

    async def list_operations(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        status: str | None = None,
        type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        exclude_parents: bool | None = None,
    ) -> dict[str, Any]:
        """The bank's operations, less those over memory the caller may not
        see (#3573, :meth:`_operation_visible`). ``total`` stays the engine's
        count, an upper bound."""
        import asyncio

        bank = self._bank_of(self._write_namespace(dataset, client_id))
        resp = await self._call(
            "list_operations",
            bank_id=bank,
            status=status,
            type=type,
            limit=limit,
            offset=offset,
            exclude_parents=exclude_parents,
        )
        if not isinstance(resp, dict) or not isinstance(resp.get("operations"), list):
            return resp
        caller = self._caller_agent(client_id)
        rows = [r for r in resp["operations"] if isinstance(r, dict)]
        verdicts = await asyncio.gather(*[self._operation_visible(bank, r, caller) for r in rows])
        kept = [r for r, ok in zip(rows, verdicts, strict=True) if ok]
        out = {**resp, "operations": kept}
        if "total" in resp:
            out["total"] = _visible_total(offset, len(kept), limit)
        return out

    async def get_operation(
        self,
        operation_id: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        include_payload: bool | None = None,
    ) -> dict[str, Any]:
        """One operation's status.

        The record is read with its payload so that a retain can be judged on
        the tags it submitted (#3573, :meth:`_operation_visible`); the payload
        is returned only when ``include_payload`` asked for it. An operation
        over memory the caller may not see is answered the way the engine
        answers an id it does not know (``status: not_found``), and so is an
        engine 404, so neither tells the caller the operation exists.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        unknown = {"operation_id": operation_id, "status": "not_found"}
        try:
            op = await self._call(
                "get_operation",
                bank_id=bank,
                operation_id=operation_id,
                include_payload=True,
            )
        except Exception as exc:
            if _http_status(exc) == 404:
                return unknown
            raise
        if isinstance(op, dict):
            if op.get("status") == "not_found":
                return unknown
            if not await self._operation_visible(bank, op, self._caller_agent(client_id)):
                return unknown
            if not include_payload:
                op = {k: v for k, v in op.items() if k != "task_payload"}

        # #2030: a completed retain that extracted zero facts must say so on
        # the record every poller reads (memory_operation_get, dashboard).
        async def _doc(document_id: str) -> Any:
            return await self._call("get_document", bank_id=bank, document_id=document_id)

        return await annotate_retain_yield(op, _doc)

    async def _require_visible_operation(
        self, bank: str, operation_id: str, client_id: str | None
    ) -> None:
        """404 ``memory.operation_not_found`` for an operation the caller may
        not see (#3573), the same answer as one the engine does not know."""
        missing = NotFound(
            "no such operation",
            code="memory.operation_not_found",
            details={"id": operation_id},
        )
        try:
            op = await self._call(
                "get_operation", bank_id=bank, operation_id=operation_id, include_payload=True
            )
        except Exception as exc:
            if _http_status(exc) == 404:
                raise missing from None
            raise
        if not isinstance(op, dict) or op.get("status") == "not_found":
            raise missing
        if not await self._operation_visible(bank, op, self._caller_agent(client_id)):
            raise missing

    @_gated_write
    async def cancel_operation(
        self, operation_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_visible_operation(bank, operation_id, client_id)
        return await self._call("cancel_operation", bank_id=bank, operation_id=operation_id)

    @_gated_write
    async def retry_operation(
        self, operation_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        await self._require_visible_operation(bank, operation_id, client_id)
        return await self._call("retry_operation", bank_id=bank, operation_id=operation_id)

    # ── tags / bank stats / consolidation ────────────────────────────────

    async def list_tags(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        q: str | None = None,
        source: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """The bank's tags with counts, less the tags only other agents'
        private documents carry (#3573). A tag those documents carry stays
        when a document the caller can see carries it too. Counts are the
        engine's, over the whole bank: an upper bound."""
        import asyncio

        bank = self._bank_of(self._write_namespace(dataset, client_id))
        resp = await self._call(
            "list_tags", bank_id=bank, q=q, source=source, limit=limit, offset=offset
        )
        if not isinstance(resp, dict) or not isinstance(resp.get("items"), list):
            return resp
        caller = self._caller_agent(client_id)
        present, foreign_tags = await self._foreign_private(bank, caller, collect_tags=True)
        if not present:
            return resp
        rows = [r for r in resp["items"] if isinstance(r, dict) and isinstance(r.get("tag"), str)]

        async def _keep(tag: str) -> bool:
            if foreign_tags is not None and tag not in foreign_tags:
                return True
            try:
                docs = await self._call(
                    "list_documents",
                    bank_id=bank,
                    tags=[tag],
                    tags_match="all_strict",
                    limit=_TAG_CHECK_LIMIT,
                    offset=0,
                )
            except Exception:
                return False
            items = docs.get("items") if isinstance(docs, dict) else None
            return any(
                isinstance(d, dict)
                and isinstance(d.get("tags"), list)
                and self._private_readable(d, caller, bank)
                for d in items or []
            )

        verdicts = await asyncio.gather(*[_keep(r["tag"]) for r in rows])
        kept = [r for r, ok in zip(rows, verdicts, strict=True) if ok]
        out = {**resp, "items": kept}
        if "total" in resp:
            out["total"] = _visible_total(offset, len(kept), limit)
        return out

    # ── knowledge base (folders/pages — #showcase) ──────────────────────────
    #
    # A page is a mental model in a folder tree, so pages follow the mental
    # model rule (#3573): a page the caller may not read is pruned from the
    # tree and is 404 on a read. Folders hold no content and are kept.

    async def _readable_page_ids(self, bank: str, tree: Any, caller: str) -> set[str]:
        """Ids of the pages in ``tree`` that ``caller`` may read."""
        pages: list[dict[str, Any]] = []

        def _walk(nodes: Any) -> None:
            for node in nodes if isinstance(nodes, list) else []:
                if not isinstance(node, dict):
                    continue
                if node.get("kind") == "page":
                    pages.append(node)
                _walk(node.get("children"))

        _walk(tree.get("roots") if isinstance(tree, dict) else None)
        return {str(p.get("id")) for p in await self._readable_models(bank, pages, caller)}

    async def knowledge_base_tree(
        self, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        tree = await self._call("get_knowledge_base_tree", bank_id=bank)
        if not isinstance(tree, dict) or not isinstance(tree.get("roots"), list):
            return tree
        readable = await self._readable_page_ids(bank, tree, self._caller_agent(client_id))

        def _prune(nodes: list[Any]) -> list[Any]:
            out: list[Any] = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("kind") == "page" and str(node.get("id")) not in readable:
                    continue
                children = node.get("children")
                if isinstance(children, list):
                    node = {**node, "children": _prune(children)}
                out.append(node)
            return out

        return {**tree, "roots": _prune(tree["roots"])}

    async def knowledge_base_page(
        self, page_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        """One page, when the caller may read it.

        The page record carries no ``trigger``, which decides how its tags
        scope it, so the page is judged on its node in the tree. A page that
        is not in the tree, or that the caller may not read, is 404, the same
        answer as a page that does not exist.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        missing = NotFound(
            "no such knowledge page",
            code="memory.knowledge_page_not_found",
            details={"page_id": page_id},
        )
        tree = await self._call("get_knowledge_base_tree", bank_id=bank)
        if str(page_id) not in await self._readable_page_ids(
            bank, tree, self._caller_agent(client_id)
        ):
            raise missing
        try:
            return await self._call("get_knowledge_base_page", bank_id=bank, page_id=page_id)
        except Exception as exc:
            if _http_status(exc) == 404:
                raise missing from None
            raise

    # ── documents (raw retained documents — #showcase) ──────────────────────

    async def list_documents(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        q: str | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """A page of the bank's documents, less the ones private to another
        agent (#3573).

        Nothing in the answer may count the rows left out, or a caller could
        probe ``q`` / ``tags`` for another agent's private document ids and
        tags. So a page short of ``limit`` is filled by reading on from the
        engine (up to :data:`_LISTING_EXTRA_FETCHES` more pages), and
        ``total`` counts only what the caller can see: ``offset`` plus the
        rows returned, plus one when the page is full (there may be more).
        ``offset`` indexes the engine's rows, as before.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        page_size = limit if limit is not None else 100
        start = offset if offset is not None else 0
        caller = self._caller_agent(client_id)
        first: Any = None
        kept: list[dict[str, Any]] = []
        for fetch in range(1 + _LISTING_EXTRA_FETCHES):
            resp = await self._call(
                "list_documents",
                bank_id=bank,
                q=q,
                tags=tags,
                tags_match=tags_match,
                limit=page_size,
                offset=start + fetch * page_size,
            )
            if fetch == 0:
                first = resp
            if not isinstance(resp, dict) or not isinstance(resp.get("items"), list):
                break
            rows = resp["items"]
            for row in rows:
                if len(kept) >= page_size:
                    break
                if isinstance(row, dict) and self._private_readable(row, caller, bank):
                    kept.append(row)
            if len(rows) < page_size or page_size <= 0 or len(kept) >= page_size:
                break
        if not isinstance(first, dict) or not isinstance(first.get("items"), list):
            return first
        out = {**first, "items": kept}
        if "total" in first:
            out["total"] = _visible_total(start, len(kept), page_size)
        return out

    async def get_document(
        self, document_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        """One document's full text, when the caller may read it (#3573).

        A document private to another agent is 404, the same answer as a
        document that does not exist, so a caller cannot learn that it does.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        missing = NotFound(
            "no such document",
            code="memory.document_not_found",
            details={"document_id": document_id},
        )
        try:
            doc = await self._call("get_document", bank_id=bank, document_id=document_id)
        except Exception as exc:
            if _http_status(exc) == 404:
                raise missing from None
            raise
        if isinstance(doc, dict) and not self._private_readable(
            doc, self._caller_agent(client_id), bank
        ):
            raise missing
        return doc

    async def bank_stats(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        refresh: bool | None = None,
    ) -> dict[str, Any]:
        """The bank's statistics (#3573).

        The counts are over the whole bank and cannot be scoped to a caller,
        so while the bank holds private memory of another agent only the
        health fields (:data:`_STATS_HEALTH_FIELDS`) are returned, with
        ``counts_withheld: true``. The operator reads the full record through
        ``GET /api/memory/banks/{bank}/stats``.
        """
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        stats = await self._call("bank_stats", bank_id=bank, refresh=refresh)
        if not isinstance(stats, dict):
            return stats
        present, _ = await self._foreign_private(bank, self._caller_agent(client_id))
        if not present:
            return stats
        out = {k: stats[k] for k in _STATS_HEALTH_FIELDS if k in stats}
        out["counts_withheld"] = True
        return out

    @_gated_write
    async def consolidate(  # type: ignore[override]  — real implementation, ABC default is a stub
        self, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = self._bank_of(self._write_namespace(dataset, client_id))
        return await self._call("consolidate", bank_id=bank)

    # ── Runtime toggles ────────────────────────────────────────────────

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self._graph_enabled,
            "extraction_slot": self._extraction_slot,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        self._graph_enabled = bool(enabled)
        if extraction_slot is not None:
            self._extraction_slot = extraction_slot

    def set_rerank_enabled(self, enabled: bool) -> None:
        self._rerank_enabled = bool(enabled)

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _fact_to_item(
        fact: dict[str, Any], bank: str, platform_bank: str = _SHARED
    ) -> dict[str, Any]:
        """Map a Hindsight RecallResult to the MemoryItem wire shape.

        ``score`` carries the engine's native ``scores.final`` (RecallScores,
        0.8.x+) when present — the comment this replaced ("Hindsight recall
        returns no numeric score") is stale; ``recall()`` only falls back to
        the :8086 reranker when the whole union comes back without one (an
        older Hindsight). ``entities``/``source_fact_ids`` are the per-result
        mention/provenance lists RecallResult already carries — additive keys,
        harmless to every caller that only reads ``id``/``text``/``score``.
        """
        scores = fact.get("scores") or {}
        final_score = scores.get("final")
        return {
            "id": fact.get("document_id") or fact.get("id"),
            # The joins ``_filter_recall_enrichment`` needs: which fact, document
            # and chunk this result is, so response-level chunks follow its ACL.
            "fact_id": fact.get("id"),
            "document_id": fact.get("document_id"),
            "chunk_id": fact.get("chunk_id"),
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or _now(),
            "dataset": _bank_namespace(bank, platform_bank),
            "tags": list(fact.get("tags") or []),
            "source": (fact.get("metadata") or {}).get("source"),
            "metadata": _decode_metadata(fact.get("metadata") or {}),
            "score": float(final_score) if isinstance(final_score, (int, float)) else None,
            "type": fact.get("type"),
            "entities": list(fact.get("entities") or []) or None,
            "source_fact_ids": list(fact.get("source_fact_ids") or []) or None,
            # Provenance (#1794): every ordinary recall result is an
            # extracted fact, as opposed to the raw-document fallback items
            # ``_document_fallback`` appends.
            "kind": "fact",
        }

    # ── raw-document recall fallback (#1794) ─────────────────────────────

    @staticmethod
    def _literal_tokens(query: str) -> list[str]:
        """Literal-looking tokens in ``query`` — see module-level comment."""
        out: list[str] = []
        for tok in _LITERAL_TOKEN_RE.findall(query or ""):
            has_digit = any(c.isdigit() for c in tok)
            has_alpha = any(c.isalpha() for c in tok)
            if has_digit and has_alpha:
                out.append(tok)
        return out

    async def _document_fallback(
        self,
        banks: list[str],
        tokens: list[str],
        *,
        client_id: str | None,
        project_groups: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Bounded raw-document scan for literal tokens fact-extraction lost.

        A document is kept only when the caller could read it from its bank
        and, when the read named a project, it is inside that scope: the
        filter every other recall result goes through (:meth:`_filter_visible`).

        Enumerates up to :data:`_DOC_FALLBACK_MAX_DOCS_PER_BANK` documents
        per bank, fetches each one's full text, and keeps any whose text
        contains a still-uncovered token. Fail-soft throughout: an engine
        that can't answer ``list_documents``/``get_document`` (older build,
        outage) yields no fallback items rather than erroring the recall.
        """
        import asyncio

        async def _bank_docs(bank: str) -> list[tuple[str, dict[str, Any]]]:
            try:
                resp = await self._call(
                    "list_documents", bank_id=bank, limit=_DOC_FALLBACK_MAX_DOCS_PER_BANK, offset=0
                )
            except Exception:
                return []
            items = resp.get("items") if isinstance(resp, dict) else None
            return [(bank, it) for it in (items or []) if isinstance(it, dict) and it.get("id")]

        per_bank = await asyncio.gather(*[_bank_docs(b) for b in banks])
        candidates = [pair for group in per_bank for pair in group]
        if not candidates:
            return []

        async def _fetch(bank: str, meta: dict[str, Any]) -> tuple[str, Any] | None:
            try:
                doc = await self._call("get_document", bank_id=bank, document_id=meta["id"])
            except Exception:
                return None
            return (bank, doc)

        fetched = await asyncio.gather(*[_fetch(b, m) for b, m in candidates])

        remaining = {t.lower() for t in tokens}
        out: list[dict[str, Any]] = []
        for pair in fetched:
            if pair is None or not remaining:
                continue
            bank, doc = pair
            if not isinstance(doc, dict):
                continue
            text = doc.get("original_text") or ""
            if not text:
                continue
            lower_text = text.lower()
            hits = [t for t in remaining if t in lower_text]
            if not hits:
                continue
            if not self._filter_visible([doc], client_id, bank=bank, project_groups=project_groups):
                continue
            out.append(
                {
                    "id": doc.get("id"),
                    "text": text,
                    "timestamp": doc.get("created_at") or _now(),
                    "dataset": self._dataset_of(bank, doc.get("tags")),
                    "tags": list(doc.get("tags") or []),
                    "source": "raw_document",
                    "metadata": {"kind": "document", "document_id": doc.get("id")},
                    "score": None,
                    "type": "document",
                    "kind": "document",
                }
            )
            for hit in hits:
                remaining.discard(hit)
            if len(out) >= _DOC_FALLBACK_MAX_RESULTS:
                break
        return out
