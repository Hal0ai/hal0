# Phase 8 — skill-informed audit (2026-05-22)

- **Status:** Draft
- **Date:** 2026-05-22
- **Drivers:** `mcp-builder` + `rag-implementation` agent skills, audited against shipped Phase 8 code

## Scope

Audit of ADR-0004 (admin MCP), ADR-0005 (Cognee memory MCP), and `src/hal0/mcp/{admin,memory,approval_queue}.py` against guidance in two newly installed agent skills:

- `anthropics/skills@mcp-builder` (59K installs)
- `wshobson/agents@rag-implementation` (9K installs)

Phase 8 was marked done in `PLAN.md` §15 on 2026-05-22; ADRs 0004 + 0005 landed alongside. This audit captures gaps surfaced by the skill content that the design conversation did not catch.

## Aligned (no action needed)

- **Transport.** Skill recommends Streamable-HTTP stateless JSON for remote servers. Shipped: `mcp.server.fastmcp.FastMCP` Streamable-HTTP, mounted via `app.mount("/mcp/admin", admin.asgi_app())` (`src/hal0/mcp/admin.py:1-20`).
- **API-coverage rule.** Skill recommends comprehensive API coverage with consistent prefixes. ADR-0004 §4's "tool ships iff it maps to an existing `/api/*` route" is stricter than the skill — no new privileged surface, no parallel API to maintain.
- **Two-tier scope policy.** Skill recommends `readOnlyHint` / `destructiveHint` annotations. ADR-0004 §4 already encodes this as autonomous-read / autonomous-write / gated-destructive at the policy layer. (Tool-level annotation plumbing still missing — see G1.)
- **Metadata filtering.** Skill recommends tag + date filters on retrieval. ADR-0005 §2 `memory_search` accepts `tags`, `before`, `after`.
- **Bundle-don't-build for memory.** Choosing Cognee over hand-rolling a vector store matches the skill's "use a real vector store" advice.

## Gaps

### G1 — MCP tool annotations not set on shipped tools

**Source:** `mcp-builder` Phase 2.3.

The skill requires every tool to set `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. ADR-0004 encodes the *policy* (which tools are read vs destructive) but `grep -n "readOnlyHint\|destructiveHint\|idempotentHint\|openWorldHint" src/hal0/mcp/admin.py` returns zero matches.

Without SDK-level annotations, MCP clients can't surface the right warning UX before invoking a destructive tool — clients have to read ADR-0004 to know `model_delete` is destructive. Approval gating still works server-side, but client-side warnings won't fire.

**Action:** add annotations to all `@mcp.tool` registrations in `admin.py`. Mechanical PR, no behaviour change.

### G2 — No MCP evaluation suite

**Source:** `mcp-builder` Phase 4.

The skill mandates a 10-question XML eval suite per server: read-only, verifiable, complex, stable, scored against the deployed MCP. Phase 8 ships only:

- A `memory_add → memory_search` round-trip smoke test (ADR-0005 pending items).
- A nightly pi-coder shim CI test (ADR-0004 §3).

Neither measures *agent-task-completion quality* through the admin MCP. Without an eval suite there is no quality regression bar before agent v0.3.

**Action:** add `tests/mcp/admin-eval.xml` + `tests/mcp/memory-eval.xml` per the skill's XML format. Run via MCP Inspector or a small harness script. Amend ADR-0004 §7 (server-side hardening) with an eval-suite subsection. ~half-day of work.

### G3 — Embedding model not pinned in ADR-0005

**Source:** `rag-implementation` §2.

ADR-0005 §6 mentions Cognee's defaults (SQLite + LanceDB + Kuzu) but does not pin the embedding model. Cognee's stock default is `BAAI/bge-small-en-v1.5` (384-dim). The skill's 2026 table recommends `voyage-3-large` (cloud) or `bge-large-en-v1.5` (local, 1024-dim) — a meaningful quality delta for long memory queries.

`grep -n "embedding\|embed_model\|embedder" src/hal0/mcp/memory.py` returns zero hits, confirming we're sitting on whatever default Cognee picks at install time.

The home-AI-box position argues for **serving the embedder from your own embed slot** rather than pulling weights via Cognee's bundled pipeline. You already operate an embed slot; pointing Cognee at it via OpenAI-compatible endpoint closes the loop and removes the silent external dep.

**Action:** amend ADR-0005 with embedding model + dimension. Wire `cognee.embedding_config` in `CogneeWrapper` to point at hal0's embed slot. Smoke-test dimension match (Kuzu/Lance schemas pin dim — silent mismatches corrupt the index).

### G4 — `memory_search` doesn't use the rerank slot

**Source:** `rag-implementation` §4.

The skill is emphatic that reranking is the single highest-leverage retrieval-quality improvement. You already operate a rerank slot at port 8086 serving `bge-reranker-v2-m3-q4_k_m` (per [`hal0_rerank_slot_wiring`](../../README.md#)). ADR-0005 §2 `memory_search` returns results scored only by Cognee's vector similarity — no second-pass rerank.

`grep -n "rerank" src/hal0/mcp/memory.py` returns zero hits. The plumbing is pure: pull top-k from Cognee, rerank against `/v1/rerankings`, return top-n. No new infrastructure.

**Action:** add `rerank: bool = False` to `memory_search` (off by default for v0.2; flip to default-on in v0.3 once eval suite from G2 confirms quality lift). Wire the rerank slot call into `CogneeWrapper.search`.

### G5 — No hybrid search (BM25 + dense)

**Source:** `rag-implementation` §3 Pattern 1.

`memory_search` is dense-only. Skill recommends BM25 + dense ensemble with Reciprocal Rank Fusion (typical 0.3/0.7 weights). For short factual memories, BM25 catches exact-match noun phrases vector search blurs out.

Cognee 0.2.x supports hybrid via configuration but it isn't enabled in `CogneeWrapper`.

**Action:** **Phase 9 candidate**, not v0.2. File against ADR-0006 (advanced memory) as a tracked deferral. Skill considers this baseline-quality, so we should not ship v1.0 without it.

## Documented divergences (note, do not act)

### D1 — Python (FastMCP) over TypeScript (MCP SDK)

**Source:** `mcp-builder` §1.3.

Skill explicitly recommends TypeScript ("models generate TS more reliably, MCPB compat, broader SDK"). We shipped Python (FastMCP). Justified: hal0 is a Python codebase, FastMCP is the upstream Python SDK, no MCPB distribution target.

**Action:** add one sentence to ADR-0004 §1 noting the divergence ("Python chosen over skill-recommended TypeScript because the orchestrator is Python; divergence accepted"). Rolls into the G1 PR. Prevents a future contributor from re-litigating.

## Suggested execution order

| # | Item | PR size | Why this order |
|---|------|---------|----------------|
| 1 | G1 (annotations) + D1 (ADR note) | small | Mechanical, low-risk, half-hour. Unblocks client-side warning UX. |
| 2 | G3 + G4 (embedding pin + rerank) | medium | Biggest retrieval-quality win available. G3 first so dimension is locked before G4's rerank consumes the embeddings. |
| 3 | G2 (eval suites) | medium | Establishes a quality bar before v0.3 agent work begins. Required to validate G4's quality lift before flipping default-on. |
| 4 | G5 → ADR-0006 deferral | none | File, do not implement in v0.2. |

## References

- `anthropics/skills@mcp-builder` — `~/.agents/skills/mcp-builder/SKILL.md`
- `wshobson/agents@rag-implementation` — `~/.agents/skills/rag-implementation/SKILL.md`
- ADR-0004 — `docs/internal/adr/0004-agents.md`
- ADR-0005 — `docs/internal/adr/0005-memory-engine-cognee.md`
- PLAN.md §15 Phase 8
- `src/hal0/mcp/admin.py`, `src/hal0/mcp/memory.py`, `src/hal0/mcp/approval_queue.py`
