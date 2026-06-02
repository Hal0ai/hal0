# Hindsight Feature Inventory — Research Dossier

**Subject:** Hindsight, the biomimetic agent-memory system by vectorize.io
**Purpose:** Exhaustive, accurate feature inventory for hal0 platform-integration planning.
**Author:** AI Memory Systems research pass, 2026-06-02.
**Primary sources:** the locally-installed `hindsight-docs` skill at
`~/.agents/skills/hindsight-docs/` (SKILL.md, `references/best-practices.md`,
`references/faq.md`, `references/openapi.json`, all of `references/developer/**`,
`references/developer/api/**`, `references/sdks/**`, `references/changelog/**`),
supplemented by WebFetch of the live docs (`hindsight.vectorize.io`) for the
`sdks/integrations/{local-mcp,hermes,claude-code}` pages and `/templates`, which
are NOT shipped in the local skill.

> **Citation convention.** Local skill files are cited by their path under
> `references/…`. Live-doc-only material is flagged `[web: <url>]`. Where the
> local skill and live docs diverge or a topic is thin, it is called out
> explicitly in the relevant section and again in the "Gaps & ambiguities"
> section at the end.

---

## 0. What Hindsight is, in one paragraph

Hindsight is a self-hostable, biomimetic **memory engine for AI agents** — a
FastAPI/PostgreSQL service exposing three core verbs (`retain`, `recall`,
`reflect`) plus an automatic background **observation-consolidation** loop and a
built-in **MCP server**. It is explicitly positioned *against* stateless RAG: it
stores structured facts + an entity knowledge graph + temporal grounding rather
than raw chunks, retrieves with four parallel strategies fused by RRF and a
cross-encoder, and reasons through a configurable per-bank "personality"
(missions, directives, disposition traits). Memory is partitioned into isolated
**memory banks**. It ships Docker / Helm / pip / embedded-Python deployment
modes and can run **fully local with no cloud dependency** (embedded PostgreSQL +
local embedder + local reranker + a built-in llama.cpp LLM).
(`SKILL.md`; `references/developer/index.md`; `references/developer/rag-vs-hindsight.md`)

---

## 1. Core model — "biomimetic memory", and retain / recall / reflect

### 1.1 What "biomimetic" means here

The biomimicry is in the *memory lifecycle*, not the math. Hindsight mimics how
human memory works:

- **Encoding with meaning, not verbatim storage.** Raw content is never stored as
  the memory; an LLM extracts facts, entities, relationships, emotions, and
  causal reasoning at write time. ("Alice joined Google last spring and was
  thrilled" → core fact + emotion + reasoning, queryable as "Why did Alice join
  Google?") (`references/developer/retain.md`)
- **Consolidation into beliefs.** After writes, a background engine deduplicates
  overlapping facts into **observations** — durable, evidence-grounded beliefs
  that strengthen, weaken, or get reconciled as new evidence arrives, preserving
  the *history* of a changing belief rather than overwriting it.
  (`references/developer/observations.md`)
- **Two temporal dimensions** like episodic memory: *when it happened*
  (occurrence time) vs *when you learned it* (mention time).
  (`references/developer/retain.md`)
- **Disposition / personality** that colours reasoning, plus pre-computed
  "mental models" that act like ready recall of frequently-needed knowledge.
  (`references/developer/reflect.md`, `.../api/mental-models.md`)

### 1.2 The three operations

| Op | Uses LLM? | Uses observations? | Disposition? | Output |
|----|-----------|--------------------|--------------|--------|
| **retain** | Yes (extraction) | No | No | Memory IDs |
| **recall** | No | Yes | No | Ranked facts (+ observations) |
| **reflect** | Yes (generation) | Yes | Yes | Reasoned answer + citations |

(`references/developer/api/main-methods.md`)

**Retain** — ingests raw content (conversation array preferred, prefixed plain
text or markdown acceptable; never pre-summarize). Pipeline: chunk → LLM fact
extraction → entity recognition + resolution (nickname/co-occurrence
disambiguation) → build a 4-edge knowledge graph (entity, temporal, semantic,
causal links) → embed → store. Then it *automatically* fires background
consolidation. Key params: `content`, `context` (high-impact on quality — always
set it), `document_id` (stable ID = upsert/replace), `timestamp` (enables
temporal ranking), `tags`, `metadata` (returned but **not filterable**),
`observation_scopes`, `async_` (default sync). Facts are typed `world` (about
others), `experience` (the bank's own actions/events), or `observation`
(consolidated). (`references/developer/retain.md`, `.../api/retain` via
best-practices, `references/best-practices.md`)

**Recall** — "TEMPR": four strategies run **in parallel** (semantic, keyword/BM25,
graph traversal, temporal), fused with Reciprocal Rank Fusion, then re-scored by
a cross-encoder, then three multiplicative boosts (recency, temporal proximity,
proof-count), then truncated to a **token budget** (`max_tokens`, default 4096)
rather than top-k. No numeric scores returned — consume in order. Key params:
`query` (≤500 tokens), `types` (`world`/`experience`/`observation`), `budget`
(`low`/`mid`/`high`), `max_tokens`, `query_timestamp` (anchors relative time +
recency), `tags`/`tags_match`, `tag_groups` (recursive and/or/not boolean tree),
`include.{entities,chunks,source_facts}`, `trace`.
(`references/developer/retrieval.md`, `references/developer/api/recall.md`)

**Reflect** — an **agentic loop** (≤10 iterations) with tools
`search_mental_models` → `search_observations` → `recall` → `expand` → `done`,
applied in that hierarchical priority. Must gather evidence before answering;
validates that every citation was actually retrieved; auto-verifies *stale*
observations against current facts. Shaped by the bank's `reflect_mission`,
`directives` (hard rules), and `disposition` traits. Key params: `query`,
`context`, `budget` (default `low` in the MCP tool / `mid` elsewhere),
`max_tokens`, `response_schema` (JSON-Schema → `structured_output`),
`tags`/`tags_match`, `include.{facts,tool_calls}`. Returns `text`, `based_on`
(memories/mental_models/directives used), `trace`, `structured_output`, `usage`.
(`references/developer/reflect.md`, `references/developer/mcp-server.md`)

### 1.3 Observations vs memories (the crux)

- **Memories / facts** = atomic, individually-extracted units (`world` /
  `experience`). Immutable record of what was said.
- **Observations** = a *third* fact type: deduplicated, evidence-grounded
  **beliefs** synthesized from multiple facts. Each tracks supporting source
  memories (with exact quotes), a **proof count**, and a computed **freshness
  trend** (`stable` / `strengthening` / `weakening` / `new` / `stale`). They are
  *refined, not overwritten* — a user who switched React→Vue yields an
  observation capturing the whole journey. Generated automatically in the
  background after retain; never part of the retain call itself. Deleting source
  memories cascades: derived observations are deleted and surviving co-source
  memories are reset for re-consolidation.
  (`references/best-practices.md`, `references/developer/observations.md`)

---

## 2. Memory banks

A **memory bank** is the unit of isolation — one isolated store of memories,
documents, entities, relationships, directives, mental models. Banks share no
data. Common patterns: one bank per user, or per agent; a shared bank + tags for
cross-user analysis. **Auto-created on first use**; configure before ingesting to
steer behavior. (`references/best-practices.md`,
`references/developer/api/memory-banks.md`)

**Lifecycle / management** (REST + SDK + CLI + MCP):
`create_bank`, `get_bank`, `get_bank_stats` (node/link counts),
`update_bank` (name + `config_updates`), `delete_bank` (wipes everything),
`clear_memories` (optionally by fact type — keeps the bank), plus
`/stats` for pending-consolidation counters. (`references/developer/mcp-server.md`,
`references/developer/admin-cli.md`)

**Config knobs** are managed by a **separate config API**
(`update_bank_config` / `get_bank_config` / `reset_bank_config`) so operational
settings change independently from identity. `get_bank_config` returns both the
**resolved** config (server defaults merged with overrides) and the raw
**overrides**. Knobs (all optional, server-wide defaults via env vars):

- **Extraction:** `retain_mission`, `retain_extraction_mode`
  (`concise`/`verbose`/`custom`), `retain_custom_instructions`,
  `retain_chunk_size` (default 3000 chars), `retain_chunk_batch_size`.
- **Entity classification:** `entity_labels` (controlled vocabulary; see §3),
  `entities_allow_free_form`.
- **Observations:** `enable_observations`, `enable_auto_consolidation`,
  `observations_mission`, `consolidation_llm_batch_size` (default 8),
  `consolidation_source_facts_max_tokens` (-1 = unlimited),
  `consolidation_source_facts_max_tokens_per_observation` (default 256).
- **Reflect personality:** `reflect_mission`, `disposition_skepticism`,
  `disposition_literalism`, `disposition_empathy` (each 1–5, default 3).
- **Directives:** hard rules (create/list/update/delete; `is_active`, `priority`,
  `tags`) injected into reflect prompts and *always* enforced — distinct from
  soft disposition.
- **Recall tuning:** `recall_budget_function` (`fixed`/`adaptive`),
  `recall_budget_fixed_{low,mid,high}` (100/300/1000),
  `recall_budget_adaptive_{low,mid,high}`, `recall_budget_{min,max}` (20/2000),
  `recall_include_chunks`, `recall_max_tokens`.
- **MCP:** `mcp_enabled_tools` (per-bank allowlist of tool names; `null` = all).
- **LLM safety:** `llm_gemini_safety_settings` (Gemini/Vertex only).

(`references/developer/api/memory-banks.md`, `references/developer/mcp-server.md`)

**Missions** are the single biggest quality lever (best-practices calls
misconfigured missions "the single biggest cause of low-quality memories"):
`retain_mission` steers extraction, `observations_mission` steers consolidation,
`reflect_mission` sets the reasoning persona. **Disposition affects `reflect`
only**, not `recall`. (`references/best-practices.md`)

---

## 3. Bank templates  *(user priority — detailed)*

A **bank template** is a declarative JSON **manifest** that provisions a fully
configured bank in a single API call instead of many. Use cases: replication
(stamp identical banks per user/agent), onboarding (known-good defaults),
sharing portable setups, shipping a recommended template alongside an
integration. (`references/developer/api/bank-templates.md`)

### 3.1 Manifest schema (`version: "1"`)

```json
{
  "version": "1",
  "bank": {
    "reflect_mission": "...",
    "retain_mission": "...",
    "retain_extraction_mode": "concise | verbose | custom | chunks",
    "retain_custom_instructions": "...",
    "retain_chunk_size": 2048,
    "disposition_skepticism": 3,
    "disposition_literalism": 3,
    "disposition_empathy": 3,
    "enable_observations": true,
    "observations_mission": "...",
    "entity_labels": ["PERSON", "ORGANIZATION"],
    "entities_allow_free_form": true
  },
  "mental_models": [
    {
      "id": "unique-lowercase-id",
      "name": "Human-Readable Name",
      "source_query": "The query that generates this mental model's content",
      "tags": ["optional", "tags"],
      "max_tokens": 2048,
      "trigger": {
        "refresh_after_consolidation": false,
        "fact_types": ["world", "experience", "observation"],
        "exclude_mental_models": false,
        "exclude_mental_model_ids": []
      }
    }
  ],
  "directives": [
    {
      "name": "directive-name",
      "content": "The directive instruction text",
      "priority": 0,
      "is_active": true,
      "tags": ["optional", "tags"]
    }
  ]
}
```

All three top-level sections (`bank`, `mental_models`, `directives`) are
**optional** — omit a section to leave that part untouched. Only fields you set
in `bank` become per-bank overrides; everything else inherits server/tenant
defaults. (`references/developer/api/bank-templates.md`)

> **Note / minor inconsistency:** the template `bank.entity_labels` example is a
> flat string array (`["PERSON","ORGANIZATION"]`), whereas the bank-config
> `entity_labels` schema (§3.4) is a list of richly-typed **label-group objects**.
> The bank-templates page lists `entity_labels` as `string[]`. Treat the
> object-form as authoritative for real label groups and verify what the
> template importer accepts before relying on rich labels in a manifest. Also,
> the template page lists `retain_extraction_mode` value `chunks`, which does not
> appear in the bank-config docs (which list only concise/verbose/custom) — flagged.

### 3.2 Import / export / dry-run / round-trip

- **Import:** `POST /v1/default/banks/{bank_id}/import` with the manifest body.
  Bank auto-created if absent. Mental models matched by `id` (update-or-create);
  directives matched by `name`. Mental-model content generates **asynchronously**
  — response carries `operation_ids` + `config_applied` /
  `mental_models_created` / `directives_created` counts.
- **Dry run:** add `?dry_run=true` — validates and returns what *would* happen;
  HTTP 400 with a detailed message on an invalid manifest.
- **Export:** `GET /v1/default/banks/{bank_id}/export` — returns only the
  **explicitly-set overrides** (not the resolved config), so the manifest stays
  portable. Round-trip = export from source bank, import into a new bank.
- **Schema endpoint:** `GET /v1/bank-template-schema` returns the live JSON
  Schema (also static at `bank-template-schema.json`).
- **Versioning:** `version` enables forward-compatible evolution — old manifests
  auto-upgrade on import; export always emits latest; manifests newer than the
  server are rejected with a clear error.

(`references/developer/api/bank-templates.md`)

### 3.3 Control-plane authoring + the Templates Hub

The Control Plane's bank-creation dialog has an "Import from template" toggle
(paste manifest JSON); any bank can be exported via **Actions → Export Template**
(copies manifest to clipboard). A public **Bank Templates Hub** at
`/templates` ships ready-to-use examples; custom templates are authored as plain
JSON manifests and contributed back via PR to the Hindsight repo.
(`references/developer/api/bank-templates.md`; `[web: hindsight.vectorize.io/templates]`)

The three example templates currently in the Hub `[web: /templates]`:

| Template | Purpose (verbatim summary) |
|----------|----------------------------|
| **Conversation** | Chat-based agents/assistants. Tracks user preferences, conversation patterns, builds a profile over time. |
| **Coding Agent** | Coding assistants. Remembers project architecture, technical decisions, coding patterns, user preferences across sessions. **High literalism** for precise technical recall. |
| **Personal Assistant** | Always-on personal assistants. Tracks commitments, routines, personal context across daily life. |

> The Hub landing page does not enumerate each template's full field values; to
> get the exact mission/disposition/mental-model/directive bodies you must open
> the individual template entry or fetch its JSON. (Flagged in Gaps.)

### 3.4 Entity-labels schema (used by templates and bank config)

A controlled vocabulary of `key:value` classification labels extracted at retain
time and stored **as entities** (so they auto-link memories in the graph and
improve semantic + BM25 retrieval). Each entry is a **label group**:

```json
{
  "entity_labels": [
    {
      "key": "memory_type",
      "description": "rule vs procedure",
      "type": "value",            // value | multi-values | text | map
      "optional": false,
      "tag": true,                 // also write key:value as a memory tag
      "values": [
        { "value": "rule",      "description": "Concise operating rule" },
        { "value": "procedure", "description": "Step-by-step instruction" }
      ]
    }
  ]
}
```

- `type: value` (pick one enum), `multi-values` (pick several), `text` (free-form
  string), `map` (structured entity with named typed `fields`, stored flat as
  `key:field:value`, e.g. `person:name:Alice`).
- `tag: true` makes the extracted label *also* a filterable tag — enabling a hard
  SQL `WHERE` filter at recall time across all four strategies (e.g. return only
  `memory_type:rule` memories). This is the recommended way to separate
  semantically-similar-but-different memories (rules vs runbooks).
- `entities_allow_free_form: false` disables ordinary named-entity extraction so
  only label entities are stored.

(`references/developer/api/memory-banks.md`, `references/best-practices.md`,
`references/developer/retain.md`)

---

## 4. Docs feature (Documents)

"Docs" in Hindsight = **Documents**: containers for retained content that provide
**source traceability** and bulk lifecycle. A document is created/updated by
retaining with a `document_id`; re-retaining the same ID **replaces** the prior
content (delete-old-facts → re-extract). When content is retained it is split
into **chunks** (raw text segments stored alongside extracted facts);
`include.chunks` in recall returns those raw segments for verbatim context.
(`references/developer/api/documents.md`)

Document operations: retain-with-`document_id` (single or `retain_batch`),
**get** (`original_text`, `content_hash`, `memory_unit_count`,
`nodes_by_fact_type`, timestamps), **update tags** (mutable `tags` only — no
re-processing; changing tags invalidates + re-queues derived observations),
**delete** (removes the doc and *all* its memories — irreversible), **list**
(filter by `q` substring on ID, by `tags`/`tags_match`, paginate).
File-upload endpoints exist (`file_convert_retain` operation: PDF/DOCX→text via a
`HINDSIGHT_API_FILE_PARSER` of `markitdown`/`iris`/`llama_parse`, overridable
per-request; conversion failures are non-retryable).
(`references/developer/api/documents.md`, `references/developer/api/operations.md`)

> Hindsight treats "documents" as *provenance + ingestion grouping*, not as a
> separately-served corpus. There is no separate "docs query" path distinct from
> recall/reflect — memories extracted from a document are retrieved through the
> normal pipeline; the document is the audit/back-link handle. The Claude Code
> integration adds a higher-level "knowledge pages" concept on top
> (`agent_knowledge_create_page`, page list/get) — that is integration-layer, not
> core API. (`[web: /sdks/integrations/claude-code]`; flagged in Gaps.)

---

## 5. Operations (admin/ops surface, async lifecycle)

All background work shares one queue (`async_operations` table) and one worker
pool. Operation **types**: `retain`, `retain_batch` (parent that splits large
submissions into child `retain` ops; aggregate status), `file_convert_retain`,
`consolidation` (bank-deduped), `refresh_mental_model`, `graph_maintenance`
(reconciles links/orphan entities/stale cooccurrences after deletes; bank-deduped),
`webhook_delivery`. (`references/developer/api/operations.md`)

**Lifecycle states:** `pending` → `processing` → `completed` | `failed` |
`cancelled`. Worker retries failed ops up to
`HINDSIGHT_API_WORKER_MAX_RETRIES`; deterministic failures skip retries.
**REST endpoints** (per bank): list (filter `status`/`type`/`limit`/`offset`/
`exclude_parents`), get status (optional `include_payload`), cancel (pending
only — 409 otherwise), retry (failed/cancelled only — resets to pending).

**Worker model.** By default the worker runs **in-process inside the API** (no
extra process, no external broker — PostgreSQL is the broker). For throughput,
disable the in-process worker (`HINDSIGHT_API_WORKER_ENABLED=false`) and run
dedicated `hindsight-worker` processes (same image, different entrypoint; metrics
port 8889; `/health` + `/metrics`). Per-worker concurrency is
`HINDSIGHT_API_WORKER_MAX_SLOTS` (default 10) with per-type reservations.
(`references/developer/services.md`, `references/developer/api/operations.md`)

**Admin CLI (`hindsight-admin`)** — bundled with `hindsight-api`:
`run-db-migration` (`--schema`; migrations also auto-run on startup unless
`HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP=false`), `backup OUTPUT.zip` /
`restore INPUT.zip` (REPEATABLE-READ consistent snapshot; restore wipes target
schema first), `decommission-worker <id>` / `decommission-workers` (release
stuck/zombie `processing` tasks back to `pending`), `worker-status` (inspect
running tasks per worker). **Zombie-op gotcha:** an unstable
`HINDSIGHT_API_WORKER_ID` (defaults to container hostname, which changes on
Docker restart) strands in-flight tasks — set a stable worker ID in production.
(`references/developer/admin-cli.md`, `references/developer/installation.md`)

**Audit logging** (off by default): `HINDSIGHT_API_AUDIT_LOG_ENABLED=true`
captures mutating ops into an `audit_log` table queryable at `/audit-logs`;
`HINDSIGHT_API_AUDIT_LOG_ACTIONS`, `…_RETENTION_DAYS`.
(`references/developer/configuration.md`)

---

## 6. Webhooks

Hindsight POSTs HTTP events to a configured URL. Configuration is **server-wide /
env-var driven** in the shipped skill (no per-bank create-webhook REST endpoint
is documented locally — the webhooks page says "registered per memory bank and
fire automatically" but does not show the registration API; flagged in Gaps):

- `HINDSIGHT_API_WEBHOOK_URL` — delivery URL (unset = disabled)
- `HINDSIGHT_API_WEBHOOK_SECRET` — HMAC signing secret (unset = unsigned)
- `HINDSIGHT_API_WEBHOOK_EVENT_TYPES` — comma list (default `consolidation.completed`)
- `HINDSIGHT_API_WEBHOOK_DELIVERY_POLL_INTERVAL_SECONDS` — default 30

(`references/developer/configuration.md`)

**Delivery semantics:** at-least-once (delivery task is committed in the same DB
transaction as the source op, so it survives a crash — your endpoint may receive
duplicates; dedupe on `operation_id`). A delivery fails on non-2xx or timeout
(default 30s); retries with exponential backoff at 5s / 5m / 30m / 2h / 5h, then
**permanent failure after 6 attempts**. (`references/developer/api/webhooks.md`)

**Event types & payloads** (documented):

1. **`consolidation.completed`** — after observation consolidation finishes for a
   bank. `data`: `observations_created`, `observations_updated`,
   `observations_deleted`, `error_message`. `status` ∈ `completed`/`failed`.
2. **`retain.completed`** — once per document after retain (sync or async); a
   batch of N fires N events. `data`: `document_id`, `tags`. For async retain the
   `operation_id` matches the retain API's; for sync it's a trace ID.

Every payload carries `event`, `bank_id`, `operation_id`, `status`, `timestamp`,
`data`. (`references/developer/api/webhooks.md`)

---

## 7. Retrieval (TEMPR pipeline in depth)

Four strategies run in parallel, then a multi-stage scoring pipeline:

1. **Semantic** — embedding cosine via pgvector HNSW; conceptual/paraphrase
   matches.
2. **Keyword (BM25)** — exact names/technical terms. **Five pluggable backends**
   via `HINDSIGHT_API_TEXT_SEARCH_EXTENSION`: `native` (Postgres tsvector — TF-IDF,
   Citus-OK), `vchord`, `pg_textsearch` (Timescale), `pgroonga` (CJK/mixed out of
   the box), `pg_search` (ParadeDB — the only true-BM25 Citus-compatible option,
   configurable tokenizer incl. jieba/lindera/ngram). BM25 is within-language; the
   `native` dictionary is set by `…_NATIVE_LANGUAGE` (default `english`).
3. **Graph traversal** — follows entity/semantic/causal links; score =
   `tanh(shared_entities×0.5) + kNN_semantic_link + causal_link` ∈ [0,3]
   (additive — independent evidence channels).
4. **Temporal** — parses time expressions, filters/ranks by occurrence date.

**Fusion → rank.** RRF with k=60, **all strategies weighted equally** (importance
from rank position, not source). Then a **cross-encoder** reranks the top
`HINDSIGHT_API_RERANKER_MAX_CANDIDATES` (default 300) candidates; raw logits are
sigmoid-normalized to [0,1], calibrated external rerankers pass through.
Then **three multiplicative boosts**: recency (α0.2, ±10%, linear 365-day decay),
temporal proximity (α0.2, ±10%, only on time-anchored queries), proof-count
(α0.1, +5% max, log curve for observations). Net swing ≈ +27% / −23%. Then
token-budget truncation by `max_tokens` (only `text` counts). Without a
cross-encoder (`rrf` provider / slim), synthetic [0.1,1.0] scores from RRF rank
are used so the boosts still work.
(`references/developer/retrieval.md`, `references/developer/index.md`)

**Budget → pipeline depth.** `low`/`mid`/`high` map (fixed mode) to recall budgets
100/300/1000, flowing into HNSW over-fetch, BM25 `LIMIT`, graph/temporal node
expansion. `adaptive` mode scales with `max_tokens`. Reranker pre-filter (300) is
independent of budget. Typical recall latency 100–600ms (CPU reranker is the
bottleneck; GPU or external reranker speeds it up).
(`references/developer/retrieval.md`, `references/developer/performance.md`)

---

## 8. Deployment / runtime — **CRITICAL for hal0: can it run fully local?**

### 8.1 Verdict: YES, fully local / self-hosted, zero cloud dependency

Every external dependency has a local substitute:

| Layer | Cloud option | **Fully-local option** |
|-------|--------------|------------------------|
| **Database** | Supabase/Neon/RDS/AlloyDB | **`pg0` embedded PostgreSQL** (default; `~/.hindsight/data/`) or self-hosted Postgres 14+ with a vector extension |
| **LLM** | OpenAI/Anthropic/Gemini/Groq/… | **built-in `llamacpp`** (auto-downloads Gemma 4 E2B GGUF ~3.5 GB, no API key), **Ollama**, **LM Studio**, llama.cpp, or any OpenAI-compatible local endpoint |
| **Embeddings** | OpenAI/Cohere/Google/ZeroEntropy | **`local`** SentenceTransformers (default `BAAI/bge-small-en-v1.5`, 384-dim, ~130 MB) or self-hosted **TEI** |
| **Reranker** | Cohere/ZeroEntropy/… | **`local`** cross-encoder (default `ms-marco-MiniLM-L-6-v2`, ~85 MB), `flashrank`, self-hosted TEI, or `rrf` (no neural rerank) |

So a hal0-style box can run Hindsight with: embedded pg0 + local BGE embedder +
local MiniLM reranker + a local LLM (llamacpp/Ollama/LM Studio) and **make no
network calls whatsoever**. (`references/developer/installation.md`,
`references/developer/models.md`, `references/developer/configuration.md`)

### 8.2 Model requirements & GPU/Strix-Halo constraints

- **LLM (write path, the bottleneck).** Fact extraction is structured, so a small
  fast model suffices — docs explicitly recommend `gpt-oss-20b`; the built-in
  llamacpp default is **Gemma 4 E2B**. Caveat: arbitrary models must support
  **≥65,000 output tokens** for reliable extraction, else set
  `HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS` lower (must stay >
  `RETAIN_CHUNK_SIZE`, default 3000). `none` is a valid provider value (recall-only,
  no LLM). (`references/developer/models.md`, `references/developer/configuration.md`)
- **Built-in llama.cpp knobs:** `HINDSIGHT_API_LLAMACPP_MODEL_PATH`,
  `…_GPU_LAYERS` (`-1` = all to GPU, `0` = CPU-only), `…_CONTEXT_SIZE` (8192),
  `…_CHAT_FORMAT` (auto), `…_NO_GRAMMAR`, `…_EXTRA_ARGS`. Requires the `local-llm`
  pip extra (`pip install 'hindsight-api-slim[local-llm]'`); the published Docker
  image does **not** bundle `llama-cpp-python` (use the `docker-compose/local-llm/`
  recipe). (`references/developer/configuration.md`)
- **GPU is optional everywhere.** "2 vCPUs on CPU-only is fine for development and
  basic workloads." The only GPU-sensitive component is the **local reranker
  (cross-encoder)** on the read path — under production traffic it benefits from a
  GPU or an external reranker. (`references/developer/installation.md`)
- **Strix-Halo specifics (hal0): the docs make no mention of AMD ROCm / iGPU /
  XDNA NPU acceleration.** The local embedder/reranker run on PyTorch/ONNX;
  llamacpp's `GPU_LAYERS` assumes a llama.cpp build with the right backend.
  Whether hal0's iGPU/NPU (via the existing Lemonade/ROCm stack) accelerates these
  is **not addressed by Hindsight docs** — for hal0, the safe default is CPU-only
  embedder+reranker, and route the heavy LLM extraction to hal0's existing
  Lemonade-served local model via the **OpenAI-compatible / Ollama / LM Studio
  provider** (point `HINDSIGHT_API_LLM_BASE_URL` at lemond:13305) rather than
  Hindsight's bundled llamacpp. (Inference, not Hindsight's responsibility —
  flagged in Gaps.) There is even a `jina-mlx` reranker for Apple-Silicon local
  inference, underscoring that hardware-specific accel is opt-in per provider.
  (`references/developer/models.md`)

### 8.3 Footprint & images

| Variant | RAM (min/rec) | Image size | Notes |
|---------|---------------|------------|-------|
| **Full** (`:latest`) | 1.5 / 2 GB | ~9 GB AMD64, ~3.7 GB ARM64 | Bundles local embedder + reranker; works out of the box (only the LLM is external) |
| **Slim** (`:slim` / `hindsight-api-slim`) | 0.5 / 1 GB | ~500 MB | No local models — requires external embedding + reranker providers (TEI/OpenAI/Cohere) |

(`references/developer/installation.md`)

### 8.4 Deployment modes

- **Docker** (single container, embedded pg0): API on `:8888`, Control Plane UI on
  `:9999`; persist `~/.hindsight-docker:/home/hindsight/.pg0`. Images Cosign-signed
  (keyless OIDC). Tags: `hindsight`, `hindsight-api`, `hindsight-control-plane`
  (+ `-slim`).
- **Helm/K8s** (`oci://ghcr.io/vectorize-io/charts/hindsight`): built-in or external
  Postgres; optional dedicated worker StatefulSet (`worker.enabled`,
  `worker.replicaCount`).
- **pip** (`hindsight-api` / `hindsight-api-slim`): `hindsight-api` CLI
  (`--port/--host/--workers/--log-level`); embedded pg0 by default or external
  `HINDSIGHT_API_DATABASE_URL`.
- **Embedded-in-Python** (`hindsight-all` / `-slim`): `HindsightServer`
  (in-process background thread) or `HindsightEmbedded` (managed subprocess daemon,
  idle-timeout auto-shutdown, shared across processes).
- **Windows** native (pg0 or external Postgres+pgvector; HF mirror for China).
- **Hindsight Cloud** (managed SaaS at `api.hindsight.vectorize.io`) — the only
  thing hal0 should *avoid* if it wants air-gapped operation.

(`references/developer/installation.md`)

### 8.5 The local MCP server (`hindsight-local-mcp`)  *(critical for agent wiring)*

A one-command, fully-local MCP server with embedded Postgres:

```bash
HINDSIGHT_API_LLM_API_KEY=sk-... uvx --from hindsight-api hindsight-local-mcp
# Local LLM, no API key:
HINDSIGHT_API_LLM_PROVIDER=ollama HINDSIGHT_API_LLM_MODEL=llama3.2 uvx --from hindsight-api hindsight-local-mcp
```

- **HTTP transport** (not stdio), endpoint `http://localhost:8888/mcp/`.
- Data persists in `~/.pg0/hindsight-mcp/`; first run downloads the ~100 MB
  embedder + inits the DB.
- Multi-bank mode (default, `bank_id` per request or `X-Bank-Id` header) or
  single-bank (`/mcp/<bank-id>/`).
- Exposes 29 tools (multi-bank) / 26 (single-bank).

The general MCP server is built into the API (enabled by default, mounted at
`/mcp`, disable with `HINDSIGHT_API_MCP_ENABLED=false`). Per-bank endpoints:
`/mcp/{bank_id}/`. Bank resolution priority: URL path > `X-Bank-Id` header >
`HINDSIGHT_MCP_BANK_ID` (default `default`). Auth is **open by default**; enable
the `ApiKeyTenantExtension` (`HINDSIGHT_API_TENANT_API_KEY`) to require
`Authorization: Bearer …`. Single-bank mode exposes 27 tools; multi-bank exposes
~30 incl. `list_banks`/`create_bank`/`get_bank_stats`. Full tool list:
retain, sync_retain, recall, reflect, mental-model CRUD + refresh + clear,
directives CRUD, list/get memory, list/get/delete document, list/get/cancel
operation, list_tags, get/update/delete bank, clear_memories.
(`references/developer/mcp-server.md`; `[web: /sdks/integrations/local-mcp]`)

---

## 9. Integrations — MCP, Hermes, Claude Code (the three hal0 must support)

### 9.1 MCP (Model Context Protocol)

The canonical wiring surface. Built-in HTTP MCP server (§8.5). Add to Claude
Code:

```bash
claude mcp add --transport http hindsight http://localhost:8888/mcp/
# with auth:
claude mcp add --transport http hindsight http://localhost:8888/mcp \
  --header "Authorization: Bearer your-secret-key" --header "X-Bank-Id: my-bank"
```

Per-bank isolation via URL path or `X-Bank-Id`. Per-bank `mcp_enabled_tools`
allowlist restricts which tools a connection can call. Custom MCP tools can be
added without forking via the `MCPExtension`
(`HINDSIGHT_API_MCP_EXTENSION=pkg.mod:Class`).
(`references/developer/mcp-server.md`, `references/developer/extensions.md`)

### 9.2 Hermes (Hermes Agent framework)  `[web: /sdks/integrations/hermes]`

Hindsight plugs into Hermes via the `hermes_agent.plugins` entry point.
Behaviour:

- **Auto-recall** (`pre_llm_call` hook): query memories, inject as ephemeral
  system-prompt context.
- **Auto-retain** (`post_llm_call` hook): store the user/assistant exchange.
- **Three explicit tools:** `hindsight_retain`, `hindsight_recall`,
  `hindsight_reflect`.

Setup: `hermes memory setup` → choose "hindsight"; verify `hermes memory status`;
disable Hermes's built-in memory tool (`hermes tools disable memory`) to avoid
conflicts. Manual config:

```
hermes config set memory.provider hindsight
echo "HINDSIGHT_API_KEY=your-key"  >> ~/.hermes/.env
echo "HINDSIGHT_API_URL=https://api.hindsight.vectorize.io" >> ~/.hermes/.env
```

Modes: **cloud** (API key) or **local** (embedded server + built-in Postgres,
needs an LLM key, daemon auto-starts on a configurable port, default **9077**).
Integration modes: `hybrid` (default, auto + tools), `context` (auto only),
`tools` (explicit only). Env knobs: `HINDSIGHT_MODE`, `HINDSIGHT_AUTO_RECALL`,
`HINDSIGHT_AUTO_RETAIN`, `HINDSIGHT_RECALL_BUDGET`. Works with Hermes Gateway
(Telegram/Discord/Slack) with per-message recall.

> **hal0 note:** hal0's bundled Hermes-Agent would point `HINDSIGHT_MODE=local`
> (or `HINDSIGHT_API_URL` at a self-hosted Hindsight in CT 105) — *not* the
> vectorize cloud. The `local` mode's embedded daemon on `:9077` collides
> conceptually with hal0's existing port map; prefer pointing Hermes at a
> standalone self-hosted Hindsight API rather than its auto-embedded daemon.

### 9.3 Claude Code  `[web: /sdks/integrations/claude-code]`

Installed as a Claude plugin:

```
claude plugin marketplace add vectorize-io/hindsight
claude plugin install hindsight-memory
```

Connection modes: external API (`hindsightApiUrl` + `hindsightApiToken`), local
auto-managed daemon (needs `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/
`HINDSIGHT_LLM_PROVIDER=claude-code`), or auto-detect an existing
`hindsight-embed` on **port 9077**. Config in `~/.hindsight/claude-code.json`
(precedence: defaults → plugin → user config → env).

Behaviour:
- **Auto-recall** on every prompt → injected as invisible `additionalContext`
  (`recallBudget` default `mid`, `recallMaxTokens` 1024).
- **Auto-retain** after responses / every N turns (`retainEveryNTurns` default
  10; SessionEnd flush).
- **Knowledge tools** (`enableKnowledgeTools`, default off): `agent_knowledge_*`
  MCP tools (`agent_knowledge_recall`, `_ingest`, `_create_page`, page list/get).
- **`/hindsight-memory:create-agent`** skill scaffolds a new agent with isolated
  memory (understands SDA project layouts).
- **Bank selection:** static `bankId` default `claude_code`, or **dynamic**
  (`dynamicBankId: true` + `dynamicBankGranularity` over `agent`/`project`/
  `session`/`channel`/`user`); explicit directory→bank mapping + Git-worktree
  support; `{user_id}` template variable for retain tags; recall from additional
  banks alongside the primary. Tool calls retained as structured JSON.

Env knobs mirror Hermes: `HINDSIGHT_AUTO_RECALL`, `HINDSIGHT_RECALL_BUDGET`,
`HINDSIGHT_RECALL_MAX_TOKENS`, `HINDSIGHT_AUTO_RETAIN`,
`HINDSIGHT_ENABLE_KNOWLEDGE_TOOLS`. The Claude-Code integration now **defaults
recall types to observations** (changelog 0.7.0).
(`references/changelog/integrations/claude-code.md`; `[web: /sdks/integrations/claude-code]`)

> **Broader integration ecosystem** (changelog lists, not all detailed): AG2,
> AgentCore, AI SDK, AutoGen, CrewAI, Dify, Flowise, LangGraph, **LiteLLM**,
> LlamaIndex, n8n, OpenAI Agents, OpenClaw/NemoClaw, OpenCode, Codex, Pydantic-AI,
> Smolagents, Strands, Pipecat, Vapi, Cloudflare OAuth proxy. LiteLLM is doubly
> relevant to hal0 (gateway already in the stack at CT 200) — Hindsight both *uses*
> LiteLLM as an LLM/embedding/reranker provider and *integrates* into a
> LiteLLM-fronted pipeline. (`references/changelog/integrations/`)

---

## 10. Best practices & anti-patterns (headline guidance)

**Do:**
- Configure missions before ingesting — be specific about domain + *what to
  ignore*; vague missions are the #1 cause of noisy memories.
- Pass the richest representation (conversation JSON > prefixed text >
  pre-summarized — never pre-summarize).
- Always set `context`; always set `timestamp` (omitting it disables temporal
  ranking); use stable `document_id` for upsert.
- Multi-tenant: tag every user retain with at least `user:<id>` and recall with
  `any_strict`/`all_strict` (plain `any` leaks across users). Naming conventions:
  `user:`, `session:`, `team:`, `topic:`, `scope:`.
- Use `tag:true` entity labels to hard-filter semantically-similar-but-distinct
  memory shapes (rules vs procedures).
- One mental model per knowledge dimension (not "everything about the user").
- Default recall `budget=mid`; reserve `high` for explicit deep flows.
- Enable `include.facts` in production for audit trails.
- Retain end-of-turn, recall at start of next turn — never retain+recall in the
  same turn (writes aren't indexed yet).

**Anti-patterns:** pre-summarizing; random-UUID `document_id` (creates dupes);
omitting `context`; using `metadata` for filtering (it's not filterable — use
tags); `tags_match=any` for multi-tenant banks; retain+recall same request; one
mega mental model; `high` budget for every recall; missing `timestamp`.
(`references/best-practices.md`)

---

## 11. hal0 relevance — feature-by-feature

| Hindsight feature | How it plugs into hal0 (self-hosted agent platform) |
|-------------------|------------------------------------------------------|
| **Fully-local stack (pg0 + local embedder/reranker + local LLM)** | Core fit: run the API in CT 105 with embedded Postgres + local BGE/MiniLM, point the LLM at hal0's Lemonade-served model via the OpenAI-compatible/Ollama provider. No cloud calls. |
| **retain/recall/reflect** | The three verbs map cleanly onto hal0's memory/agent layer; reflect's structured-output + citations suit the dashboard's audit/inbox surfaces. |
| **Observations + freshness** | Gives hal0 evolving per-agent beliefs (e.g. "user switched stacks") for free — no app-side consolidation logic. |
| **Memory banks** | One bank per hal0 agent / per user — natural isolation; auto-create means zero provisioning friction. |
| **Bank templates** | Ship hal0-curated templates (coding-agent, personal-assistant, support) as JSON manifests; one `import` call provisions an agent's memory persona; export = portable agent personality. |
| **Documents + chunks** | Source traceability for ingested files/conversations; deep-link memories back to hal0 sources via `metadata`. |
| **Operations API + admin CLI** | Drives async-job polling already standardized in hal0 (PLAN §9 "poll-to-terminal"); `backup/restore` + `worker-status`/`decommission-*` fit hal0's ops/runbook discipline. |
| **Webhooks** | `consolidation.completed` / `retain.completed` can drive hal0 dashboard live updates / journal events; HMAC-signable. (But registration API is env-only locally — verify per-bank registration.) |
| **TEMPR retrieval (esp. pgroonga/pg_search)** | Multilingual/CJK BM25 backends matter if hal0 serves non-English; otherwise `native` + local pipeline is enough. |
| **Built-in MCP server** | hal0's MCP host platform (the `/agents` view) can register Hindsight's `/mcp/{bank_id}/` as an aftermarket MCP server per agent — exactly hal0's "host arbitrary MCP servers" model. Per-bank `mcp_enabled_tools` = capability gating. |
| **Hermes integration** | hal0 bundles Hermes-Agent — wire `HINDSIGHT_MODE=local`/self-hosted URL, `hybrid` mode, disable Hermes's built-in memory tool. |
| **Claude Code integration** | hal0 dev workflows (Claude Code) get persistent project memory via the plugin pointed at the self-hosted API; dynamic per-project banks + Git-worktree mapping fit hal0's worktree-heavy agent dispatch. |
| **Extensions (Tenant/Http/MCP/Validator)** | hal0 can add its own auth (X-hal0-Agent header → custom TenantExtension), rate limits (OperationValidator), and bespoke MCP tools without forking. |
| **Monitoring (Prometheus `/metrics` + OTel)** | Drops into hal0's existing Prometheus/Grafana expectations; metric names like `hindsight_recall_duration_seconds`. |
| **Slim image** | If hal0 wants Hindsight tiny + offloads embed/rerank to existing hal0 capability slots (TEI-style), the 500 MB slim image is the play. |

---

## Gaps & ambiguities (do NOT assume)

1. **Strix-Halo / ROCm / NPU acceleration is undocumented.** Hindsight docs never
   mention AMD iGPU, ROCm, or XDNA. The local embedder/reranker run on PyTorch/ONNX
   and llamacpp `GPU_LAYERS` needs an appropriately-built llama.cpp. **Don't assume
   hal0's iGPU/NPU accelerates Hindsight's local models** — plan CPU embed/rerank
   and route LLM extraction to hal0's Lemonade-served model via an
   OpenAI-compatible/Ollama provider. Validate empirically.
2. **Webhook registration API.** The webhooks page says webhooks are "registered
   per memory bank" but the shipped skill only documents *server-wide env-var*
   config and the two event types. A per-bank webhook CRUD REST endpoint, if it
   exists, was not in the local skill — verify against `openapi.json` / live API
   before designing per-agent webhook fan-out.
3. **Bank-template `entity_labels` form mismatch.** Template manifest lists
   `entity_labels` as `string[]`; bank-config documents rich label-group objects.
   Also `retain_extraction_mode: chunks` appears only in the template schema.
   Confirm what the importer accepts.
4. **Templates Hub field values.** `/templates` names three templates but doesn't
   publish each one's full mission/disposition/mental-model bodies on the landing
   page — fetch individual entries for exact contents.
5. **"Docs feature" = Documents (provenance), not a separate served corpus.** The
   richer "knowledge pages" concept is **Claude-Code-integration-layer** only, not
   core API. Don't conflate.
6. **Hermes/Claude-Code integration details are live-doc-only** (not in the local
   skill) and are summarized from a single fetched page each; versions move fast
   (Claude-Code at 0.7.0). Re-verify exact config keys at integration time.
7. **`hindsight-control-plane`** UI is a separate Next.js process; if hal0 exposes
   it, note it emits its own routes (treat like any standalone web UI — own
   port/subdomain).
8. Local skill **does not ship** `references/cookbook/**` or
   `references/sdks/integrations/**` (only the changelog stubs exist locally);
   recipe-level patterns were not available offline.
