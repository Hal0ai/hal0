"""Cognee wrapper — public contract for hal0's memory engine.

Implements the four operations specified in
``docs/internal/adr/0005-memory-engine-cognee.md`` §2 — ``add``,
``search``, ``list_items``, ``delete`` — as a thin async layer over
``cognee``'s Python API.

The wrapper exists because:

  - Cognee's public surface evolves; pinning hal0 to it directly would
    leak version churn into ``/mcp/memory`` callers (see ADR-0005
    §"Consequences").
  - ADR-0005 §3's namespace rule (``shared`` + optional
    ``private:<client_id>``) is enforced **here**, not in Cognee. v0.2
    is single-user box, so we don't stand up Cognee's multi-user RBAC;
    we filter results post-hoc.
  - ADR-0005 §6 disables graph extraction + Memify for v0.2. The full
    ``cognee.cognify`` pipeline assumes a structured-output-reliable
    LLM. We run a stripped pipeline (classify → chunk → embed) so the
    LLM_API_KEY is only consulted by the future Phase 9 graph builds.
  - ADR-0005 §5 mandates an audit log keyed on the Bearer-extracted
    ``client_id``. The wrapper emits ``hal0.memory.audit`` structlog
    events for every op; ``client_id`` injection happens here.

A small SQLite sidecar at ``<cognee_dir>/hal0_memory_index.sqlite``
mirrors the rich-schema fields (dataset, tags, source, metadata,
timestamp) so we can apply the ADR-0005 §2 search filters that Cognee
1.0's vector retriever does not natively respect (dataset isolation,
tag AND-match, date range). Phase 9 will revisit this when Cognee's
``ENABLE_BACKEND_ACCESS_CONTROL`` graduates from "global on/off" to
"per-dataset RBAC" (see ADR-0006 pending).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

# Cognee bootstrap. Import is deferred to first-use because importing
# `cognee` at module-import time runs the package's logging side-effect
# (it writes to ~/.cognee/logs/...). Keeping the import lazy means hal0
# tests that never touch memory don't spawn that log file.
_COGNEE: Any = None
_COGNEE_LOCK = threading.Lock()


def _cognee() -> Any:
    """Lazy-import + cache the cognee module.

    Cognee writes a log file on first import; deferring keeps the
    side-effect out of the hal0 import graph for callers (tests,
    one-shot CLI commands) that don't touch memory.
    """
    global _COGNEE
    if _COGNEE is None:
        with _COGNEE_LOCK:
            if _COGNEE is None:
                import cognee

                _COGNEE = cognee
    return _COGNEE


def _audit_logger():
    """Return a structlog logger bound to this module's name.

    Called fresh on every audit emit so tests that reconfigure
    structlog's processor chain (see ``tests/memory/conftest.py``'s
    ``captured_audit_events`` fixture) actually intercept the events.
    ``structlog.get_logger`` caches the processor chain at call time,
    so a module-level cache would freeze the chain to whatever was
    configured at hal0 import — which precedes pytest's setup.
    """
    return structlog.get_logger(__name__)


# ── Defaults pulled from ADR-0005 §1 + §3 ──────────────────────────────────

# ADR-0005 §1: SQLite + LanceDB + Kuzu, all embedded, file-based.
_DEFAULT_VECTOR_PROVIDER = "lancedb"
_DEFAULT_GRAPH_PROVIDER = "kuzu"

# ADR-0005 §3: default namespace is `shared`. The constructor's
# `private_mode` flag rewrites this to `private:<client_id>`.
SHARED_DATASET = "shared"
PRIVATE_PREFIX = "private:"

# Default install path per the CLAUDE.md system layout. Tests override
# this via the conftest fixture (`tmp_path`).
DEFAULT_COGNEE_DIR = Path("/var/lib/hal0/memory/cognee")

# Audit event name — must match the structlog test fixtures that capture
# events for the §5 audit-log assertions.
AUDIT_EVENT = "hal0.memory.audit"


# ── Public payload shape ──────────────────────────────────────────────────


@dataclass
class MemoryRecord:
    """The wire shape returned by ``search`` and ``list_items``.

    Matches the field set listed in ADR-0005 §2 (``memory_search``
    returns ``list of {id, text, score, timestamp, dataset, tags,
    source, metadata}``). ``score`` is ``None`` for list_items because
    no query is involved.
    """

    id: str
    text: str
    timestamp: str  # ISO-8601 UTC
    dataset: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "timestamp": self.timestamp,
            "dataset": self.dataset,
            "tags": list(self.tags),
            "source": self.source,
            "metadata": dict(self.metadata),
            "score": self.score,
        }


# ── Wrapper ───────────────────────────────────────────────────────────────


class CogneeWrapper:
    """Async wrapper around Cognee — see module docstring.

    One instance per (Cognee dir, client_id) is the intended usage —
    the orchestrator (sibling worktree, not ours) constructs one per
    request after extracting ``client_id`` from the Bearer token.

    The wrapper is **not** thread-safe across concurrent calls on the
    same instance. Cognee's own internals serialize on a per-dataset
    SQLAlchemy session, and our sidecar uses a per-call connection
    (sqlite3 module is thread-safe for that pattern), but a single
    event loop calling ``add`` then ``search`` concurrently is allowed
    — asyncio gather() patterns work.
    """

    def __init__(
        self,
        cognee_dir: str | Path = DEFAULT_COGNEE_DIR,
        client_id: str = "anonymous",
        *,
        private_mode: bool = False,
        embedding_provider: str = "fastembed",
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        embedding_dimensions: int = 384,
        graph_enabled: bool = False,
        graph_route: str = "upstream",
    ) -> None:
        """Configure Cognee + the sidecar SQLite index.

        :param cognee_dir: Where Cognee stores its SQLite + LanceDB +
            Kuzu files. Created if missing.
        :param client_id: Identity extracted from the Bearer token by
            the caller (see ``hal0.api.middleware.auth.AuthIdentity``).
            Stamped onto every audit-log event and into the ``source``
            field of every add.
        :param private_mode: When True, the wrapper writes to
            ``private:<client_id>`` instead of ``shared``. Reads always
            see ``shared`` PLUS the caller's own private namespace —
            never another client's private namespace. ADR-0005 §3.
        :param embedding_provider/model/dimensions: Match the ADR-0005
            §1 default (fastembed + bge-small-en-v1.5, 384-dim). Tests
            override these to keep the embedding model tiny.
        :param graph_enabled: ADR-0014 §1 — when True, ``add`` enqueues a
            background ``cognee.cognify`` pass after the foreground add
            returns (entity + relation extraction for ``search(mode=
            "graph"|"hybrid")``). Default False per ADR-0014: structured-
            output reliability on small local models is too low for a
            silent default. The vector path is unaffected.
        :param graph_route: ADR-0014 §2 — "upstream" | "primary" |
            "agent". Stored for dashboard/CLI introspection (``status``);
            the LLM-routing implementation itself lands in v0.4 with the
            eval suite. v0.3 ships the gate + the introspection surface.
        """
        self._cognee_dir = Path(cognee_dir)
        self._cognee_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = self._cognee_dir / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Cognee's relational config defaults DB_PATH to
        # ``<system_root>/databases`` and SQLAlchemy errors out if the
        # directory doesn't exist BEFORE the first connect (sqlite
        # can't create the parent dir for you). Pre-create here so the
        # first ``cognee.add`` lands cleanly.
        (self._cognee_dir / "databases").mkdir(parents=True, exist_ok=True)

        self._client_id = client_id
        self._private_mode = private_mode
        # Pre-compute the effective write-dataset so ``add`` is one branch
        # of a conditional, not a string-concat at every call.
        self._write_dataset = f"{PRIVATE_PREFIX}{client_id}" if private_mode else SHARED_DATASET
        # Read-dataset list per ADR-0005 §3 — always `shared` plus this
        # client's own private namespace (if any items live there).
        self._read_datasets: list[str] = [SHARED_DATASET, f"{PRIVATE_PREFIX}{client_id}"]

        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

        # ADR-0014 §1+§2 — graph extraction toggle + route, mutable so
        # the dashboard/CLI flip path can update them without rebuilding
        # Cognee (which would re-init embeddings + LanceDB).
        self._graph_enabled = bool(graph_enabled)
        self._graph_route = str(graph_route or "upstream")
        # ADR-0014 §6 — per-build error counter; the dashboard renders
        # this as a chip beside the toggle. Bounded so the chip stays
        # one line.
        self._graph_build_errors = 0
        self._graph_builds_ok = 0
        self._graph_last_built_at: str | None = None
        self._graph_last_error: str | None = None
        # In-flight graph build tasks. Tracked so ``set_graph_enabled
        # (False)`` (ADR-0014 §6 cancel-in-flight) can cooperatively
        # cancel without leaving zombie tasks attached to the loop.
        self._graph_tasks: set[asyncio.Task[Any]] = set()

        # Tail buffer of audit events emitted by this instance. The
        # structlog channel is the production audit-log surface
        # (journald via the hal0 service's structlog config); this
        # in-memory mirror exists so tests can inspect events without
        # racing against Cognee's own ``structlog.configure`` calls.
        # Capped at 1024 entries to keep the API server's memory
        # footprint bounded — production callers rely on journald
        # for retention, not this buffer.
        self.audit_tail: list[dict] = []
        self._audit_tail_max = 1024

        # Sidecar SQLite index. Lives next to Cognee's own files so
        # ``$HAL0_HOME`` snapshots cover both. The schema mirrors the
        # rich-schema fields ADR-0005 §2 promises — Cognee's chunk
        # payload does NOT carry dataset/source/metadata at search time
        # in 1.0.x, so we shadow them here.
        self._sidecar = self._cognee_dir / "hal0_memory_index.sqlite"
        self._init_sidecar()

        # Push Cognee env + config exactly once per instance. Cognee
        # reads these as module-level singletons, so the last writer
        # wins — multiple wrapper instances pointed at the SAME dir
        # will fight over embedding provider config. v0.2 ships one
        # singleton wrapper per process; Phase 9 may revisit.
        self._configure_cognee()

    # ── Sidecar SQLite schema ──────────────────────────────────────────

    def _init_sidecar(self) -> None:
        """Create the sidecar table if missing.

        Schema fields are 1:1 with ADR-0005 §2's memory_search return:
        ``id, text, timestamp, dataset, tags, source, metadata``. Tags
        and metadata are JSON columns so we don't fight SQLite typing.
        ``cognee_data_id`` + ``cognee_dataset_id`` link back to
        Cognee's own IDs for delete + future reconciliation.
        """
        with sqlite3.connect(self._sidecar) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hal0_memory_items (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    cognee_data_id TEXT,
                    cognee_dataset_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_hal0_memory_dataset
                    ON hal0_memory_items(dataset);
                CREATE INDEX IF NOT EXISTS idx_hal0_memory_timestamp
                    ON hal0_memory_items(timestamp);
                """
            )

    def _sidecar_conn(self) -> sqlite3.Connection:
        """Return a per-call sqlite3 connection.

        Per-call avoids the "single connection across threads" footgun.
        sqlite3 will serialise writes via its own lock; under v0.2 load
        (interactive memory adds, not a firehose) this is fine.
        """
        conn = sqlite3.connect(self._sidecar)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Cognee bootstrap ───────────────────────────────────────────────

    def _configure_cognee(self) -> None:
        """Push hal0's Cognee config: dirs, providers, env vars.

        Done once at construction. Tests rely on the conftest fixture
        to give each test its own ``cognee_dir`` so concurrent test
        runs don't clobber each other's Cognee state.

        ``ENABLE_BACKEND_ACCESS_CONTROL`` is held OFF because ADR-0005
        §4 defers multi-user RBAC to Phase 9 — the wrapper enforces
        dataset isolation post-hoc on the sidecar instead.

        ``COGNEE_SKIP_CONNECTION_TEST`` shuts up Cognee's startup probe
        to the LLM endpoint. v0.2 doesn't run cognify so no LLM call
        ever fires; the probe would 401 unhelpfully.
        """
        # Cognee env vars are read by the embedding/LLM config at
        # the moment the engine is requested — set before the first
        # cognee.add() call.
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
        os.environ.setdefault("CACHING", "false")
        os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")
        os.environ.setdefault("LLM_API_KEY", "sk-hal0-noop-v0.2-no-cognify")
        os.environ["EMBEDDING_PROVIDER"] = self._embedding_provider
        os.environ["EMBEDDING_MODEL"] = self._embedding_model
        os.environ["EMBEDDING_DIMENSIONS"] = str(self._embedding_dimensions)
        os.environ["HUGGINGFACE_TOKENIZER"] = self._embedding_model
        # Cognee's relational + graph configs derive their db paths
        # from ``system_root_directory`` at first read, then memoize
        # via ``@lru_cache``. Setting DB_PATH explicitly here side-steps
        # that derivation so the first wrapper in a long-lived process
        # AND every subsequent wrapper in a different cognee_dir agree
        # on where SQLite + Kuzu live.
        databases_path = str(self._cognee_dir / "databases")
        os.environ["DB_PATH"] = databases_path
        os.environ["GRAPH_DATABASE_URL"] = str(self._cognee_dir / "databases" / "cognee.kuzu")

        cognee = _cognee()
        cognee.config.system_root_directory(str(self._cognee_dir))
        cognee.config.data_root_directory(str(self._data_dir))
        # Invalidate the lru_cache'd config + engine singletons so the
        # new path actually takes effect. A second wrapper in the same
        # process (different cognee_dir) would otherwise inherit the
        # first's directory because Cognee memoises configs + engines.
        _clear_cognee_caches()
        cognee.config.set_vector_db_provider(_DEFAULT_VECTOR_PROVIDER)
        cognee.config.set_graph_database_provider(_DEFAULT_GRAPH_PROVIDER)
        cognee.config.set_embedding_provider(self._embedding_provider)

    # ── Internal: stripped cognify pipeline ────────────────────────────

    async def _chunk_and_embed(self, dataset: str) -> None:
        """Run classify → chunk → add_data_points on a dataset.

        This is the ADR-0005 §6 "graph extraction disabled" path: we
        skip ``extract_graph_and_summarize`` (which needs an LLM) and
        only emit chunk DataPoints into LanceDB so the vector retriever
        has something to search against.
        """
        cognee = _cognee()
        # Imports are local because they're only needed on the write
        # path — keeps the hal0 import graph small for read-only callers.
        from cognee.modules.pipelines.tasks.task import Task
        from cognee.tasks.documents.classify_documents import (
            classify_documents,
        )
        from cognee.tasks.documents.extract_chunks_from_documents import (
            extract_chunks_from_documents,
        )
        from cognee.tasks.storage.add_data_points import add_data_points

        tasks = [
            Task(classify_documents),
            # 512 tokens/chunk matches Cognee's default; small enough that
            # bge-small-en-v1.5's 512-token context isn't exceeded.
            Task(extract_chunks_from_documents, max_chunk_size=512),
            # embed_triplets=False because we're not building a graph in
            # v0.2 — only chunk embeddings.
            Task(add_data_points, embed_triplets=False),
        ]
        await cognee.run_custom_pipeline(
            tasks=tasks,
            dataset=dataset,
            pipeline_name="hal0_chunk_embed_only",
        )

    # ── Audit log ──────────────────────────────────────────────────────

    def _audit(self, op: str, dataset: str, **extra: Any) -> None:
        """Emit a structured audit event (ADR-0005 §5).

        Every call surface logs ``hal0.memory.audit`` with at minimum
        ``client_id, op, dataset, timestamp`` so journald / log
        aggregators can build per-client memory access traces. ``extra``
        carries op-specific context (e.g. ``count`` for delete).
        """
        event = {
            "event": AUDIT_EVENT,
            "client_id": self._client_id,
            "op": op,
            "dataset": dataset,
            "timestamp": _now_iso(),
            **extra,
        }
        # In-memory tail (bounded). Mirror first so a structlog
        # misconfiguration can't suppress the audit trail tests assert
        # on.
        self.audit_tail.append(event)
        if len(self.audit_tail) > self._audit_tail_max:
            # Drop oldest entries in batch (cheaper than per-call shift).
            del self.audit_tail[: len(self.audit_tail) - self._audit_tail_max]
        # Structured journald-bound emission.
        _audit_logger().info(AUDIT_EVENT, **{k: v for k, v in event.items() if k != "event"})

    # ── Public API ─────────────────────────────────────────────────────

    async def add(
        self,
        text: str,
        dataset: str = SHARED_DATASET,
        tags: list[str] | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Add a memory item. Returns ``{id, timestamp}``.

        ``dataset`` is the caller-requested namespace; when
        ``private_mode=True`` is on the instance, ANY value here is
        promoted to ``private:<client_id>`` per ADR-0005 §3 — clients
        cannot escape their own private bucket by passing
        ``dataset="shared"``.

        ``source`` defaults to the constructor's ``client_id``. Callers
        may pass an override (e.g. a sub-agent identifier) but the
        ``client_id`` from the Bearer token is what shows up in the
        audit log either way — clients can annotate but cannot
        impersonate.
        """
        if tags is None:
            tags = []
        if metadata is None:
            metadata = {}
        effective_dataset = self._effective_write_dataset(dataset)
        effective_source = source or self._client_id
        item_id = str(uuid.uuid4())
        timestamp = _now_iso()

        cognee = _cognee()
        add_result = await cognee.add(
            [text],
            dataset_name=effective_dataset,
            node_set=list(tags),
        )
        await self._chunk_and_embed(effective_dataset)

        cognee_dataset_id = (
            str(add_result.dataset_id) if hasattr(add_result, "dataset_id") else None
        )
        # ``cognee.add`` doesn't echo the per-item data_id in the result
        # — we have to ask the dataset what its newest item is. Cheap
        # because the dataset is tiny per-add and we just appended.
        cognee_data_id = await self._latest_cognee_data_id(effective_dataset)

        with self._sidecar_conn() as conn:
            conn.execute(
                """
                INSERT INTO hal0_memory_items
                    (id, text, timestamp, dataset, tags, source, metadata,
                     cognee_data_id, cognee_dataset_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    text,
                    timestamp,
                    effective_dataset,
                    json.dumps(list(tags)),
                    effective_source,
                    json.dumps(metadata),
                    cognee_data_id,
                    cognee_dataset_id,
                ),
            )
            conn.commit()

        self._audit("add", effective_dataset, item_id=item_id, tags=list(tags))

        # ADR-0014 §1+§6 — when graph extraction is on, enqueue a
        # background cognify pass after the foreground add returns. The
        # task is registered so ``set_graph_enabled(False)`` (and the
        # ADR-0014 §6 "disable cancels in-flight" path) can cancel
        # cleanly without leaving zombie pipeline work. Failures are
        # caught + counted inside ``_build_graph`` so a flaky
        # structured-output LLM never blocks ingestion.
        if self._graph_enabled:
            try:
                task = asyncio.create_task(self._build_graph(effective_dataset))
            except RuntimeError:
                # No running loop (rare: sync test harness). Treat as
                # no-op — gate is still respected, build just doesn't
                # fire.
                task = None
            if task is not None:
                self._graph_tasks.add(task)
                task.add_done_callback(self._graph_tasks.discard)
        return {"id": item_id, "timestamp": timestamp}

    # ── ADR-0014 graph extraction ──────────────────────────────────────

    async def _build_graph(self, dataset: str) -> None:
        """Run ``cognee.cognify`` on a dataset, counting outcomes.

        Errors are caught + reported via :py:meth:`graph_status` rather
        than re-raised — per ADR-0014 §6, failed builds log + drop
        silently so a misconfigured graph route can never block memory
        ingestion. Route resolution itself (upstream vs primary vs
        agent) lands in v0.4 with the eval suite; v0.3 runs Cognee's
        default cognify against whatever ``LLM_API_KEY`` the env carries.
        """
        cognee = _cognee()
        try:
            await cognee.cognify(datasets=[dataset], run_in_background=False)
        except asyncio.CancelledError:
            # ADR-0014 §6 "disable cancels in-flight" — let the
            # cancellation propagate so the task set drops the entry.
            raise
        except Exception as exc:
            self._graph_build_errors = min(self._graph_build_errors + 1, 9_999)
            self._graph_last_error = f"{type(exc).__name__}: {exc}"[:200]
            self._graph_last_built_at = _now_iso()
            _audit_logger().warning(
                "hal0.memory.graph.build_failed",
                client_id=self._client_id,
                dataset=dataset,
                error=self._graph_last_error,
                route=self._graph_route,
            )
            return
        self._graph_builds_ok += 1
        self._graph_last_built_at = _now_iso()
        self._graph_last_error = None
        _audit_logger().info(
            "hal0.memory.graph.build_ok",
            client_id=self._client_id,
            dataset=dataset,
            route=self._graph_route,
        )

    def graph_status(self) -> dict[str, Any]:
        """Return the ADR-0014 §status payload for dashboard + CLI.

        Shape::

            {
              "enabled":   bool,
              "route":     "upstream" | "primary" | "agent",
              "in_flight": int,
              "builds_ok": int,
              "errors":    int,
              "last_built_at": iso8601 | None,
              "last_error":    str | None,
            }
        """
        return {
            "enabled": self._graph_enabled,
            "route": self._graph_route,
            "in_flight": len(self._graph_tasks),
            "builds_ok": self._graph_builds_ok,
            "errors": self._graph_build_errors,
            "last_built_at": self._graph_last_built_at,
            "last_error": self._graph_last_error,
        }

    def set_graph_enabled(self, enabled: bool, route: str | None = None) -> None:
        """Flip the graph-extraction gate at runtime (ADR-0014 §6).

        Setting ``enabled = False`` cancels in-flight build tasks so a
        disable-while-busy doesn't dribble out cognify writes after the
        user thinks the feature is off.

        ``route`` updates the stored route label (``upstream`` /
        ``primary`` / ``agent``). The actual route-resolution
        implementation lands in v0.4 with the eval suite; v0.3 stores
        the pick so a process restart preserves user intent + the
        dashboard always reflects current state.
        """
        if route is not None and route not in {"upstream", "primary", "agent"}:
            raise ValueError(
                f"graph route {route!r} is not valid; choose from 'upstream' | 'primary' | 'agent'"
            )
        if route is not None:
            self._graph_route = route
        if not enabled and self._graph_enabled:
            # Disable flip — cancel anything in flight per ADR-0014 §6.
            for t in list(self._graph_tasks):
                t.cancel()
        self._graph_enabled = bool(enabled)

    async def search(
        self,
        query: str,
        limit: int = 10,
        dataset: str | list[str] = SHARED_DATASET,
        tags: list[str] | None = None,
        before: str | None = None,
        after: str | None = None,
        mode: str = "vector",
    ) -> list[dict[str, Any]]:
        """Vector + filter search. Returns list of dicts (MemoryRecord shape).

        Filters layered in order (cheapest first):
          1. **Dataset**: the caller's request intersected with what
             the caller is allowed to see (``shared`` + own private,
             never another client's private). ADR-0005 §3.
          2. **Tags**: AND-match on the sidecar's stored tags. Cognee
             1.0's ``node_name`` param is silently ignored by the CHUNKS
             retriever, so we enforce here.
          3. **Date range**: ISO-8601 ``before`` / ``after`` against the
             stored timestamp.

        We over-fetch from Cognee (``top_k = limit * 5``, capped at 100)
        because filtering happens after retrieval — under-fetching would
        starve narrow filters. ``limit`` is still respected on the
        return.
        """
        if tags is None:
            tags = []
        # ADR-0014 §6 — accept the mode param so callers (MCP + CLI +
        # dashboard) can already opt in to graph/hybrid; v0.3 falls
        # back to vector for graph + hybrid because the route
        # resolution lands in v0.4. We still validate so a typo
        # surfaces immediately.
        if mode not in {"vector", "graph", "hybrid"}:
            raise ValueError(
                f"search mode {mode!r} is not valid; choose from 'vector' | 'graph' | 'hybrid'"
            )
        if mode != "vector" and not self._graph_enabled:
            _audit_logger().info(
                "hal0.memory.search.mode_fallback",
                client_id=self._client_id,
                requested_mode=mode,
                resolved_mode="vector",
                reason="graph_extraction_disabled",
            )
        allowed_datasets = self._allowed_read_datasets(dataset)

        cognee = _cognee()
        # Local import: SearchType is part of Cognee's deep module tree;
        # importing at module level slows wrapper-only call sites.
        from cognee.modules.search.types.SearchType import (
            SearchType,
        )

        try:
            raw = await cognee.search(
                query_text=query,
                # CHUNKS = pure vector retrieval, no LLM call. ADR-0005 §6
                # defers graph + summary modes to Phase 9.
                query_type=SearchType.CHUNKS,
                datasets=allowed_datasets,
                top_k=min(100, max(limit * 5, limit)),
            )
        except Exception as exc:
            # Cognee raises a tower of "store is empty" errors depending
            # on what's missing:
            #   - NoDataError              → vector collection unbuilt.
            #   - DatasetNotFoundError     → caller's dataset never
            #     written to (e.g. Bob searches his private:bob before
            #     writing anything there).
            #   - CollectionNotFoundError  → low-level LanceDB miss.
            #   - DatabaseNotCreatedError  → first-ever search on a
            #     fresh install — SQLite migrations haven't run yet
            #     (Cognee runs migrations lazily on first add).
            #   - sqlite OperationalError  → ``no such table:
            #     principals`` from the same first-run window.
            # All map to "no results" in the §2 contract — return ``[]``
            # so callers don't have to special-case fresh stores.
            empty_store_markers = {
                "NoDataError",
                "DatasetNotFoundError",
                "CollectionNotFoundError",
                "DatabaseNotCreatedError",
                "SearchPreconditionError",
            }
            exc_str = str(exc)
            is_first_run = (
                type(exc).__name__ in empty_store_markers
                or "no such table" in exc_str.lower()
                or "principals" in exc_str
                or "SearchPreconditionError" in exc_str
                or "DatabaseNotCreatedError" in exc_str
            )
            if is_first_run:
                self._audit("search", ",".join(allowed_datasets), query=query, results=0)
                return []
            raise

        # Pull the matching texts back through the sidecar so we get the
        # rich-schema fields. Cognee chunks share text identity via
        # source_content_hash, but the easiest path is text-match against
        # what we stored — content is one chunk per add at 512 tokens,
        # ingest text is the canonical form.
        texts_in_order = [_chunk_text(r) for r in raw]
        scores_in_order = [_chunk_score(r) for r in raw]
        out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        with self._sidecar_conn() as conn:
            for text, score in zip(texts_in_order, scores_in_order, strict=True):
                if text is None:
                    continue
                rows = conn.execute(
                    """
                    SELECT * FROM hal0_memory_items
                    WHERE text = ? AND dataset IN ({})
                    ORDER BY timestamp DESC
                    """.format(",".join("?" * len(allowed_datasets))),
                    (text, *allowed_datasets),
                ).fetchall()
                for row in rows:
                    if row["id"] in seen_ids:
                        continue
                    record = _row_to_record(row, score=score)
                    if not _passes_filters(record, tags, before, after):
                        continue
                    out.append(record.to_dict())
                    seen_ids.add(row["id"])
                    if len(out) >= limit:
                        break
                if len(out) >= limit:
                    break

        self._audit(
            "search",
            ",".join(allowed_datasets),
            query=query,
            results=len(out),
        )
        return out

    async def list_items(
        self,
        dataset: str = SHARED_DATASET,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Paginated list. Cursor is the last-seen item id.

        Ordering is by timestamp DESC. The cursor scheme is "id of the
        last item the client saw" — opaque-ish but easy to debug because
        it matches the wire id.

        We list across the caller's allowed datasets (``shared`` + own
        private) when ``dataset == "shared"``. Passing a specific
        ``private:<id>`` lists only that namespace, but the wrapper
        still scopes to the caller's own client_id — passing
        ``private:bob`` from ``alice`` yields an empty list, not an
        error (ADR-0005 §3 leans toward fail-open-empty for reads).
        """
        allowed_datasets = self._allowed_read_datasets(dataset)
        with self._sidecar_conn() as conn:
            params: list[Any] = list(allowed_datasets)
            placeholders = ",".join("?" * len(allowed_datasets))
            cursor_clause = ""
            if cursor:
                row = conn.execute(
                    "SELECT timestamp FROM hal0_memory_items WHERE id = ?",
                    (cursor,),
                ).fetchone()
                if row is not None:
                    cursor_clause = " AND timestamp < ?"
                    params.append(row["timestamp"])
            rows = conn.execute(
                f"""
                SELECT * FROM hal0_memory_items
                WHERE dataset IN ({placeholders}){cursor_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (*params, limit + 1),
            ).fetchall()

        next_cursor: str | None = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = rows[-1]["id"]
        items = [_row_to_record(r, score=None).to_dict() for r in rows]
        self._audit("list_items", ",".join(allowed_datasets), count=len(items))
        return {"items": items, "next_cursor": next_cursor}

    async def delete(self, ids: list[str]) -> dict[str, int]:
        """Delete by sidecar id. Returns ``{deleted: int}``.

        We delete from BOTH the sidecar AND Cognee's own dataset rows
        (via ``cognee.datasets.delete_data``). A failure on the Cognee
        side does not unwind the sidecar delete — better to leak a
        Cognee chunk than to falsely report success on a 2-id batch
        where one half succeeded. The audit log records the count
        Cognee acknowledged.

        Empty list is a no-op (``{"deleted": 0}``) — defensive against
        callers handing us ``ids=[]`` rather than guarding upstream.
        """
        if not ids:
            self._audit("delete", "-", requested=0, deleted=0)
            return {"deleted": 0}

        cognee = _cognee()
        deleted = 0
        # Capture the wipes by dataset so the audit log can summarise
        # per-namespace; collapses to a single comma-joined string in
        # the final event.
        wipes_by_dataset: dict[str, int] = {}
        with self._sidecar_conn() as conn:
            for item_id in ids:
                row = conn.execute(
                    "SELECT cognee_data_id, cognee_dataset_id, dataset "
                    "FROM hal0_memory_items WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    continue
                # Only the caller's own datasets can be deleted. Defends
                # against a leaked id being used to reach into a different
                # client's private namespace. (v0.2 single-user => moot
                # in practice, but the check is cheap + Phase-9-ready.)
                if row["dataset"] not in self._allowed_read_datasets(SHARED_DATASET):
                    continue
                if row["cognee_data_id"] and row["cognee_dataset_id"]:
                    try:
                        await cognee.datasets.delete_data(
                            dataset_id=uuid.UUID(row["cognee_dataset_id"]),
                            data_id=uuid.UUID(row["cognee_data_id"]),
                        )
                    except Exception:
                        # Cognee may already have evicted the chunk —
                        # treat the sidecar as the source of truth for
                        # the count.
                        _audit_logger().warning(
                            "hal0.memory.delete.cognee_miss",
                            client_id=self._client_id,
                            item_id=item_id,
                        )
                conn.execute(
                    "DELETE FROM hal0_memory_items WHERE id = ?",
                    (item_id,),
                )
                deleted += 1
                wipes_by_dataset[row["dataset"]] = wipes_by_dataset.get(row["dataset"], 0) + 1
            conn.commit()

        self._audit(
            "delete",
            ",".join(sorted(wipes_by_dataset)) or "-",
            requested=len(ids),
            deleted=deleted,
        )
        return {"deleted": deleted}

    # ── Internal helpers ───────────────────────────────────────────────

    def _effective_write_dataset(self, requested: str) -> str:
        """Apply the §3 namespace rule to a write.

        Two postures:

          1. ``private_mode=True`` (per-client wrapper, the legacy
             shape used by `tests/memory/`): the constructor pins the
             effective dataset to ``private:<client_id>``; ANY
             ``requested`` value here is promoted to it. Clients can't
             smuggle private data into ``shared`` from a private
             instance, and can't escape into ``shared`` either.

          2. ``private_mode=False`` (singleton wrapper, the
             production shape used by ``app.state.memory_wrapper`` —
             see issue #367): identity already resolved by the
             transport layer (REST / MCP) and stamped onto the
             ``dataset`` string. If the caller passes
             ``private:<x>``, persist VERBATIM — the REST/MCP layer
             owns the cross-client guard (regex on agent id +
             rejection of body ``private:*`` when the private toggle
             is off). The wrapper used to collapse this to ``shared``,
             which silently leaked every agent's "private" write into
             the global bucket. ``shared`` + custom datasets still
             pass through unchanged.
        """
        if self._private_mode:
            return self._write_dataset
        # Non-private wrapper: trust the resolved dataset string from
        # the caller — REST + MCP enforce identity + reject hostile
        # private:* values before this point (issue #367).
        return requested or SHARED_DATASET

    def _allowed_read_datasets(self, requested: str | list[str]) -> list[str]:
        """Intersect requested datasets with the caller's read scope.

        Always includes ``shared`` plus the caller's own
        ``private:<client_id>``. If the caller explicitly asks for a
        different private namespace, it's dropped silently — read
        attempts on another client's private bucket don't error
        (avoid leaking existence) but never return data.
        """
        if isinstance(requested, str):
            requested_list = [requested]
        else:
            requested_list = list(requested) if requested else [SHARED_DATASET]
        out: list[str] = []
        own_private = f"{PRIVATE_PREFIX}{self._client_id}"
        for ds in requested_list:
            if ds == SHARED_DATASET:
                out.append(SHARED_DATASET)
                # Always read own private alongside shared (the §3
                # default — clients see their own data without having to
                # opt in per call).
                if own_private not in out:
                    out.append(own_private)
            elif ds == own_private:
                if own_private not in out:
                    out.append(own_private)
            elif ds.startswith(PRIVATE_PREFIX):
                # Another client's private — silently dropped.
                continue
            else:
                # Caller-defined custom dataset (e.g. an integration
                # naming convention). Pass through; ADR-0005 §3 only
                # speaks about shared + private:<id> — custom names are
                # opaque to the namespace rule.
                if ds not in out:
                    out.append(ds)
        return out

    async def _latest_cognee_data_id(self, dataset_name: str) -> str | None:
        """Look up the Cognee data_id for the most recent add to a dataset.

        We add one item at a time, so the latest item in the named
        dataset is the one we just stored. Cognee 1.0's ``add`` API
        returns the dataset_id but not the per-data_id; the official
        accessor for that is ``cognee.modules.data.methods.get_dataset_data``.
        """
        _cognee()  # ensure cognee root is importable
        # Imports kept local for the same reason as the cognify pipeline
        # imports: keep the wrapper's module-import graph small.
        from cognee.modules.data.methods.get_dataset_data import (
            get_dataset_data,
        )
        from cognee.modules.data.methods.get_datasets import get_datasets
        from cognee.modules.users.methods import get_default_user

        user = await get_default_user()
        for d in await get_datasets(user_id=user.id):
            if d.name != dataset_name:
                continue
            items = await get_dataset_data(d.id)
            if not items:
                return None
            # Cognee orders items oldest-first; the most recent add is
            # the last element.
            return str(items[-1].id)
        return None


# ── Row + result helpers (module-level so tests can reach them) ────────────


def _row_to_record(row: sqlite3.Row, score: float | None) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        text=row["text"],
        timestamp=row["timestamp"],
        dataset=row["dataset"],
        tags=json.loads(row["tags"] or "[]"),
        source=row["source"],
        metadata=json.loads(row["metadata"] or "{}"),
        score=score,
    )


def _passes_filters(
    record: MemoryRecord,
    tags: list[str],
    before: str | None,
    after: str | None,
) -> bool:
    """Apply tag AND-match + date range to a single record."""
    if tags:
        record_tags = set(record.tags)
        if not all(t in record_tags for t in tags):
            return False
    if before and record.timestamp >= before:
        return False
    return not (after and record.timestamp <= after)


def _chunk_text(chunk: Any) -> str | None:
    """Extract the human-readable text from a Cognee CHUNKS-mode result.

    Cognee chunks are dicts with a ``text`` key in 1.0.x. The handler
    is defensive (returns None on shape change) so a future Cognee
    bump that renames the field surfaces as "no results" rather than
    a KeyError.
    """
    if isinstance(chunk, dict):
        return chunk.get("text")
    return getattr(chunk, "text", None)


def _chunk_score(chunk: Any) -> float | None:
    """Best-effort score field. Cognee 1.0's CHUNKS retriever doesn't
    surface a distance/score per result; we return None and let the
    caller fall back to retrieval order if it needs ranking signal.
    """
    if isinstance(chunk, dict):
        return chunk.get("score")
    return getattr(chunk, "score", None)


def _now_iso() -> str:
    """UTC ISO-8601 timestamp matching ADR-0005 §2 (date filter input)."""
    return datetime.now(UTC).isoformat()


def _clear_cognee_caches() -> None:
    """Drop Cognee's process-wide ``@lru_cache`` singletons.

    Cognee memoises its config objects + DB engines on module-level
    ``functools.lru_cache`` wrappers. A second wrapper construction in
    the same process (e.g. between pytest functions, or two requests
    against differently-configured agents in a single API server)
    would otherwise see the first wrapper's directory layout because
    the cached config returns immediately without re-reading
    ``system_root_directory``.

    Every catch is silent because Cognee's internal module names shift
    across minor versions; the ``cognee==1.0.7`` pin keeps the happy
    path stable, but if any of these imports fail post-upgrade we'd
    rather degrade to "second wrapper inherits the first's dirs" than
    crash on construction.
    """
    import_specs = [
        ("cognee.infrastructure.databases.relational.config", "get_relational_config"),
        ("cognee.infrastructure.databases.relational.config", "get_migration_config"),
        ("cognee.infrastructure.databases.graph.config", "get_graph_config"),
        ("cognee.infrastructure.databases.vector.config", "get_vectordb_config"),
        ("cognee.infrastructure.databases.vector.embeddings.config", "get_embedding_config"),
        (
            "cognee.infrastructure.databases.relational.create_relational_engine",
            "create_relational_engine",
        ),
        (
            "cognee.infrastructure.databases.vector.create_vector_engine",
            "create_vector_engine",
        ),
        (
            "cognee.infrastructure.databases.graph.get_graph_engine",
            "create_graph_engine",
        ),
        (
            "cognee.infrastructure.databases.vector.embeddings.get_embedding_engine",
            "get_embedding_engine",
        ),
    ]
    for module_path, attr in import_specs:
        try:
            mod = __import__(module_path, fromlist=[attr])
            fn = getattr(mod, attr, None)
            cache_clear = getattr(fn, "cache_clear", None)
            if callable(cache_clear):
                cache_clear()
        except Exception:
            continue


# ── async wrapper sync barrier ─────────────────────────────────────────────


# Cognee internals occasionally schedule background work through
# ``asyncio.ensure_future``. We do NOT block on those here — Cognee
# resolves them on the next await. If a test runs ``add`` then exits
# the event loop immediately, the background work cancels cleanly
# (LanceDB is durable mid-flush). If we ever see ghost rows, the
# fix is to add ``await asyncio.sleep(0)`` here — leaving the hook
# in place as a comment so the next reader knows to look.
async def _yield() -> None:  # pragma: no cover - sentinel only
    await asyncio.sleep(0)
