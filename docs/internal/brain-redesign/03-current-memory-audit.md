# 03 — Current Memory / "Brain" Subsystem Audit (as-built)

**Status:** audit for redesign planning
**Date:** 2026-06-02
**Scope:** the memory subsystem as it exists on `main` — `src/hal0/memory/`, the
`hal0-memory` MCP server, the `/api/memory/*` REST surface, the in-process
dispatcher, the CLI, and the Hermes `memory_cognee` plugin.
**Engine:** Cognee `==1.0.7` (embedded SQLite + LanceDB + Kuzu), per
[ADR-0005](../adr/0005-memory-engine-cognee.md) and
[ADR-0014](../adr/0014-cognee-graph-extraction-model-gate.md).

Everything below was verified against source. Where a behaviour is documented in
an ADR but **not** present in code, that is called out explicitly. Citations are
`path:line`.

---

## 0. TL;DR for the redesign

- Cognee is wrapped behind exactly **one** seam: `CogneeWrapper`
  (`src/hal0/memory/cognee_wrapper.py`). Every other layer (MCP, REST, dispatcher,
  CLI, Hermes plugin, stats) calls *that wrapper's five methods* — never `cognee`
  directly. The wrapper is the engine-swap seam.
- But the wrapper is **not** an abstract base / Protocol. It is a concrete class.
  There is no `MemoryProvider` ABC inside hal0 (the only ABC named that way lives
  in the *Hermes* venv, upstream, and the hal0 plugin subclasses it — that is a
  different, agent-side abstraction). A swap to e.g. Hindsight means writing a new
  class with the same five async method signatures and changing the single
  construction site in `create_app`.
- The documented **issue #317** namespace-forcing bug ("`/api/memory/add` forces
  `dataset="shared"`") is **FIXED** in current code (PRs #366/#369). The fix is
  live and verifiable — see §4.1. The *risk* it represents (two transport surfaces
  drifting on namespace logic) is now mitigated by a shared
  `hal0.memory.namespace` module.
- The data model is **vector-only in practice**. Graph extraction (Kuzu / cognify)
  exists as a gated, default-OFF, fire-and-forget background task that is never
  read back — `search` always uses `SearchType.CHUNKS` (pure vector) and falls
  back to vector for `mode="graph"|"hybrid"`. There is also a **sidecar SQLite
  index** that is the *real* source of truth for the rich schema (dataset, tags,
  source, metadata, timestamp) and for all filtering.
- Two live defects found beyond #317: (a) the Hermes REST client calls **wrong
  route paths** for `list`/`delete` (§4.2); (b) test coverage exercises the
  wrapper only against **fakes/stubs** — the real Cognee integration path has no
  CI coverage outside the `tests/memory/` suite, which needs the heavy Cognee
  fixture (§4.4).

---

## 1. Architecture as-built

### 1.1 Layer map

```
                       ┌─────────────────────────────────────────────────────┐
                       │                    CALLERS                           │
                       │                                                      │
   MCP clients ───►  /mcp/memory  (FastMCP sub-app)                          │
   (agents)            │  hal0.mcp.memory.build_server                        │
                       │                                                      │
   MCP admin ──────► hal0.mcp.admin  ──► MemoryDispatcher (in-process)        │
   (memory_* tools)    │  hal0.dispatcher.memory_dispatcher                   │
                       │                                                      │
   HTTP / Hermes ───► /api/memory/{add,search,list,delete}  (REST shims)      │
   / CLI / dashboard   │  hal0.api.routes.memory                              │
                       │                                                      │
   dashboard sidebar ─► /api/agents/{id}/memory/stats                         │
                       │  hal0.api.agents.memory_stats                        │
                       └───────────────────────┬──────────────────────────────┘
                                               │  all paths converge on…
                            ┌──────────────────▼──────────────────┐
                            │  hal0.memory.namespace               │
                            │  resolve_write_dataset / read_datasets│ (pure fns)
                            └──────────────────┬──────────────────┘
                                               │
                            ┌──────────────────▼──────────────────┐
                            │  CogneeWrapper  (THE SEAM)           │
                            │  add / search / list_items / delete  │
                            │  + graph_status / set_graph_enabled  │
                            │  + set_rerank_enabled                │
                            └───────┬───────────────────┬─────────┘
                                    │                   │
                  ┌─────────────────▼──┐      ┌─────────▼───────────────────┐
                  │ cognee 1.0.7        │      │ sidecar SQLite              │
                  │  - LanceDB (vector) │      │  hal0_memory_index.sqlite   │
                  │  - Kuzu (graph,OFF) │      │  (dataset/tags/source/      │
                  │  - SQLite (rel/doc) │      │   metadata/timestamp +      │
                  │  cognee.add/search/ │      │   cognee_data_id link)      │
                  │  cognify/delete     │      │  → ALL filtering happens here│
                  └─────────────────────┘      └─────────────────────────────┘
```

The wiring point is `create_app` in `src/hal0/api/__init__.py:1075-1155`:
one `CogneeWrapper` singleton is built (`:1108`), stashed on
`app.state.memory_wrapper` (`:1121`), and handed to both the MCP mount and the
in-process dispatcher.

### 1.2 The MCP server — `hal0.mcp.memory`

`src/hal0/mcp/memory.py`. `build_server(wrapper, ...)` returns a FastMCP instance
mounted at `/mcp/memory` by `mount_mcp_servers`
(`src/hal0/api/mcp_mount.py:151-163`). Four tools are registered
(`src/hal0/mcp/memory.py:454-460`):

| Tool | Documented schema (ADR-0005 §2) | Handler | Annotations (`:400-413`) |
|------|----------------------------------|---------|--------------------------|
| `memory_add(text, dataset="shared", tags=[], metadata={})` → `{id, timestamp}` | `source` is **server-injected**, callers passing it are rejected (`:205-208`) | `_memory_add` `:170` | `readOnly=F destructive=F idempotent=F` |
| `memory_search(query, limit=10, dataset="shared"\|list, tags=[], before=null, after=null)` → `{results:[…]}` | private-mode reads union `[shared, private:<id>]` (`:254`) | `_memory_search` `:224` | `readOnly=T idempotent=T` |
| `memory_list(dataset="shared", cursor=null, limit=50)` → `{items, next_cursor}` | resolves a **single** dataset string (`:285`) | `_memory_list` `:274` | `readOnly=T idempotent=T` |
| `memory_delete(ids: list[str])` → `{deleted: int}` | bulk-delete approval gating lives in `hal0.mcp.admin` (`:299-312` docstring) | `_memory_delete` `:299` | `readOnly=F **destructive=T** idempotent=T` |

Validation is hand-rolled (no pydantic) via `_require` / `_optional` /
`_normalise_tags` (`:101-133`) so the error envelope matches `admin.py`. Errors
surface as `{"status":"error","error":{"code":"mcp.memory_schema"|"mcp.memory_failed", ...}}`
(`:377-387`).

`make_dispatcher(wrapper, client_id_resolver, private_resolver)` (`:339`) builds the
async closure `_dispatch(tool, args)` that every transport ultimately calls. The
resolvers are 0-arg callables read **at call time** so the same closure serves both
the standalone server and the in-process dispatcher.

### 1.3 The in-process dispatcher — `MemoryDispatcher`

`src/hal0/dispatcher/memory_dispatcher.py`. A thin class wrapping
`make_dispatcher` (`:79`). Its reason for existing (`:1-28` docstring): the admin
MCP server's `memory_*` tool family must reach Cognee **without** an HTTP
loop-back through `/mcp/memory`. It is callable (`__call__` `:95`) so
`hal0.mcp.admin.dispatch(..., memory_dispatcher=…)` can treat it as a plain
`Callable[[str, dict], Awaitable[dict]]`. Constructed in `create_app`
(`src/hal0/api/__init__.py:1136`) with the same resolvers
(`client_id_resolver` / `private_resolver` from `mcp_mount`).

### 1.4 The REST shims — `hal0.api.routes.memory`

`src/hal0/api/routes/memory.py`, mounted at `/api/memory`
(`src/hal0/api/__init__.py:877-878`). Two route families:

1. **Graph-gate routes** (ADR-0014): `GET /graph/status` (`:174`) and
   `PUT /graph` (`:213`). These read/flip `[memory.graph]` in `hal0.toml`
   (`load_hal0_config` / `save_hal0_config`) **and** flip the live wrapper via
   `wrapper.set_graph_enabled(...)` (`:265`) so no restart is needed.

2. **CRUD shims** added in #302/#303 (`:276-461`):
   `POST /add` (`:291`), `POST /search` (`:348`), `GET /list` (`:392`),
   `POST /delete` (`:426`). These exist because the bootstrap + CLI + dashboard
   were POSTing to `/mcp/memory` as if it were one-shot JSON-RPC, which real
   FastMCP transport (initialize + session-tagged calls) does not support. The
   shims are "the cheapest unblock so identity cards actually get written"
   (`:285-288` docstring). They are a **parallel path** to the MCP transport, not
   a replacement.

Identity on the REST surface (post-ADR-0012, no auth): the `X-hal0-Agent` header
(`_agent_id` `:87`, validated against `^[a-zA-Z0-9_\-]{1,64}$`, `private:` prefix
rejected) and the `X-hal0-Private: 1` toggle (`_is_private` `:128`). Absent agent
header → `"anonymous"`.

### 1.5 The per-agent stats route — `hal0.api.agents.memory_stats`

`src/hal0/api/agents/memory_stats.py`, mounted under `/api/agents`
(`src/hal0/api/__init__.py:1004`). `GET /{agent_id}/memory/stats` (`:110`) returns
`{agent_id, namespace, writes, reads, last_write, available}` for the sidebar chip.
It reads through the **same in-process wrapper** (`app.state.memory_wrapper`,
`:141`), scoped to `private:<agent_id>` (`:78-87`). Notable limitations baked into
the code:
- `writes` is `len(items)` of a single 500-item page (`:165`, `_LIST_PAGE_LIMIT`),
  capped — not a true count.
- `reads` is **hard-coded `0`** (`:217`) because the wrapper exposes no
  per-namespace read counter.
- Only `hermes` is a known agent (`_KNOWN_AGENT_IDS` `:68`); anything else → 404.

### 1.6 The CLI — `hal0.cli.memory_commands`

`src/hal0/cli/memory_commands.py`. Despite the package name, the CLI only covers
the **graph gate**, not CRUD. Three commands, all thin HTTP clients to the local
API (`:1-13` docstring):
- `hal0 memory graph status` → `GET /api/memory/graph/status` (`:46`)
- `hal0 memory graph enable [--route … --provider … --model …]` → `PUT /api/memory/graph` (`:99`)
- `hal0 memory graph disable` → `PUT /api/memory/graph` (`:160`)

There is **no** `hal0 memory add/search/list/delete` CLI. Operator CRUD over
memory is not exposed on the command line.

### 1.7 The Hermes plugin — `agents/hermes/plugins/memory_cognee`

`src/hal0/agents/hermes/plugins/memory_cognee/`. This is **copied verbatim** into
`$HERMES_HOME/plugins/memory/hal0-cognee/` at provision time
(`__init__.py:1-24`). It is the agent-side consumer:

- `__init__.py` — exposes `register(ctx)` and re-exports `Hal0CogneeProvider` so
  either Hermes discovery path works.
- `provider.py` — `Hal0CogneeProvider(MemoryProvider)`. Subclasses the **upstream
  Hermes ABC** `agent.memory_provider.MemoryProvider` (with a vendored stub
  fallback for hal0's own tests, `:35-56`). Plugin `name = "hal0-cognee"`. Wraps
  the async REST client in `asyncio.run` because Hermes' memory hooks are sync
  (`:16-18` docstring). Best-effort: transport failures fall back to empty context
  / silent drop (`:18-21`). Hooks:
  - `system_prompt_block()` (`:128`) — injects a "you have durable memory at
    `private:<agent>`" block.
  - `prefetch(query)` (`:137`) — `search(limit=5)`, formats hits as a markdown
    list. Returns `""` on any failure.
  - `sync_turn(user, assistant)` (`:163`) — `add(...)` the turn, tagged
    `["chat","hermes"]`, **unless** `agent_context ∈ {cron, flush, subagent}`
    (`_SKIP_WRITE_CONTEXTS` `:66`).
  - `on_memory_write(...)` (`:207`) — mirrors built-in memory writes into hal0.
  - `get_tool_schemas()` returns `[]` (`:186`) — **no model-visible tools**; recall
    is implicit via prefetch + system prompt. CRUD is left to the MCP server.
- `_client.py` — `Hal0MemoryClient`, a tiny async httpx client hitting
  `/api/memory/*` with `X-hal0-Agent` (sourced from `HAL0_AGENT_ID`,
  default `hermes-agent`). **Never sends a `dataset` field** — the server resolves
  the namespace from the header (the #317 contract, `:3-13` docstring).

### 1.8 End-to-end write path (trace)

**Via Hermes (the live agent path):**
1. Hermes finishes a turn → `Hal0CogneeProvider.sync_turn` (`provider.py:163`).
2. → `Hal0MemoryClient.add(text, tags=["chat","hermes"])` → `POST /api/memory/add`
   with `X-hal0-Agent: <HAL0_AGENT_ID>`, **no `dataset`** (`_client.py:106-123`).
3. → `memory_add` route (`routes/memory.py:291`): rejects body `source`,
   `_agent_id` validates the header, `_is_private` reads the toggle,
   `resolve_write_dataset(None, private, client_id)` → `"shared"` (or
   `private:<agent>` if `X-hal0-Private: 1`).
4. → `wrapper.add(text, dataset, tags, source=agent_id, metadata, client_id)`
   (`routes/memory.py:338`).
5. → `CogneeWrapper.add` (`cognee_wrapper.py:484`):
   `_effective_write_dataset` (`:1117`) applies the §3 rule, `cognee.add([text],
   dataset_name=…, node_set=tags)` (`:523`), then `_chunk_and_embed` runs the
   stripped classify→chunk→embed pipeline (`:412`, **no graph**), then a row is
   INSERTed into the sidecar (`:538-558`), an audit event is emitted (`:560`), and
   — only if `graph_enabled` — a background `cognify` task is fired and forgotten
   (`:575-585`). Returns `{id, timestamp}`.

**Via an MCP client:** identical from step 5 down, but steps 2-4 are replaced by
`/mcp/memory` → `make_dispatcher._dispatch` → `_memory_add` (`memory.py:170`) →
`wrapper.add(...)`. The admin MCP `memory_*` tools take the in-process
`MemoryDispatcher` shortcut to the same `_dispatch`.

### 1.9 End-to-end recall path (trace)

1. `prefetch`/MCP `memory_search`/`POST /api/memory/search` → `wrapper.search`
   (`cognee_wrapper.py:679`).
2. `mode` is validated; `graph`/`hybrid` **fall back to vector** with an audit
   note when graph is disabled (`:719-726`).
3. `_allowed_read_datasets` (`:1149`) intersects the request with
   `[shared, private:<client_id>]` (other clients' private buckets silently
   dropped).
4. `cognee.search(query_type=SearchType.CHUNKS, datasets=…, top_k=min(100, limit*5))`
   (`:737-744`) — **pure vector**. A tower of "empty store" exceptions all map to
   `[]` (`:745-784`).
5. Returned chunk **texts** are matched back against the **sidecar** to recover the
   rich schema (`:807-830`); tag AND-match + date range applied by `_passes_filters`
   (`:1244`).
6. Optional rerank pass (`_maybe_rerank` `:1003`) posts candidates to the
   `embed-rerank` slot (`:8086/rerank`) and reorders by `relevance_score`; any
   failure falls through to vector order.
7. Clip to `limit`, audit, return `list[dict]`.

---

## 2. Data model

### 2.1 Datasets / namespaces

The namespace rule (ADR-0005 §3) is implemented as two pure functions in
`src/hal0/memory/namespace.py`, shared by REST and MCP so they cannot drift
(`:1-28` docstring — this module exists *because* of #317):

- `DEFAULT_DATASET = "shared"`, `PRIVATE_PREFIX = "private:"` (`:32-33`).
- `resolve_write_dataset(requested, *, private, client_id)` (`:41`):
  - `private=True` → `private:<client_id>` (raises `MemoryNamespaceError` if no
    `client_id`).
  - empty / `None` → `"shared"`.
  - a body value starting with `private:` while `private=False` → **rejected**
    (`:69-73`) — the toggle is the only path in (PR #366 hardening).
  - otherwise pass through verbatim (custom datasets allowed).
- `resolve_read_datasets(...)` (`:77`): a list passes through; empty + private →
  `[shared, private:<id>]`; empty otherwise → `"shared"`; non-empty string →
  delegate to `resolve_write_dataset`.

Wrapper-level enforcement (the actual scoping) lives in `CogneeWrapper`:
- `_effective_write_dataset` (`:1117`): in `private_mode=True` instances, **any**
  requested dataset is forced to the constructor's `private:<client_id>`; in
  `private_mode=False` (the production singleton), the resolved string is persisted
  **verbatim** — REST/MCP already guarded it (this is the #367/#366 fix; see §4.1).
- `_allowed_read_datasets` (`:1149`): always unions `shared` + the caller's own
  `private:<client_id>`; other clients' `private:*` are dropped silently
  (fail-open-empty, never error — avoids leaking existence).

Custom datasets (anything not `shared`/`private:*`) are opaque to the rule and pass
through. ADR-0011 reserves an `agents` dataset for identity cards.

### 2.2 What Cognee actually stores vs what the sidecar stores

This is the single most important data-model fact for the redesign:

| Concern | Where it lives | Notes |
|---------|---------------|-------|
| Vector embeddings | **Cognee → LanceDB** | 384-dim `fastembed` `BAAI/bge-small-en-v1.5` (`cognee_wrapper.py:167-168`). Embeds chunked at 512 tokens (`:436`). |
| Graph (entities/relations) | **Cognee → Kuzu** — but **OFF by default** | `cognify` only runs as a fire-and-forget background task when `graph_enabled` (`:575-585`); the result is **never queried** — `search` always uses `SearchType.CHUNKS`. Graph is write-only dead weight today. |
| Relational / document store | Cognee → SQLite (`<dir>/databases/`) | Cognee's own internal bookkeeping. |
| **dataset, tags, source, metadata, timestamp** | **Sidecar SQLite** (`hal0_memory_index.sqlite`) | Cognee 1.0.x's chunk payload does **not** carry these at search time, so the wrapper shadows them (`:317-347`, schema at `:331-341`). **All filtering (dataset isolation, tag AND, date range) runs against the sidecar, not Cognee** (`:786-830`). |
| Cognee back-link | `cognee_data_id` / `cognee_dataset_id` columns | Used only for `delete` (`:960-965`). Recovered post-add via `_latest_cognee_data_id` (`:1198`), a "ask the dataset for its newest item" heuristic that assumes one-item-per-add. |

The search join is **text-equality** between the Cognee chunk text and the sidecar
`text` column (`:811-818`) — there is no shared id between LanceDB chunks and
sidecar rows. This is a fragile coupling (see §4.3).

### 2.3 On-disk layout

Default root `DEFAULT_COGNEE_DIR = /var/lib/hal0/memory/cognee`
(`cognee_wrapper.py:101`). Under it:
- `data/` — Cognee document data root (`:235`).
- `databases/` — Cognee relational SQLite + `cognee.kuzu` graph
  (`:242`, `:394-396`).
- `databases/` also holds LanceDB tables (via `set_vector_db_provider("lancedb")`).
- `hal0_memory_index.sqlite` — the sidecar, alongside Cognee's files so
  `$HAL0_HOME` snapshots cover both (`:307`).

Cognee is configured via env vars + `cognee.config.*` in `_configure_cognee`
(`:362-408`): `ENABLE_BACKEND_ACCESS_CONTROL=false` (RBAC deferred),
`COGNEE_SKIP_CONNECTION_TEST=true`, `LLM_API_KEY=sk-hal0-noop-...` (no LLM call
fires on the v0.2 path), embedding provider/model/dims, and explicit `DB_PATH` /
`GRAPH_DATABASE_URL`. `_clear_cognee_caches` (`:1288`) is called to bust Cognee's
`@lru_cache`d config/engine singletons so a second wrapper in the same process
doesn't inherit the first's dirs.

### 2.4 Audit trail

Every op emits a `hal0.memory.audit` structlog event (`AUDIT_EVENT` `:105`,
`_audit` `:449`) with `{client_id, op, dataset, timestamp, …}`, mirrored into a
bounded in-memory `audit_tail` (cap 1024, `:299-300`). This is the ADR-0005 §5
audit surface; production retention is journald. The `reads` counter the stats
endpoint wants does **not** exist — audit events are emitted but never aggregated.

---

## 3. Who consumes memory today

| Consumer | Surface used | Status |
|----------|--------------|--------|
| **Hermes agent** (bundled) | `/api/memory/{add,search}` via `Hal0CogneeProvider` → `Hal0MemoryClient` | **Live**, the primary writer/reader. `add` on every turn, `search` on prefetch. |
| **MCP clients / external agents** | `/mcp/memory` FastMCP (`memory_add/search/list/delete`) | Available; mounted only when the wrapper inits. The intended cross-app path. |
| **MCP admin tools** | `memory_*` via in-process `MemoryDispatcher` | Available; bypasses HTTP. |
| **Dashboard — Memory tab** | `GET/PUT /api/memory/graph[/status]` only (`ui/src/api/hooks/useMemory.ts`, `ui/src/api/endpoints.ts:120-121`) | **Live but graph-gate ONLY.** The MemoryMap (`ui/src/dash/memory-map.jsx`, PR #370) renders the graph-extraction toggle + counters. There is **no** dataset/item explorer hitting `/api/memory/list` or `/search`. |
| **Dashboard — sidebar agent block** | `GET /api/agents/hermes/memory/stats` (`endpoints.ts:99`) | Live; renders `writes` / `last_write` chip; `reads` always 0. |
| **CLI** | `hal0 memory graph {status,enable,disable}` | Live; **no CRUD subcommands**. |

PLAN.md §5 describes a future "Cognee dataset explorer (shared / private /
`agents`)" for the Memory tab — that explorer is **not built**; only the graph gate
ships.

---

## 4. Known defects & limitations

### 4.1 Issue #317 — namespace-forcing bug: **FIXED (verify before re-filing)**

The known-context bug ("`/api/memory/add` forces `dataset="shared"` regardless of
`private:*`") is **no longer present**. Verified:

- `git log` shows the fixes: `9ee21a3 fix(memory): REST /api/memory/add honors
  private:* namespace (#366)` and `58b582f fix(memory): honor per-call client_id on
  read path + audit stamping (#369)`.
- The REST handler now calls
  `resolve_write_dataset(body.get("dataset"), private=private, client_id=…)`
  (`src/hal0/api/routes/memory.py:329-333`) — it does **not** hard-code `"shared"`.
- The wrapper's `_effective_write_dataset` (`cognee_wrapper.py:1142-1147`) persists
  a resolved `private:<id>` **verbatim** in the non-private singleton instead of
  collapsing it to `shared`. The collapsing-to-shared behaviour (the actual #317
  symptom) is explicitly called out as the old bug in the docstring (`:1136-1141`).
- Shared logic now lives in `hal0.memory.namespace`, consumed by both REST
  (`routes/memory.py:29-34`) and MCP (`mcp/memory.py:69-75`), so the
  drift-between-surfaces root cause is structurally addressed.

**Action for redesign:** treat #317 as closed; the auto-memory index entry
`hal0_memory_dataset_namespace_bug` is stale and should be marked resolved.

### 4.2 Hermes REST client calls **wrong route paths** for list/delete (live latent bug)

`src/hal0/agents/hermes/plugins/memory_cognee/_client.py`:
- `list_items` calls `GET /api/memory` (`:139`) — but the server route is
  `GET /api/memory/list` (`routes/memory.py:392`). **No bare `GET /api/memory`
  route exists.**
- `delete` calls `DELETE /api/memory/{id}` (`:143`) — but the server route is
  `POST /api/memory/delete` with an `ids` body (`routes/memory.py:426`). **No
  `DELETE /api/memory/{id}` route exists.**

`add` (`POST /api/memory/add`) and `search` (`POST /api/memory/search`) are
correct. Because the provider only ever calls `add` + `search` at runtime
(`provider.py` never calls `list_items`/`delete`), this is **latent** — it would
404 the moment anything drives the client's list/delete methods. Worth fixing or
deleting the dead methods during the redesign.

### 4.3 Sidecar text-equality join is fragile

`search` re-derives the rich schema by matching Cognee's returned chunk **text**
against the sidecar `text` column (`cognee_wrapper.py:811-818`). Consequences:
- Two memories with identical text collide and can't be told apart.
- Any Cognee-side text normalization (whitespace, truncation) silently breaks the
  join → "no results" rather than an error.
- `_latest_cognee_data_id` (`:1198`) assumes strictly one item per add and reads
  "the last element" — concurrent adds to the same dataset could mis-link
  `cognee_data_id`, corrupting later deletes.

### 4.4 Test coverage gaps

- `tests/mcp/test_memory.py` (235 LOC) and `tests/api/test_memory_rest_routes.py`
  (494 LOC) exercise the dispatch/route/namespace logic against **`_FakeWrapper` /
  `StubWrapper`** — **not real Cognee** (`test_memory.py:8`,
  `test_memory_rest_routes.py:18,38`). They prove the transport + namespace
  contract, nothing about Cognee integration.
- The real wrapper-vs-Cognee behaviour (the stripped cognify pipeline, the sidecar
  join, delete back-linking, first-run empty-store handling) is only in
  `tests/memory/` (`test_cognee_wrapper.py`, `test_namespace_wrapper.py`,
  `test_graph_gate.py`, `test_rerank.py`), all marked `slow` and dependent on the
  heavy Cognee fixture (`tests/memory/conftest.py`). Per the auto-memory note on
  CI, these `slow`/Cognee suites are not in the default CI gate — meaning the
  fragile text-join + delete back-link paths (§4.3) can regress without failing CI.
- No test asserts the Hermes `_client.py` route paths against the real router (which
  is why §4.2 went unnoticed).

### 4.5 Graph extraction is write-only dead weight

`mode="graph"|"hybrid"` is accepted and validated (`cognee_wrapper.py:714-717`) but
**always falls back to vector** (`:719`). `cognify` runs only as a fire-and-forget
background task (`:575-585`, `_build_graph` `:590`) whose output is never read.
Route resolution (`upstream`/`primary`/`agent`) is stored but unimplemented —
"lands in v0.4 with the eval suite" (`:597-598`, `:662-665`). So Kuzu accumulates a
graph nobody queries, and the only observable effect of enabling it is build
counters in `graph_status()` (`:629`).

### 4.6 Other limitations

- **`reads` counter** is hard-coded `0` (`memory_stats.py:217`) — no per-namespace
  read aggregation exists.
- **`writes`** is a capped single-page count, not a real count
  (`memory_stats.py:165`); the docstring flags a v0.4 `count(dataset=…)` method as
  the right fix (`:158-163`).
- **Single-process singleton.** `_configure_cognee` pushes Cognee config as
  module-level singletons; "last writer wins" if multiple wrappers point at the same
  dir (`:310-315` comment). v0.2 ships exactly one wrapper per process.
- **No idle eviction / TTL / hygiene.** Memify is deferred (ADR-0005 §6). Memory
  grows unbounded.
- **`/mcp/memory` JSON-RPC is awkward for HTTP callers** — the whole REST-shim
  family exists because real FastMCP transport needs initialize + session-tagged
  calls (`routes/memory.py:283-288`). This is the "stopgap" noted in
  `memory_dispatcher.py:5`.
- **`memory_list` MCP tool asymmetry.** Unlike `memory_search`, the list tool
  resolves a **single** dataset string (`mcp/memory.py:285`) and does not union
  `[shared, private:<id>]` at the tool layer; it relies on the wrapper's
  `_allowed_read_datasets` to re-add `shared`. The REST `/list` similarly uses
  `resolve_write_dataset` (`routes/memory.py:409`), so a private-mode list returns
  only the private bucket from the resolver's perspective. Minor, but a behavioural
  inconsistency between `search` and `list`.

---

## 5. Coupling — how tightly is Cognee wired in?

**Cognee touchpoints are confined to one file.** A repo-wide grep confirms that
outside of `src/hal0/memory/cognee_wrapper.py`, nothing imports `cognee`. Every
other layer imports `CogneeWrapper` (or `MemoryRecord`) from `hal0.memory` and calls
its five async methods. The package `__init__.py` re-exports only those two names
(`src/hal0/memory/__init__.py:13-15`) and declares "only the wrapper is public."

**The engine-swap seam is therefore `CogneeWrapper` itself.** To swap to a different
engine (e.g. Hindsight) you would:
1. Implement a class with the same async surface:
   `add(text, dataset, tags, source, metadata, client_id) -> {id, timestamp}`,
   `search(query, limit, dataset, tags, before, after, mode, client_id) -> list[dict]`,
   `list_items(dataset, cursor, limit, client_id) -> {items, next_cursor}`,
   `delete(ids, client_id) -> {deleted}`, plus the runtime-flip helpers
   `graph_status()`, `set_graph_enabled()`, `set_rerank_enabled()`.
2. Change the **single construction site** in `create_app`
   (`src/hal0/api/__init__.py:1108`).

**But there is no formal abstraction.** Caveats that make the swap less clean than
it looks:
- **No ABC / Protocol** defines the contract. The wrapper is a concrete class; the
  contract is implied by call sites + the MCP/REST shims + the test stubs. The
  `MemoryProvider` ABC referenced anywhere in code is the **Hermes upstream** one
  (`provider.py:36`), an agent-side abstraction unrelated to the engine seam.
- **Leaky surface.** Callers depend on Cognee-shaped concepts that a new engine must
  reproduce: the `dataset` namespace model, the `private:<id>` convention, the
  `mode` enum, the graph-status/route counters (ADR-0014), and the rerank toggle.
  The graph-status payload shape is a hard contract — the dashboard depends on every
  key (`routes/memory.py:178-179`).
- **The sidecar SQLite is part of the contract too.** Filtering semantics (tag AND,
  date range, dataset isolation) are implemented in the wrapper against the sidecar,
  not delegated to the engine. A new engine that natively supports these would make
  the sidecar redundant — but any migration must preserve the existing sidecar data
  or re-ingest.
- **Config coupling.** `[memory.graph]` + `[memory.embedding]` config blocks
  (`hal0.config.schema`) and the boot-time wiring in `create_app:1083-1118` are
  Cognee/rerank-flavoured. A swap touches the config schema, not just the class.

**Net:** the seam is real and narrow (one file, one construction site), which is the
subsystem's biggest strength. But it is a concrete-class seam, not an interface, and
the contract leaks Cognee-era concepts (datasets, graph route enum, sidecar-backed
filtering) that a redesign should decide to either keep as the canonical model or
explicitly shed.

---

## 6. Verdict

### Strengths to keep
- **Single narrow seam.** Cognee is genuinely contained to `cognee_wrapper.py`;
  the rest of the stack speaks one five-method contract. This is the redesign's best
  asset.
- **Shared namespace module.** `hal0.memory.namespace` killed the #317
  drift-between-surfaces class of bug by single-sourcing the rule. Keep this pattern.
- **Defensive transport layer.** Hand-rolled validation, server-injected `source`
  (anti-impersonation), `private:` prefix rejection, consistent error envelopes,
  best-effort agent hooks that never wedge the loop. The audit trail is real.
- **Rerank integration** is a clean optional second pass that fails safe.

### Weaknesses that motivate redesign
- **Two-store split with a fragile join.** The sidecar SQLite, not Cognee, is the
  real source of truth for the schema and all filtering; the two are stitched by
  **text equality** (§4.3). This is the deepest structural smell.
- **Cognee's graph half is dead weight.** Kuzu + cognify cost ingestion latency and
  disk but are never read (§4.5). Either commit to the graph or drop the engine that
  ships it.
- **No formal engine interface.** The swap seam is a concrete class; an explicit ABC
  / Protocol (plus a conformance test suite) would de-risk the very swap this audit
  is preparing (§5).
- **Real integration path is under-tested.** Transport is well covered against
  stubs; the Cognee-touching path is `slow`/excluded from the default gate (§4.4).
- **Missing operability.** No CRUD CLI, no dataset explorer UI (despite PLAN), no
  real counts, no `reads` metric, no hygiene/TTL/eviction, unbounded growth.
- **Latent route mismatch** in the Hermes client (§4.2) shows the shim layer drifted
  from the routes without anything catching it.

**Bottom line:** the *plumbing* (namespace resolution, transport, audit, the single
seam) is solid and worth carrying forward. The *engine choice and its storage model*
— Cognee + a shadow SQLite joined by text, with an unused graph — are the parts the
redesign should put on the table. Whatever replaces Cognee should be hidden behind a
real interface (not just a concrete class), own its own filtering (retiring the
sidecar), and either use a graph or not ship one.
