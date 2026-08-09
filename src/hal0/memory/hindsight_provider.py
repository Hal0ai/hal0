"""HindsightProvider — the platform memory engine (brain-redesign P1).

Maps hal0's engine-neutral MemoryProvider contract onto the shared
``hindsight-api`` over REST. Key design points (spec §3, §4b, P1):

* **Bank mapping** lives HERE (not namespace.py, which is unchanged): hal0
  namespace ``private:<agent>`` → Hindsight bank ``private__<agent>`` (``:``:
  ``__``); ``project:<id>`` → ``project__<id>``; ``shared``/``agents`` pass
  through.
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

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from hal0.memory.provider import MemoryProvider

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
# (below) keeps such a doc addressable only by its owning agent.
_VISIBILITY_PRIVATE = "visibility:private"
_AGENT_TAG_PREFIX = "agent:"
# The front-door sentinel for a missing X-hal0-Agent; collapses to ``unknown``
# on both write (agent tag) and read (caller identity) so an absent identity
# can never match a real agent's private docs.
_ANONYMOUS = "anonymous"
_UNKNOWN_AGENT = "unknown"

# Unified-bank project tagging (#1300). The same compensator pattern as
# ``visibility:private``: pre-unified multi-bank mode isolates ``project:<id>`` by
# giving it its own bank, so when unified mode collapses it onto ``shared`` the
# scope has to survive as a tag or it ceases to exist. The tag is written
# verbatim as the namespace name (``project:apollo``) so there is exactly one
# spelling of a project scope across banks, tags, and the wire.
_PROJECT = "project:"

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


def _projects_of(tags: list[str] | tuple[str, ...] | None) -> set[str]:
    """Every ``project:<id>`` tag on a doc. A doc with none is unscoped.

    Plural because a caller may legitimately file one document under more
    than one project by passing extra ``project:`` tags; the read filter
    treats them as an OR (visible from any of its projects).
    """
    return {t for t in tags or () if isinstance(t, str) and t.startswith(_PROJECT)}


def namespace_to_bank(namespace: str) -> str:
    """Map a hal0 namespace to a Hindsight bank id (spec §3 table)."""
    return namespace.replace(":", "__")


def bank_to_namespace(bank: str) -> str:
    """Inverse of :func:`namespace_to_bank` — map a Hindsight bank id back to
    its hal0 namespace (``private__hermes`` -> ``private:hermes``; ``shared``
    stays ``shared``). Only the prefix delimiter is rewritten, so agent ids
    that contain single underscores survive untouched."""
    return bank.replace("__", ":", 1)


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

        payload = {"model": self._model, "query": query, "documents": list(documents)}
        timeout = httpx.Timeout(
            connect=self._connect_timeout_s, read=self._read_timeout_s, write=2.0, pool=None
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._base_url}/v1/rerankings", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception:
            return []
        results = body.get("results") if isinstance(body, dict) else None
        return results if isinstance(results, list) else []


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        extraction_slot: str = "utility",
        unified_bank: bool = False,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._reranker = reranker
        # Unified-bank mode ([memory] unified_bank): collapse every namespace
        # onto the single ``shared`` bank. The X-hal0-Private toggle no longer
        # forks a ``private:<agent>`` bank — writes still land in ``shared`` but
        # are stamped ``visibility:private`` (see ``add``), and recall stops
        # fanning out (``_allowed_namespaces`` returns just ``[shared]``). The
        # constructor default is False so directly-constructed providers keep
        # the pre-unified multi-bank behavior; the live default (True) is carried by
        # config -> ``provider_from_config``.
        self._unified_bank = bool(unified_bank)
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
        self._write_health: dict[str, Any] | None = None
        self._write_health_at: float | None = None

    @property
    def hindsight_client(self) -> Any:
        """REST client handle for the engine admin surface (memory_admin routes)."""
        return self._client

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

        bank = namespace_to_bank(self._write_namespace(_SHARED, None))
        counts = await self._sample_operations(bank)
        if counts is not None:
            previous = self._ops_sample
            self._ops_sample = counts
            if previous is not None and counts["failed"] > previous["failed"]:
                delta = counts["failed"] - previous["failed"]
                self._mark_write_failure(
                    f"{delta} retain operation(s) failed on bank {bank} since the last check",
                    reason="retain_operations_failing",
                )

        degraded = self.write_degraded
        if degraded:
            reason = self._last_write_reason or "retain_failed"
        elif counts is None:
            reason = "unknown"
        else:
            reason = "ok"

        out: dict[str, Any] = {
            "degraded": degraded,
            "reason": reason,
            "last_error": self._last_write_error if degraded else None,
            "operations": dict(counts) if counts is not None else None,
            "bank": bank,
        }
        self._write_health = out
        self._write_health_at = now
        return dict(out)

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
        # Unified mode: one bank. No cross-bank fan-out, no own-private
        # expansion — every read resolves to ``shared`` alone, EXCEPT the
        # ``agents`` registry namespace, which keeps its own bank.
        reqs = self._requested_list(requested)
        if self._unified_bank:
            out: list[str] = []
            for ds in reqs:
                target = _AGENTS if ds == _AGENTS else _SHARED
                if target not in out:
                    out.append(target)
            return out
        cid = client_id or self._client_id
        own = f"{_PRIVATE}{cid}"
        out = []
        for ds in reqs:
            if ds == _SHARED:
                out += [d for d in (_SHARED, own) if d not in out]
            elif ds == own and own not in out:
                out.append(own)
            elif ds.startswith(_PRIVATE):
                continue  # foreign private — dropped (fail-open-empty)
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
        # The REST/MCP front door already resolved the write namespace via
        # namespace.resolve_write_dataset; trust it verbatim here.
        return requested or _SHARED

    # ── ACL: visibility:private read enforcement (unified mode) ─────────

    def _caller_agent(self, client_id: str | None) -> str:
        """Resolve the reading caller's agent identity for private-visibility
        matching. Mirrors the write-path collapse in ``add``: a missing id or
        the ``anonymous`` front-door sentinel becomes ``unknown`` so an
        unauthenticated caller can never match another agent's private docs."""
        cid = client_id or self._client_id or ""
        if not cid or cid == _ANONYMOUS:
            return _UNKNOWN_AGENT
        return cid

    @staticmethod
    def _is_visible_to(item: dict[str, Any], caller_agent: str) -> bool:
        """Read-side enforcement of the ``visibility:private`` tag (PR #1244).

        Non-private docs are shared-readable exactly as before. A private doc
        is returned ONLY to the agent whose id matches the doc's ``agent:<id>``
        tag; docs missing that tag (should not happen — ``add`` always stamps
        one) are treated as private-to-nobody and withheld (fail-closed)."""
        tags = item.get("tags") or []
        if _VISIBILITY_PRIVATE not in tags:
            return True
        owner = _agent_of(tags)
        return owner is not None and owner == caller_agent

    # ── ACL: project:<id> read scoping (unified mode) ───────────────────

    @staticmethod
    def _requested_scope(requested: str | list[str] | None) -> tuple[set[str], bool]:
        """Split a read request into ``(project namespaces, wants unscoped)``.

        ``wants_unscoped`` is True when the caller asked for at least one
        non-project namespace (``shared``, ``agents``, ``private:<id>``) —
        those are the reads that should see docs carrying no project tag.
        An empty request scopes to nothing at all, so neither is True
        (#1451: ``[]`` used to read back as ``[shared]`` here too, which is
        what let the unified-mode delete gate wave shared docs through).

        Both sides of the comparison must be deduplicated (#1668): a caller
        repeating the same project entry (e.g. a client-side retry building
        ``["project:apollo", "project:apollo"]``) must not make ``projects``
        (a set) look smaller than the raw request count and flip
        ``wants_unscoped`` True — that silently admitted unscoped shared docs
        into a call that only ever named one project.
        """
        reqs = HindsightProvider._requested_list(requested)
        projects = {ds for ds in reqs if isinstance(ds, str) and ds.startswith(_PROJECT)}
        non_empty = {r for r in reqs if r}
        return projects, len(projects) < len(non_empty)

    @staticmethod
    def _wants_private_only(requested: str | list[str] | None) -> bool:
        """True when every requested namespace is ``private:<id>`` (#1654).

        ``_is_visible_to`` only enforces OWNERSHIP of private docs — a
        non-private doc always passes it, unconditionally, regardless of
        what was requested. So a read for ``private:<agent>`` alone (no
        ``shared``) currently returns identically to a ``shared`` read: the
        caller's own private docs AND every ordinary public doc in the bank.
        Callers that explicitly ask for the private namespace ONLY (e.g. the
        per-agent memory-stats chip) want private docs alone; this is the
        signal :meth:`list_items` uses to apply that extra filter. A mixed
        request (``["shared", "private:x"]``) is NOT private-only — it wants
        the ordinary union — so this returns ``False`` unless every entry is
        a private namespace.
        """
        reqs = HindsightProvider._requested_list(requested)
        return bool(reqs) and all(isinstance(ds, str) and ds.startswith(_PRIVATE) for ds in reqs)

    @staticmethod
    def _is_in_scope(item: dict[str, Any], projects: set[str], wants_unscoped: bool) -> bool:
        """Read-side enforcement of the ``project:<id>`` tag (#1300).

        A doc carrying project tags is visible only from a read that asked
        for one of those projects; an unscoped doc is visible only from a
        read that asked for a non-project namespace. Together these
        reproduce the bank isolation pre-unified multi-bank mode gets for free.

        NOTE (pre-fix data): documents written to ``project:<id>`` before
        this landed carry no project tag, so they read as unscoped —
        i.e. as the ordinary ``shared`` writes they had silently become.
        That is the only honest reading; there is nothing on the doc that
        records which project it was meant for.
        """
        doc_projects = _projects_of(item.get("tags"))
        if doc_projects:
            return bool(doc_projects & projects)
        return wants_unscoped

    def _filter_visible(
        self,
        items: list[dict[str, Any]],
        client_id: str | None,
        requested: str | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply unified-bank read ACL: private visibility AND project scope.

        The two filters compose — a doc must pass both. Neither shadows the
        other: an agent's private doc filed under a project stays invisible to
        other agents reading that project, and a project doc stays invisible
        to a shared read even though its owner could see it.

        No-op in pre-unified multi-bank mode, where per-bank ACL
        (``_allowed_namespaces`` never returns a foreign private bank, and
        ``project:<id>`` has its own bank) already isolates both and the tags
        are harmless markers."""
        if not self._unified_bank:
            return items
        caller = self._caller_agent(client_id)
        projects, wants_unscoped = self._requested_scope(requested)
        return [
            it
            for it in items
            if self._is_visible_to(it, caller) and self._is_in_scope(it, projects, wants_unscoped)
        ]

    # ── Core five ──────────────────────────────────────────────────────

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
    ) -> dict[str, Any]:
        # ``private:<agent>`` / ``project:<id>`` still arrive from the front
        # door even in unified mode; capture that intent before the bank
        # collapses to shared, so it can survive as a tag.
        requested_private = isinstance(dataset, str) and dataset.startswith(_PRIVATE)
        requested_project = (
            dataset if isinstance(dataset, str) and dataset.startswith(_PROJECT) else None
        )
        ns = self._write_namespace(dataset, client_id)
        bank = namespace_to_bank(ns)

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

        # Server-side tag enforcement: an ``agent:<id>`` tag on every write
        # (unless the caller already supplied one), plus ``visibility:private``
        # when the write came in under the private toggle. Caller tags are
        # preserved.
        out_tags = list(tags or [])
        if not any(str(t).startswith(_AGENT_TAG_PREFIX) for t in out_tags):
            out_tags.append(f"{_AGENT_TAG_PREFIX}{agent}")
        if requested_private and _VISIBILITY_PRIVATE not in out_tags:
            out_tags.append(_VISIBILITY_PRIVATE)
        # #1300: only in unified mode — pre-unified multi-bank mode isolates the
        # project by bank, so tagging there would be a second owner of the
        # same fact (and would change existing deployments' tag sets).
        if self._unified_bank and requested_project and requested_project not in out_tags:
            out_tags.append(requested_project)

        # Extraction quality suffers without a ``context`` line and a
        # timestamp; always supply both unless the caller already did.
        context = meta.pop("context", None) or meta.get("source") or f"{agent} conversation turn"
        timestamp = meta.pop("timestamp", None) or _now()

        # Full RetainRequest item shape (entities/observation_scopes/strategy/
        # update_mode) + the sync/async toggle — forwarded only when the
        # caller actually set them, so a client fake built against the older
        # narrow ``retain(bank_id, content, document_id, context, metadata,
        # tags, timestamp)`` shape (every existing test double) keeps working
        # unmodified; only a caller that opts into the new surface sees it.
        extra: dict[str, Any] = {}
        if entities is not None:
            extra["entities"] = entities
        if observation_scopes is not None:
            extra["observation_scopes"] = observation_scopes
        if strategy is not None:
            extra["strategy"] = strategy
        if update_mode is not None:
            extra["update_mode"] = update_mode
        if sync:
            extra["sync"] = True

        try:
            resp = await self._call(
                "retain",
                bank_id=bank,
                content=text,
                document_id=resolved_id,
                context=context,
                metadata={k: str(v) for k, v in meta.items()},
                tags=out_tags,
                timestamp=timestamp,
                **extra,
            )
        except Exception as exc:
            # #1420: a raised retain is write-path evidence, distinct from the
            # daemon-reachability signal ``_call`` already updated (a 4xx means
            # the daemon is fine AND the write still didn't land).
            self._mark_write_failure(str(exc), reason="retain_failed")
            raise
        document_id = resolved_id
        out: dict[str, Any] = {"id": document_id, "timestamp": _now()}
        resp_dict = resp if isinstance(resp, dict) else {}
        # retain is async by default on this engine — surface the operation
        # id(s) so callers (dashboard ingestion indicator, CLI, memory_operation_*
        # MCP tools) can poll instead of wondering why list doesn't show the
        # item yet. ``items_count``/``operation_ids`` used to be dropped on the
        # floor entirely — surface them whenever the engine reports them so a
        # caller doing per-item accounting doesn't have to guess.
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
    ) -> dict[str, Any]:
        """Page through a bank, honouring ``cursor`` (#1471).

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
        """
        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
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

        items: list[dict[str, Any]] = []
        next_cursor: str | None = None
        # Walk banks from the cursor's bank onward, carrying its offset into
        # the first one only — later banks start from the top.
        for bank in banks[banks.index(start_bank) :]:
            bank_offset = offset if bank == start_bank else 0
            while len(items) < limit:
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
                bank_offset += len(raw)
                # Same read ACL as recall: in unified mode the shared bank holds
                # every agent's private docs AND every project's docs, so list
                # must not surface other agents' private items or out-of-scope
                # project items. No-op in pre-unified multi-bank mode. Applied
                # BEFORE the page cut so the page fills with visible rows.
                page = self._filter_visible(
                    [self._list_fact_to_item(fact, bank) for fact in raw], client_id, dataset
                )
                if private_only:
                    page = [it for it in page if _VISIBILITY_PRIVATE in (it.get("tags") or [])]
                items.extend(page)
                total = resp.get("total")
                exhausted = len(raw) < fetch if not isinstance(total, int) else bank_offset >= total
                if exhausted:
                    break
            if len(items) >= limit:
                # More may remain in THIS bank — resume here next call. The
                # offset counts raw rows consumed, not visible ones, so the
                # walk stays stable regardless of what the ACL filtered.
                consumed = bank_offset - max(0, len(items) - limit)
                next_cursor = _encode_cursor(bank, consumed)
                break
        return {"items": items[:limit], "next_cursor": next_cursor}

    @staticmethod
    def _list_fact_to_item(fact: dict[str, Any], bank: str) -> dict[str, Any]:
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
            "dataset": bank.replace("__", ":"),
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
        requested: str | list[str] | None = None,
    ) -> dict[str, str]:
        """Map each of ``ids`` the caller is allowed to delete to the
        **document_id** the engine deletes by, under unified-bank visibility.

        The return type is a mapping, not a set, because the caller-supplied
        id and the engine-addressable id are not always the same string
        (#1456). ``id`` on every read surface is now the document_id, but a
        caller may still hold a per-fact id — from a pre-fix ``list``
        response or from ``metadata.fact_id`` — and the resolution scan below
        already has both fields in hand, so accepting either costs nothing.
        The engine is addressed by document_id in every case; matching on the
        fact-id field is what made a *correct* document_id fail-closed
        withheld and left ``POST /api/memory/delete`` deleting nothing.

        In unified mode the single ``shared`` bank holds every agent's
        ``visibility:private`` docs and every project's docs, so a blind
        delete-by-id would let any caller remove another agent's private memory
        — or reach out of the project scope they addressed — by guessing an id.
        Read/search/list all enforce visibility and scope; delete must too. A
        doc is deletable when the caller could have read it in this same call:
        the exact predicates the read paths use (:meth:`_is_visible_to` and
        :meth:`_is_in_scope`). An id we cannot resolve to a doc in the caller's
        banks is **withheld** (fail-closed), matching the read filter's posture.
        """
        if not ids:
            return {}
        caller = self._caller_agent(client_id)
        projects, wants_unscoped = self._requested_scope(requested)
        wanted = set(ids)
        out: dict[str, str] = {}
        for bank in banks:
            if not wanted - out.keys():
                break
            offset = 0
            while wanted - out.keys():
                try:
                    resp = await self._call(
                        "list_memories", bank_id=bank, limit=_DELETE_SCAN_PAGE, offset=offset
                    )
                except Exception:
                    break  # fail-soft per bank; unresolved ids stay withheld
                items = resp.get("items", [])
                for fact in items:
                    document_id = fact.get("document_id") or fact.get("id")
                    # Either handle resolves to the same document. Ordered so
                    # the canonical id wins when a caller passes both.
                    matches = (
                        wanted.intersection({i for i in (document_id, fact.get("id")) if i})
                        - out.keys()
                    )
                    if not matches:
                        continue
                    if self._is_visible_to(fact, caller) and self._is_in_scope(
                        fact, projects, wants_unscoped
                    ):
                        for given in matches:
                            out[given] = document_id
                if len(items) < _DELETE_SCAN_PAGE:
                    break  # last page
                offset += _DELETE_SCAN_PAGE
        return out

    async def delete(
        self,
        ids: list[str],
        *,
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, int]:
        deleted = 0
        # We don't know which bank each document_id lives in without a
        # lookup; try the caller's allowed banks. Hindsight 404s a missing
        # document (NOT idempotent-200), so a per-bank probe that misses
        # must continue the sweep — previously the first 404 aborted the
        # whole call, which made every private-bank item undeletable
        # (the shared bank is probed first and always 404'd).
        # ``dataset`` is passed through verbatim: an empty list is an
        # unaddressable scope, NOT an unset one, and must sweep no banks
        # (#1451 — ``dataset or _SHARED`` here is what turned an approved
        # foreign-bank delete into a shared-bank delete).
        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        if not banks:
            return {"deleted": 0}
        # Unified mode: gate deletes on the same visibility + project ACL the
        # read paths enforce, so one agent can't delete another's private doc
        # by id, and a project-scoped delete can't reach outside its project.
        # Legacy multi-bank mode needs no gate — _allowed_namespaces never
        # yields a foreign private bank and each project has its own bank, so
        # bank isolation already protects both.
        deletable = (
            await self._deletable_ids(set(ids), banks, client_id, dataset)
            if self._unified_bank
            else None
        )
        for given_id in ids:
            if deletable is None:
                document_id = given_id
            elif given_id in deletable:
                document_id = deletable[given_id]
            else:
                continue  # withheld: not visible to this caller (fail-closed)
            for bank in banks:
                try:
                    res = await self._call("delete_document", bank_id=bank, document_id=document_id)
                except Exception as exc:
                    if _http_status(exc) == 404:
                        continue  # not in this bank — keep sweeping
                    raise
                if int(res.get("memory_units_deleted", 0)) > 0:
                    deleted += 1
                    break
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
    ) -> RecallResults:
        """Fan out per-bank recall to the caller's allowed banks, merge under
        one token budget.

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

        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        if not banks:
            return RecallResults([])
        effective_types = list(types) if types else list(_DEFAULT_RECALL_TYPES)

        async def _one(bank: str) -> tuple[list[dict[str, Any]], dict, dict, dict]:
            kwargs: dict[str, Any] = {
                "bank_id": bank,
                "query": query,
                "types": effective_types,
                "max_tokens": max_tokens,
                "tags": tags,
            }
            # Only forward the extended knobs when the caller set them, so
            # clients/fakes that don't know the param stay compatible.
            if tags_match is not None:
                kwargs["tags_match"] = tags_match
            if tag_groups is not None:
                kwargs["tag_groups"] = tag_groups
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
            items = [self._fact_to_item(f, bank) for f in resp.get("results", [])]
            return (
                items,
                resp.get("entities") or {},
                resp.get("chunks") or {},
                resp.get("source_facts") or {},
            )

        per_bank = await asyncio.gather(*[_one(b) for b in banks])
        union: list[dict[str, Any]] = []
        entities_merged: dict[str, Any] = {}
        chunks_merged: dict[str, Any] = {}
        source_facts_merged: dict[str, Any] = {}
        for items, ents, chks, sfacts in per_bank:
            union.extend(items)
            entities_merged.update(ents)
            chunks_merged.update(chks)
            source_facts_merged.update(sfacts)

        # Enforce visibility:private + project scope BEFORE the rerank + token
        # budget so that docs the caller may not see never consume the caller's
        # budget (and never leak). No-op in pre-unified multi-bank mode. Done
        # pre-budget so the returned count reflects only visible docs.
        union = self._filter_visible(union, client_id, dataset)
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
        return RecallResults(
            budgeted,
            entities=entities_merged or None,
            chunks=chunks_merged or None,
            source_facts=source_facts_merged or None,
        )

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
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
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

    # ── single-memory curation (non-destructive "this is wrong" path) ───

    async def _ensure_memory_visible(
        self, bank: str, memory_id: str, client_id: str | None, dataset: str | list[str] | None
    ) -> None:
        """Unified-bank guard: fetch the memory unit and check it against the
        same ``visibility:private``/``project:<id>`` predicates every read
        path enforces, so a caller cannot curate or inspect another agent's
        private memory (or reach outside a project scope) just by guessing
        its id. No-op in pre-unified multi-bank mode — bank isolation already
        does the job there, matching :meth:`delete`'s ``_unified_bank`` gate.

        Hindsight's single-memory GET response shape isn't in the published
        OpenAPI schema (an empty ``{}`` response model) — this assumes it
        carries ``tags`` the same way list/recall items do. A memory with no
        (or unrecognised) ``tags`` field reads as unscoped/shared, the same
        fail-open default an untagged doc gets everywhere else on this ACL.
        """
        if not self._unified_bank:
            return
        fact = await self._call("get_memory", bank_id=bank, memory_id=memory_id)
        if not isinstance(fact, dict):
            fact = {}
        caller = self._caller_agent(client_id)
        projects, wants_unscoped = self._requested_scope(dataset)
        if not (
            self._is_visible_to(fact, caller) and self._is_in_scope(fact, projects, wants_unscoped)
        ):
            raise PermissionError(f"memory {memory_id!r} is not visible to this caller")

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        await self._ensure_memory_visible(bank, memory_id, client_id, dataset)
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
        """Revision history for one memory unit (curation audit trail)."""
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        await self._ensure_memory_visible(bank, memory_id, client_id, dataset)
        return await self._call("memory_history", bank_id=bank, memory_id=memory_id)

    # ── mental models ─────────────────────────────────────────────────────
    #
    # Bank-scoped configuration objects, not per-document facts — they carry
    # no ``visibility:private``/``project:<id>`` tag of their own, so (as with
    # ``reflect``) the only ACL enforcement is picking the right single bank.
    # In unified mode every agent's mental models live in the one ``shared``
    # bank and are mutually visible; this mirrors the existing REST admin
    # surface (``memory_admin.py``), which applies no additional ACL here
    # either.

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "list_mental_models",
            bank_id=bank,
            tags=tags,
            tags_match=tags_match,
            detail=detail,
            limit=limit,
            offset=offset,
        )

    async def get_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call("get_mental_model", bank_id=bank, mental_model_id=mental_model_id)

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "update_mental_model",
            bank_id=bank,
            mental_model_id=mental_model_id,
            name=name,
            source_query=source_query,
            max_tokens=max_tokens,
            tags=tags,
            trigger=trigger,
        )

    async def delete_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "delete_mental_model", bank_id=bank, mental_model_id=mental_model_id
        )

    async def refresh_mental_model(
        self, mental_model_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "refresh_mental_model", bank_id=bank, mental_model_id=mental_model_id
        )

    # ── directives ───────────────────────────────────────────────────────
    #
    # Same bank-scoped-configuration posture as mental models (see above).

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "list_directives",
            bank_id=bank,
            tags=tags,
            tags_match=tags_match,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    async def get_directive(
        self, directive_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call("get_directive", bank_id=bank, directive_id=directive_id)

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "create_directive",
            bank_id=bank,
            name=name,
            content=content,
            priority=priority,
            is_active=is_active,
            tags=tags,
        )

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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "update_directive",
            bank_id=bank,
            directive_id=directive_id,
            name=name,
            content=content,
            priority=priority,
            is_active=is_active,
            tags=tags,
        )

    async def delete_directive(
        self, directive_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "list_operations",
            bank_id=bank,
            status=status,
            type=type,
            limit=limit,
            offset=offset,
            exclude_parents=exclude_parents,
        )

    async def get_operation(
        self,
        operation_id: str,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        include_payload: bool | None = None,
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "get_operation",
            bank_id=bank,
            operation_id=operation_id,
            include_payload=include_payload,
        )

    async def cancel_operation(
        self, operation_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call("cancel_operation", bank_id=bank, operation_id=operation_id)

    async def retry_operation(
        self, operation_id: str, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
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
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call(
            "list_tags", bank_id=bank, q=q, source=source, limit=limit, offset=offset
        )

    async def bank_stats(
        self,
        *,
        dataset: str = _SHARED,
        client_id: str | None = None,
        refresh: bool | None = None,
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call("bank_stats", bank_id=bank, refresh=refresh)

    async def consolidate(  # type: ignore[override]  — real implementation, ABC default is a stub
        self, *, dataset: str = _SHARED, client_id: str | None = None
    ) -> dict[str, Any]:
        bank = namespace_to_bank(self._write_namespace(dataset, client_id))
        return await self._call("consolidate", bank_id=bank)

    # ── Runtime toggles ────────────────────────────────────────────────

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self._graph_enabled,
            "extraction_slot": self._extraction_slot,
            "route": self._extraction_slot,  # deprecated mirror (ADR-0023)
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
    def _fact_to_item(fact: dict[str, Any], bank: str) -> dict[str, Any]:
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
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or _now(),
            "dataset": bank.replace("__", ":"),
            "tags": list(fact.get("tags") or []),
            "source": (fact.get("metadata") or {}).get("source"),
            "metadata": dict(fact.get("metadata") or {}),
            "score": float(final_score) if isinstance(final_score, (int, float)) else None,
            "type": fact.get("type"),
            "entities": list(fact.get("entities") or []) or None,
            "source_fact_ids": list(fact.get("source_fact_ids") or []) or None,
        }
