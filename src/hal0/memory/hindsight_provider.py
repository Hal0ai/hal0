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
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hal0.memory.provider import MemoryProvider

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
# ``visibility:private``: legacy multi-bank mode isolates ``project:<id>`` by
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
        # the legacy multi-bank behavior; the live default (True) is carried by
        # config -> ``provider_from_config``.
        self._unified_bank = bool(unified_bank)
        # ADR-0023: reporting-only on this engine (Hindsight builds its graph
        # natively via its own extraction LLM, which hal0 points at the
        # `extraction_slot` via the hindsight-api systemd drop-in). Seeded from
        # [memory.graph] so graph_status() agrees with hal0.toml.
        self._graph_enabled = bool(graph_enabled)
        self._extraction_slot = extraction_slot
        self._rerank_enabled = reranker is not None

    @property
    def hindsight_client(self) -> Any:
        """REST client handle for the engine admin surface (memory_admin routes)."""
        return self._client

    # ── ACL: the caller's allowed namespaces → banks ───────────────────

    def _allowed_namespaces(self, requested: str | list[str], client_id: str | None) -> list[str]:
        # Unified mode: one bank. No cross-bank fan-out, no own-private
        # expansion — every read resolves to ``shared`` alone, EXCEPT the
        # ``agents`` registry namespace, which keeps its own bank.
        if self._unified_bank:
            reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
            out: list[str] = []
            for ds in reqs:
                target = _AGENTS if ds == _AGENTS else _SHARED
                if target not in out:
                    out.append(target)
            return out or [_SHARED]
        cid = client_id or self._client_id
        own = f"{_PRIVATE}{cid}"
        reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
        out: list[str] = []
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
        """
        reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
        projects = {ds for ds in reqs if isinstance(ds, str) and ds.startswith(_PROJECT)}
        return projects, len(projects) < len([r for r in reqs if r])

    @staticmethod
    def _is_in_scope(item: dict[str, Any], projects: set[str], wants_unscoped: bool) -> bool:
        """Read-side enforcement of the ``project:<id>`` tag (#1300).

        A doc carrying project tags is visible only from a read that asked
        for one of those projects; an unscoped doc is visible only from a
        read that asked for a non-project namespace. Together these
        reproduce the bank isolation legacy multi-bank mode gets for free.

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

        No-op in legacy multi-bank mode, where per-bank ACL
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
    ) -> dict[str, str]:
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
        # #1300: only in unified mode — legacy multi-bank mode isolates the
        # project by bank, so tagging there would be a second owner of the
        # same fact (and would change existing deployments' tag sets).
        if self._unified_bank and requested_project and requested_project not in out_tags:
            out_tags.append(requested_project)

        # Extraction quality suffers without a ``context`` line and a
        # timestamp; always supply both unless the caller already did.
        context = meta.pop("context", None) or meta.get("source") or f"{agent} conversation turn"
        timestamp = meta.pop("timestamp", None) or _now()

        resp = await self._client.retain(
            bank_id=bank,
            content=text,
            document_id=resolved_id,
            context=context,
            metadata={k: str(v) for k, v in meta.items()},
            tags=out_tags,
            timestamp=timestamp,
        )
        document_id = resolved_id
        out = {"id": document_id, "timestamp": _now()}
        # retain is async on this engine — surface the operation id so
        # callers (dashboard ingestion indicator, CLI) can poll instead of
        # wondering why list doesn't show the item yet.
        operation_id = (resp or {}).get("operation_id") if isinstance(resp, dict) else None
        if operation_id:
            out["operation_id"] = str(operation_id)
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
    ) -> list[dict[str, Any]]:
        # search delegates to recall (back-compat surface); the fan-out lives
        # in recall (Task P1-3). limit is honored after the merge.
        out = await self.recall(
            query=query,
            max_tokens=max(256, limit * 256),
            dataset=dataset,
            tags=tags,
            client_id=client_id,
        )
        return out[:limit]

    async def list_items(
        self,
        dataset: str = _SHARED,
        cursor: str | None = None,
        limit: int = 50,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        items: list[dict[str, Any]] = []
        for bank in banks:
            if len(items) >= limit:
                break
            try:
                resp = await self._client.list_memories(bank_id=bank, limit=limit, offset=0)
            except Exception:
                continue  # fail-soft per bank
            for fact in resp.get("items", []):
                items.append(self._list_fact_to_item(fact, bank))
        # Same read ACL as recall: in unified mode the shared bank holds every
        # agent's private docs AND every project's docs, so list must not
        # surface other agents' private items or out-of-scope project items.
        # No-op in legacy multi-bank mode.
        items = self._filter_visible(items, client_id, dataset)
        return {"items": items[:limit], "next_cursor": None}

    @staticmethod
    def _list_fact_to_item(fact: dict[str, Any], bank: str) -> dict[str, Any]:
        """Map a Hindsight /memories/list item to the MemoryItem wire shape."""
        return {
            "id": fact.get("id") or fact.get("document_id"),
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or fact.get("date") or _now(),
            "dataset": bank.replace("__", ":"),
            "tags": list(fact.get("tags") or []),
            "source": None,
            "metadata": {},
            "score": None,
            "type": fact.get("fact_type"),
        }

    async def _deletable_ids(
        self,
        ids: set[str],
        banks: list[str],
        client_id: str | None,
        requested: str | list[str] | None = None,
    ) -> set[str]:
        """Return the subset of ``ids`` the caller is allowed to delete under
        unified-bank visibility.

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
            return set()
        caller = self._caller_agent(client_id)
        projects, wants_unscoped = self._requested_scope(requested)
        wanted = set(ids)
        out: set[str] = set()
        for bank in banks:
            if not wanted - out:
                break
            offset = 0
            while wanted - out:
                try:
                    resp = await self._client.list_memories(
                        bank_id=bank, limit=_DELETE_SCAN_PAGE, offset=offset
                    )
                except Exception:
                    break  # fail-soft per bank; unresolved ids stay withheld
                items = resp.get("items", [])
                for fact in items:
                    fid = fact.get("id") or fact.get("document_id")
                    if fid not in wanted or fid in out:
                        continue
                    if self._is_visible_to(fact, caller) and self._is_in_scope(
                        fact, projects, wants_unscoped
                    ):
                        out.add(fid)
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
        banks = [
            namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset or _SHARED, client_id)
        ]
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
        for document_id in ids:
            if deletable is not None and document_id not in deletable:
                continue  # withheld: not visible to this caller (fail-closed)
            for bank in banks:
                try:
                    res = await self._client.delete_document(bank_id=bank, document_id=document_id)
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
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fan out per-bank recall to the caller's allowed banks, merge under
        one token budget. Hindsight has no server-side cross-bank query and
        returns no numeric score, so we re-rank the union via the :8086
        reranker, with the §4b precedence ladder as the tiebreak.

        In unified-bank mode ``_allowed_namespaces`` collapses to a single
        ``shared`` bank, so this is a one-bank recall (the rerank + budget still
        apply). ``tags_match`` (``any``/``all``) is a passthrough to Hindsight's
        tag filter — only forwarded when set.
        """
        import asyncio

        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        if not banks:
            return []
        effective_types = list(types) if types else list(_DEFAULT_RECALL_TYPES)

        async def _one(bank: str) -> list[dict[str, Any]]:
            kwargs: dict[str, Any] = {
                "bank_id": bank,
                "query": query,
                "types": effective_types,
                "max_tokens": max_tokens,
                "tags": tags,
            }
            # Only forward tags_match when the caller set it, so clients/fakes
            # that don't know the param stay compatible.
            if tags_match is not None:
                kwargs["tags_match"] = tags_match
            resp = await self._client.recall(**kwargs)
            return [self._fact_to_item(f, bank) for f in resp.get("results", [])]

        per_bank = await asyncio.gather(*[_one(b) for b in banks])
        union: list[dict[str, Any]] = [item for bank_items in per_bank for item in bank_items]
        # Enforce visibility:private + project scope BEFORE the rerank + token
        # budget so that docs the caller may not see never consume the caller's
        # budget (and never leak). No-op in legacy multi-bank mode. Done
        # pre-budget so the returned count reflects only visible docs.
        union = self._filter_visible(union, client_id, dataset)
        if not union:
            return []

        union = await self._rerank_union(query, union)
        union.sort(key=self._precedence_key)  # stable: precedence wins ties
        return self._apply_token_budget(union, max_tokens)

    @staticmethod
    def _precedence_key(item: dict[str, Any]) -> tuple[int, float]:
        """§4b ladder: shared/curated observations rank above raw private
        facts. Lower tuple sorts first. Second element is negative rerank
        score so higher score sorts earlier within the same tier.
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

        ``score`` is always None — Hindsight recall returns no numeric score;
        ordering carries the relevance signal.
        """
        return {
            "id": fact.get("document_id") or fact.get("id"),
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or _now(),
            "dataset": bank.replace("__", ":"),
            "tags": list(fact.get("tags") or []),
            "source": (fact.get("metadata") or {}).get("source"),
            "metadata": dict(fact.get("metadata") or {}),
            "score": None,
            "type": fact.get("type"),
        }
